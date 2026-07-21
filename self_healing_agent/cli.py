"""Command-line interface for Self-Healing Code Agent."""

from __future__ import annotations

import argparse
import ast
from difflib import unified_diff
import os
import shlex
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax

from .agent import RepairError, repair_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="self-heal", description="Ejecuta tests, propone una reparación con OpenAI y la valida automáticamente.")
    parser.add_argument("--test-command", default="pytest -q", help="Comando de test (default: pytest -q).")
    parser.add_argument("--source", type=Path, help="Archivo a reparar; evita la detección automática.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-5.6"), help="Modelo de OpenAI.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Raíz del proyecto (default: directorio actual).")
    parser.add_argument("--yes", action="store_true", help="Aplica un parche aprobado sin pedir confirmación.")
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


def main() -> None:
    console = Console()
    status = console.status("[bold cyan]🧠 Preparando agente…[/bold cyan]", spinner="dots12")
    status_active = False
    args = build_parser().parse_args()
    root = args.root.resolve()
    if args.action and not args.test_file:
        raise SystemExit("Uso: python -m self_healing_agent.cli test ruta/al/test.py")
    if args.action == "test":
        test_file = (root / args.test_file).resolve() if not args.test_file.is_absolute() else args.test_file
        if not test_file.exists():
            raise SystemExit(f"No existe el test: {test_file}")
        args.test_command = f"pytest -q {shlex.quote(str(test_file))}"
        if args.source is None:
            args.source = source_from_test(test_file)
    source = (root / args.source).resolve() if args.source and not args.source.is_absolute() else args.source

    def notify(message: str) -> None:
        nonlocal status_active
        status.update(f"[bold cyan]🧠 {message}[/bold cyan]")
        if not status_active:
            status.start()
            status_active = True

    def approve(target: Path, original: str, proposed: str) -> bool:
        nonlocal status_active
        if status_active:
            status.stop()
            status_active = False
        console.print(Panel.fit(f"[bold green]✓ Reviewer Agent aprobó[/bold green]\n{target.relative_to(root)}", title="Propuesta lista"))
        diff = "\n".join(unified_diff(original.splitlines(), proposed.splitlines(), fromfile="original", tofile="propuesta", lineterm=""))
        console.print(Syntax(diff or "(sin cambios)", "diff", theme="monokai", line_numbers=True))
        return args.yes or Confirm.ask("¿Aplicar este parche?", default=True, console=console)

    try:
        result = repair_once(args.test_command, root, source, args.model, approve=approve, notify=notify)
        if status_active:
            status.stop()
        console.print(result)
    except RepairError as error:
        if status_active:
            status.stop()
        console.print(f"\n[bold red]⛔ Reparación detenida:[/bold red] {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
