import hashlib
import importlib.util
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.config import Settings
from backend.shared import AgentRequest, ApplicationError, ErrorCode, PatchProposal, ToolResult
from backend.tools.service import ToolService
from backend.tools.workspace import WorkspaceManager


def environment(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for path, content in {"a.py": "one\n", "b.py": "two\n", "test_a.py": "fixed tests\n", "pyproject.toml": "[project]\n"}.items():
        (source / path).write_text(content, encoding="utf-8", newline="")
    manager = WorkspaceManager(tmp_path / "work", {"case": source})
    request = AgentRequest(task_id=str(uuid4()), workspace=manager.create_workspace(source, str(uuid4())),
                           task_description="Fix", repository_id="A", language="python")
    tools = ToolService(manager, Settings().maven_home)
    return request, tools


def proposal(changes=None, revision="revision"):
    return PatchProposal(proposal_id=str(uuid4()), agent_id=str(uuid4()), base_revision=revision,
        changes=changes or [dict(path=path, before_sha256=hashlib.sha256(content.encode()).hexdigest(), content="fixed\n")
                            for path, content in [("a.py", "one\n"), ("b.py", "two\n")]])


def apply(value, request, execute, revision="revision"):
    assert importlib.util.find_spec("backend.agent.proposals"), "coordinator proposal application required"
    from backend.agent.proposals import apply_proposal
    return apply_proposal(value, request, execute, snapshot_revision=revision)


@pytest.mark.parametrize("kind", ["revision", "hash", "test", "config", "alias"])
def test_all_prechecks_finish_before_any_write(tmp_path, kind):
    request, tools = environment(tmp_path)
    value = proposal()
    if kind == "revision": value.base_revision = "old"
    if kind == "hash": value.changes[1].before_sha256 = "a" * 64
    if kind == "test": value.changes[1].path = "test_a.py"
    if kind == "config": value.changes[1].path = "pyproject.toml"
    if kind == "alias": value.changes[1].path = "A.py"
    writes = []
    def execute(workspace, call):
        if call.name == "write_file": writes.append(call)
        return tools.execute_tool(workspace, call)
    with pytest.raises((ApplicationError, ValidationError)):
        apply(value, request, execute)
    assert writes == [] and (request.workspace / "a.py").read_text() == "one\n"


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_partial_apply_restores_originals_and_reports_actual_attempts(tmp_path, rollback_fails):
    request, tools = environment(tmp_path)
    writes = []
    def execute(workspace, call):
        if call.name == "write_file":
            writes.append(call)
            if len(writes) == 2 or rollback_fails and len(writes) >= 3:
                return ToolResult(call_id=call.id, tool_name=call.name, success=False,
                                  error_code=ErrorCode.FILE_IO_ERROR, error_message="Injected write failure")
        return tools.execute_tool(workspace, call)
    with pytest.raises(ApplicationError) as caught:
        apply(proposal(), request, execute)
    assert caught.value.details["restoration_succeeded"] is (not rollback_fails)
    assert len(writes) >= 3 and caught.value.details["trace"]
    if not rollback_fails:
        assert (request.workspace / "a.py").read_text() == "one\n"
        assert (request.workspace / "b.py").read_text() == "two\n"


def test_valid_proposal_records_real_writes(tmp_path):
    request, tools = environment(tmp_path)
    trace = apply(proposal(), request, tools.execute_tool)
    assert sum(call.name == "write_file" and result.success for call, result in trace) == 2
    assert (request.workspace / "a.py").read_text() == (request.workspace / "b.py").read_text() == "fixed\n"
