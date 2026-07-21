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
    if os.getenv("OPENAI_API_KEY") == "mock":
        class DummyClient:
            pass
        return DummyClient()
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
        if os.getenv("OPENAI_API_KEY") == "mock":
            fixes = {}
            for target in targets:
                name = target.name
                if name == "calculator.py":
                    fixes[target] = (
                        "def multiply(left: int, right: int) -> int:\n"
                        "    \"\"\"Multiply two integers.\"\"\"\n"
                        "    # Intentional defect for the live demo: the agent should change + to *.\n"
                        "    return left * right\n"
                    )
                elif name == "aggregator.go":
                    fixes[target] = (
                        "package aggregator\n\n"
                        "import (\n"
                        "\t\"context\"\n"
                        "\t\"encoding/json\"\n"
                        "\t\"fmt\"\n"
                        "\t\"net/http\"\n"
                        "\t\"sync\"\n"
                        "\t\"time\"\n"
                        ")\n\n"
                        "type Quote struct {\n"
                        "\tMarket string  `json:\"market\"`\n"
                        "\tPrice  float64 `json:\"price\"`\n"
                        "}\n\n"
                        "func FetchAndAggregate(ctx context.Context, urls []string) (map[string]float64, error) {\n"
                        "\tclient := &http.Client{Timeout: 2 * time.Second}\n"
                        "\tquotes := make(chan Quote)\n"
                        "\tvar wg sync.WaitGroup\n"
                        "\tvar errs = make(chan error, len(urls))\n\n"
                        "\tfor _, url := range urls {\n"
                        "\t\twg.Add(1)\n"
                        "\t\tgo func(targetURL string) {\n"
                        "\t\t\tdefer wg.Done()\n"
                        "\t\t\treq, err := http.NewRequestWithContext(ctx, \"GET\", targetURL, nil)\n"
                        "\t\t\tif err != nil {\n"
                        "\t\t\t\terrs <- err\n"
                        "\t\t\t\treturn\n"
                        "\t\t\t}\n"
                        "\t\t\tresp, err := client.Do(req)\n"
                        "\t\t\tif err != nil {\n"
                        "\t\t\t\terrs <- err\n"
                        "\t\t\t\treturn\n"
                        "\t\t\t}\n"
                        "\t\t\tdefer resp.Body.Close()\n\n"
                        "\t\t\tif resp.StatusCode != http.StatusOK {\n"
                        "\t\t\t\terrs <- fmt.Errorf(\"feed server returned status %d\", resp.StatusCode)\n"
                        "\t\t\t\treturn\n"
                        "\t\t\t}\n\n"
                        "\t\t\tvar quote Quote\n"
                        "\t\t\tif err := json.NewDecoder(resp.Body).Decode(&quote); err != nil {\n"
                        "\t\t\t\terrs <- err\n"
                        "\t\t\t\treturn\n"
                        "\t\t\t}\n"
                        "\t\t\tquotes <- quote\n"
                        "\t\t}(url)\n"
                        "\t}\n\n"
                        "\tgo func() {\n"
                        "\t\twg.Wait()\n"
                        "\t\tclose(quotes)\n"
                        "\t\tclose(errs)\n"
                        "\t}()\n\n"
                        "\tbestPrices := make(map[string]float64)\n"
                        "\tfor quote := range quotes {\n"
                        "\t\tif quote.Price > bestPrices[quote.Market] {\n"
                        "\t\t\tbestPrices[quote.Market] = quote.Price\n"
                        "\t\t}\n"
                        "\t}\n\n"
                        "\tif len(errs) > 0 {\n"
                        "\t\treturn nil, <-errs\n"
                        "\t}\n\n"
                        "\treturn bestPrices, nil\n"
                        "}\n"
                    )
                elif name == "counter.go":
                    fixes[target] = (
                        "package backend\n\n"
                        "import \"sync\"\n\n"
                        "// RequestCounter represents a metric that a HTTP handler could update per request.\n"
                        "type RequestCounter struct {\n"
                        "\tmu    sync.Mutex\n"
                        "\tvalue int\n"
                        "}\n\n"
                        "func (c *RequestCounter) Increment() {\n"
                        "\tc.mu.Lock()\n"
                        "\tdefer c.mu.Unlock()\n"
                        "\tc.value++\n"
                        "}\n\n"
                        "func (c *RequestCounter) Value() int {\n"
                        "\tc.mu.Lock()\n"
                        "\tdefer c.mu.Unlock()\n"
                        "\treturn c.value\n"
                        "}\n"
                    )
                else:
                    fixes[target] = target.read_text(encoding="utf-8")
            return fixes

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
        if os.getenv("OPENAI_API_KEY") == "mock":
            return "APPROVE"
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
        if os.getenv("OPENAI_API_KEY") == "mock":
            modified_names = [p.name for p in proposed_patches.keys()]
            return f"fix: auto-resolved concurrency or logic defect in {', '.join(modified_names)}"
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
        ask_report: Callable[[], bool] | None = None,
        ask_report_path: Callable[[str], str] | None = None,
        notify_report_saved: Callable[[str], None] | None = None,
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

        # Executive report generation
        if ask_report:
            try:
                want_report = ask_report()
                if want_report and ask_report_path:
                    first_target = targets[0]
                    default_name = f"Repair_Report_{evidence.language.capitalize()}_{first_target.stem}.md"
                    default_path = str(Path.home() / "Desktop" / default_name)
                    selected_path_str = ask_report_path(default_path)
                    if selected_path_str:
                        selected_path = Path(os.path.expanduser(selected_path_str))
                        from .telemetry import generate_executive_report
                        generate_executive_report(
                            root=root,
                            evidence=evidence,
                            proposed_patches=proposed_patches,
                            sandbox_output=verified.output,
                            ai_time_seconds=duration_seconds,
                            human_saved_minutes=roi_report["history"][-1]["human_time_saved_minutes"],
                            output_path=selected_path
                        )
                        if notify_report_saved:
                            notify_report_saved(str(selected_path))
            except Exception:
                pass

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
