from __future__ import annotations

import threading
import time
from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.config import Settings
from backend.shared import AgentResult, TaskStep, ToolCall, ToolResult


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
        assert response.status_code == 500
        assert str(tmp_path) not in response.text
