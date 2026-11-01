import importlib.util
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.experience import ExperienceStore
from backend.shared import AgentRequest, ToolCall
from backend.tools import ToolService, WorkspaceManager
from backend.tools.workspace import inventory
from tests.scripted_multi_agent import ScriptedRoleFactory


def run(tmp_path, factory, *, max_steps=20, language="python", invalid_source=False):
    assert importlib.util.find_spec("backend.agent.orchestrator"), "runtime collaboration coordinator required"
    from backend.agent.orchestrator import MultiAgentCoordinator
    settings = Settings.from_env()
    project = settings.project(f"{language}-checkout-complex")
    manager = WorkspaceManager(tmp_path / "work", {project.id: project.source})
    request = AgentRequest(task_id=str(uuid4()), workspace=manager.create_workspace(project.source, str(uuid4())),
        language=project.language, repository_id=project.repository_id, task_description=project.task_example,
        execution_mode="multi", max_steps=max_steps, capabilities=project.capabilities)
    store = ExperienceStore(tmp_path / "experience.db")
    tools = ToolService(manager, settings.maven_home)
    if invalid_source:
        path = factory.fixes[project.id][0]["path"]
        assert tools.execute_tool(request.workspace, ToolCall(id=str(uuid4()), name="write_file",
            arguments={"path": path, "content": "this is not valid source !!!\n"})).success
    agent = MultiAgentCoordinator(factory, tools.execute_tool, store)
    steps, events = [], []
    result = agent.run(request, on_step=steps.append, on_event=events.append)
    return result, agent, store, steps, events


def test_real_roles_handoff_real_tools_final_tests_and_three_experiences(tmp_path):
    factory = ScriptedRoleFactory()
    result, agent, store, steps, events = run(tmp_path, factory)
    assert result.status == "succeeded" and result.tests_passed and len(result.experience_ids) == 3
    assert {event.role for event in events} >= {"diagnosis", "repair", "review", "coordinator", "extractor"}
    assert [event.sequence for event in events] == list(range(1, len(events)+1))
    assert [step.sequence for step in steps] == list(range(1, len(steps)+1))
    assert result.tool_results[-1].metadata["final_verification"] and result.tool_results[-1].metadata["tests"] == 18
    assert agent.last_run_audit["decision_rounds"] == 4
    assert all(client.closed for client in factory.clients)
    assert all(len(store.get_experience(identifier).issues) == 2 for identifier in result.experience_ids)


@pytest.mark.parametrize("language", ["python", "java"])
@pytest.mark.parametrize("when", ["baseline", "proposal"])
def test_compilation_failure_is_repaired_within_remaining_budget(tmp_path, language, when):
    factory = ScriptedRoleFactory(invalid_first=when == "proposal")
    result, agent, store, steps, events = run(tmp_path, factory, language=language, invalid_source=when == "baseline")
    assert result.status == "succeeded" and result.tests_passed and result.experience_saved
    failures = [step.result for step in steps if step.call.name == "run_tests" and not step.result.success]
    assert any(result.error_code == "COMMAND_FAILED" for result in failures)
    assert result.tool_results[-1].metadata["tests"] == 18
    assert factory.counts["repair"] == (1 if when == "baseline" else 2)


@pytest.mark.parametrize("fail_role", [None, "review"])
def test_terminal_role_events_preserve_available_provider_usage(tmp_path, fail_role):
    scripted = ScriptedRoleFactory(fail_role=fail_role)
    def factory():
        delegate = scripted()
        class Client:
            audit = []
            def complete(self, messages, *, tools=None):
                try:
                    return delegate.complete(messages, tools=tools)
                finally:
                    self.audit = [{"usage": {"total_tokens": 7}}]
            def close(self): delegate.close()
        return Client()
    result, agent, store, steps, events = run(tmp_path, factory)
    relevant = [event for event in events if event.role == ("review" if fail_role else "extractor") and event.status != "started"]
    assert relevant and relevant[-1].usage.reported_tokens == 7


def test_environment_and_incomplete_test_runs_are_not_repairable_compile_errors():
    from backend.agent.orchestrator import repairable_test_failure
    from backend.shared import ToolResult, ErrorCode
    for language, message, code in [("java", "Java executable was not found", ErrorCode.COMMAND_FAILED),
                                     ("java", "Dependency resolution failed", ErrorCode.COMMAND_FAILED),
                                     ("python", "No tests collected", ErrorCode.COMMAND_FAILED),
                                     ("python", "SyntaxError", ErrorCode.COMMAND_TIMEOUT)]:
        result = ToolResult(call_id=str(uuid4()), tool_name="run_tests", success=False, output=message,
            error_code=code, error_message=message, metadata={"runner": "maven" if language == "java" else "pytest", "exit_code": 2})
        assert not repairable_test_failure(result, language)


@pytest.mark.parametrize("option", ["partial_first", "reject_first", "stale_first"])
def test_rejected_stale_or_partial_proposals_resume_within_shared_budget(tmp_path, option):
    factory = ScriptedRoleFactory(**{option: True})
    result, agent, store, steps, events = run(tmp_path, factory)
    assert result.status == "succeeded" and result.experience_saved
    assert factory.counts["repair"] == 2 and agent.last_run_audit["decision_rounds"] <= 20
    if option == "partial_first":
        finals = [step.result for step in steps if step.result.metadata.get("final_verification")]
        assert len(finals) == 2 and not finals[0].success and finals[1].success
    if option == "reject_first":
        first_write = next(step for step in steps if step.call.name == "write_file")
        accepted = next(event for event in events if event.role == "review" and "accepted" in event.summary)
        assert first_write.completed_at >= accepted.created_at


def test_budget_exhaustion_has_no_unreviewed_writes_or_success_extraction(tmp_path):
    factory = ScriptedRoleFactory()
    result, agent, store, steps, events = run(tmp_path, factory, max_steps=3)
    assert result.status == "stopped" and result.experience_saved is None
    assert agent.last_run_audit["decision_rounds"] == 3 and not store.list_experiences()
    assert not any(step.call.name == "write_file" for step in steps)
    assert factory.counts.get("extractor", 0) == 0 and all(client.closed for client in factory.clients)


def test_role_failure_is_explicit_and_clients_are_closed(tmp_path):
    factory = ScriptedRoleFactory(fail_role="review")
    result, _, store, steps, events = run(tmp_path, factory)
    assert result.status == "failed" and not result.repair_succeeded and not store.list_experiences()
    assert not any(step.call.name == "write_file" for step in steps)
    assert any(event.role == "review" and event.status == "failed" for event in events)
    assert all(client.closed for client in factory.clients)
