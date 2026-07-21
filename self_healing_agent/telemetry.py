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
