"""Submission durability tests; no model calls or experiment outcomes are generated."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import sqlite3
from threading import Lock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.api import main
from backend.api.store import TaskIdempotencyConflict, TaskStorageError, TaskStore
from backend.config import Settings
from backend.experience import recovery
from backend.shared import TaskCreateRequest


def request(**changes):
    return TaskCreateRequest(**{"task_description": "修复真实项目", "source_project": "sample", **changes})


class RecordingExecutor:
    """Record submissions without executing an agent or spending model tokens."""

    def __init__(self, **kwargs):
        self.calls = []
        self.lock = Lock()
        self.reject = False

    def submit(self, *args):
        with self.lock:
            self.calls.append(args)
        if self.reject:
            raise RuntimeError("Scheduler unavailable")

    def shutdown(self, **kwargs):
        pass


@pytest.fixture
def api(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pom.xml").write_text("<project />", encoding="utf-8")
    settings = replace(Settings(), workspace_root=tmp_path / "workspaces", database_path=tmp_path / "app.db",
                       projects={"sample": source}, knowledge_automation_enabled=False)
    executors = []

    def factory(**kwargs):
        executor = RecordingExecutor(**kwargs)
        executors.append(executor)
        return executor

    monkeypatch.setattr(main, "ThreadPoolExecutor", factory)
    return settings, executors


def test_store_canonical_request_uuid_and_single_transaction(tmp_path):
    with closing(TaskStore(tmp_path / "app.db")) as store:
        key = str(uuid4())
        first, created = store.create_idempotent(request(), key.upper())
        explicit = request(max_steps=20, execution_mode="single", knowledge_recovery_id=None)
        replay, created_again = store.create_idempotent(explicit, key)
        assert created and not created_again and replay == first
        assert store.get_submission(explicit, key) == first
        assert store.get_request(first.task_id) == request()
        with sqlite3.connect(store.path) as connection:
            saved = connection.execute("SELECT * FROM api_task_submissions").fetchall()
            assert len(saved) == 1 and saved[0][0] == key and len(saved[0][1]) == 64
            assert connection.execute("SELECT count(*) FROM api_tasks").fetchone()[0] == 1


@pytest.mark.parametrize("changes", [
    {"task_description": "另一项修复"}, {"source_project": "other"}, {"max_steps": 21},
    {"execution_mode": "multi"}, {"knowledge_recovery_id": str(uuid4())},
])
def test_store_same_key_changed_request_conflicts(tmp_path, changes):
    with closing(TaskStore(tmp_path / "app.db")) as store:
        key = str(uuid4())
        original, _ = store.create_idempotent(request(), key)
        with pytest.raises(TaskIdempotencyConflict):
            store.create_idempotent(request(**changes), key)
        with pytest.raises(TaskIdempotencyConflict):
            store.get_submission(request(**changes), key)
        assert [item.task_id for item in store.list_tasks()] == [original.task_id]


def test_key_and_task_are_rolled_back_together(tmp_path):
    with closing(TaskStore(tmp_path / "app.db")) as store:
        store.initialize()
        with sqlite3.connect(store.path) as connection:
            connection.execute("""CREATE TRIGGER reject_submission BEFORE INSERT ON api_task_submissions
                                  BEGIN SELECT RAISE(ABORT, 'Test storage failure'); END""")
        key = str(uuid4())
        with pytest.raises(TaskStorageError):
            store.create_idempotent(request(), key)
        assert store.list_tasks() == [] and store.get_submission(request(), key) is None
        with sqlite3.connect(store.path) as connection:
            connection.execute("DROP TRIGGER reject_submission")
        _, created = store.create_idempotent(request(), key)
        assert created


def test_concurrent_connections_accept_one_task(tmp_path):
    path = tmp_path / "app.db"
    with closing(TaskStore(path)) as first, closing(TaskStore(path)) as second:
        first.initialize()
        second.initialize()
        key = str(uuid4())
        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(lambda index: (first if index % 2 else second)
                                        .create_idempotent(request(), key), range(24)))
        assert len({response.task_id for response, _ in outcomes}) == 1
        assert sum(created for _, created in outcomes) == 1
        assert len(first.list_tasks()) == 1


def test_api_replays_acceptance_after_task_finishes_and_project_disappears(api):
    settings, executors = api
    app = main.create_app(settings)
    headers = {"Idempotency-Key": str(uuid4())}
    with TestClient(app) as client:
        first = client.post("/api/tasks", json=request().model_dump(), headers=headers)
        assert first.status_code == 202
        app.state.tasks.finish(first.json()["task_id"], error="Terminal outcome")
        settings.projects.clear()
        replay = client.post("/api/tasks", json=request().model_dump(), headers=headers)
        assert replay.status_code == 202 and replay.json() == first.json()
        conflict = client.post("/api/tasks", json=request(max_steps=30).model_dump(), headers=headers)
        assert conflict.status_code == 409
        assert len(executors[0].calls) == 1


def test_api_concurrent_replays_schedule_once(api):
    settings, executors = api
    app = main.create_app(settings)
    headers = {"Idempotency-Key": str(uuid4())}
    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: client.post("/api/tasks", json=request().model_dump(),
                                                             headers=headers), range(24)))
        assert all(result.status_code == 202 for result in results)
        assert all(result.json() == results[0].json() for result in results)
        assert len(executors[0].calls) == 1


def test_restart_does_not_reschedule_committed_submission(api):
    settings, executors = api
    headers = {"Idempotency-Key": str(uuid4())}
    with TestClient(main.create_app(settings)) as client:
        original = client.post("/api/tasks", json=request().model_dump(), headers=headers)
        assert len(executors[0].calls) == 1
    with TestClient(main.create_app(settings)) as client:
        replay = client.post("/api/tasks", json=request().model_dump(), headers=headers)
        assert replay.status_code == 202 and replay.json() == original.json()
        assert client.get(f"/api/tasks/{replay.json()['task_id']}").json()["status"] == "stopped"
        assert executors[1].calls == []


def test_no_key_remains_independent_and_invalid_key_is_rejected(api):
    settings, executors = api
    app = main.create_app(settings)
    with TestClient(app) as client:
        first = client.post("/api/tasks", json=request().model_dump())
        second = client.post("/api/tasks", json=request().model_dump())
        invalid = client.post("/api/tasks", json=request().model_dump(), headers={"Idempotency-Key": "invalid"})
        assert first.json()["task_id"] != second.json()["task_id"]
        assert invalid.status_code == 422 and len(executors[0].calls) == 2
        parameters = client.get("/openapi.json").json()["paths"]["/api/tasks"]["post"]["parameters"]
        key = next(parameter for parameter in parameters if parameter["name"] == "Idempotency-Key")
        assert key["in"] == "header" and not key["required"]
        assert {"type": "string", "format": "uuid"} in key["schema"]["anyOf"]
        preflight = client.options("/api/tasks", headers={"Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Idempotency-Key,Content-Type"})
        assert preflight.status_code == 200


def test_recovery_attachment_and_scheduler_failure_are_not_repeated(api, monkeypatch):
    settings, executors = api
    attached = []
    monkeypatch.setattr(recovery, "attach_task", lambda *args: attached.append(args))
    body = request(knowledge_recovery_id=str(uuid4())).model_dump()
    headers = {"Idempotency-Key": str(uuid4())}
    app = main.create_app(settings)
    executors[0].reject = True
    with TestClient(app) as client:
        first = client.post("/api/tasks", json=body, headers=headers)
        replay = client.post("/api/tasks", json=body, headers=headers)
        assert replay.status_code == 202 and replay.json() == first.json()
        assert len(attached) == len(executors[0].calls) == 1
        assert client.get(f"/api/tasks/{first.json()['task_id']}").json()["status"] == "failed"


def test_invalid_project_does_not_claim_submission_key(api):
    settings, executors = api
    app = main.create_app(settings)
    headers = {"Idempotency-Key": str(uuid4())}
    with TestClient(app) as client:
        invalid = client.post("/api/tasks", json=request(source_project="missing").model_dump(), headers=headers)
        assert invalid.status_code == 400
        accepted = client.post("/api/tasks", json=request().model_dump(), headers=headers)
        assert accepted.status_code == 202 and len(executors[0].calls) == 1
