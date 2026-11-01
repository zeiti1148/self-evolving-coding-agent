from __future__ import annotations

import copy
import json
import time
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
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="Fix a NullPointerException", max_steps=4,
                           repository_id="test-repository")
    store = ExperienceStore(tmp_path / "experiences.db")
    def execute(workspace, call):
        return ToolResult(call_id=call.id, tool_name=call.name, success=True, output="  output\n",
                          metadata={"tests": 2, "changed_files": ["src/Main.java"]})
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


@pytest.mark.parametrize("passed", [True, False])
def test_final_summary_uses_program_result_instead_of_pretest_model_claim(setup, passed):
    request, store, _ = setup
    model_claim = "代码修改已完成，等待程序独立运行最终测试。" if passed else "模型声称修复成功。"
    def execute(workspace, call):
        return ToolResult(call_id=call.id, tool_name=call.name, success=passed,
            error_code=None if passed else ErrorCode.TEST_FAILED,
            error_message=None if passed else "最终测试失败",
            metadata={"runner": "maven", "tests": 2, "failures": 0 if passed else 1, "errors": 0,
                      "skipped": 0, "changed_files": ["src/Main.java"]})
    llm = SequenceLLM([{"role": "assistant", "content": model_claim}, extraction()])
    result = Agent(llm, execute, store).run(request)
    assert result.tests_passed is passed
    assert model_claim not in result.summary
    assert ("2 项测试全部通过" if passed else "最终测试未通过") in result.summary


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
    monkeypatch.setattr(store, "save_experiences", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("database secret")))
    result = Agent(SequenceLLM([{"role": "assistant", "content": "Done"}, extraction()]), execute, store).run(request)
    assert result.status == "succeeded" and result.experience_saved is False
    assert "secret" not in result.model_dump_json()


def test_retrieved_experience_reaches_model_and_trace(setup):
    request, store, execute = setup
    saved = store.save_experience(ExperienceCreate(**EXPERIENCE, scope="project", repository_id=request.repository_id))
    llm = SequenceLLM([{"role": "assistant", "content": "Done"}, extraction()])
    result = Agent(llm, execute, store).run(request)
    assert saved.id in llm.requests[0][1]["content"]
    assert result.tool_results[-1].metadata["retrieved_experience_ids"] == [saved.id]


def test_callback_failure_is_terminal_and_not_silently_dropped(setup):
    request, store, execute = setup
    def fail(step):
        raise RuntimeError("failed")
    result = Agent(SequenceLLM([tool_message()]), execute, store).run(request, on_step=fail)
    assert result.status == "failed" and "记录" in result.error_message
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


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_client_cleanup_failure_preserves_result_and_audit(setup, caplog, status):
    request, store, execute = setup

    class ClosingModel(SequenceLLM):
        def close(self):
            raise RuntimeError("private-close-detail")

    responses = ([{"role": "assistant", "content": "Done"}, extraction()] if status == "succeeded"
                 else [ApplicationError(ErrorCode.LLM_ERROR, "Model unavailable")])
    agent = Agent(ClosingModel(responses), execute, store)
    result = agent.run(request)
    assert result.status == status
    assert agent.last_run_audit["client_cleanup_failed"] is True
    assert agent.last_run_audit["decision_rounds"] == 1
    assert "cleanup" in caplog.text.lower() and "private-close-detail" not in caplog.text
    if status == "succeeded":
        assert result.tests_passed and result.experience_saved
        assert store.get_experience(result.experience_id).source_task_id == request.task_id
    else:
        assert result.tests_passed is None and result.error_message == "Model unavailable"


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
    with pytest.raises(ApplicationError, match="配置"):
        ChatClient(Settings()).complete([])


def test_wire_audit_records_references_model_and_usage_without_secrets():
    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="test-secret", llm_model="test")
    ref = {"id": str(uuid4()), "solution": "private reference body"}
    def handler(request):
        assert ref["id"] in request.content.decode()
        return httpx.Response(200, json={"id": "response1", "model": "served-model", "usage": {"prompt_tokens": 12,"completion_tokens": 5,"total_tokens": 17},
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Done"}}]})
    client = ChatClient(settings, transport=httpx.MockTransport(handler))
    client.complete([{"role": "system", "content": "system"}, {"role":"user","content":json.dumps({"reference_experiences":[ref]})}], tools=[{}])
    assert client.audit[0]["request_experience_ids"] == [ref["id"]]
    assert client.audit[0]["returned_model"] == "served-model"
    assert client.audit[0]["usage"]["total_tokens"] == 17
    serialized = json.dumps(client.audit)
    assert "test-secret" not in serialized and "private reference body" not in serialized
    client.close()


def test_connect_failure_retries_are_bounded_and_read_timeout_does_not_retry():
    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="test-secret", llm_model="test")
    for error_type, expected in [(httpx.ConnectError, 3),(httpx.ReadTimeout, 1)]:
        def handler(request):
            raise error_type("failure", request=request)
        client = ChatClient(settings, transport=httpx.MockTransport(handler))
        with pytest.raises(ApplicationError):
            client.complete([])
        assert len(client.audit) == expected
        client.close()


def test_disabled_experience_is_not_retrieved_or_injected(setup, monkeypatch):
    request, store, execute = setup
    monkeypatch.setattr(store, "search_experiences", lambda *a, **k: pytest.fail("retrieval must be disabled"))
    llm=SequenceLLM([{"role":"assistant","content":"Done"},extraction()])
    agent=Agent(llm,execute,store,use_experience=False)
    result=agent.run(request)
    assert result.status == "succeeded" and json.loads(llm.requests[0][1]["content"])["reference_experiences"] == []
    assert agent.last_run_audit["experience_enabled"] is False


def test_deadline_prevents_later_tools_and_experience_writes(setup):
    request,store,execute=setup
    class SlowLLM:
        def complete(self,*a,**k):
            time.sleep(.02)
            return tool_message()
    result=Agent(SlowLLM(),execute,store,timeout_seconds=.005).run(request)
    assert result.status == "stopped" and result.tests_passed is None and result.steps == []
    assert result.experience_saved is None


def test_compressed_response_is_rejected_before_body_is_consumed():
    class NeverRead(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("compressed body must be rejected before decompression")
            yield b""
    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="test-secret", llm_model="test")
    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=NeverRead())
    client = ChatClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ApplicationError, match="无效响应"):
        client.complete([])
    client.close()


def test_raw_response_chunks_observe_task_deadline():
    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                time.sleep(.005)
                yield b" "
    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="test-secret", llm_model="test")
    client = ChatClient(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=SlowStream())))
    client.set_deadline(time.monotonic() + .02)
    with pytest.raises(ApplicationError) as error:
        client.complete([])
    assert error.value.code == ErrorCode.AGENT_STOPPED
    client.close()


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectTimeout])
def test_network_timeout_after_task_budget_is_stopped(error_type):
    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="test-secret", llm_model="test")
    def handler(request):
        time.sleep(.02)
        raise error_type("timed out", request=request)
    client = ChatClient(settings, transport=httpx.MockTransport(handler))
    client.set_deadline(time.monotonic() + .005)
    with pytest.raises(ApplicationError) as error:
        client.complete([])
    assert error.value.code == ErrorCode.AGENT_STOPPED
    assert len(client.audit) == 1
    client.close()


@pytest.mark.parametrize("raw,expected", [
    ("NumberFormatException and missing port-range validation", "NumberFormatException"),
    ("java.lang.ArrayIndexOutOfBoundsException caused by array access", "ArrayIndexOutOfBoundsException"),
])
def test_extracted_error_description_becomes_retrievable_class(setup, raw, expected):
    request, store, execute = setup
    payload = {**EXPERIENCE, "error_type": raw}
    model = SequenceLLM([{"role": "assistant", "content": "Done"},
                         {"role": "assistant", "content": json.dumps(payload)}])
    result = Agent(model, execute, store).run(request)
    assert result.experience_saved is True
    matches = store.search_experiences("", tags=["java"], error_type=expected)
    assert [entry.id for entry in matches] == [result.experience_id]
