import json
import threading
from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.config import Settings
from backend.projects import ProjectDefinition, load_catalog, source_revision
from backend.shared import AgentResult, ExperienceCreate


def configured(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath=["."]\n')
    (source / "app.py").write_text("def count(): return 0\n")
    definition = ProjectDefinition(id="demo", label="Python demo", source=source, language="python",
                                   repository_id="team/repository", task_example="Fix multiple issues")
    return replace(Settings(), projects={"demo": source}, project_definitions={"demo": definition},
                   database_path=tmp_path / "experiences.db", workspace_root=tmp_path / "workspaces")


def test_catalog_and_task_metadata_are_server_owned(tmp_path):
    settings = configured(tmp_path)
    seen = []
    entered = threading.Event()

    class Recorder:
        def run(self, request, *, on_step):
            seen.append(request)
            entered.set()
            return AgentResult(task_id=request.task_id, status="stopped", summary="Recorded", repair_succeeded=False)

    app = create_app(settings, agent_factory=lambda *_: Recorder())
    with TestClient(app) as client:
        projects = client.get("/api/projects").json()
        assert projects[0]["id"] == "demo" and projects[0]["language"] == "python"
        assert projects[0]["source_revision"].startswith("tree:")
        assert "source" not in projects[0] and str(tmp_path) not in json.dumps(projects)
        payload = {"task_description": "Fix", "source_project": "demo", "repository_id": "fake"}
        assert client.post("/api/tasks", json=payload).status_code == 422
        del payload["repository_id"]
        assert client.post("/api/tasks", json=payload).status_code == 202 and entered.wait(5)
        assert seen[0].language == "python" and seen[0].repository_id == "team/repository"
        assert seen[0].source_revision == projects[0]["source_revision"]


def test_merge_preview_filter_conflict_and_idempotence(tmp_path):
    app = create_app(configured(tmp_path))
    store = app.state.experiences
    base = dict(task_summary="Fix value", root_cause="Malformed value", solution="Validate input", verification="Tests passed",
                error_type="ValueError", language="python", scope="project", repository_id="team/repository")
    one = store.save_experience(ExperienceCreate(**base))
    two = store.save_experience(ExperienceCreate(**base))
    other = store.save_experience(ExperienceCreate(**{**base, "repository_id": "other"}))
    with TestClient(app) as client:
        assert len(client.get("/api/experiences?repository_id=team/repository&language=python").json()) == 2
        assert client.get("/api/experiences?language=java").json() == []
        body = {"experience_ids": [one.id, two.id]}
        preview = client.post("/api/experiences/merge-preview", json=body)
        assert preview.status_code == 200 and preview.json()["can_merge"]
        merged = client.post("/api/experiences/merge", json=body)
        assert merged.status_code == 200 and set(merged.json()["merged_from"]) == {one.id, two.id}
        assert client.post("/api/experiences/merge", json=body).json()["id"] == merged.json()["id"]
        assert client.post("/api/experiences/merge", json={"experience_ids": [merged.json()["id"], other.id]}).status_code == 409
        assert client.post("/api/experiences/merge", json={"experience_ids": [one.id, str(uuid4())]}).status_code == 404
        assert client.post("/api/experiences/merge", json={"experience_ids": [one.id, one.id]}).status_code == 422


def test_revision_identifies_local_changes_and_catalog_paths(tmp_path):
    settings = configured(tmp_path)
    project = settings.project("demo")
    previous = source_revision(project.source)
    (project.source / "app.py").write_text("def count(): return 1\n")
    assert source_revision(project.source) != previous
    catalog = tmp_path / "catalog.json"
    data = project.model_dump(mode="json")
    data["source"] = "source"
    catalog.write_text(json.dumps([data]), encoding="utf-8")
    assert load_catalog(catalog, tmp_path)["demo"].source == project.source


def test_language_mismatch_is_rejected_before_task_submission(tmp_path):
    settings = configured(tmp_path)
    definition = settings.project_definitions["demo"].model_copy(update={"language": "java"})
    with TestClient(create_app(replace(settings, project_definitions={"demo": definition}))) as client:
        assert client.get("/api/projects").json() == []
        assert client.post("/api/tasks", json={"source_project": "demo", "task_description": "Fix"}).status_code == 400


def test_revision_uses_copied_baseline_even_if_source_changes(tmp_path):
    from backend.tools.workspace import inventory
    settings = configured(tmp_path)
    source = settings.projects["demo"]
    baseline = inventory(source)
    before = source_revision(source, baseline)
    (source / "app.py").write_text("def count(): return 99\n")
    assert source_revision(source, baseline) == before
    assert source_revision(source) != before
