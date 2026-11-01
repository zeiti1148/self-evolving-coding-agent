"""A comparison is historical evidence, never a comparison against today's project."""
import hashlib
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.config import Settings
from backend.shared import AgentResult, TaskCreateRequest, TaskStep, ToolCall


BEFORE = '  def greet(name):\r\n    return name.strip()\r\n'
AFTER = '  def greet(name):\r\n    return name.strip() if name else "guest"\r\n'


def prepare(tmp_path, *, legacy=False, before=BEFORE, after=AFTER, freeze=True):
    source = tmp_path / "source"
    source.mkdir()
    (source / "service.py").write_bytes(before.encode())
    (source / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0"\n')
    (source / "test_service.py").write_text("def test_ok(): assert True\n")
    (source / ".env").write_text("secret-fixture")
    settings = replace(Settings(), projects={"sample": source}, workspace_root=tmp_path / "work", database_path=tmp_path / "db.sqlite")
    app = create_app(settings)
    task_id = app.state.tasks.create(TaskCreateRequest(source_project="sample", task_description="查看改动")).task_id
    app.state.tasks.start(task_id)
    manager = app.state.tools.manager
    workspace = manager.create_workspace(source, task_id)
    if legacy:
        manifest = manager.root / ".records" / f"{task_id}.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data.pop("before_code", None)
        manifest.write_text(json.dumps(data), encoding="utf-8")
        manager.records.clear()
    call = ToolCall(id=str(uuid4()), name="write_file", arguments={"path": "service.py", "content": after})
    result = app.state.tools.execute_tool(workspace, call)
    assert result.success
    app.state.tasks.append(task_id, TaskStep(sequence=1, call=call, result=result, completed_at=datetime.now(timezone.utc)))
    if freeze and not legacy:
        manager.capture_changes(workspace)
    app.state.tasks.finish(task_id, error="Fixture: changes recorded without claiming test success")
    return settings, app, task_id, workspace


def file_url(task_id):
    return f"/api/tasks/{task_id}/code-diff?path=service.py"


def test_snapshot_survives_restart_and_later_project_and_workspace_edits(tmp_path):
    settings, app, task_id, workspace = prepare(tmp_path)
    (settings.projects["sample"] / "service.py").write_text("new project code")
    (workspace / "service.py").write_text("later workspace edit")
    with TestClient(create_app(settings)) as client:
        listing = client.get(f"/api/tasks/{task_id}/code-changes")
        assert listing.status_code == 200, listing.text
        assert listing.json()["mode"] == "snapshot"
        assert listing.json()["files"][0]["path"] == "service.py"
        response = client.get(file_url(task_id))
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["before_content"] == BEFORE and data["after_content"] == AFTER
        assert data["before_sha256"] == hashlib.sha256(BEFORE.encode()).hexdigest()
        assert data["after_sha256"] == hashlib.sha256(AFTER.encode()).hexdigest()
        assert data["additions"] == data["deletions"] == 1
        assert [line["kind"] for line in data["hunks"][0]["lines"]] == ["context", "removed", "added"]
        assert response.headers["cache-control"] == "no-store"
        assert str(tmp_path) not in response.text


def test_legacy_uses_hash_checked_original_and_successful_write_record(tmp_path):
    _, app, task_id, _ = prepare(tmp_path, legacy=True)
    with TestClient(app) as client:
        response = client.get(file_url(task_id))
        assert response.status_code == 200, response.text
        assert response.json()["mode"] == "recorded_writes"
        assert response.json()["before_content"] == BEFORE
        assert response.json()["after_content"] == AFTER


@pytest.mark.parametrize("target", ["source", "workspace"])
def test_legacy_rejects_unverifiable_content(tmp_path, target):
    settings, app, task_id, workspace = prepare(tmp_path, legacy=True)
    root = settings.projects["sample"] if target == "source" else workspace
    (root / "service.py").write_text("unrelated private code")
    with TestClient(app) as client:
        response = client.get(file_url(task_id))
        assert response.status_code == 409
        assert "unrelated private code" not in response.text


@pytest.mark.parametrize("path", ["../secret.py", ".env", "test_service.py", "pyproject.toml", "C:/secret.py", "service.py/../.env"])
def test_diff_cannot_read_arbitrary_files(tmp_path, path):
    _, app, task_id, _ = prepare(tmp_path)
    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/code-diff", params={"path": path})
        assert response.status_code == 404
        assert "secret-fixture" not in response.text


def test_creation_deletion_and_empty_files_are_distinct(tmp_path):
    _, app, task_id, workspace = prepare(tmp_path, freeze=False, before="")
    manager = app.state.tools.manager
    (workspace / "service.py").unlink()
    (workspace / "new.py").write_bytes(b"")
    manager.capture_changes(workspace)
    with TestClient(app) as client:
        listing = client.get(f"/api/tasks/{task_id}/code-changes").json()
        assert {item["path"]: item["change_type"] for item in listing["files"]} == {"new.py": "added", "service.py": "deleted"}
        added = client.get(f"/api/tasks/{task_id}/code-diff", params={"path": "new.py"}).json()
        assert added["before_content"] is None and added["after_content"] == ""
        assert added["format_only"] is False
        deleted = client.get(file_url(task_id)).json()
        assert deleted["after_content"] is None and deleted["before_content"] == ""
        assert deleted["format_only"] is False


def test_new_task_without_end_snapshot_does_not_fall_back_to_current_code(tmp_path):
    _, app, task_id, _ = prepare(tmp_path, freeze=False)
    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/code-changes")
        assert response.status_code == 409 and "快照" in response.json()["detail"]


def test_large_diff_is_bounded_and_clear(tmp_path):
    _, app, task_id, _ = prepare(tmp_path, before="x\n" * 4001)
    with TestClient(app) as client:
        response = client.get(file_url(task_id))
        assert response.status_code == 413


def test_newline_only_change_is_visible(tmp_path):
    _, app, task_id, _ = prepare(tmp_path, before="value = 1\r\n", after="value = 1\n")
    with TestClient(app) as client:
        data = client.get(file_url(task_id)).json()
        assert data["format_only"] is True
        assert data["before_content"] != data["after_content"]


def test_snapshot_capture_is_immutable_and_missing_task_is_clear(tmp_path):
    _, app, task_id, workspace = prepare(tmp_path)
    (workspace / "service.py").write_text("later edit")
    app.state.tools.manager.capture_changes(workspace)
    with TestClient(app) as client:
        assert client.get(file_url(task_id)).json()["after_content"] == AFTER
        assert client.get(f"/api/tasks/{uuid4()}/code-changes").status_code == 404


def test_partial_snapshot_write_rolls_back_without_leaving_a_false_record(tmp_path, monkeypatch):
    _, app, task_id, workspace = prepare(tmp_path, freeze=False)
    from backend.tools import code_changes
    def fail(*args):
        raise OSError('Simulated storage fault')
    monkeypatch.setattr(code_changes.os, 'replace', fail)
    with pytest.raises(OSError):
        app.state.tools.manager.capture_changes(workspace)
    records = workspace.parent / '.records'
    assert sorted(path.name for path in records.iterdir()) == [f'{task_id}.json']
    assert (workspace / 'service.py').read_bytes() == AFTER.encode()


def test_corrupted_snapshot_is_rejected_and_not_returned(tmp_path):
    _, app, task_id, workspace = prepare(tmp_path)
    path = workspace.parent / '.records' / f'{task_id}.changes.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['files'][0]['after_content'] = 'wrong snapshot private content'
    path.write_text(json.dumps(data), encoding='utf-8')
    with TestClient(app) as client:
        response = client.get(file_url(task_id))
        assert response.status_code == 409
        assert 'wrong snapshot private content' not in response.text


@pytest.mark.parametrize('mode, storage_fails', [('single', False), ('multi', False), ('single', True)])
def test_api_captures_snapshots_for_both_modes_without_overriding_task_outcome(tmp_path, monkeypatch, mode, storage_fails):
    settings, _, _, _ = prepare(tmp_path)
    class Stub:
        def __init__(self, tools):
            self.tools = tools
        def run(self, request, *, on_step, on_event=None):
            call = ToolCall(id=str(uuid4()), name='write_file', arguments={'path': 'service.py', 'content': AFTER})
            result = self.tools.execute_tool(request.workspace, call)
            on_step(TaskStep(sequence=1, call=call, result=result))
            return AgentResult(task_id=request.task_id, status='stopped', summary='未运行最终测试',
                               repair_succeeded=False, changed_files=['service.py'], steps=[call], tool_results=[result])
    app = create_app(settings, agent_factory=lambda tools, _: Stub(tools), multi_agent_factory=lambda tools, _: Stub(tools))
    if storage_fails:
        def fail(*args):
            raise OSError('Storage failure fixture')
        monkeypatch.setattr(app.state.tools.manager, 'capture_changes', fail)
    with TestClient(app) as client:
        response = client.post('/api/tasks', json={'source_project': 'sample', 'task_description': '记录修改', 'execution_mode': mode})
        assert response.status_code == 202
        task_id = response.json()['task_id']
        for _ in range(200):
            task = client.get(f'/api/tasks/{task_id}').json()
            if task['status'] not in {'running', 'pending'}:
                break
            time.sleep(.01)
        assert task['status'] == 'stopped' and task['result']['tests_passed'] is None
        response = client.get(file_url(task_id))
        assert response.status_code == (409 if storage_fails else 200)
        if not storage_fails:
            assert response.json()['mode'] == 'snapshot' and response.json()['after_content'] == AFTER
