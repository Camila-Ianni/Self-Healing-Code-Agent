"""Command-line interface for Self-Healing Code Agent."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .agent import RepairError, repair_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="self-heal", description="Ejecuta tests, propone una reparación con OpenAI y la valida automáticamente.")
    parser.add_argument("--test-command", default="pytest -q", help="Comando de test (default: pytest -q).")
    parser.add_argument("--source", type=Path, help="Archivo a reparar; evita la detección automática.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-5.6"), help="Modelo de OpenAI.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Raíz del proyecto (default: directorio actual).")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = args.root.resolve()
    source = (root / args.source).resolve() if args.source and not args.source.is_absolute() else args.source
    try:
        print(repair_once(args.test_command, root, source, args.model))
    except RepairError as error:
        print(f"\n⛔ Reparación detenida: {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
