import json
from uuid import uuid4

import pytest

from backend.agent.core import Agent
from backend.experience import ExperienceStore
from backend.shared import AgentRequest, ToolResult
from backend.tests.test_agent import SequenceLLM


def setup_run(tmp_path, *, repository="A", payload=None):
    request = AgentRequest(task_id=str(uuid4()), workspace=tmp_path, task_description="Fix all three groups",
                           language="python", repository_id=repository)
    store = ExperienceStore(tmp_path / "extraction.db")
    def execute(workspace, call):
        return ToolResult(call_id=call.id, tool_name=call.name, success=True,
                          metadata={"runner": "pytest", "tests": 18, "failures": 0, "errors": 0, "skipped": 0,
                                    "changed_files": ["app/names.py", "app/numbers.py", "app/items.py"]})
    return request, store, execute


def extracted():
    return {"experiences": [dict(task_summary=label, root_cause=f"Unchecked {label}",
        solution=f"Validate {label} before computation", verification="model invented 999 tests",
        applicability=[condition], issues=[dict(error_type=error, root_cause=f"{label}: {defect}",
            solution=f"Check {defect}", files=[f"app/{module}.py"]) for defect in defects])
        for label, module, error, condition, defects in (
            ("names", "names", "AttributeError", "Missing and blank names have an explicit fallback", ["missing", "blank"]),
            ("numbers", "numbers", "ValueError", "Malformed and negative input are rejected", ["format", "range"]),
            ("items", "items", "IndexError", "Selection validates bounds before access", ["boundary", "null collection"]))]}


def test_one_repair_saves_three_complex_experiences(tmp_path):
    request, store, execute = setup_run(tmp_path)
    llm = SequenceLLM([{"role": "assistant", "content": "Fixed"}, {"content": json.dumps(extracted())}])
    agent = Agent(llm, execute, store)
    result = agent.run(request)
    assert result.status == "succeeded" and result.experience_saved
    assert len(result.experience_ids) == 3 and result.experience_id == result.experience_ids[0]
    saved = [store.get_experience(identifier) for identifier in result.experience_ids]
    assert all(len(item.issues) == 2 and item.evidence[0].tests == 18 for item in saved)
    assert len({item.evidence[0].tool_call_id for item in saved}) == 1
    assert len(llm.requests) == 2
    assert agent.last_run_audit["experience_extraction"] == {
        "status": "succeeded", "stage": "completed", "record_index": 2, "saved_count": 3}
    schema = json.loads(llm.requests[-1][1]["content"])["output_schema"]
    record = schema["properties"]["experiences"]["items"]
    assert record["properties"]["applicability"]["type"] == "array"
    assert record["properties"]["applicability"]["items"]["type"] == "string"
    assert record["properties"]["tags"]["type"] == "array"
    assert record["properties"]["issues"]["items"]["$ref"] == "#/$defs/RepairIssue"
    assert "RepairIssue" in schema["$defs"]
    assert {"issues", "applicability"}.issubset(record["required"])
    assert not {"verification", "evidence", "source_task_id"} & record["properties"].keys()


def test_model_cannot_override_scope_or_evidence(tmp_path):
    request, store, execute = setup_run(tmp_path)
    payload = extracted()
    for item in payload["experiences"]:
        item.update(schema_version=1, scope="global", repository_id="B", source_task_id=str(uuid4()),
                    reuse_status="candidate", evidence=[{"tests": 999}], merged_from=[str(uuid4())],
                    derived_from=[str(uuid4())], supersedes_id=str(uuid4()), batch_id=str(uuid4()))
    llm = SequenceLLM([{"role": "assistant", "content": "Fixed"}, {"content": json.dumps(payload)}])
    result = Agent(llm, execute, store).run(request)
    assert result.experience_saved
    for item in store.list_experiences():
        assert item.scope == "project" and item.repository_id == "A" and item.schema_version == 3
        assert item.source_task_id == request.task_id and item.evidence[0].tests == 18
        assert not item.derived_from and not item.merged_from and item.supersedes_id is None


@pytest.mark.parametrize("invalid", ["file", "duplicate", "empty", "bad-second"])
def test_invalid_batch_preserves_repair_success(tmp_path, invalid):
    request, store, execute = setup_run(tmp_path)
    payload = extracted()
    if invalid == "file": payload["experiences"][1]["issues"][0]["files"] = ["other/secret.py"]
    if invalid == "duplicate": payload["experiences"][1] = dict(payload["experiences"][0], task_summary="Copy")
    if invalid == "empty": payload["experiences"] = []
    if invalid == "bad-second": payload["experiences"][1]["solution"] = ""
    result = Agent(SequenceLLM([{"role": "assistant", "content": "Fixed"}, {"content": json.dumps(payload)}]), execute, store).run(request)
    assert result.status == "succeeded" and result.repair_succeeded and result.tests_passed
    assert result.experience_saved is False and result.experience_ids == [] and result.experience_save_error
    assert store.list_experiences() == []


def test_missing_repository_never_autopublishes(tmp_path):
    request, store, execute = setup_run(tmp_path, repository=None)
    llm = SequenceLLM([{"role": "assistant", "content": "Fixed"}, {"content": json.dumps(extracted()["experiences"][0])}])
    result = Agent(llm, execute, store).run(request)
    assert result.repair_succeeded and result.experience_saved is False
    assert store.list_experiences() == [] and len(llm.requests) == 1


def test_legacy_single_response_still_saves_one_v3_record(tmp_path):
    request, store, execute = setup_run(tmp_path)
    llm = SequenceLLM([{"role": "assistant", "content": "Fixed"}, {"content": json.dumps(extracted()["experiences"][0])}])
    result = Agent(llm, execute, store).run(request)
    assert result.experience_saved and len(result.experience_ids) == 1
    assert store.get_experience(result.experience_id).schema_version == 3


@pytest.mark.parametrize("bad_count", [0, True, None])
def test_zero_or_invalid_final_test_count_is_not_success(tmp_path, bad_count):
    request, store, _ = setup_run(tmp_path)
    def execute(workspace, call):
        return ToolResult(call_id=call.id, tool_name=call.name, success=True,
                          metadata={"tests": bad_count, "runner": "pytest"})
    llm = SequenceLLM([{"role": "assistant", "content": "Fixed"}])
    result = Agent(llm, execute, store).run(request)
    assert not result.repair_succeeded and result.status != "succeeded"
    assert not store.list_experiences() and len(llm.requests) == 1


@pytest.mark.parametrize("invalid, stage, error_type", [
    ("applicability-string", "schema_validation", "ValidationError"),
    ("private-extra-key", "schema_validation", "ValidationError"),
    ("invalid-json", "json_decode", "JSONDecodeError"),
    ("unsupported-file", "source_grounding", "ValueError"),
])
def test_extraction_diagnostic_names_failure_stage_without_private_model_data(tmp_path, invalid, stage, error_type):
    request, store, execute = setup_run(tmp_path)
    payload = extracted()
    private = "private-model-payload-never-log"
    if invalid == "applicability-string":
        payload["experiences"][0]["applicability"] = private
    elif invalid == "private-extra-key":
        payload["experiences"][0][private] = private
    elif invalid == "unsupported-file":
        payload["experiences"][0]["issues"][0]["files"] = [private + ".py"]
    content = private if invalid == "invalid-json" else json.dumps(payload)
    agent = Agent(SequenceLLM([{"content": "Fixed"}, {"content": content}]), execute, store)
    result = agent.run(request)
    diagnostic = agent.last_run_audit["experience_extraction"]
    assert result.repair_succeeded and result.tests_passed and result.experience_saved is False
    assert store.list_experiences() == []
    assert diagnostic["status"] == "failed" and diagnostic["stage"] == stage
    assert diagnostic["error_type"] == error_type
    assert private not in json.dumps(agent.last_run_audit)
    if invalid == "applicability-string":
        assert diagnostic["field_errors"] == [{"field": ["applicability"], "type": "list_type"}]
    elif invalid == "private-extra-key":
        assert diagnostic["field_errors"] == [{"field": ["<unknown>"], "type": "extra_forbidden"}]


def test_storage_failure_diagnostic_does_not_include_exception_message(tmp_path, monkeypatch):
    request, store, execute = setup_run(tmp_path)
    private = "private-storage-connection-details"
    def fail_save(*args, **kwargs):
        raise RuntimeError(private)
    monkeypatch.setattr(store, "save_experiences", fail_save)
    agent = Agent(SequenceLLM([{"content": "Fixed"}, {"content": json.dumps(extracted())}]), execute, store)
    result = agent.run(request)
    assert result.repair_succeeded and result.experience_saved is False
    diagnostic = agent.last_run_audit["experience_extraction"]
    assert diagnostic["stage"] == "storage" and diagnostic["error_type"] == "RuntimeError"
    assert private not in json.dumps(agent.last_run_audit)
