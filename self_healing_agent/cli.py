"""Command-line interface for Self-Healing Code Agent."""

from __future__ import annotations

import argparse
import ast
import os
import shlex
import subprocess
from pathlib import Path

from .controller import RepairController, RepairError, restore_all_backups
from .sandbox import DockerSandbox, LocalSubprocessSandbox
from .view import TerminalView


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="self-heal", description="Ejecuta tests, propone una reparación con OpenAI y la valida automáticamente.")
    parser.add_argument("--test-command", default="pytest -q", help="Comando de test (default: pytest -q).")
    parser.add_argument("--source", type=Path, help="Archivo a reparar; admite múltiples rutas o evita la detección automática.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-5.6"), help="Modelo de OpenAI.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Raíz del proyecto (default: directorio actual).")
    parser.add_argument("--yes", action="store_true", help="Aplica un parche aprobado sin pedir confirmación ni interrupción manual.")
    parser.add_argument("--sandbox", choices=["docker", "subprocess"], default="docker", help="Entorno aislado de validación (default: docker).")
    parser.add_argument("--sandbox-image", default="self-healing-sandbox:latest", help="Imagen Docker aislada para validar (default: self-healing-sandbox:latest).")
    parser.add_argument("--commit", action="store_true", help="Genera un commit automático en Git con un mensaje descriptivo de la IA al completar con éxito.")
    parser.add_argument("--ci-mode", action="store_true", help="Modo CI/CD headless: corre de forma autónoma, valida en sandbox, crea commit y empuja cambios a remoto.")
    parser.add_argument("--rollback", nargs="?", const="all", help="Restaura archivos a partir de copias ocultas .bak. Opcional: ruta de archivo específico.")
    parser.add_argument("action", nargs="?", choices=["test"], help="Atajo: test <archivo_test.py> para ejecutar un test concreto.")
    parser.add_argument("test_file", nargs="?", type=Path, help="Archivo de test para el atajo `test`.")
    return parser


def source_from_test(test_file: Path) -> Path | None:
    """Infer the first local module imported by a selected Python test file."""
    try:
        tree = ast.parse(test_file.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in ast.walk(tree):
        module = node.module if isinstance(node, ast.ImportFrom) else None
        if module and node.level == 0:
            candidate = test_file.parent / f"{module.split('.')[0]}.py"
            if candidate.exists():
                return candidate
        if isinstance(node, ast.Import):
            for imported in node.names:
                candidate = test_file.parent / f"{imported.name.split('.')[0]}.py"
                if candidate.exists():
                    return candidate
    return None


def load_env_file(root: Path) -> None:
    """Load variables from .env file in root into os.environ."""
    env_path = root / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                parts = line.split("=", 1)
                k = parts[0].strip()
                v = parts[1].strip().strip('"').strip("'")
                if k and v:
                    os.environ[k] = v


def main() -> None:
    args = build_parser().parse_args()
    root = args.root.resolve()
    load_env_file(root)
    
    # Check if rollback command was invoked
    if args.rollback:
        if args.rollback == "all":
            restored = restore_all_backups(root)
            if restored:
                print(f"✅ Rollback completado. Se restauraron los siguientes archivos:\n" + "\n".join(f"- {r}" for r in restored))
            else:
                print("ℹ No se encontraron archivos de respaldo (.bak) para restaurar.")
        else:
            target_path = Path(args.rollback)
            if not target_path.is_absolute():
                target_path = root / target_path
            backup_path = target_path.parent / f".{target_path.name}.bak"
            if backup_path.exists():
                import shutil
                shutil.move(str(backup_path), str(target_path))
                print(f"✅ Rollback completado. Se restauró {target_path.relative_to(root)}")
            else:
                print(f"⛔ No se encontró archivo de respaldo para {args.rollback}")
        raise SystemExit(0)

    # Force yes and commit flags if ci_mode is set
    if args.ci_mode:
        args.yes = True
        args.commit = True

    if args.action and not args.test_file:
        raise SystemExit("Uso: python -m self_healing_agent.cli test ruta/al/test.py")
    if args.action == "test":
        test_file = (root / args.test_file).resolve() if not args.test_file.is_absolute() else args.test_file
        if not test_file.exists():
            raise SystemExit(f"No existe el test: {test_file}")
        try:
            rel_test = test_file.relative_to(root)
            args.test_command = f"pytest -q {shlex.quote(str(rel_test))}"
        except ValueError:
            args.test_command = f"pytest -q {shlex.quote(str(test_file))}"
        if args.source is None:
            args.source = source_from_test(test_file)
            
    source = (root / args.source).resolve() if args.source and not args.source.is_absolute() else args.source
    
    view = TerminalView(root, args.yes)
    
    # Initialize sandbox with automatic fallback to subprocess if Docker is not available
    sandbox = None
    if args.sandbox == "docker":
        docker_available = True
        try:
            res = subprocess.run(["docker", "info"], capture_output=True, check=False)
            if res.returncode != 0:
                docker_available = False
        except FileNotFoundError:
            docker_available = False
            
        if docker_available:
            sandbox = DockerSandbox(args.sandbox_image)
        else:
            sandbox = LocalSubprocessSandbox()
            sandbox.fallback_triggered = True
    else:
        sandbox = LocalSubprocessSandbox()

    controller = RepairController(args.model, sandbox)
    try:
        view.success(
            controller.repair_once(
                args.test_command,
                root,
                [source] if source else None,
                approve=None if args.ci_mode else view.approve,
                notify=view.notify,
                display_evidence=view.display_failure_evidence,
                notify_sandbox_fallback=view.display_sandbox_fallback,
                confirm_rollback=None if args.yes else view.ask_rollback,
                git_commit=args.commit,
                display_roi=view.display_roi,
                ci_push=args.ci_mode
            )
        )
    except RepairError as error:
        view.error(str(error))
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
