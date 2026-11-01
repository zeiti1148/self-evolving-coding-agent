from __future__ import annotations

import threading
import time
from contextlib import closing
from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.store import TaskStorageError, TaskStore
from backend.config import Settings
from backend.shared import AgentResult, TaskCreateRequest, TaskStep, ToolCall, ToolResult


def configured(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pom.xml").write_text("<project />")
    return replace(Settings(), workspace_root=tmp_path / "workspaces", database_path=tmp_path / "data.db", projects={"sample": source})


def test_pending_running_steps_and_terminal_snapshots(tmp_path):
    entered, release = threading.Event(), threading.Event()
    class FakeAgent:
        def run(self, request, *, on_step):
            call = ToolCall(id=str(uuid4()), name="run_tests")
            result = ToolResult(call_id=call.id, tool_name=call.name, success=True)
            on_step(TaskStep(sequence=1, call=call, result=result))
            entered.set()
            assert release.wait(5)
            return AgentResult(task_id=request.task_id, status="stopped", summary="Stopped", repair_succeeded=True,
                               tests_passed=True, steps=[call], tool_results=[result])
    app = create_app(configured(tmp_path), agent_factory=lambda *_: FakeAgent())
    with TestClient(app) as client:
        response = client.post("/api/tasks", json={"task_description": "Fix", "source_project": "sample"})
        assert response.status_code == 202 and response.json()["status"] == "pending"
        task_id = response.json()["task_id"]
        try:
            assert entered.wait(5)
            status = client.get(f"/api/tasks/{task_id}").json()
            assert status["status"] == "running" and status["steps_completed"] == 1 and status["result"] is None
            steps = client.get(f"/api/tasks/{task_id}/steps").json()
            assert len(steps["steps"]) == 1 and steps["status"] == "running"
            snapshot = app.state.tasks.get_steps(task_id)
            snapshot.steps.clear()
            assert len(app.state.tasks.get_steps(task_id).steps) == 1
        finally:
            release.set()
        for _ in range(100):
            status = client.get(f"/api/tasks/{task_id}").json()
            if status["status"] == "stopped":
                break
            time.sleep(0.01)
        assert status["status"] == "stopped" and status["result"]["tests_passed"] is True


def test_api_validation_unknown_projects_and_paging(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        assert client.post("/api/tasks", json={"task_description": "", "source_project": "sample"}).status_code == 422
        assert client.post("/api/tasks", json={"task_description": "Fix", "source_project": "C:/secret"}).status_code == 400
        assert client.get("/api/tasks/invalid").status_code == 422
        assert client.get(f"/api/tasks/{uuid4()}").status_code == 404
        assert client.get(f"/api/tasks/{uuid4()}/steps").status_code == 404
        assert client.get("/api/experiences?limit=0").status_code == 422
        assert client.get("/api/experiences?offset=-1").status_code == 422
        assert client.get("/api/experiences").json() == []


def test_model_configuration_failure_becomes_visible_terminal_error(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        task_id = client.post("/api/tasks", json={"task_description": "Fix", "source_project": "sample"}).json()["task_id"]
        for _ in range(100):
            status = client.get(f"/api/tasks/{task_id}").json()
            if status["status"] == "failed":
                break
            time.sleep(0.01)
        assert status["status"] == "failed" and "Configure" in status["error_message"]
        assert status["result"]["tests_passed"] is None


def test_database_fault_is_not_an_empty_library(tmp_path):
    settings = configured(tmp_path)
    settings.database_path.write_bytes(b"not a sqlite database")
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/experiences")
        assert response.status_code == 503
        assert str(tmp_path) not in response.text


def test_task_details_steps_and_audit_survive_api_restart(tmp_path):
    settings = configured(tmp_path)

    class Recorder:
        last_run_audit = {"decision_rounds": 1, "tool_calls": 1}

        def run(self, request, *, on_step):
            call = ToolCall(id=str(uuid4()), name="read_file", arguments={"path": "App.java"})
            result = ToolResult(call_id=call.id, tool_name=call.name, success=True, output="original output")
            on_step(TaskStep(sequence=1, call=call, result=result))
            return AgentResult(task_id=request.task_id, status="stopped", summary="Stopped",
                               repair_succeeded=False, steps=[call], tool_results=[result])

    first = create_app(settings, agent_factory=lambda *_: Recorder())
    with TestClient(first) as client:
        response = client.post("/api/tasks", json={"task_description": "Fix the sample", "source_project": "sample"})
        assert response.status_code == 202
        task_id = response.json()["task_id"]
    # Shutdown waits for the worker; experiment scripts also read after lifespan exits.
    expected = first.state.tasks.get(task_id).model_dump(mode="json")
    second = create_app(settings)
    with TestClient(second) as client:
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200 and response.json() == expected
        assert client.get(f"/api/tasks/{task_id}/steps").json()["steps"][0]["result"]["output"] == "original output"
        assert second.state.tasks.get_audit(task_id) == Recorder.last_run_audit
        assert second.state.tasks.get_request(task_id).task_description == "Fix the sample"
        history = client.get("/api/tasks?limit=1&offset=0")
        assert history.status_code == 200 and history.json() == [expected]


def test_api_startup_exposes_interrupted_tasks_without_scheduling_them(tmp_path):
    settings = configured(tmp_path)
    with closing(TaskStore(settings.database_path)) as old:
        task_id = old.create(TaskCreateRequest(task_description="Fix", source_project="sample")).task_id
        old.start(task_id)
        call = ToolCall(id=str(uuid4()), name="read_file")
        old.append(task_id, TaskStep(sequence=1, call=call,
            result=ToolResult(call_id=call.id, tool_name=call.name, success=True, output="Saved before restart")))
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/tasks/{task_id}").json()
        assert response["status"] == "stopped" and response["result"] is None
        assert "restart" in response["error_message"].lower()
        assert response["steps_completed"] == 1
        assert client.get(f"/api/tasks/{task_id}/steps").json()["steps"][0]["result"]["output"] == "Saved before restart"


def test_task_history_is_paginated_and_validated(tmp_path):
    app = create_app(configured(tmp_path))
    ids = []
    for _ in range(3):
        task_id = app.state.tasks.create().task_id
        app.state.tasks.finish(task_id, error="Not scheduled")
        ids.append(task_id)
    with TestClient(app) as client:
        assert [row["task_id"] for row in client.get("/api/tasks?limit=2").json()] == ids[::-1][:2]
        assert [row["task_id"] for row in client.get("/api/tasks?limit=2&offset=2").json()] == ids[:1]
        assert client.get("/api/tasks?offset=3").json() == []
        for query in ("limit=0", "limit=101", "offset=-1", "offset=9223372036854775808"):
            assert client.get(f"/api/tasks?{query}").status_code == 422


def test_task_database_fault_returns_503_instead_of_missing_or_accepted(tmp_path):
    settings = configured(tmp_path)
    settings.database_path.write_bytes(b"not a sqlite database")
    with TestClient(create_app(settings)) as client:
        task_id = str(uuid4())
        for url in ("/api/tasks", f"/api/tasks/{task_id}", f"/api/tasks/{task_id}/steps"):
            response = client.get(url)
            assert response.status_code == 503 and str(tmp_path) not in response.text
        response = client.post("/api/tasks", json={"task_description": "Fix", "source_project": "sample"})
        assert response.status_code == 503 and "task_id" not in response.json()


def test_background_recording_failure_is_visible_instead_of_running_forever(tmp_path, monkeypatch):
    settings = configured(tmp_path)

    class Recorder:
        def run(self, request, *, on_step):
            return AgentResult(task_id=request.task_id, status="stopped", summary="Stopped", repair_succeeded=False)

    app = create_app(settings, agent_factory=lambda *_: Recorder())

    def unavailable(*args, **kwargs):
        raise TaskStorageError()

    monkeypatch.setattr(app.state.tasks, "finish", unavailable)
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"task_description": "Fix", "source_project": "sample"}).json()["task_id"]
        for _ in range(100):
            response = client.get(f"/api/tasks/{task_id}")
            if response.status_code == 503:
                break
            time.sleep(.01)
        assert response.status_code == 503
        assert client.get(f"/api/tasks/{task_id}/steps").status_code == 503
        assert client.get("/api/tasks").status_code == 503
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/tasks/{task_id}").json()["status"] == "stopped"


def test_api_rejects_final_result_that_substitutes_completed_output(tmp_path):
    class InconsistentAgent:
        def run(self, request, *, on_step):
            call = ToolCall(id=str(uuid4()), name="read_file")
            result = ToolResult(call_id=call.id, tool_name=call.name, success=True, output="recorded")
            on_step(TaskStep(sequence=1, call=call, result=result.model_copy(deep=True)))
            result.output = "substituted"
            return AgentResult(task_id=request.task_id, status="stopped", summary="Stopped",
                               repair_succeeded=False, steps=[call], tool_results=[result])

    app = create_app(configured(tmp_path), agent_factory=lambda *_: InconsistentAgent())
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"task_description": "Fix", "source_project": "sample"}).json()["task_id"]
        for _ in range(100):
            status = client.get(f"/api/tasks/{task_id}").json()
            if status["status"] == "failed":
                break
            time.sleep(.01)
        assert status["status"] == "failed" and status["result"] is None
        assert client.get(f"/api/tasks/{task_id}/steps").json()["steps"][0]["result"]["output"] == "recorded"
