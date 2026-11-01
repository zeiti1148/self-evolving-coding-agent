from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.store import TaskStore
from backend.config import Settings
from backend.shared import AgentEvent, TaskCreateRequest
from tests.scripted_multi_agent import ScriptedRoleFactory


def event(task, sequence=1, **updates):
    return AgentEvent(**(dict(event_id=str(uuid4()), task_id=task, agent_id=str(uuid4()), role="diagnosis",
        phase="diagnosis", status="started", sequence=sequence, summary="Inspecting source") | updates))


def test_events_survive_restart_and_unfinished_roles_stop(tmp_path):
    path = tmp_path / "history.db"
    store = TaskStore(path)
    task = store.create(TaskCreateRequest(task_description="Fix", source_project="test", execution_mode="multi"))
    store.start(task.task_id)
    method = getattr(store, "append_agent_event", None)
    assert method is not None, "role events must be durable"
    started = event(task.task_id)
    method(task.task_id, started)
    with pytest.raises(ValueError): method(task.task_id, started)
    store.close()
    reopened = TaskStore(path)
    assert reopened.get(task.task_id).status == "stopped"
    events = reopened.get_agent_events(task.task_id)
    assert events[0] == started and events[1].status == "stopped" and events[1].agent_id == started.agent_id
    with pytest.raises(ValueError): reopened.append_agent_event(task.task_id, event(task.task_id, 3))
    assert reopened.get_request(task.task_id).execution_mode == "multi"


def test_events_are_task_bound_and_paginated(tmp_path):
    store = TaskStore(tmp_path / "history.db")
    first, second = store.create(), store.create()
    store.start(first.task_id); store.start(second.task_id)
    method = getattr(store, "append_agent_event", None)
    assert method is not None
    started = event(first.task_id)
    method(first.task_id, started)
    method(first.task_id, event(first.task_id, 2, agent_id=started.agent_id, status="completed"))
    with pytest.raises(ValueError): method(second.task_id, started)
    assert store.get_agent_events(second.task_id) == []
    assert len(store.get_agent_events(first.task_id, limit=1)) == 1
    assert store.get_agent_events(first.task_id, after_sequence=1)[0].status == "completed"


def test_multi_api_runs_real_roles_and_history_survives_new_app(tmp_path):
    base = Settings.from_env()
    project = base.project("python-checkout-complex")
    settings = replace(base, database_path=tmp_path / "api.db", workspace_root=tmp_path / "work",
                       projects={project.id: project.source}, project_definitions={project.id: project})
    factory = ScriptedRoleFactory()
    app = create_app(settings, llm_factory=factory)
    with TestClient(app) as client:
        response = client.post("/api/tasks", json={"source_project": project.id, "task_description": project.task_example,
                                                   "execution_mode": "multi"})
        assert response.status_code == 202
        task_id = response.json()["task_id"]
    with TestClient(create_app(settings, llm_factory=factory)) as client:
        task = client.get(f"/api/tasks/{task_id}").json()
        assert task["status"] == "succeeded" and len(task["result"]["experience_ids"]) == 3
        response = client.get(f"/api/tasks/{task_id}/agents")
        assert response.status_code == 200
        events = response.json()
        assert {item["role"] for item in events} >= {"diagnosis", "repair", "review"}
        assert client.get(f"/api/tasks/{task_id}/agents", params={"after_sequence": events[-1]["sequence"]}).json() == []
        assert client.get(f"/api/tasks/{uuid4()}/agents").status_code == 404
        assert client.get(f"/api/tasks/{task_id}/agents?limit=101").status_code == 422
        for identifier in task["result"]["experience_ids"]:
            assert client.get(f"/api/experiences/{identifier}").json()["source_task_id"] == task_id
