"""Controller layer: coordinates evidence, Fixer, Reviewer, user approval, sandbox and telemetry."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from .model import StackTraceParser, FailureEvidence, load_business_rules
from .sandbox import Sandbox, SandboxError, run_tests
from .telemetry import TelemetryManager


FIXER_PROMPT = """You are a senior software engineer repairing defects in multiple files.
Return ONLY a single, valid JSON object mapping file paths (relative to the project root)
to their complete, corrected contents.

Format:
```json
{
  "relative/path/to/file1.go": "complete new content...",
  "relative/path/to/file2.go": "complete new content..."
}
```

Ensure the JSON is correctly formatted and escaped. Output ONLY the valid JSON block."""

REVIEWER_PROMPT = """You are a strict senior code reviewer in a two-agent repair system.
Review the proposed full-file replacements against the original source and failing-test output.
Reject changes that introduce security risks, infinite loops, data races, deadlocks,
unnecessary public API changes, or obvious performance regressions. Reply APPROVE alone only
when safe; otherwise reply REJECT followed by a short reason. Never write code."""

COMMIT_PROMPT = """You are a senior software developer writing a concise, conventional git commit message.
Based on the target files modified and a brief description, write a one-line commit message.
Use conventional commits format (e.g. 'fix: resolve race condition in aggregator.go').
Do not add markdown formatting, explanations, or quotes. Output ONLY the commit message itself."""


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


def restore_all_backups(root: Path) -> list[str]:
    """Helper to scan recursively and restore all hidden .bak files in the workspace."""
    restored = []
    for p in root.rglob(".*.bak"):
        if p.is_file():
            # e.g., ".aggregator.go.bak" -> "aggregator.go"
            orig_name = p.name[1:-4]
            orig_path = p.parent / orig_name
            try:
                shutil.move(str(p), str(orig_path))
                restored.append(str(orig_path.relative_to(root)))
            except Exception:
                pass
    return restored


class RepairController:
    def __init__(self, model: str, sandbox: Sandbox):
        self.model = model
        self.sandbox = sandbox

    def rollback_backups(self, backups: dict[Path, Path]) -> None:
        """Restores targets from their respective hidden backup files."""
        for target, backup in backups.items():
            if backup.exists():
                try:
                    shutil.move(str(backup), str(target))
                except Exception:
                    pass

    def cleanup_backups(self, backups: dict[Path, Path]) -> None:
        """Deletes the temporary hidden backups."""
        for backup in backups.values():
            if backup.exists():
                try:
                    backup.unlink()
                except Exception:
                    pass

    def _fix(self, targets: list[Path], evidence: FailureEvidence, root: Path) -> dict[Path, str]:
        sources_list = []
        for target in targets:
            try:
                rel = target.relative_to(root.resolve())
            except ValueError:
                rel = target.name
            sources_list.append(
                f"FILE: {rel}\n"
                f"--- SOURCE ---\n"
                f"{target.read_text(encoding='utf-8')}\n"
                f"--- END ---"
            )
        
        # Load business rules context (RAG)
        rules = load_business_rules(root)
        rules_part = ""
        if rules:
            rules_part = (
                f"\n--- BUSINESS & ARCHITECTURE RULES ---\n"
                f"{rules}\n\n"
                f"IMPORTANT: The proposed corrections MUST strictly follow all architectural and business rules listed above. "
                f"Ensure timeouts, resource limits and design patterns are strictly satisfied.\n\n"
            )

        sources_str = "\n\n".join(sources_list)
        prompt_input = (
            f"--- ERROR DETAILS ---\n"
            f"Language: {evidence.language}\n"
            f"Error Message: {evidence.error_message}\n\n"
            f"--- SOURCE FILES TO REPAIR ---\n{sources_str}\n\n"
            f"{rules_part}"
            f"--- RAW FAILED TEST OUTPUT ---\n{evidence.raw_output}\n"
            f"--- END ---"
        )

        try:
            response = _client().responses.create(
                model=self.model,
                instructions=FIXER_PROMPT,
                input=prompt_input,
            )
        except Exception as exc:
            raise RepairError(f"Error al llamar a OpenAI ({exc.__class__.__name__}): {exc}")
        output_text = response.output_text.strip()
        if not output_text:
            raise RepairError("El Fixer no devolvió código.")
        
        cleaned = _clean_model_output(output_text)
        
        try:
            data = json.loads(cleaned)
            fixes = {}
            for rel_path, content in data.items():
                resolved_path = (root / rel_path).resolve()
                for t in targets:
                    if t.resolve() == resolved_path or t.name == Path(rel_path).name:
                        fixes[t] = content
                        break
            if not fixes:
                raise RepairError("El Fixer no devolvió cambios para los archivos objetivo en el JSON.")
            return fixes
        except Exception as exc:
            if len(targets) == 1:
                return {targets[0]: cleaned}
            raise RepairError(f"Error decodificando la respuesta JSON del Fixer: {exc}\nRespuesta recibida:\n{cleaned}")

    def _review(self, proposed_patches: dict[Path, str], evidence: FailureEvidence, root: Path) -> str:
        # Load business rules context (RAG)
        rules = load_business_rules(root)
        rules_part = ""
        if rules:
            rules_part = (
                f"\n--- BUSINESS & ARCHITECTURE RULES ---\n"
                f"{rules}\n\n"
                f"IMPORTANT: Verify that the proposed changes strictly comply with the architectural and business rules listed above. "
                f"Reject the patches if timeouts are exceeded, constraints are violated or custom patterns are ignored.\n\n"
            )

        review_inputs = []
        for path, proposed in proposed_patches.items():
            original = path.read_text(encoding="utf-8")
            review_inputs.append(
                f"FILE: {path.name}\n"
                f"--- ORIGINAL ---\n{original}\n"
                f"--- PROPOSED ---\n{proposed}\n"
            )
        review_str = "\n".join(review_inputs)

        prompt_input = (
            f"--- PROPOSED PATCHES ---\n{review_str}\n"
            f"--- ERROR MESSAGE ---\n{evidence.error_message}\n\n"
            f"{rules_part}"
            f"--- RAW TEST OUTPUT ---\n{evidence.raw_output}\n"
            f"--- END ---"
        )
        
        try:
            response = _client().responses.create(
                model=self.model,
                instructions=REVIEWER_PROMPT,
                input=prompt_input,
            )
        except Exception as exc:
            raise RepairError(f"Error al llamar a OpenAI ({exc.__class__.__name__}): {exc}")
        verdict = response.output_text.strip()
        if not verdict:
            raise RepairError("El Reviewer no devolvió un veredicto.")
        return verdict

    def _generate_commit_message(self, proposed_patches: dict[Path, str]) -> str:
        summary_lines = []
        for path in proposed_patches.keys():
            summary_lines.append(f"Modified: {path.name}")
        summary_str = "\n".join(summary_lines)

        try:
            response = _client().responses.create(
                model=self.model,
                instructions=COMMIT_PROMPT,
                input=f"--- CHANGES ---\n{summary_str}\n--- END ---"
            )
        except Exception as exc:
            raise RepairError(f"Error al llamar a OpenAI ({exc.__class__.__name__}): {exc}")
        msg = response.output_text.strip()
        msg = msg.replace('`', '').replace('"', '').replace("'", "")
        if "\n" in msg:
            msg = msg.split("\n")[0].strip()
        return msg or "fix: code repaired via AI agent"

    def repair_once(
        self,
        command: str,
        root: Path,
        source_paths: list[Path] | Path | None,
        approve: Callable[[Path, str, str], bool] | None = None,
        notify: Callable[[str], None] | None = None,
        display_evidence: Callable[[FailureEvidence], None] | None = None,
        notify_sandbox_fallback: Callable[[], None] | None = None,
        confirm_rollback: Callable[[], bool] | None = None,
        git_commit: bool = False,
        display_roi: Callable[[dict], None] | None = None,
        ci_push: bool = False,
    ) -> str:
        event = notify or (lambda _: None)
        
        # Start timing for ROI telemetries
        start_time = time.time()
        
        # Handle list vs single path compatibility
        targets = []
        if source_paths:
            if isinstance(source_paths, list):
                targets = [p.resolve() for p in source_paths]
            else:
                targets = [source_paths.resolve()]
        
        event("Modelo: ejecutando tests y parseando el stack trace…")
        initial = run_tests(command, root)
        if initial.passed:
            return "✅ Los tests ya pasan. No hay nada que reparar."

        # Deep parsing of the failure output
        evidence = StackTraceParser.parse(initial.output, root, initial.returncode)
        
        if display_evidence:
            display_evidence(evidence)

        if not targets:
            if not evidence.target_files:
                raise RepairError("No pude detectar el archivo fuente. Usá --source ruta/al/archivo.")
            targets = evidence.target_files

        for target in targets:
            if not target.exists():
                raise RepairError(f"No existe el archivo fuente: {target}")
            try:
                target.relative_to(root.resolve())
            except ValueError as exc:
                raise RepairError(f"El archivo fuente {target} debe estar dentro del proyecto actual.") from exc

        targets_display = ", ".join(str(t.relative_to(root.resolve())) for t in targets)
        event(f"Controlador: Fixer Agent analizando {targets_display}…")
        
        # Propose repairs (returns dict of target: proposed_content)
        proposed_patches = self._fix(targets, evidence, root)
        
        event("Controlador: Reviewer Agent auditando la propuesta en bloque…")
        verdict = self._review(proposed_patches, evidence, root)
        if verdict.upper() != "APPROVE":
            raise RepairError(f"El Reviewer rechazó la propuesta:\n{verdict}")
        
        # Show diffs and request user approval
        if approve:
            for target, proposed in proposed_patches.items():
                original = target.read_text(encoding="utf-8")
                if not approve(target, original, proposed):
                    raise RepairError("Parche cancelado por el usuario; no se modificó ningún archivo.")

        # Check for Sandbox environment fallback notify
        if hasattr(self.sandbox, "fallback_triggered") and self.sandbox.fallback_triggered and notify_sandbox_fallback:
            notify_sandbox_fallback()

        event(f"Sandbox: validando parches usando {self.sandbox.__class__.__name__}…")
        try:
            verified = self.sandbox.validate(command, root, proposed_patches)
        except SandboxError as exc:
            raise RepairError(str(exc)) from exc
        if not verified.passed:
            raise RepairError(f"El sandbox rechazó el parche; el archivo local no cambió.\n\n{verified.output}")

        # Local backup creation (hidden .name.bak in the same folder)
        backups = {}
        try:
            for target, proposed in proposed_patches.items():
                backup = target.parent / f".{target.name}.bak"
                shutil.copy2(target, backup)
                backups[target] = backup
                
                # Apply proposed patch locally
                target.write_text(proposed, encoding="utf-8")
            
            # Interactive post-test manual verification
            if confirm_rollback:
                keep = confirm_rollback()
                if not keep:
                    self.rollback_backups(backups)
                    raise RepairError("Parche rechazado tras prueba manual. Se restauró el código original.")
            
            # Clean up backups if confirmed
            self.cleanup_backups(backups)
            
        except Exception as exc:
            # Fallback rollback in case of any write errors
            self.rollback_backups(backups)
            raise exc

        # Calculate final ROI metrics
        duration_seconds = time.time() - start_time
        telemetry = TelemetryManager(root)
        roi_report = telemetry.record_repair(targets, evidence.language, evidence.error_message, duration_seconds)
        
        if display_roi:
            display_roi(roi_report)

        # Git commit integration if requested
        if git_commit:
            commit_msg = self._generate_commit_message(proposed_patches)
            event("Git: registrando cambios y creando commit automático…")
            try:
                for target in proposed_patches.keys():
                    subprocess.run(["git", "add", str(target)], cwd=root, check=True)
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=root, check=True)
                
                # Push back in CI/CD mode
                if ci_push:
                    event("Git: empujando cambios al repositorio remoto (CI Mode)…")
                    subprocess.run(["git", "push", "origin", "HEAD"], cwd=root, check=True)
                    return f"✅ Parche validado en sandbox, aplicado, commit local creado y empujado a la rama remota: '{commit_msg}'."
                
                return f"✅ Reparación validada en sandbox, aplicada localmente y confirmada en Git: '{commit_msg}'."
            except Exception as git_err:
                return f"✅ Reparación aplicada y validada, pero falló la operación de Git: {git_err}."

        return f"✅ Reparación validada en sandbox y aplicada localmente a {targets_display}."
