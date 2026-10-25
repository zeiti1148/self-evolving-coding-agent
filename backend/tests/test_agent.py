from __future__ import annotations

import copy
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from backend.agent.core import Agent
from backend.agent.llm import ChatClient
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.shared import AgentRequest, ApplicationError, ErrorCode, ExperienceCreate, ToolResult


def tool_message(name="read_file", arguments=None, raw_id="model_call_123"):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": raw_id, "type": "function", "function":
            {"name": name, "arguments": json.dumps(arguments or {"path": "src/Main.java"})}}]}


EXPERIENCE = {"task_summary": "Fix null greeting", "root_cause": "Null input was dereferenced.",
              "solution": "Use a default value for null.", "verification": "tests", "tags": ["java"], "error_type": "NullPointerException"}


class SequenceLLM:
    def __init__(self, messages):
        self.responses = iter(messages)
        self.requests = []

    def complete(self, messages, *, tools=None):
        self.requests.append(copy.deepcopy(messages))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def setup(tmp_path):
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="Fix a NullPointerException", max_steps=4)
    store = ExperienceStore(tmp_path / "experiences.db")
    def execute(workspace, call):
        return ToolResult(call_id=call.id, tool_name=call.name, success=True, output="  output\n", metadata={"tests": 2})
    return request, store, execute


def extraction():
    return {"role": "assistant", "content": json.dumps(EXPERIENCE)}


def test_multiround_mapping_callbacks_final_test_and_persistence(setup):
    request, store, execute = setup
    llm = SequenceLLM([tool_message(), {"role": "assistant", "content": "Fixed."}, extraction()])
    seen = []
    result = Agent(llm, execute, store).run(request, on_step=seen.append)
    assert result.status == "succeeded" and result.tests_passed and result.experience_saved
    assert len(seen) == 2 and seen[-1].result.metadata["final_verification"]
    assert llm.requests[1][-1]["tool_call_id"] == "model_call_123"
    assert result.steps[0].id != "model_call_123"
    saved = store.list_experiences()[0]
    assert saved.source_task_id == request.task_id and result.experience_id == saved.id
    assert result.steps[-1].id in saved.verification


@pytest.mark.parametrize("code,expected", [(ErrorCode.TEST_FAILED, False), (ErrorCode.COMMAND_TIMEOUT, None), (ErrorCode.COMMAND_FAILED, None)])
def test_final_verification_tristate(setup, code, expected):
    request, store, _ = setup
    def execute(workspace, call):
        return ToolResult(call_id=call.id, tool_name=call.name, success=False, error_code=code, error_message="test problem")
    llm = SequenceLLM([{"role": "assistant", "content": "Done"}])
    result = Agent(llm, execute, store).run(request)
    assert result.status == "failed" and result.tests_passed is expected
    assert result.experience_saved is None and store.list_experiences() == []


def test_budget_exhaustion_keeps_stopped_even_with_passing_final_tests(setup):
    request, store, execute = setup
    request.max_steps = 1
    llm = SequenceLLM([tool_message()])
    result = Agent(llm, execute, store).run(request)
    assert result.status == "stopped" and result.tests_passed is True and result.repair_succeeded
    assert len(result.steps) == 2 and len(llm.requests) == 1 and result.experience_saved is None


@pytest.mark.parametrize("extract", ["not JSON", "{}", json.dumps({**EXPERIENCE, "root_cause": ""})])
def test_extraction_failure_preserves_success(setup, extract):
    request, store, execute = setup
    llm = SequenceLLM([{"role": "assistant", "content": "Done"}, {"role": "assistant", "content": extract}])
    result = Agent(llm, execute, store).run(request)
    assert result.status == "succeeded" and result.tests_passed and result.experience_saved is False
    assert result.experience_id is None and store.list_experiences() == []


def test_storage_failure_preserves_success(setup, monkeypatch):
    request, store, execute = setup
    monkeypatch.setattr(store, "save_experience", lambda _: (_ for _ in ()).throw(RuntimeError("database secret")))
    result = Agent(SequenceLLM([{"role": "assistant", "content": "Done"}, extraction()]), execute, store).run(request)
    assert result.status == "succeeded" and result.experience_saved is False
    assert "secret" not in result.model_dump_json()


def test_retrieved_experience_reaches_model_and_trace(setup):
    request, store, execute = setup
    saved = store.save_experience(ExperienceCreate(**EXPERIENCE))
    llm = SequenceLLM([{"role": "assistant", "content": "Done"}, extraction()])
    result = Agent(llm, execute, store).run(request)
    assert saved.id in llm.requests[0][1]["content"]
    assert result.tool_results[-1].metadata["retrieved_experience_ids"] == [saved.id]


def test_callback_failure_is_terminal_and_not_silently_dropped(setup):
    request, store, execute = setup
    def fail(step):
        raise RuntimeError("failed")
    result = Agent(SequenceLLM([tool_message()]), execute, store).run(request, on_step=fail)
    assert result.status == "failed" and "record" in result.error_message
    assert len(result.steps) == 1 and result.tests_passed is None


def test_tool_failure_feedback_and_write_requires_new_test(setup):
    request, store, success = setup
    request.max_steps = 5
    def execute(workspace, call):
        if call.name == "unknown":
            return ToolResult(call_id=call.id, tool_name=call.name, success=False, error_code=ErrorCode.INVALID_TOOL_CALL, error_message="unknown")
        return success(workspace, call)
    llm = SequenceLLM([tool_message("unknown"), tool_message("run_tests"), tool_message("write_file"),
                       {"role": "assistant", "content": "Done"}, extraction()])
    result = Agent(llm, execute, store).run(request)
    assert result.status == "succeeded"
    assert "INVALID_TOOL_CALL" in llm.requests[1][-1]["content"]
    assert [c.name for c in result.steps] == ["unknown", "run_tests", "write_file", "run_tests"]


def test_multiple_calls_are_sequential_with_unique_internal_ids(setup):
    request, store, execute = setup
    message = tool_message()
    message["tool_calls"].extend(tool_message("list_files", raw_id="second")["tool_calls"])
    llm = SequenceLLM([message, {"role": "assistant", "content": "Done"}, extraction()])
    result = Agent(llm, execute, store).run(request)
    assert [c.name for c in result.steps] == ["read_file", "list_files", "run_tests"]
    assert [m["tool_call_id"] for m in llm.requests[1] if m["role"] == "tool"] == ["model_call_123", "second"]


def test_model_error_does_not_claim_verification(setup):
    request, store, execute = setup
    result = Agent(SequenceLLM([ApplicationError(ErrorCode.LLM_ERROR, "Unavailable")]), execute, store).run(request)
    assert result.status == "failed" and result.tests_passed is None and not result.steps


def test_http_protocol_and_safe_error_messages():
    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="test-secret", llm_model="test")
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        assert json.loads(request.content)["stream"] is False
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Done"}}]})
    assert ChatClient(settings, transport=httpx.MockTransport(handler)).complete([])["content"] == "Done"
    client = ChatClient(settings, transport=httpx.MockTransport(lambda _: httpx.Response(401, text="test-secret")))
    with pytest.raises(ApplicationError, match="HTTP 401") as error:
        client.complete([])
    assert "test-secret" not in str(error.value)


def test_missing_model_configuration_fails_safely():
    with pytest.raises(ApplicationError, match="Configure"):
        ChatClient(Settings()).complete([])
