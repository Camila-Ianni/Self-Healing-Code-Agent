"""Ephemeral validation sandboxes: validates patches in isolated/constrained environments."""

from __future__ import annotations

import abc
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .model import TestResult


class SandboxError(RuntimeError):
    """Raised when the isolated validation environment is unavailable or unsafe."""


def _text(value: str | bytes | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


def run_tests(command: str, cwd: Path, timeout: int = 60) -> TestResult:
    """Run the currently checked-out code to collect initial failure evidence."""
    try:
        completed = subprocess.run(command, cwd=cwd, shell=True, text=True, capture_output=True, timeout=timeout, check=False)
        output = (completed.stdout + "\n" + completed.stderr).strip()
        return TestResult(completed.returncode == 0, output, completed.returncode)
    except subprocess.TimeoutExpired as error:
        output = (_text(error.stdout) + "\n" + _text(error.stderr)).strip()
        return TestResult(False, f"TEST TIMEOUT after {timeout}s\n{output}", 124)


class Sandbox(abc.ABC):
    """Abstract base class representing a validation sandbox environment."""

    @abc.abstractmethod
    def validate(self, command: str, root: Path, target: Path, proposed: str) -> TestResult:
        """Validate the proposed changes inside a copy of the project in the sandbox."""
        pass


class DockerSandbox(Sandbox):
    """Copies a project into a constrained, disposable Docker container for validation."""

    def __init__(self, image: str = "self-healing-sandbox:latest", timeout: int = 180):
        self.image = image
        self.timeout = timeout
        self.fallback_triggered = False

    def validate(self, command: str, root: Path, proposed_patches: dict[Path, str] | Path, proposed: str | None = None) -> TestResult:
        if isinstance(proposed_patches, Path):
            patches = {proposed_patches: proposed}
        else:
            patches = proposed_patches

        sandbox_tmp = tempfile.mkdtemp(prefix="self-healing-")
        try:
            sandbox_root = Path(sandbox_tmp) / "workspace"
            shutil.copytree(
                root,
                sandbox_root,
                ignore=shutil.ignore_patterns(".git", ".venv", ".self-healing-backups", "__pycache__", ".pytest_cache"),
            )
            for target, proposed_content in patches.items():
                try:
                    relative_target = target.resolve().relative_to(root.resolve())
                except ValueError as exc:
                    raise SandboxError(f"El archivo a validar ({target}) debe pertenecer al proyecto.") from exc
                (sandbox_root / relative_target).write_text(proposed_content, encoding="utf-8")

            docker_command = [
                "docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", "768m",
                "--cpus", "2.0", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--tmpfs", "/build:rw,exec,nosuid,size=512m", "-e", "HOME=/tmp",
                "-e", "GOTMPDIR=/build", "-e", "GOCACHE=/build/cache",
                "-v", f"{sandbox_root}:/workspace:rw", "-w", "/workspace",
                self.image, "sh", "-c", command,
            ]
            try:
                completed = subprocess.run(docker_command, text=True, capture_output=True, timeout=self.timeout, check=False)
            except FileNotFoundError as exc:
                raise SandboxError("Docker no está instalado. Instalalo o ejecutá la demo en un entorno con Docker.") from exc
            except subprocess.TimeoutExpired as exc:
                output = (_text(exc.stdout) + "\n" + _text(exc.stderr)).strip()
                return TestResult(False, f"SANDBOX TIMEOUT after {self.timeout}s\n{output}", 124)

            output = (completed.stdout + "\n" + completed.stderr).strip()
            if completed.returncode == 125:
                raise SandboxError(
                    f"No se pudo iniciar el sandbox Docker ({output}). Construí la imagen: "
                    "docker build -t self-healing-sandbox:latest -f Dockerfile.sandbox ."
                )
            return TestResult(completed.returncode == 0, output, completed.returncode)
        finally:
            shutil.rmtree(sandbox_tmp, ignore_errors=True)


class LocalSubprocessSandbox(Sandbox):
    """Copies a project into a temporary local directory and runs tests with strict resource/env limits."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self.fallback_triggered = False

    def validate(self, command: str, root: Path, proposed_patches: dict[Path, str] | Path, proposed: str | None = None) -> TestResult:
        if isinstance(proposed_patches, Path):
            patches = {proposed_patches: proposed}
        else:
            patches = proposed_patches

        # Setup resource limits inside the subprocess (POSIX only)
        def limit_resources():
            if sys.platform == "win32":
                return
            import resource
            
            # Limit CPU time to avoid infinite loops (soft: 30s, hard: 45s)
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (30, 45))
            except Exception:
                pass
            
            # Limit file sizes created by subprocesses to avoid disk filling (soft/hard: 500MB)
            try:
                resource.setrlimit(resource.RLIMIT_FSIZE, (500 * 1024 * 1024, 500 * 1024 * 1024))
            except Exception:
                pass
            
            # Limit file descriptors (soft/hard: 1024)
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
            except Exception:
                pass

        sandbox_tmp = tempfile.mkdtemp(prefix="self-healing-local-")
        try:
            sandbox_root = Path(sandbox_tmp) / "workspace"
            shutil.copytree(
                root,
                sandbox_root,
                ignore=shutil.ignore_patterns(".git", ".venv", ".self-healing-backups", "__pycache__", ".pytest_cache"),
            )
            
            # Apply proposed patches in sandbox
            for target, proposed_content in patches.items():
                try:
                    relative_target = target.resolve().relative_to(root.resolve())
                except ValueError as exc:
                    raise SandboxError(f"El archivo a validar ({target}) debe pertenecer al proyecto.") from exc
                (sandbox_root / relative_target).write_text(proposed_content, encoding="utf-8")
            
            # Make sure build/cache directories exist in temporary folder
            tmp_dir = sandbox_root / "tmp"
            tmp_dir.mkdir(exist_ok=True)
            gocache_dir = sandbox_root / "gocache"
            gocache_dir.mkdir(exist_ok=True)
            gotmp_dir = sandbox_root / "gotmp"
            gotmp_dir.mkdir(exist_ok=True)

            # Build safe environment: remove credential/keys, redirect HOME and Go folders
            safe_env = {
                "PATH": os.environ.get("PATH", ""),
                "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                "HOME": str(tmp_dir),
                "GOTMPDIR": str(gotmp_dir),
                "GOCACHE": str(gocache_dir),
                "PYTHONPATH": str(sandbox_root),
            }
            
            # Keep VIRTUAL_ENV if present to preserve packages
            if "VIRTUAL_ENV" in os.environ:
                safe_env["VIRTUAL_ENV"] = os.environ["VIRTUAL_ENV"]
                # Prefix PATH with virtualenv bin
                venv_bin = os.path.join(os.environ["VIRTUAL_ENV"], "bin")
                if venv_bin not in safe_env["PATH"]:
                    safe_env["PATH"] = venv_bin + os.pathsep + safe_env["PATH"]

            kwargs = {
                "cwd": sandbox_root,
                "shell": True,
                "text": True,
                "capture_output": True,
                "timeout": self.timeout,
                "env": safe_env,
            }
            if sys.platform != "win32":
                kwargs["preexec_fn"] = limit_resources

            try:
                completed = subprocess.run(command, **kwargs)
                output = (completed.stdout + "\n" + completed.stderr).strip()
                return TestResult(completed.returncode == 0, output, completed.returncode)
            except subprocess.TimeoutExpired as exc:
                output = (_text(exc.stdout) + "\n" + _text(exc.stderr)).strip()
                return TestResult(False, f"LOCAL SANDBOX TIMEOUT after {self.timeout}s\n{output}", 124)
        finally:
            shutil.rmtree(sandbox_tmp, ignore_errors=True)
