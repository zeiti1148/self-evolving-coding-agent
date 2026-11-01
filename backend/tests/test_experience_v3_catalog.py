import json
from uuid import uuid4

import pytest

from backend.config import ROOT, Settings
from backend.shared import ErrorCode, ToolCall
from backend.tools.service import ToolService
from backend.tools.workspace import WorkspaceManager, inventory


IDS = ["java-checkout-complex", "python-checkout-complex", "java-booking-transfer", "python-booking-transfer"]


def test_v3_catalog_is_registered_with_distinct_identities():
    path = ROOT / "benchmarks/experience-v3/catalog.json"
    assert path.is_file(), "V3 independent source and transfer fixtures are required"
    settings = Settings.from_env()
    projects = [settings.project(identifier) for identifier in IDS]
    assert len({project.repository_id for project in projects}) == 4
    assert [project.expected_tests for project in projects] == [18, 18, 12, 12]
    assert all(project.capabilities for project in projects)


@pytest.mark.parametrize("identifier", IDS)
def test_v3_original_fails_partial_still_fails_and_fixture_passes_without_mutating_source(tmp_path, identifier):
    path = ROOT / "benchmarks/experience-v3/catalog.json"
    assert path.is_file(), "V3 catalog is required"
    settings = Settings.from_env()
    project = settings.project(identifier)
    fixes = json.loads((ROOT / "backend/tests/fixtures/experience_v3_fixes.json").read_text(encoding="utf-8"))[identifier]
    original = inventory(project.source)
    manager = WorkspaceManager(tmp_path / "workspaces", {identifier: project.source})
    tools = ToolService(manager, settings.maven_home)
    workspace = manager.create_workspace(project.source, str(uuid4()))
    def invoke(name="run_tests", **arguments):
        return tools.execute_tool(workspace, ToolCall(id=str(uuid4()), name=name, arguments=arguments))
    baseline = invoke()
    assert baseline.error_code == ErrorCode.TEST_FAILED, baseline.model_dump()
    assert baseline.metadata["tests"] == project.expected_tests
    first, *rest = fixes
    assert invoke("write_file", **first).success
    partial = invoke()
    assert not partial.success and partial.metadata["tests"] == project.expected_tests
    assert 0 < partial.metadata["failures"] + partial.metadata["errors"] < baseline.metadata["failures"] + baseline.metadata["errors"]
    for repair in rest:
        assert invoke("write_file", **repair).success
    final = invoke()
    assert final.success, final.model_dump()
    assert final.metadata["tests"] == project.expected_tests
    assert final.metadata["failures"] == final.metadata["errors"] == final.metadata["skipped"] == 0
    assert inventory(project.source) == original
