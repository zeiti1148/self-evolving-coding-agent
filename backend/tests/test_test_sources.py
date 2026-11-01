"""Task links expose only unchanged test sources from the task's owned copy."""
import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.config import Settings
from backend.shared import TaskCreateRequest


def prepared(tmp_path, language="java", content=None):
    source = tmp_path / "source"
    relative = "src/test/java/demo/ServiceTest.java" if language == "java" else "tests/test_rules.py"
    original = source / relative
    original.parent.mkdir(parents=True)
    raw = content if content is not None else "  // 中文测试\r\nassert value == '<script>literal</script>';\r\n\r\n".encode()
    original.write_bytes(raw)
    (source / ("pom.xml" if language == "java" else "pyproject.toml")).write_text("<project />" if language == "java" else "[project]\n")
    (source / ".env").write_text("private-fixture-data")
    (source / "Business.java").write_text("business source")
    settings = replace(Settings(), database_path=tmp_path / "tasks.db", workspace_root=tmp_path / "work", projects={"sample": source})
    app = create_app(settings)
    task_id = app.state.tasks.create(TaskCreateRequest(source_project="sample", task_description="读取测试源码")).task_id
    workspace = app.state.tools.manager.create_workspace(source, task_id)
    app.state.tasks.finish(task_id, error="Read-only API fixture; no model was run")
    return settings, app, task_id, workspace, relative, raw


@pytest.mark.parametrize("language", ["java", "python"])
def test_task_sources_preserve_bytes_and_survive_restart_without_using_latest_project(tmp_path, language):
    settings, app, task_id, workspace, relative, raw = prepared(tmp_path, language)
    digest = hashlib.sha256(raw).hexdigest()
    with TestClient(app) as client:
        listing = client.get(f"/api/tasks/{task_id}/test-sources")
        assert listing.status_code == 200, listing.text
        assert listing.json() == {"task_id": task_id, "files": [{"path": relative, "language": language, "sha256": digest}]}
        response = client.get(f"/api/tasks/{task_id}/test-source", params={"path": relative})
        assert response.status_code == 200, response.text
        assert response.json() == {"task_id": task_id, "path": relative, "language": language,
                                   "sha256": digest, "content": raw.decode(), "size_bytes": len(raw)}
        assert response.headers["cache-control"] == "no-store"
        assert str(workspace) not in response.text
    (settings.projects["sample"] / relative).write_text("new project version")
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/tasks/{task_id}/test-source", params={"path": relative}).json()["content"] == raw.decode()


@pytest.mark.parametrize("path", ["../outside_test.py", "/etc/test_secret.py", "C:/secret.py", "tests\\test_rules.py", ".env", "Business.java", "tests/test_added.py"])
def test_rejects_non_test_private_new_and_outside_paths(tmp_path, path):
    _, app, task_id, workspace, _, _ = prepared(tmp_path)
    (workspace / "tests").mkdir()
    (workspace / "tests/test_added.py").write_text("not in original task")
    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/test-source", params={"path": path})
        assert response.status_code in {403, 404}
        assert "private-fixture-data" not in response.text and str(tmp_path) not in response.text


def test_changed_tests_are_not_presented_as_original_evidence(tmp_path):
    _, app, task_id, workspace, relative, _ = prepared(tmp_path)
    (workspace / relative).write_text("modified test source")
    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/test-source", params={"path": relative})
        assert response.status_code == 409
        assert "modified test source" not in response.text


def test_viewing_sources_is_read_only_and_does_not_create_test_steps(tmp_path):
    settings, app, task_id, _, relative, _ = prepared(tmp_path)
    with TestClient(app) as client:
        before = client.get(f"/api/tasks/{task_id}").json()
        for _ in range(2):
            assert client.get(f"/api/tasks/{task_id}/test-source", params={"path": relative}).status_code == 200
        assert client.get(f"/api/tasks/{task_id}").json() == before
        assert client.get(f"/api/tasks/{task_id}/steps").json()["steps"] == []
        assert client.get(f"/api/tasks/{task_id}/agents").json() == []


def test_workspace_ownership_must_match_the_task_request_after_restart(tmp_path):
    settings, app, task_id, _, relative, _ = prepared(tmp_path)
    other = tmp_path / "other-project"
    other.mkdir()
    manifest = settings.workspace_root / ".records" / f"{task_id}.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["source"] = str(other)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    configured = replace(settings, projects={**settings.projects, "other": other})
    with TestClient(create_app(configured)) as client:
        response = client.get(f"/api/tasks/{task_id}/test-source", params={"path": relative})
        assert response.status_code == 404 and "归属" in response.json()["detail"]


def test_missing_task_file_and_cleaned_workspace_have_clear_errors(tmp_path):
    _, app, task_id, workspace, relative, _ = prepared(tmp_path)
    with TestClient(app) as client:
        assert client.get(f"/api/tasks/{uuid4()}/test-sources").status_code == 404
        (workspace / relative).unlink()
        assert client.get(f"/api/tasks/{task_id}/test-source", params={"path": relative}).status_code == 404
        app.state.tools.manager.cleanup_workspace(workspace)
        response = client.get(f"/api/tasks/{task_id}/test-sources")
        assert response.status_code == 404 and "工作副本" in response.json()["detail"]


@pytest.mark.parametrize("raw, expected", [(b"a" * 200001, 413), (b"\xff\xfe\x00", 415)], ids=["too-large", "invalid-utf8"])
def test_bounded_utf8_reader(tmp_path, raw, expected):
    _, app, task_id, _, relative, _ = prepared(tmp_path, content=raw)
    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/test-source", params={"path": relative})
        assert response.status_code == expected


def test_test_file_link_cannot_expose_an_outside_file(tmp_path):
    _, app, task_id, workspace, relative, _ = prepared(tmp_path)
    outside = tmp_path / "private.py"
    outside.write_text("outside-private-fixture")
    (workspace / relative).unlink()
    try:
        (workspace / relative).symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this host")
    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/test-source", params={"path": relative})
        assert response.status_code == 403
        assert "outside-private-fixture" not in response.text
