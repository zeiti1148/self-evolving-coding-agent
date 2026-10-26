from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from backend.shared import ErrorCode, ToolCall
from backend.tools.service import ToolService
from backend.tools.workspace import WorkspaceManager, inventory


ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "benchmarks/multi-issue/catalog.json").read_text(encoding="utf-8"))
FIXES = json.loads((Path(__file__).parent / "fixtures/multi_issue_fixes.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_multi_issue_baseline_partial_and_complete_repair(tmp_path, case):
    maven = Path(os.environ.get("MAVEN_HOME", ROOT / ".cache/maven/apache-maven-3.9.11"))
    if case["language"] == "java" and not (maven / "bin/m2.conf").is_file():
        pytest.skip("Install the project Maven to run Java multi-issue acceptance")
    source = ROOT / case["source"]
    before = inventory(source)
    manager = WorkspaceManager(tmp_path / "workspaces", {case["id"]: source})
    tool = ToolService(manager, maven)
    workspace = manager.create_workspace(source, str(uuid4()))
    def invoke(name="run_tests", **arguments):
        return tool.execute_tool(workspace, ToolCall(id=str(uuid4()), name=name, arguments=arguments))
    baseline = invoke()
    assert baseline.error_code == ErrorCode.TEST_FAILED, baseline.model_dump()
    assert baseline.metadata["tests"] == case["expected_tests"]
    assert len(baseline.metadata["error_types"]) >= 3
    first, *remaining = FIXES[case["id"]]
    assert invoke("write_file", path=first["path"], content=first["content"]).success
    partial = invoke()
    assert partial.error_code == ErrorCode.TEST_FAILED
    assert 0 < partial.metadata["failures"] + partial.metadata["errors"] < baseline.metadata["failures"] + baseline.metadata["errors"]
    for repair in remaining:
        assert invoke("write_file", path=repair["path"], content=repair["content"]).success
    final = invoke()
    assert final.success, final.model_dump()
    assert final.metadata["tests"] == case["expected_tests"]
    assert final.metadata["failures"] == final.metadata["errors"] == final.metadata["skipped"] == 0
    assert final.metadata["runner"] == ("maven" if case["language"] == "java" else "pytest")
    assert inventory(source) == before
