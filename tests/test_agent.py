from pathlib import Path
import sys

import pytest

from self_healing_agent.agent import RepairError, infer_source_file, run_tests


def test_run_tests_captures_a_failure(tmp_path: Path) -> None:
    result = run_tests(f'"{sys.executable}" -c \'import sys; print("boom"); sys.exit(1)\'', tmp_path)
    assert not result.passed
    assert result.returncode == 1
    assert "boom" in result.output


def test_infer_source_file_prefers_non_test_local_frame(tmp_path: Path) -> None:
    source = tmp_path / "calculator.py"
    source.write_text("def multiply(): pass\n", encoding="utf-8")
    output = f'File "{tmp_path / "test_calculator.py"}", line 4\nFile "{source}", line 2'
    assert infer_source_file(output, tmp_path) == source


def test_infer_source_file_rejects_missing_target(tmp_path: Path) -> None:
    with pytest.raises(RepairError):
        infer_source_file('File "/outside/missing.py", line 1', tmp_path)
