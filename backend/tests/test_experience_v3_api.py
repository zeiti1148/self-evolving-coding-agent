from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.config import Settings
from backend.tests.test_experience_promotion import draft, source


def test_publication_api_previews_revises_filters_and_reports_conflicts(tmp_path):
    settings = replace(Settings(), database_path=tmp_path / "api.db", workspace_root=tmp_path / "work")
    app = create_app(settings)
    original = source(app.state.experiences)
    with TestClient(app) as client:
        preview = client.post("/api/experiences/promotion-preview", json={"source_id": original.id})
        assert preview.status_code == 200 and preview.json()["can_publish"]
        body = {"source_id": original.id, "draft": draft().model_dump(mode="json")}
        first = client.post("/api/experiences/publish-global", json=body)
        assert first.status_code == 200
        identifier = first.json()["id"]
        assert client.post("/api/experiences/publish-global", json=body).json()["id"] == identifier
        body["draft"]["solution"] = "Use the target rejection policy after checking input"
        assert client.post("/api/experiences/publish-global", json=body).status_code == 409
        body["supersedes_id"] = identifier
        revised = client.post("/api/experiences/publish-global", json=body)
        assert revised.status_code == 200 and revised.json()["supersedes_id"] == identifier
        graph = client.get(f'/api/experiences/{revised.json()["id"]}/provenance')
        assert graph.status_code == 200 and len(graph.json()["nodes"]) == 3
        rows = client.get("/api/experiences", params={"source_task_id": original.source_task_id, "reuse_status": "active"}).json()
        assert len(rows) == 3 and all(row["source_task_id"] == original.source_task_id for row in rows)
        active = client.get("/api/experiences", params={"scope": "global", "active_only": "true"}).json()
        assert [row["id"] for row in active] == [revised.json()["id"]]
        assert client.get(f"/api/experiences/{uuid4()}").status_code == 404
        body["draft"]["evidence"] = []
        assert client.post("/api/experiences/publish-global", json=body).status_code == 422


def test_experience_storage_fault_is_503_not_an_empty_list(tmp_path):
    settings = replace(Settings(), database_path=tmp_path / "invalid.db", workspace_root=tmp_path / "work")
    settings.database_path.write_bytes(b"broken sqlite file")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/experiences").status_code == 503
        assert client.get(f"/api/experiences/{uuid4()}").status_code == 503
