from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

from backend.shared import ErrorCode, ToolCall
from backend.tools.service import ToolService
from backend.tools.workspace import WorkspaceManager


@pytest.fixture
def python_service(tmp_path):
    source = tmp_path / "source"
    (source / "tests").mkdir(parents=True)
    (source / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths=["tests"]\npythonpath=["."]\n', encoding="utf-8")
    (source / "calculator.py").write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    (source / "tests/test_calculator.py").write_text(
        "from calculator import divide\ndef test_regular():\n    assert divide(4, 2) == 2\n"
        "def test_zero():\n    assert divide(4, 0) == 0\n", encoding="utf-8")
    manager = WorkspaceManager(tmp_path / "workspaces", {"sample": source})
    service = ToolService(manager, tmp_path / "maven", python_executable=sys.executable)
    workspace = manager.create_workspace(source, str(uuid4()))
    return service, workspace, source


def call(fixture, name="run_tests", **args):
    service, workspace, _ = fixture
    return service.execute_tool(workspace, ToolCall(id=str(uuid4()), name=name, arguments=args))


def test_real_pytest_failure_then_repair_has_fresh_evidence(python_service):
    service, workspace, source = python_service
    failed = call(python_service)
    assert failed.error_code == ErrorCode.TEST_FAILED
    assert failed.metadata["runner"] == "pytest"
    assert failed.metadata["tests"] == 2 and failed.metadata["failures"] == 1
    assert "ZeroDivisionError" in failed.metadata["error_types"]
    assert "test_zero" in failed.metadata["test_failures"][0]["test"]
    fixed = "def divide(a, b):\n    return 0 if b == 0 else a / b\n"
    assert call(python_service, "write_file", path="calculator.py", content=fixed).success
    passed = call(python_service)
    assert passed.success and passed.metadata["tests"] == 2
    assert passed.metadata["test_failures"] == [] and passed.metadata["error_types"] == []
    assert passed.metadata["changed_files"] == ["calculator.py"]
    assert (source / "calculator.py").read_text() != fixed
    reports = list((service.manager.root / ".test-reports" / workspace.name).glob("*.xml"))
    assert len(reports) == 2
    assert call(python_service, "read_file", path="../.test-reports/anything.xml").error_code == ErrorCode.PATH_OUTSIDE_WORKSPACE


@pytest.mark.parametrize("path", ["tests/test_calculator.py", "TESTS/new.py", "nested/test_new.py", "new_test.py",
                                  "conftest.py", "nested/Conftest.py", "pyproject.toml", "pytest.ini", "setup.cfg",
                                  "setup.py", "requirements-dev.txt", "uv.lock", ".agent-project.json", "sitecustomize.py"])
def test_python_acceptance_files_and_runner_config_are_read_only(python_service, path):
    result = call(python_service, "write_file", path=path, content="skip", create_parent=True)
    assert result.error_code == ErrorCode.COMMAND_NOT_ALLOWED


def test_direct_test_tampering_is_detected(python_service):
    (python_service[1] / "tests/test_calculator.py").write_text("def test_fake(): pass")
    assert call(python_service).error_code == ErrorCode.COMMAND_NOT_ALLOWED


def test_old_report_cannot_make_an_empty_run_pass(python_service, monkeypatch):
    import backend.tools.service as module
    service, workspace, _ = python_service
    reports = service.manager.root / ".test-reports" / workspace.name
    reports.mkdir(parents=True)
    (reports / "old.xml").write_text('<testsuite tests="2"/>')
    monkeypatch.setattr(module, "run_process", lambda *args: ("", {"exit_code": 0}, None))
    assert call(python_service).error_code == ErrorCode.COMMAND_FAILED


@pytest.mark.parametrize("body", ["import pytest\n@pytest.mark.skip\ndef test_skip(): pass\n",
                                  "def helper(): pass\n",
                                  "this is not python !\n"])
def test_skipped_zero_and_collection_errors_do_not_pass(tmp_path, body):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[tool.pytest.ini_options]\n')
    (source / "test_case.py").write_text(body)
    manager = WorkspaceManager(tmp_path / "workspaces", {"sample": source})
    fixture = ToolService(manager, tmp_path), manager.create_workspace(source, str(uuid4())), source
    result = call(fixture)
    assert not result.success and result.error_code == ErrorCode.COMMAND_FAILED


def test_python_timeout_is_distinct(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[tool.pytest.ini_options]\n')
    (source / "test_case.py").write_text("import time\ndef test_slow(): time.sleep(10)\n")
    manager = WorkspaceManager(tmp_path / "workspaces", {"sample": source})
    fixture = ToolService(manager, tmp_path), manager.create_workspace(source, str(uuid4())), source
    result = call(fixture, timeout_seconds=1)
    assert result.error_code == ErrorCode.COMMAND_TIMEOUT and result.metadata["runner"] == "pytest"


@pytest.mark.parametrize("command", [["python", "-c", "pass"], ["python", "-m", "pytest", "-k", "regular"], ["pip", "install", "x"]])
def test_python_commands_cannot_bypass_test_entrypoint(python_service, command):
    assert call(python_service, "run_command", command=command).error_code == ErrorCode.COMMAND_NOT_ALLOWED


def test_python_version_uses_configured_interpreter(python_service):
    result = call(python_service, "run_command", command=["python", "--version"])
    assert result.success and "Python" in result.output


def test_new_marker_cannot_change_registered_runner(python_service):
    # An out-of-band mutation is also caught, not just model tool writes.
    (python_service[1] / "pom.xml").write_text("<project/>")
    assert call(python_service).error_code == ErrorCode.COMMAND_NOT_ALLOWED


def test_local_credentials_are_not_copied_or_exposed(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[tool.pytest.ini_options]\n')
    (source / ".ENV.local").write_text("SECRET=not-for-model")
    (source / "private.key").write_text("private key")
    (source / "test_ok.py").write_text("def test_ok(): pass\n")
    manager = WorkspaceManager(tmp_path / "workspaces", {"sample": source})
    fixture = ToolService(manager, tmp_path), manager.create_workspace(source, str(uuid4())), source
    assert not (fixture[1] / ".ENV.local").exists()
    assert not (fixture[1] / "private.key").exists()
    assert call(fixture).success
    assert call(fixture, "read_file", path=".env.local").error_code == ErrorCode.COMMAND_NOT_ALLOWED
    # Also hide a sensitive file deposited out of band after workspace creation.
    (fixture[1] / ".env").write_text("SECRET=external")
    assert ".env" not in call(fixture, "list_files", recursive=True).output
