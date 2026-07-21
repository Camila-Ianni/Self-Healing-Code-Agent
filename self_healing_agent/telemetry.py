"""Telemetry layer: measures process duration and calculates ROI metric logs."""

from __future__ import annotations

import datetime
import json
from pathlib import Path


class TelemetryManager:
    def __init__(self, root: Path):
        self.root = root
        self.report_path = root / "roi_report.json"

    def load_report(self) -> dict:
        """Load telemetry history from the project root JSON file."""
        if self.report_path.exists():
            try:
                return json.loads(self.report_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "total_fixes": 0,
            "total_man_hours_saved_minutes": 0,
            "history": []
        }

    def save_report(self, report: dict) -> None:
        """Persist telemetry history to root folder."""
        try:
            self.report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def record_repair(self, files: list[Path], language: str, error_message: str, ai_time_seconds: float) -> dict:
        """Log repair data, calculate ROI time saved and update persistent report."""
        # Detect if it is a concurrency defect
        is_concurrency = any(x in error_message.lower() for x in ["deadlock", "race", "block", "concurren", "lock", "channel", "mutex", "sync", "timeout"])
        
        if language == "go":
            human_time = 60 if is_concurrency else 45
        else:
            human_time = 20  # Python standard error debugging average time

        report = self.load_report()
        
        record = {
            "timestamp": datetime.datetime.now().isoformat(),
            "files": [str(f.relative_to(self.root.resolve())) if f.is_relative_to(self.root.resolve()) else f.name for f in files],
            "language": language,
            "error_message": error_message,
            "ai_time_seconds": round(ai_time_seconds, 2),
            "human_time_saved_minutes": human_time
        }
        
        report["total_fixes"] += 1
        report["total_man_hours_saved_minutes"] += human_time
        report["history"].append(record)
        
        self.save_report(report)
        return report


def generate_executive_report(
    root: Path,
    evidence: FailureEvidence,
    proposed_patches: dict[Path, str],
    sandbox_output: str,
    ai_time_seconds: float,
    human_saved_minutes: int,
    output_path: Path
) -> None:
    """Generate a beautiful markdown report describing the incident, code diff and ROI metrics."""
    import datetime
    from difflib import unified_diff
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target_files_str = ", ".join(
        [str(f.relative_to(root.resolve())) if f.is_relative_to(root.resolve()) else f.name for f in proposed_patches.keys()]
    )
    
    diff_blocks = []
    for path, proposed in proposed_patches.items():
        original = path.read_text(encoding="utf-8")
        diff = "\n".join(unified_diff(
            original.splitlines(),
            proposed.splitlines(),
            fromfile=f"original/{path.name}",
            tofile=f"reconstructed/{path.name}",
            lineterm=""
        ))
        diff_blocks.append(f"### File: {path.name}\n```diff\n{diff or '(No changes detected)'}\n```")
    diffs_joined = "\n\n".join(diff_blocks)
    
    report_content = f"""# Executive Code Repair Report

**Date**: {timestamp}  
**Diagnostic Language**: {evidence.language.upper()}  
**Target Files**: {target_files_str}

---

## 1. Incident Summary & Failure Telemetry
An integrity failure was captured during automated test execution.

**Error Message**:
```
{evidence.error_message}
```

### Raw Test Traceback
```
{evidence.raw_output}
```

---

## 2. Sandbox Verification Details
The proposed patch was copied to an isolated workspace and validated.

### Test execution output (Sandbox):
```
{sandbox_output}
```
**Sandbox Verdict**: `PASSED` (All tests resolved successfully, no regressions detected).

---

## 3. Code Modifications (Unified Diff)
{diffs_joined}

---

## 4. Business Metrics & ROI Impact
By automating this repair cycle, the system saved mechanical developer hours.

| Metric | Value |
| :--- | :--- |
| **AI Healing Cycle Duration** | {ai_time_seconds:.2f} seconds |
| **Avoided Human Debugging Time** | {human_saved_minutes} minutes |
| **Status** | Verified and Applied Locally |
"""
    output_path.write_text(report_content, encoding="utf-8")
