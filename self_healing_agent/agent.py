"""Compatibility facade. New code should use model, controller, sandbox and view modules."""

from .controller import RepairController, RepairError
from .model import TestResult, infer_source_file as _infer_source_file
from .sandbox import DockerSandbox, run_tests


def repair_once(command, root, source_path, model, approve=None, notify=None):
    """Preserve the original public API while delegating to the controller."""
    return RepairController(model, DockerSandbox()).repair_once(command, root, source_path, approve, notify)


def infer_source_file(test_output, root):
    """Compatibility wrapper for callers using the original RepairError contract."""
    try:
        return _infer_source_file(test_output, root)
    except ValueError as exc:
        raise RepairError(str(exc)) from exc
