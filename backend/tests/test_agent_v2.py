import copy
import json
from uuid import uuid4

from backend.agent.core import Agent
from backend.experience import ExperienceStore
from backend.shared import AgentRequest, ErrorCode, ExperienceCreate, ToolResult


class SequenceModel:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.requests = []

    def complete(self, messages, *, tools=None):
        self.requests.append(copy.deepcopy(messages))
        return next(self.messages)


def entry(**updates):
    data = dict(task_summary="Handle missing input", error_type="AttributeError", root_cause="Missing input is dereferenced",
                solution="Guard missing input", verification="Original test evidence", tags=["python"],
                language="python", scope="project", repository_id="repo-a")
    data.update(updates)
    return ExperienceCreate(**data)


def test_final_test_discovers_second_issue_retrieves_and_resumes(tmp_path):
    store = ExperienceStore(tmp_path / "memory.db")
    first = store.save_experience(entry())
    second = store.save_experience(entry(error_type="ValueError", solution="Validate numeric input"))
    other = store.save_experience(entry(repository_id="repo-b"))
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="Fix AttributeError",
                           language="python", repository_id="repo-a", source_revision="git:example", max_steps=3)
    extraction = entry().model_dump()
    extraction.update(scope="global", repository_id=None, language="java", source_task_id=str(uuid4()),
                      evidence=[{"invented": True}], merged_from=[first.id], issues=[
        {"error_type": "AttributeError", "root_cause": "Missing input", "solution": "Guard missing input", "files": ["app.py"]},
        {"error_type": "ValueError", "root_cause": "Malformed count", "solution": "Validate count", "files": ["count.py"]}])
    model = SequenceModel([
        {"role": "assistant", "content": "First issue fixed."},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "write-count", "type": "function", "function":
            {"name": "write_file", "arguments": json.dumps({"path": "count.py", "content": "fixed"})}}]},
        {"role": "assistant", "content": "Both fixed."},
        {"role": "assistant", "content": json.dumps(extraction)},
    ])
    runs = 0

    def execute(workspace, call):
        nonlocal runs
        if call.name == "run_tests":
            runs += 1
            if runs == 1:
                return ToolResult(call_id=call.id, tool_name=call.name, success=False, error_code=ErrorCode.TEST_FAILED,
                                  error_message="Count still fails", metadata={"error_types": ["ValueError"], "tests": 12})
        return ToolResult(call_id=call.id, tool_name=call.name, success=True,
                          metadata={"runner": "pytest", "tests": 12, "changed_files": ["app.py", "count.py"]})

    agent = Agent(model, execute, store)
    result = agent.run(request)
    assert result.status == "succeeded" and result.experience_saved and runs == 2
    assert result.steps[0].name == "run_tests" and result.steps[-1].name == "run_tests"
    assert "Count still fails" in model.requests[1][-1]["content"]
    updated = json.loads(model.requests[1][1]["content"])
    assert {x["id"] for x in updated["reference_experiences"]} == {first.id, second.id}
    assert other.id not in json.dumps(model.requests)
    saved = store.get_experience(result.experience_id)
    assert saved.language == "python" and saved.repository_id == "repo-a" and saved.scope == "project"
    assert saved.source_revision == request.source_revision and len(saved.issues) == 2
    assert saved.merged_from == [] and saved.source_task_id == request.task_id
    assert saved.evidence[0].tool_call_id == result.steps[-1].id and saved.evidence[0].tests == 12
    assert agent.last_run_audit["final_verification_attempts"] == 2


def test_unresolved_multi_issue_budget_remains_stopped(tmp_path):
    store = ExperienceStore(tmp_path / "memory.db")
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="Fix", max_steps=2)
    model = SequenceModel([{"role": "assistant", "content": "Done"}] * 2)

    def fail(workspace, call):
        return ToolResult(call_id=call.id, tool_name=call.name, success=False, error_code=ErrorCode.TEST_FAILED,
                          error_message="Still broken", metadata={"error_types": ["NullPointerException"]})

    result = Agent(model, fail, store).run(request)
    assert result.status == "stopped" and result.tests_passed is False and result.experience_saved is None
    assert len(model.requests) == 2 and len(result.steps) == 2 and not store.list_experiences()


def test_mismatched_runner_cannot_create_misleading_evidence(tmp_path):
    store = ExperienceStore(tmp_path / "memory.db")
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="Fix", language="java")
    model = SequenceModel([{"role": "assistant", "content": "Done"},
                           {"role": "assistant", "content": entry().model_dump_json()}])
    def execute(workspace, call):
        return ToolResult(call_id=call.id, tool_name=call.name, success=True, metadata={"runner": "pytest", "tests": 12})
    result = Agent(model, execute, store).run(request)
    assert result.repair_succeeded and result.experience_saved is False and not store.list_experiences()
