import json
from uuid import uuid4

import pytest

from backend.agent.core import Agent
from backend.experience import ExperienceStore
from backend.experience import retrieval
from backend.shared import AgentRequest, ApplicationError, ConflictResolution, ErrorCode, ToolResult
from backend.tests.test_agent import SequenceLLM, tool_message
from backend.tests.test_experience_policy import seed


def evidence(description, results, language="python"):
    assert hasattr(retrieval, "adaptive_retrieval_evidence")
    return retrieval.adaptive_retrieval_evidence(description, results, language)


def test_result(error=None, *, success=False, code="TEST_FAILED", output=""):
    return dict(tool_name="run_tests", success=success, error_code=None if success else code,
                output=output, metadata={"error_types": [error] if error else [],
                    "test_failures": [{"test": "test_contract", "error_type": error}] if error else []})


test_result.__test__ = False


def test_no_failure_evidence_does_not_inject_generic_task_matches():
    value = evidence("Normalize a customer record and preserve the contract", [])
    assert value.query is None and value.error_types == ()
    assert value.reason == "no_failure_evidence"


@pytest.mark.parametrize("language,description,expected", [
    ("python", "Fix decimal conversion raising ValueError", ("ValueError",)),
    ("java", "修复用户输入的空指针", ("NullPointerException",)),
])
def test_explicit_task_exception_can_retrieve_before_testing(language, description, expected):
    value = evidence(description, [], language)
    assert description in value.query and value.error_types == expected
    assert value.reason == "task_error"


def test_current_failure_replaces_historical_error_filter_and_query():
    value = evidence("Repair the workflow", [test_result("ValueError", output="obsolete_marker"),
        dict(tool_name="read_file", success=True, output="unrelated_content"),
        test_result("IndexError", output="current_marker")])
    assert value.error_types == ("IndexError",)
    assert "current_marker" in value.query
    assert "obsolete_marker" not in value.query and "unrelated_content" not in value.query


def test_passing_tests_clear_stale_failure_even_when_task_names_an_exception():
    value = evidence("Fix ValueError", [test_result("ValueError"), test_result(success=True)])
    assert value.query is None and value.error_types == () and value.reason == "tests_passed"


def test_new_failure_after_pass_reactivates_retrieval_with_bounded_evidence():
    value = evidence("Repair " + "z" * 10000, [test_result(success=True), test_result("TypeError", output="x" * 10000)])
    assert value.query is not None and len(value.query) <= 6000
    assert value.error_types == ("TypeError",) and value.reason == "test_failure"


@pytest.mark.parametrize("code", ["COMMAND_TIMEOUT", "FILE_NOT_FOUND", "COMMAND_FAILED"])
def test_environment_failures_do_not_reuse_old_test_evidence(code):
    value = evidence("Fix ValueError", [test_result("ValueError"), test_result(code=code)])
    assert value.query is None and value.reason == "test_execution_error"


@pytest.mark.parametrize('reviewed', [True, False])
def test_runtime_injects_after_failure_and_withdraws_after_pass(tmp_path, reviewed):
    store = ExperienceStore(tmp_path / "refs.db")
    relevant = seed(store)
    # These records must remain excluded when the adaptive gate opens.
    forbidden = [seed(store, repository_id="B"), seed(store, language="java"),
                 seed(store, status="candidate"), seed(store, capabilities=["unavailable"])]
    [review] = store.list_conflicts()
    assert review.relation == 'unknown'
    # Differing capability requirements require review even when this query
    # cannot use one side. Test gate timing with an explicitly reviewed fixture,
    # and separately prove an open adaptive gate cannot bypass a pending review.
    if reviewed:
        store.resolve_conflict(review.id, ConflictResolution(version=review.version,
            resolution='confirm_compatible', note='Unit fixture review: both notes preserve the same checked boundary behavior.'))
    llm = SequenceLLM([tool_message("run_tests"), tool_message("read_file"), tool_message("run_tests"),
                      ApplicationError(ErrorCode.LLM_ERROR, "Stop after observing withdrawal")])
    tests = 0

    def execute(workspace, call):
        nonlocal tests
        if call.name == "run_tests":
            tests += 1
            passed = tests > 1
            return ToolResult(call_id=call.id, tool_name=call.name, success=passed,
                error_code=None if passed else ErrorCode.TEST_FAILED, output="" if passed else "ValueError",
                error_message=None if passed else "Test failed",
                metadata={"runner": "pytest", "tests": 1, "failures": 0 if passed else 1, "errors": 0,
                          "skipped": 0, "error_types": [] if passed else ["ValueError"]})
        return ToolResult(call_id=call.id, tool_name=call.name, success=True, output="source")

    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="Repair the customer parser",
                           repository_id="A", language="python", max_steps=6)
    agent = Agent(llm, execute, store, retrieval_strategy="adaptive")
    agent.run(request)
    ids = [[entry["id"] for entry in json.loads(messages[1]["content"])["reference_experiences"]] for messages in llm.requests]
    assert ids == ([[], [relevant.id], [relevant.id], []] if reviewed else [[], [], [], []])
    assert all(entry.id not in json.dumps(llm.requests) for entry in forbidden)
    reasons = {item.get("reason") for item in agent.last_run_audit["retrieval_events"]}
    assert {"no_failure_evidence", "test_failure", "tests_passed"}.issubset(reasons)


def test_runtime_retrieves_only_current_failure_types(tmp_path):
    seen = []

    class Store:
        def retrieve_experiences(self, query="", **kwargs):
            seen.append(kwargs["error_types"])
            return []

    llm = SequenceLLM([tool_message("run_tests"), tool_message("run_tests"), ApplicationError(ErrorCode.LLM_ERROR, "stop")])
    errors = iter(["ValueError", "IndexError"])

    def execute(workspace, call):
        error = next(errors)
        return ToolResult(call_id=call.id, tool_name=call.name, success=False, error_code=ErrorCode.TEST_FAILED,
                          output=error, error_message="Test failed", metadata={"error_types": [error]})

    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="Repair a parser",
                           repository_id="A", language="python", max_steps=4)
    Agent(llm, execute, Store(), retrieval_strategy="adaptive").run(request)
    assert ["ValueError"] in seen and seen[-1] == ["IndexError"]
    assert all(len(values) == 1 for values in seen)


def test_multi_runtime_retrieves_from_real_failing_baseline(tmp_path):
    from backend.agent.orchestrator import MultiAgentCoordinator
    from backend.config import Settings
    from backend.tools import ToolService, WorkspaceManager
    from tests.scripted_multi_agent import ScriptedRoleFactory

    settings = Settings.from_env()
    project = settings.project("python-checkout-complex")
    manager = WorkspaceManager(tmp_path / "work", {project.id: project.source})
    workspace = manager.create_workspace(project.source, str(uuid4()))
    store = ExperienceStore(tmp_path / "refs.db")
    published = seed(store, scope="global", repository_id=None)
    factory = ScriptedRoleFactory()
    tools = ToolService(manager, settings.maven_home)
    coordinator = MultiAgentCoordinator(factory, tools.execute_tool, store, retrieval_strategy="adaptive")
    request = AgentRequest(task_id=str(uuid4()), workspace=workspace, task_description=project.task_example,
        language=project.language, repository_id=project.repository_id, capabilities=project.capabilities,
        execution_mode="multi", max_steps=12)
    result = coordinator.run(request)
    assert result.repair_succeeded and result.tests_passed
    roles = [json.loads(messages[1]["content"]) for messages in factory.requests]
    assert any(published.id in [entry["id"] for entry in payload.get("reference_experiences", [])] for payload in roles)
    assert all(event.get("reason") == "test_failure" for event in coordinator.last_run_audit["retrieval_events"])
    # Language grouping must work for nested multi-role audits as well.
    assert coordinator.last_run_audit["language"] == project.language
