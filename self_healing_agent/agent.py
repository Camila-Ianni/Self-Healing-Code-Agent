"""The repair loop: run tests, ask a model for a replacement, validate or roll back."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


FIXER_PROMPT = """You are a senior software engineer repairing one focused defect.
You receive a source file and the output of its failing tests. Return ONLY the complete,
corrected contents of that exact source file. Do not use Markdown fences, explanations,
or change public APIs unless the failure demands it. Make the smallest correct change."""

REVIEWER_PROMPT = """You are a strict senior code reviewer in a two-agent repair system.
Review a proposed full-file replacement against the original source and failing-test output.
Reject changes that introduce security risks, infinite loops, data races, deadlocks,
unnecessary public API changes, or obvious performance regressions. Reply with exactly
APPROVE on the first line only when the patch is safe and relevant. Otherwise reply with
REJECT on the first line followed by a short reason. You do not write code."""


@dataclass
class TestResult:
    passed: bool
    output: str
    returncode: int


class RepairError(RuntimeError):
    """Raised when a repair cannot safely be completed."""


def run_tests(command: str, cwd: Path) -> TestResult:
    """Execute a test command and retain stdout/stderr for diagnosis."""
    completed = subprocess.run(command, cwd=cwd, shell=True, text=True, capture_output=True, check=False)
    output = (completed.stdout + "\n" + completed.stderr).strip()
    return TestResult(completed.returncode == 0, output, completed.returncode)


def infer_source_file(test_output: str, root: Path) -> Path:
    """Find a local, non-test Python frame in pytest's traceback."""
    candidates = re.findall(r'File "([^"]+\.py)"', test_output)
    for raw_path in reversed(candidates):
        path = Path(raw_path)
        if not path.is_absolute():
            path = root / path
        try:
            resolved = path.resolve()
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        if resolved.exists() and "test" not in resolved.name.lower():
            return resolved
    raise RepairError("No pude detectar el archivo fuente. Usá --source ruta/al/archivo.py.")


def _clean_model_output(text: str) -> str:
    """Tolerate accidental fenced code while keeping the protocol simple."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip() + "\n"
    return text + ("" if text.endswith("\n") else "\n")


def _client():
    """Create an authenticated OpenAI client only when the repair is needed."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RepairError("Falta la dependencia openai. Ejecutá: pip install -e '.[dev]'") from exc
    if not os.getenv("OPENAI_API_KEY"):
        raise RepairError("Falta OPENAI_API_KEY. Copiá .env.example a .env o exportá la variable.")

    return OpenAI()


def request_repair(source_path: Path, test_output: str, model: str) -> str:
    """Ask the Fixer for a full replacement of one source file."""
    source = source_path.read_text(encoding="utf-8")
    user_prompt = f"""SOURCE FILE: {source_path.name}
--- SOURCE ---
{source}
--- FAILED TEST OUTPUT ---
{test_output}
--- END ---
Return the complete corrected source file now."""
    response = _client().responses.create(model=model, instructions=FIXER_PROMPT, input=user_prompt)
    if not response.output_text.strip():
        raise RepairError("El modelo no devolvió código.")
    return _clean_model_output(response.output_text)


def review_repair(source_path: Path, original: str, proposed: str, test_output: str, model: str) -> str:
    """Ask an independent Reviewer to gate the Fixer's proposal before it is written."""
    prompt = f"""FILE: {source_path.name}
--- ORIGINAL ---
{original}
--- PROPOSED REPLACEMENT ---
{proposed}
--- FAILED TEST OUTPUT ---
{test_output}
--- END ---"""
    response = _client().responses.create(model=model, instructions=REVIEWER_PROMPT, input=prompt)
    verdict = response.output_text.strip()
    if not verdict:
        raise RepairError("El Reviewer no devolvió un veredicto.")
    return verdict


def repair_once(
    command: str,
    root: Path,
    source_path: Path | None,
    model: str,
    approve: Callable[[Path, str, str], bool] | None = None,
    notify: Callable[[str], None] | None = None,
) -> str:
    """Run tests, repair a source file, and keep the patch only when tests pass."""
    event = notify or (lambda _: None)
    event("Ejecutando tests y capturando evidencia…")
    initial = run_tests(command, root)
    if initial.passed:
        return "✅ Los tests ya pasan. No hay nada que reparar."

    target = source_path.resolve() if source_path else infer_source_file(initial.output, root)
    if not target.exists():
        raise RepairError(f"No existe el archivo fuente: {target}")
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise RepairError("El archivo fuente debe estar dentro del proyecto actual.") from exc

    event(f"Fixer Agent: analizando {target.relative_to(root)} con {model}…")
    repaired = request_repair(target, initial.output, model)
    original = target.read_text(encoding="utf-8")
    event("Reviewer Agent: auditando seguridad, concurrencia y rendimiento…")
    verdict = review_repair(target, original, repaired, initial.output, model)
    if not verdict.upper().startswith("APPROVE"):
        raise RepairError(f"El Reviewer rechazó la propuesta:\n{verdict}")
    if approve and not approve(target, original, repaired):
        raise RepairError("Parche cancelado por el usuario; no se modificó ningún archivo.")

    backup_dir = root / ".self-healing-backups"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / f"{target.name}.bak"
    shutil.copy2(target, backup)
    target.write_text(repaired, encoding="utf-8")

    event("Aplicando el parche aprobado y ejecutando la validación final…")
    verified = run_tests(command, root)
    if verified.passed:
        return f"✅ Reparación validada. Parche aplicado en {target.relative_to(root)} (backup: {backup.relative_to(root)})."

    target.write_text(original, encoding="utf-8")
    raise RepairError("La propuesta no hizo pasar los tests; restauré el archivo original.\n\n" f"Salida de validación:\n{verified.output}")
