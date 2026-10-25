from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from backend.shared import ApplicationError, ErrorCode, ToolCall
from backend.tools.process import run_process
from backend.tools.service import ToolService
from backend.tools.workspace import WorkspaceManager


@pytest.fixture
def service(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "src").mkdir()
    (source / "src/Main.java").write_bytes(b"  class Main {}\r\n\r\n")
    (source / "pom.xml").write_text("<project />")
    (source / "src/test").mkdir()
    (source / "src/test/Test.java").write_text("// test")
    manager = WorkspaceManager(tmp_path / "workspaces", {"sample": source})
    tool = ToolService(manager, tmp_path / "no-maven")
    workspace = manager.create_workspace(source, str(uuid4()))
    return tool, workspace, source


def invoke(service, name, **arguments):
    tool, workspace, _ = service
    call = ToolCall(id=str(uuid4()), name=name, arguments=arguments)
    result = tool.execute_tool(workspace, call)
    assert result.call_id == call.id and result.tool_name == call.name
    return result


def test_copy_write_read_and_source_preserved(service):
    tool, workspace, source = service
    assert invoke(service, "read_file", path="src/Main.java").output == "  class Main {}\r\n\r\n"
    result = invoke(service, "write_file", path="src/Main.java", content="    fixed\n")
    assert result.success and result.metadata["changed_files"] == ["src/Main.java"]
    assert (source / "src/Main.java").read_bytes() == b"  class Main {}\r\n\r\n"
    assert invoke(service, "read_file", path="src/Main.java").output == "    fixed\n"
    assert "src/Main.java" in invoke(service, "list_files", recursive=True).output
    assert invoke(service, "list_files", max_entries=1).metadata["truncated"]
    assert invoke(service, "read_file", path="src/Main.java", max_chars=3).metadata["truncated"]


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "C:/Windows/win.ini", "C:secret", "\\\\server\\share", "a/../../b", "a\\b", "NUL", "test.", "foo ", "a:b"])
def test_rejects_escaped_and_aliased_paths(service, path):
    result = invoke(service, "read_file", path=path)
    assert result.error_code == ErrorCode.PATH_OUTSIDE_WORKSPACE


@pytest.mark.parametrize("name,args", [
    (" run_tests ", {}), ("unknown", {}), ("read_file", {}), ("list_files", {"recursive": "true"}),
    ("read_file", {"path": "src/Main.java", "start_line": 3, "end_line": 2}),
    ("run_command", {"command": "mvn test"}), ("run_tests", {"timeout_seconds": 999}),
    ("write_file", {"path": "x", "content": "x", "unexpected": True}),
])
def test_strict_arguments_and_dispatch(service, name, args):
    assert invoke(service, name, **args).error_code == ErrorCode.INVALID_TOOL_CALL


@pytest.mark.parametrize("command", [["cmd", "/c", "echo bad"], ["mvn", "test", "-DskipTests"], ["mvn", "-f", "../pom.xml", "test"], ["mvn", "exec:exec"], ["java", "-jar", "evil.jar"], ["mvn", "test;echo bad"]])
def test_rejects_command_injection_and_test_skipping(service, command):
    assert invoke(service, "run_command", command=command).error_code == ErrorCode.COMMAND_NOT_ALLOWED


def test_build_and_tests_protected(service):
    assert invoke(service, "write_file", path="pom.xml", content="bad").error_code == ErrorCode.COMMAND_NOT_ALLOWED
    assert invoke(service, "write_file", path="src/test/Test.java", content="bad").error_code == ErrorCode.COMMAND_NOT_ALLOWED
    tool, workspace, _ = service
    (workspace / "src/test/Test.java").write_text("tampered")
    assert invoke(service, "run_tests").error_code == ErrorCode.COMMAND_NOT_ALLOWED


def test_unregistered_workspace_and_source_are_rejected(service, tmp_path):
    tool, workspace, source = service
    result = tool.execute_tool(source, ToolCall(id=str(uuid4()), name="list_files"))
    assert result.error_code == ErrorCode.INVALID_WORKSPACE
    with pytest.raises(ApplicationError):
        tool.manager.create_workspace(tmp_path, str(uuid4()))
    with pytest.raises(ApplicationError):
        tool.manager.create_workspace(source, "../escape")
    with pytest.raises(ApplicationError):
        tool.manager.cleanup_workspace(tool.manager.root)
    assert source.exists() and workspace.exists()


def test_manifest_survives_restart_and_cleanup_is_bounded(service):
    tool, workspace, source = service
    manager = WorkspaceManager(tool.manager.root, {"sample": source})
    assert manager.get(workspace).baseline == tool.manager.get(workspace).baseline
    manager.cleanup_workspace(workspace)
    assert not workspace.exists() and source.is_dir()
    with pytest.raises(ApplicationError):
        manager.cleanup_workspace(workspace)


def test_link_escape_and_copy_rejected(service, tmp_path):
    tool, workspace, source = service
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "escape"
    if os.name == "nt":
        # Junctions work without developer mode; user-controlled names stay in Python args.
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        assert result.returncode == 0
    else:
        link.symlink_to(outside, target_is_directory=True)
    assert invoke(service, "write_file", path="escape/pwned", content="bad").error_code == ErrorCode.PATH_OUTSIDE_WORKSPACE
    with pytest.raises(ApplicationError):
        tool.manager.cleanup_workspace(workspace)
    assert not (outside / "pwned").exists()
    if os.name == "nt":
        link.rmdir()  # Remove only the junction itself, never its target.
    else:
        link.unlink()


def test_process_output_is_bounded_and_failures_distinct(tmp_path):
    output, metadata, code = run_process([sys.executable, "-c", "print('x'*200000)"], tmp_path, 5, limit=1000)
    assert len(output) == 1000 and metadata["truncated"] and code is None
    _, metadata, code = run_process([sys.executable, "-c", "raise SystemExit(7)"], tmp_path, 5)
    assert code == ErrorCode.COMMAND_FAILED and metadata["exit_code"] == 7
    _, _, code = run_process([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, 1)
    assert code == ErrorCode.COMMAND_TIMEOUT


def test_missing_file_and_maven_are_structured(service):
    assert invoke(service, "read_file", path="missing").error_code == ErrorCode.FILE_NOT_FOUND
    assert invoke(service, "run_tests").error_code == ErrorCode.COMMAND_FAILED


def test_process_descendant_does_not_survive_parent(tmp_path):
    marker = tmp_path / "child-survived"
    child = f"import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).write_text('bad')"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(.3)"
    _, _, code = run_process([sys.executable, "-c", parent], tmp_path, 5)
    assert code is None
    time.sleep(2.2)
    assert not marker.exists()


@pytest.mark.parametrize("xml,exit_code,expected", [
    ('<testsuite tests="2" failures="0" errors="0" skipped="0"/>', 0, None),
    ('<testsuite tests="2" failures="1" errors="0" skipped="0"/>', 1, ErrorCode.TEST_FAILED),
    ('<testsuite tests="0"/>', 0, ErrorCode.COMMAND_FAILED),
    ('<testsuite tests="2" skipped="1"/>', 0, ErrorCode.COMMAND_FAILED),
    ('<testsuite tests="2"/>', 1, ErrorCode.COMMAND_FAILED),
    ('broken', 0, ErrorCode.COMMAND_FAILED),
])
def test_fresh_test_reports_control_verdict(service, monkeypatch, xml, exit_code, expected):
    import backend.tools.service as module
    _, workspace, _ = service
    reports = workspace / "target/surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-old.xml").write_text('<testsuite tests="99"/>')
    def fake_run(*args):
        assert not (reports / "TEST-old.xml").exists()
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "TEST-current.xml").write_text(xml)
        return "output\n", {"exit_code": exit_code}, ErrorCode.COMMAND_FAILED if exit_code else None
    monkeypatch.setattr(module, "maven_argv", lambda *args: ["maven"])
    monkeypatch.setattr(module, "run_process", fake_run)
    result = invoke(service, "run_tests")
    assert result.error_code == expected
    assert result.output == "output\n"
