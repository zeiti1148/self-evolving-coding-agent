"""Each evaluation case needs real failing originals and a verified repair."""
import json
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.shared import ToolCall
from backend.tools import ToolService, WorkspaceManager
from backend.tools.workspace import inventory
from scripts.experiment_v3 import ROOT, load_cases


@pytest.mark.parametrize("identifier", [f"{language}-{domain}-evaluation" for language in ("python", "java")
                                       for domain in ("ticketing", "inventory")])
def test_new_evaluation_originals_fail_and_known_repairs_pass(tmp_path, identifier):
    project = load_cases()[identifier]
    original = inventory(project.source)
    manager = WorkspaceManager(tmp_path / "workspaces", {identifier: project.source})
    workspace = manager.create_workspace(project.source, str(uuid4()))
    settings = Settings.from_env()
    tools = ToolService(manager, settings.maven_home, python_executable=settings.python_executable)
    before = tools.execute_tool(workspace, ToolCall(id=str(uuid4()), name="run_tests"))
    assert before.error_code == "TEST_FAILED" and before.metadata["tests"] == 12, before.error_message or before.output[-2000:]
    assert before.metadata["skipped"] == 0
    fixes = json.loads((ROOT / "backend/tests/fixtures/evaluation_v3_fixes.json").read_text(encoding="utf-8"))
    for fix in fixes[identifier]:
        result = tools.execute_tool(workspace, ToolCall(id=str(uuid4()), name="write_file", arguments=fix))
        assert result.success
    after = tools.execute_tool(workspace, ToolCall(id=str(uuid4()), name="run_tests"))
    assert after.success and after.metadata["tests"] == 12, after.output[-2000:]
    assert after.metadata["failures"] == after.metadata["errors"] == after.metadata["skipped"] == 0
    assert inventory(project.source) == original
