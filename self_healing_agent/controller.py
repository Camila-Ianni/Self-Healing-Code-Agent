"""Controller layer: coordinates evidence, Fixer, Reviewer, user approval and sandbox."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable

from .model import StackTraceParser, FailureEvidence
from .sandbox import Sandbox, SandboxError, run_tests


FIXER_PROMPT = """You are a senior software engineer repairing one focused defect.
Return ONLY the complete corrected contents of the supplied source file. Make the smallest
safe change, preserve public APIs, and address the exact failed-test evidence."""

REVIEWER_PROMPT = """You are a strict senior code reviewer in a two-agent repair system.
Review a proposed replacement against the original and failed-test output. Reject security
risks, destructive operations, infinite loops, data races, deadlocks, needless public API
changes and obvious performance regressions. Reply APPROVE alone only when safe; otherwise
reply REJECT followed by a short reason. Never write code."""


class RepairError(RuntimeError):
    """Raised when a repair cannot safely be completed."""


def _client():
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RepairError("Falta la dependencia openai. Ejecutá: pip install -e '.[dev]'") from exc
    if not os.getenv("OPENAI_API_KEY"):
        raise RepairError("Falta OPENAI_API_KEY. Copiá .env.example a .env o exportá la variable.")
    return OpenAI()


def _clean_model_output(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip() + "\n"
    return text + ("" if text.endswith("\n") else "\n")


class RepairController:
    def __init__(self, model: str, sandbox: Sandbox):
        self.model = model
        self.sandbox = sandbox

    def _fix(self, target: Path, evidence: FailureEvidence) -> str:
        source = target.read_text(encoding="utf-8")
        
        # Build structured frame summary for the prompt
        frames_summary = []
        for i, f in enumerate(evidence.frames):
            try:
                rel = f.file_path.relative_to(target.parent.parent)
            except ValueError:
                rel = f.file_path.name
            frames_summary.append(
                f"Frame #{i+1}: File {rel}, Line {f.line_number}"
                + (f", in {f.function_name}" if f.function_name else "")
                + (f" -> Code: {f.code_line}" if f.code_line else "")
            )
        frames_str = "\n".join(frames_summary)

        prompt_input = (
            f"FILE: {target.name}\n"
            f"--- ERROR DETAILS ---\n"
            f"Language: {evidence.language}\n"
            f"Error Message: {evidence.error_message}\n"
            f"Parsed Stack Trace:\n{frames_str}\n\n"
            f"--- SOURCE CODE ---\n{source}\n"
            f"--- RAW FAILED TEST OUTPUT ---\n{evidence.raw_output}\n"
            f"--- END ---"
        )

        response = _client().responses.create(
            model=self.model,
            instructions=FIXER_PROMPT,
            input=prompt_input,
        )
        if not response.output_text.strip():
            raise RepairError("El Fixer no devolvió código.")
        return _clean_model_output(response.output_text)

    def _review(self, target: Path, original: str, proposed: str, evidence: FailureEvidence) -> str:
        prompt_input = (
            f"FILE: {target.name}\n"
            f"--- ORIGINAL ---\n{original}\n"
            f"--- PROPOSED ---\n{proposed}\n"
            f"--- ERROR MESSAGE ---\n{evidence.error_message}\n"
            f"--- RAW TEST OUTPUT ---\n{evidence.raw_output}\n"
            f"--- END ---"
        )
        
        response = _client().responses.create(
            model=self.model,
            instructions=REVIEWER_PROMPT,
            input=prompt_input,
        )
        verdict = response.output_text.strip()
        if not verdict:
            raise RepairError("El Reviewer no devolvió un veredicto.")
        return verdict

    def repair_once(
        self,
        command: str,
        root: Path,
        source_path: Path | None,
        approve: Callable[[Path, str, str], bool] | None = None,
        notify: Callable[[str], None] | None = None,
        display_evidence: Callable[[FailureEvidence], None] | None = None,
        notify_sandbox_fallback: Callable[[], None] | None = None,
    ) -> str:
        event = notify or (lambda _: None)
        
        event("Modelo: ejecutando tests y parseando el stack trace…")
        initial = run_tests(command, root)
        if initial.passed:
            return "✅ Los tests ya pasan. No hay nada que reparar."

        # Deep parsing of the failure output
        evidence = StackTraceParser.parse(initial.output, root, initial.returncode)
        
        if display_evidence:
            display_evidence(evidence)

        if source_path:
            target = source_path.resolve()
        else:
            if not evidence.target_file:
                raise RepairError("No pude detectar el archivo fuente. Usá --source ruta/al/archivo.")
            target = evidence.target_file

        if not target.exists():
            raise RepairError(f"No existe el archivo fuente: {target}")
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise RepairError("El archivo fuente debe estar dentro del proyecto actual.") from exc

        event(f"Controlador: Fixer Agent analizando {target.relative_to(root)}…")
        original = target.read_text(encoding="utf-8")
        proposed = self._fix(target, evidence)
        
        event("Controlador: Reviewer Agent auditando la propuesta…")
        verdict = self._review(target, original, proposed, evidence)
        if verdict.upper() != "APPROVE":
            raise RepairError(f"El Reviewer rechazó la propuesta:\n{verdict}")
        
        if approve and not approve(target, original, proposed):
            raise RepairError("Parche cancelado por el usuario; no se modificó ningún archivo.")

        # Check for Sandbox environment fallback notify
        if hasattr(self.sandbox, "fallback_triggered") and self.sandbox.fallback_triggered and notify_sandbox_fallback:
            notify_sandbox_fallback()

        event(f"Sandbox: validando el parche usando {self.sandbox.__class__.__name__}…")
        try:
            verified = self.sandbox.validate(command, root, target, proposed)
        except SandboxError as exc:
            raise RepairError(str(exc)) from exc
        if not verified.passed:
            raise RepairError(f"El sandbox rechazó el parche; el archivo local no cambió.\n\n{verified.output}")

        backup_dir = root / ".self-healing-backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"{target.name}.bak"
        shutil.copy2(target, backup)
        target.write_text(proposed, encoding="utf-8")
        return f"✅ Reparación validada en sandbox y aplicada en {target.relative_to(root)} (backup: {backup.relative_to(root)})."
