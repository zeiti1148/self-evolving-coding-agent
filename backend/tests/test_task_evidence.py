from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.api.main import create_app
from backend.api.task_evidence import _reference_evidence
from backend.config import Settings
from backend.shared import AgentResult, ExperienceCreate, TaskCreateRequest, TaskStep, ToolCall, ToolResult


TEST_FILE = "src/test/java/example/ServiceTest.java"
TEST_TEXT = "package example;\nclass ServiceTest {\n  void missingName() { check(null); }\n}\n"


def settings_for(tmp_path):
    source = tmp_path / "source"
    (source / "src/test/java/example").mkdir(parents=True)
    (source / TEST_FILE).write_text(TEST_TEXT, encoding="utf-8")
    (source / "pom.xml").write_text("<project />", encoding="utf-8")
    return replace(Settings(), projects={"sample": source}, database_path=tmp_path / "evidence.db", workspace_root=tmp_path / "work")


def add_task(app, *, before_changes=(), before_tests=12, after_tests=12, no_final=False, audit=None, mode="multi"):
    tasks = app.state.tasks
    task_id = tasks.create(TaskCreateRequest(task_description="修复名称缺失时的异常", source_project="sample", execution_mode=mode)).task_id
    tasks.start(task_id)
    manager = app.state.tools.manager
    workspace = manager.create_workspace(app.state.settings.projects["sample"], task_id)
    calls, results = [], []
    for index in (1, 2):
        call = ToolCall(id=str(uuid4()), name="run_tests")
        data = {"runner": "maven", "tests": before_tests if index == 1 else after_tests,
                "failures": 2 if index == 1 else 0, "errors": 7 if index == 1 else 0, "skipped": 0,
                "changed_files": list(before_changes or []) if index == 1 else ["src/main/java/example/Names.java"]}
        if index == 1:
            if before_changes is None:
                data.pop("changed_files")
            data["test_failures"] = [{"test": "example.ServiceTest.missingName", "error_type": "NullPointerException",
                "message": "Cannot access D:\\secret\\credentials; api_key=secret-value"}]
        elif not no_final:
            data["final_verification"] = True
        result = ToolResult(call_id=call.id, tool_name="run_tests", success=index == 2, metadata=data,
                            error_code="TEST_FAILED" if index == 1 else None,
                            error_message="Original defect" if index == 1 else None)
        tasks.append(task_id, TaskStep(sequence=index, call=call, result=result))
        calls.append(call); results.append(result)
    tasks.finish(task_id, AgentResult(task_id=task_id, status="succeeded", summary="完成", repair_succeeded=True,
        tests_passed=True, experience_saved=False, experience_save_error="测试夹具未提取经验", steps=calls, tool_results=results), audit=audit or {})
    return task_id, workspace


def test_evidence_compares_same_task_original_tests_and_locates_failure(tmp_path):
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        task_id, _ = add_task(app)
        response = client.get(f"/api/tasks/{task_id}/evidence")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["tests"]["comparable"] and body["tests"]["original_tests_unchanged"]
        assert body["tests"]["before"]["passed"] == 3
        assert body["tests"]["after"]["passed"] == 12
        assert body["tests"]["final_verified"]
        case = body["failure_cases"][0]
        assert case["source_file"] == TEST_FILE and case["source_line"] == 3
        assert case["message"] == "访问了缺失的对象或输入。"
        assert "secret" not in response.text and str(tmp_path) not in response.text
        assert body["reference_recording"] == "unavailable" and body["references"] == []


@pytest.mark.parametrize("change", ["missing-baseline", "already-changed", "different-tests", "no-final", "changed-original-test"])
def test_incomplete_or_noncomparable_evidence_never_claims_comparison(tmp_path, change):
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        options = {"before_changes": None} if change == "missing-baseline" else {"before_changes": ["Names.java"]} if change == "already-changed" else {}
        if change == "different-tests": options["after_tests"] = 11
        if change == "no-final": options["no_final"] = True
        task_id, workspace = add_task(app, **options)
        if change == "changed-original-test":
            (workspace / TEST_FILE).write_text("changed", encoding="utf-8")
        body = client.get(f"/api/tasks/{task_id}/evidence").json()
        assert body["tests"]["comparable"] is False
        assert body["tests"]["unavailable_reason"]
        if change in {"missing-baseline", "already-changed"}: assert body["tests"]["before"] is None
        if change == "no-final": assert body["tests"]["final_verified"] is False
        if change == "changed-original-test":
            assert body["tests"]["original_tests_unchanged"] is False
            assert body["failure_cases"][0]["source_file"] is None


def test_evidence_and_actual_injections_survive_restart_without_raw_audit(tmp_path):
    settings = settings_for(tmp_path)
    first = create_app(settings)
    with TestClient(first) as client:
        experience = first.state.experiences.save_experience(ExperienceCreate(task_summary="先检查缺失输入", root_cause="缺少检查",
            solution="检查缺失输入，再处理内容", verification="已通过原始测试", source_task_id=str(uuid4())))
        row = {"phase": "diagnosis", "success": True, "http_status": 200,
               "request_experience_ids": [experience.id], "request_experience_hashes": ["a" * 64],
               "raw_prompt": "DO NOT EXPOSE", "api_key": "secret", "response_id": "private-id"}
        audit = {"execution_mode": "multi", "model_requests": [
            {"role": "diagnosis", "request_experience_ids": [str(uuid4())], "requests": [row, dict(row)]},
            {"role": "repair", "requests": [{**row, "phase": "repair"}]},
            {"role": "review", "requests": [{**row, "phase": "review"}]},
            {"role": "extractor", "requests": [row]}]}
        task_id, _ = add_task(first, audit=audit)
        expected = client.get(f"/api/tasks/{task_id}/evidence").json()
    second = create_app(settings)
    with TestClient(second) as client:
        response = client.get(f"/api/tasks/{task_id}/evidence")
        assert response.json() == expected
        assert "DO NOT EXPOSE" not in response.text and "secret" not in response.text and "private-id" not in response.text
        refs = response.json()["references"]
        assert len(refs) == 1 and refs[0]["request_count"] == 4
        assert refs[0]["roles"] == [{"role": "diagnosis", "request_count": 2}, {"role": "repair", "request_count": 1}, {"role": "review", "request_count": 1}]
        assert refs[0]["source_task_id"] == experience.source_task_id
        assert response.json()["reference_recording"] == "recorded"


def test_planned_retrieval_failed_requests_and_unrecognized_hashes_are_not_claimed(tmp_path):
    app = create_app(settings_for(tmp_path))
    identifier = str(uuid4())
    audit = {"retrieved_experience_ids": [identifier], "model_requests": [
        {"role": "repair", "request_experience_ids": [identifier], "request_experience_hashes": ["a" * 64], "requests": []},
        {"role": "review", "requests": [{"success": False, "http_status": 503, "request_experience_ids": [identifier], "request_experience_hashes": ["a" * 64]}]}]}
    with TestClient(app):
        references, recorded = _reference_evidence(audit, app.state.experiences)
        assert references == [] and not recorded
        references, recorded = _reference_evidence({"model_requests": [{"success": True, "http_status": 200,
            "request_experience_ids": [identifier], "request_experience_hashes": ["invalid"]}]}, app.state.experiences)
        assert references == [] and not recorded


def test_missing_task_legacy_task_and_invalid_identifier(tmp_path):
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        assert client.get(f"/api/tasks/{uuid4()}/evidence").status_code == 404
        assert client.get("/api/tasks/invalid/evidence").status_code == 422
        task_id = app.state.tasks.create().task_id
        app.state.tasks.finish(task_id, error="old record")
        body = client.get(f"/api/tasks/{task_id}/evidence").json()
        assert body["source_project"] is None and body["tests"]["before"] is None
        assert body["tests"]["comparable"] is False


def test_final_only_test_is_not_a_before_and_after_comparison(tmp_path):
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        tasks = app.state.tasks
        task_id = tasks.create(TaskCreateRequest(task_description="验证现有代码", source_project="sample")).task_id
        tasks.start(task_id)
        app.state.tools.manager.create_workspace(app.state.settings.projects["sample"], task_id)
        call = ToolCall(id=str(uuid4()), name="run_tests")
        result = ToolResult(call_id=call.id, tool_name="run_tests", success=True,
            metadata={"runner": "maven", "tests": 12, "failures": 0, "errors": 0, "skipped": 0,
                      "changed_files": [], "final_verification": True})
        tasks.append(task_id, TaskStep(sequence=1, call=call, result=result))
        tasks.finish(task_id, AgentResult(task_id=task_id, status="succeeded", summary="验证完成", repair_succeeded=True,
            tests_passed=True, experience_saved=False, experience_save_error="没有修改可提取经验", steps=[call], tool_results=[result]))
        body = client.get(f"/api/tasks/{task_id}/evidence").json()
        assert body["tests"]["before"] is None
        assert body["tests"]["after"]["tool_call_id"] == call.id
        assert body["tests"]["after"]["sequence"] == 1
        assert body["tests"]["final_verified"] is True
        assert body["tests"]["comparable"] is False
        assert body["tests"]["unavailable_reason"]
        assert body["failure_cases"] == []
