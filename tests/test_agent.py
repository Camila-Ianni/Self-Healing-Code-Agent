from pathlib import Path
import sys

import pytest

from self_healing_agent.agent import RepairError, infer_source_file, run_tests
from self_healing_agent.model import StackTraceParser
from self_healing_agent.sandbox import LocalSubprocessSandbox


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


def test_stack_trace_parser_go_timeout(tmp_path: Path) -> None:
    test_file = tmp_path / "aggregator_test.go"
    test_file.write_text("package aggregator\n", encoding="utf-8")
    
    output = 'aggregator_test.go:33: timeout: feed workers are blocked; inspect channel consumption and sync.WaitGroup'
    evidence = StackTraceParser.parse(output, tmp_path)
    assert evidence.language == "go"
    assert "timeout:" in evidence.error_message
    assert len(evidence.frames) == 1
    assert evidence.frames[0].file_path == test_file
    assert evidence.frames[0].line_number == 33


def test_stack_trace_parser_python_exception(tmp_path: Path) -> None:
    source_file = tmp_path / "calculator.py"
    source_file.write_text("def multiply(): pass\n", encoding="utf-8")
    test_file = tmp_path / "test_calculator.py"
    test_file.write_text("def test_multiply(): assert False\n", encoding="utf-8")
    
    output = f'File "{test_file}", line 4, in test_multiply\n    assert multiply(2, 3) == 6\n  File "{source_file}", line 2, in multiply\n    return left + right\nAssertionError: assert 5 == 6'
    
    evidence = StackTraceParser.parse(output, tmp_path)
    assert evidence.language == "python"
    assert evidence.error_message == "AssertionError: assert 5 == 6"
    assert len(evidence.frames) == 2
    assert evidence.frames[0].file_path == test_file
    assert evidence.frames[1].file_path == source_file
    assert evidence.frames[1].line_number == 2
    assert evidence.frames[1].function_name == "multiply"
    assert evidence.frames[1].code_line == "return left + right"
    assert evidence.target_files[0] == source_file


def test_local_subprocess_sandbox_success(tmp_path: Path) -> None:
    source_file = tmp_path / "calc.py"
    source_file.write_text("def add(a, b): return a + b\n", encoding="utf-8")
    
    sandbox = LocalSubprocessSandbox(timeout=5)
    # Validate changing the add method to return a * b
    proposed = "def add(a, b): return a * b\n"
    res = sandbox.validate(f'"{sys.executable}" -c "import calc; assert calc.add(2, 3) == 6"', tmp_path, source_file, proposed)
    assert res.passed
    assert res.returncode == 0


def test_local_subprocess_sandbox_failure(tmp_path: Path) -> None:
    source_file = tmp_path / "calc.py"
    source_file.write_text("def add(a, b): return a + b\n", encoding="utf-8")
    
    sandbox = LocalSubprocessSandbox(timeout=5)
    # If we propose returning a - b, the test asserting 6 should fail
    proposed = "def add(a, b): return a - b\n"
    res = sandbox.validate(f'"{sys.executable}" -c "import calc; assert calc.add(2, 3) == 6"', tmp_path, source_file, proposed)
    assert not res.passed
    assert res.returncode != 0


def test_go_aggregator_repair_flow(tmp_path: Path) -> None:
    import shutil
    from unittest.mock import patch, MagicMock
    
    src_dir = Path(__file__).parent.parent / "example" / "go_market_aggregator"
    dest_dir = tmp_path / "go_market_aggregator"
    shutil.copytree(src_dir, dest_dir)
    
    mock_client = MagicMock()
    mock_fixer_response = MagicMock()
    mock_fixer_response.output_text = """package aggregator

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"time"
)

type Quote struct {
	Market string  `json:"market"`
	Price  float64 `json:"price"`
}

func FetchAndAggregate(ctx context.Context, urls []string) (map[string]float64, error) {
	client := &http.Client{Timeout: 2 * time.Second}
	quotes := make(chan Quote)
	var wg sync.WaitGroup
	var errs = make(chan error, len(urls))

	for _, url := range urls {
		wg.Add(1)
		go func(targetURL string) {
			defer wg.Done()
			req, err := http.NewRequestWithContext(ctx, "GET", targetURL, nil)
			if err != nil {
				errs <- err
				return
			}
			resp, err := client.Do(req)
			if err != nil {
				errs <- err
				return
			}
			defer resp.Body.Close()

			if resp.StatusCode != http.StatusOK {
				errs <- fmt.Errorf("feed server returned status %d", resp.StatusCode)
				return
			}

			var quote Quote
			if err := json.NewDecoder(resp.Body).Decode(&quote); err != nil {
				errs <- err
				return
			}
			quotes <- quote
		}(url)
	}

	go func() {
		wg.Wait()
		close(quotes)
		close(errs)
	}()

	bestPrices := make(map[string]float64)
	for quote := range quotes {
		if quote.Price > bestPrices[quote.Market] {
			bestPrices[quote.Market] = quote.Price
		}
	}

	if len(errs) > 0 {
		return nil, <-errs
	}

	return bestPrices, nil
}
"""
    mock_reviewer_response = MagicMock()
    mock_reviewer_response.output_text = "APPROVE"
    
    mock_client.responses.create.side_effect = [mock_fixer_response, mock_reviewer_response]
    
    with patch("self_healing_agent.controller._client", return_value=mock_client):
        from self_healing_agent.controller import RepairController
        from self_healing_agent.sandbox import LocalSubprocessSandbox
        
        cmd = "go test -v"
        controller = RepairController("gpt-5.6", LocalSubprocessSandbox(timeout=60))
        approve_callback = lambda target, orig, prop: True
        
        target_file = dest_dir / "aggregator.go"
        
        res_msg = controller.repair_once(cmd, dest_dir, target_file, approve=approve_callback)
        assert "Reparación validada en sandbox" in res_msg
        
        content = target_file.read_text(encoding="utf-8")
        assert "go func() {" in content


def test_stack_trace_parser_multi_file(tmp_path: Path) -> None:
    file1 = tmp_path / "module1.py"
    file1.write_text("def f(): g()\n", encoding="utf-8")
    file2 = tmp_path / "module2.py"
    file2.write_text("def g(): assert False\n", encoding="utf-8")
    
    output = (
        f'File "{tmp_path / "test_run.py"}", line 4, in test_run\n'
        f'    module1.f()\n'
        f'  File "{file1}", line 2, in f\n'
        f'    module2.g()\n'
        f'  File "{file2}", line 2, in g\n'
        f'    def g(): assert False\n'
        f'AssertionError: assert False'
    )
    evidence = StackTraceParser.parse(output, tmp_path)
    assert file2 in evidence.target_files
    assert file1 in evidence.target_files
    assert evidence.target_files[0] == file2
    assert evidence.target_files[1] == file1


def test_controller_rollback_triggers(tmp_path: Path) -> None:
    from unittest.mock import patch, MagicMock
    file1 = tmp_path / "app.py"
    file1.write_text("v1\n", encoding="utf-8")
    
    mock_client = MagicMock()
    mock_fixer_response = MagicMock()
    mock_fixer_response.output_text = '{"app.py": "v2\\n"}'
    mock_reviewer_response = MagicMock()
    mock_reviewer_response.output_text = "APPROVE"
    mock_client.responses.create.side_effect = [mock_fixer_response, mock_reviewer_response]
    
    from self_healing_agent.controller import RepairController
    from self_healing_agent.sandbox import LocalSubprocessSandbox
    
    with patch("self_healing_agent.controller._client", return_value=mock_client):
        controller = RepairController("gpt-5.6", LocalSubprocessSandbox(timeout=10))
        
        with patch.object(controller.sandbox, "validate", return_value=MagicMock(passed=True)):
            with patch("self_healing_agent.controller.run_tests") as mock_run_tests:
                mock_res_fail = MagicMock(passed=False, output='File "app.py", line 1', returncode=1)
                mock_res_pass = MagicMock(passed=True, output='', returncode=0)
                mock_run_tests.side_effect = [mock_res_fail, mock_res_pass]
                
                with pytest.raises(RepairError, match="Parche rechazado"):
                    controller.repair_once(
                        command="pytest",
                        root=tmp_path,
                        source_paths=[file1],
                        approve=lambda t, o, p: True,
                        confirm_rollback=lambda: False
                    )
                
                assert file1.read_text(encoding="utf-8") == "v1\n"
                assert not (tmp_path / ".app.py.bak").exists()


def test_git_commit_integration(tmp_path: Path) -> None:
    from unittest.mock import patch, MagicMock
    file1 = tmp_path / "app.py"
    file1.write_text("v1\n", encoding="utf-8")
    
    mock_client = MagicMock()
    mock_fix = MagicMock(output_text='{"app.py": "v2\\n"}')
    mock_rev = MagicMock(output_text="APPROVE")
    mock_cmt = MagicMock(output_text="fix: patch applied")
    mock_client.responses.create.side_effect = [mock_fix, mock_rev, mock_cmt]
    
    from self_healing_agent.controller import RepairController
    from self_healing_agent.sandbox import LocalSubprocessSandbox
    
    with patch("self_healing_agent.controller._client", return_value=mock_client):
        controller = RepairController("gpt-5.6", LocalSubprocessSandbox(timeout=10))
        
        with patch.object(controller.sandbox, "validate", return_value=MagicMock(passed=True)):
            with patch("self_healing_agent.controller.run_tests") as mock_run:
                mock_run.side_effect = [
                    MagicMock(passed=False, output='File "app.py", line 1', returncode=1),
                    MagicMock(passed=True, output="", returncode=0)
                ]
                
                with patch("self_healing_agent.controller.subprocess.run") as mock_sub:
                    res_msg = controller.repair_once(
                        command="pytest",
                        root=tmp_path,
                        source_paths=[file1],
                        approve=lambda t, o, p: True,
                        git_commit=True
                    )
                    assert "confirmada en Git" in res_msg
                    mock_sub.assert_any_call(["git", "add", str(file1.resolve())], cwd=tmp_path, check=True)
                    mock_sub.assert_any_call(["git", "commit", "-m", "fix: patch applied"], cwd=tmp_path, check=True)


def test_telemetry_roi_metrics(tmp_path: Path) -> None:
    from self_healing_agent.telemetry import TelemetryManager
    mgr = TelemetryManager(tmp_path)
    
    report = mgr.record_repair(
        files=[tmp_path / "aggregator.go"],
        language="go",
        error_message="timeout: goroutines blocked in deadlock",
        ai_time_seconds=12.5
    )
    assert report["total_fixes"] == 1
    assert report["total_man_hours_saved_minutes"] == 60
    
    report2 = mgr.record_repair(
        files=[tmp_path / "calc.py"],
        language="python",
        error_message="AssertionError: assert 5 == 6",
        ai_time_seconds=3.1
    )
    assert report2["total_fixes"] == 2
    assert report2["total_man_hours_saved_minutes"] == 80
    assert len(report2["history"]) == 2
    assert report2["history"][0]["language"] == "go"
    assert report2["history"][1]["language"] == "python"


def test_load_business_rules(tmp_path: Path) -> None:
    from self_healing_agent.model import load_business_rules
    assert load_business_rules(tmp_path) == ""
    
    rules_file = tmp_path / "architecture_rules.md"
    rules_file.write_text("Rule 1: All operations must have 200ms timeout.", encoding="utf-8")
    
    assert "Rule 1" in load_business_rules(tmp_path)


def test_ci_mode_headless_push(tmp_path: Path) -> None:
    from unittest.mock import patch, MagicMock
    from self_healing_agent.controller import RepairController
    from self_healing_agent.sandbox import LocalSubprocessSandbox
    
    file1 = tmp_path / "app.py"
    file1.write_text("v1\n", encoding="utf-8")
    
    mock_client = MagicMock()
    mock_fix = MagicMock(output_text='{"app.py": "v2\\n"}')
    mock_rev = MagicMock(output_text="APPROVE")
    mock_cmt = MagicMock(output_text="fix: patch applied via CI")
    mock_client.responses.create.side_effect = [mock_fix, mock_rev, mock_cmt]
    
    with patch("self_healing_agent.controller._client", return_value=mock_client):
        controller = RepairController("gpt-5.6", LocalSubprocessSandbox(timeout=10))
        
        with patch.object(controller.sandbox, "validate", return_value=MagicMock(passed=True)):
            with patch("self_healing_agent.controller.run_tests") as mock_run:
                mock_run.side_effect = [
                    MagicMock(passed=False, output='File "app.py", line 1', returncode=1),
                    MagicMock(passed=True, output="", returncode=0)
                ]
                
                with patch("self_healing_agent.controller.subprocess.run") as mock_sub:
                    res_msg = controller.repair_once(
                        command="pytest",
                        root=tmp_path,
                        source_paths=[file1],
                        approve=None,
                        git_commit=True,
                        ci_push=True
                    )
                    assert "empujado a la rama remota" in res_msg
                    mock_sub.assert_any_call(["git", "add", str(file1.resolve())], cwd=tmp_path, check=True)
                    mock_sub.assert_any_call(["git", "commit", "-m", "fix: patch applied via CI"], cwd=tmp_path, check=True)
                    mock_sub.assert_any_call(["git", "push", "origin", "HEAD"], cwd=tmp_path, check=True)


