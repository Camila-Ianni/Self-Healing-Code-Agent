"""Model layer: immutable test evidence and stack-trace source discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestResult:
    passed: bool
    output: str
    returncode: int


@dataclass(frozen=True)
class ParsedFrame:
    file_path: Path
    line_number: int
    function_name: str | None
    code_line: str | None
    language: str  # "python" or "go"


@dataclass(frozen=True)
class FailureEvidence:
    passed: bool
    returncode: int
    raw_output: str
    error_message: str
    frames: list[ParsedFrame]
    target_files: list[Path]
    language: str  # "python" or "go"


class StackTraceParser:
    @staticmethod
    def parse(test_output: str, root: Path, returncode: int = 1) -> FailureEvidence:
        # Determine language based on file extensions or test tool name in output
        is_go = ".go" in test_output or "go test" in test_output or "panic:" in test_output
        language = "go" if is_go else "python"

        frames: list[ParsedFrame] = []
        error_message = ""

        if is_go:
            # Match go error lines: file_path.go:line: message or file_path.go:line
            go_error_pattern = re.compile(r'([^\s:]+\.go):(\d+):?(.*)')
            for line in test_output.splitlines():
                m = go_error_pattern.search(line)
                if m:
                    file_str, line_str, msg = m.groups()
                    file_path = Path(file_str)
                    if not file_path.is_absolute():
                        file_path = root / file_path
                    try:
                        resolved = file_path.resolve()
                        resolved.relative_to(root.resolve())
                    except ValueError:
                        continue
                    
                    if resolved.exists():
                        frames.append(ParsedFrame(
                            file_path=resolved,
                            line_number=int(line_str),
                            function_name=None,
                            code_line=None,
                            language="go"
                        ))
                        if msg.strip() and not error_message:
                            error_message = msg.strip()
            
            # Parse panics / stack traces in Go if no frames collected yet
            if not frames:
                panic_match = re.search(r'panic:\s*(.*)', test_output)
                if panic_match:
                    error_message = f"Panic: {panic_match.group(1).strip()}"
                
                go_trace_pattern = re.compile(r'([^\s:]+\.go):(\d+)')
                for m in go_trace_pattern.finditer(test_output):
                    file_str, line_str = m.groups()
                    file_path = Path(file_str)
                    if not file_path.is_absolute():
                        file_path = root / file_path
                    try:
                        resolved = file_path.resolve()
                        resolved.relative_to(root.resolve())
                    except ValueError:
                        continue
                    if resolved.exists():
                        frames.append(ParsedFrame(
                            file_path=resolved,
                            line_number=int(line_str),
                            function_name=None,
                            code_line=None,
                            language="go"
                        ))
            
            if not error_message:
                error_message = "Go test failure or deadlock timeout"

        else:
            # Parse python exceptions: AssertionError: ... or NameError: ... at the end
            lines = test_output.splitlines()
            for line in reversed(lines):
                if re.match(r'^[a-zA-Z0-9_]+Error:', line) or re.match(r'^[a-zA-Z0-9_]+AssertionError:', line) or re.match(r'^AssertionError:', line):
                    error_message = line.strip()
                    break
            if not error_message:
                error_message = "Python test failure"

            # Parse python traceback frames
            python_trace_pattern = re.compile(r'File "([^"]+\.py)", line (\d+)(?:, in ([^\n]+))?')
            matches = list(python_trace_pattern.finditer(test_output))
            for m in matches:
                file_str, line_str, func = m.groups()
                file_path = Path(file_str)
                if not file_path.is_absolute():
                    file_path = root / file_path
                try:
                    resolved = file_path.resolve()
                    resolved.relative_to(root.resolve())
                except ValueError:
                    continue
                
                if resolved.exists():
                    code_line = None
                    start_idx = m.end()
                    remaining = test_output[start_idx:].lstrip()
                    if remaining:
                        code_line_candidate = remaining.splitlines()[0].strip()
                        if not code_line_candidate.startswith('File "') and "Traceback" not in code_line_candidate:
                            code_line = code_line_candidate

                    frames.append(ParsedFrame(
                        file_path=resolved,
                        line_number=int(line_str),
                        function_name=func.strip() if func else None,
                        code_line=code_line,
                        language="python"
                    ))

        # Infer target files: all unique non-test files in frames (closest to error first)
        target_files = []
        seen = set()
        for frame in reversed(frames):
            if "test" not in frame.file_path.name.lower() and frame.file_path not in seen:
                target_files.append(frame.file_path)
                seen.add(frame.file_path)
        
        # Fallback if frames exist but are all test files
        if not target_files and frames:
            target_files = [frames[-1].file_path]

        return FailureEvidence(
            passed=False,
            returncode=returncode,
            raw_output=test_output,
            error_message=error_message,
            frames=frames,
            target_files=target_files,
            language=language
        )


def infer_source_file(test_output: str, root: Path) -> Path:
    """Extract the last local, non-test Python or Go source frame from test output."""
    evidence = StackTraceParser.parse(test_output, root)
    if evidence.target_files:
        return evidence.target_files[0]
    raise ValueError("No pude detectar el archivo fuente. Usá --source ruta/al/archivo.")
