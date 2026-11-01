"""Chinese task descriptions must not select a different scripted repair case."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.config import Settings
from tests.scripted_multi_agent import ScriptedRoleFactory


@pytest.mark.parametrize("mode", ["single", "multi"])
def test_chinese_booking_description_uses_registered_project_and_real_tests(tmp_path, mode):
    base = Settings.from_env()
    project = base.project("python-booking-transfer")
    settings = replace(base, database_path=tmp_path / "chinese.db", workspace_root=tmp_path / "work",
                       projects={project.id: project.source}, project_definitions={project.id: project})
    factory = ScriptedRoleFactory()
    with TestClient(create_app(settings, llm_factory=factory)) as client:
        response = client.post("/api/tasks", json={"source_project": project.id,
            "task_description": "修复预约项目的输入处理，保持原有测试和正常行为。", "execution_mode": mode})
        assert response.status_code == 202
        task_id = response.json()["task_id"]
    with TestClient(create_app(settings, llm_factory=factory)) as client:
        task = client.get(f"/api/tasks/{task_id}").json()
        assert task["status"] == "succeeded", task
        result = task["result"]
        assert result["tests_passed"] and len(result["experience_ids"]) == 3
        steps = client.get(f"/api/tasks/{task_id}/steps").json()["steps"]
        final = [step["result"] for step in steps if step["result"]["metadata"].get("final_verification")][-1]
        assert final["metadata"]["tests"] == 12
        assert all(path.startswith("booking/") for path in result["changed_files"])
        for identifier in result["experience_ids"]:
            record = client.get(f"/api/experiences/{identifier}").json()
            assert record["repository_id"] == project.repository_id
            assert all(path.startswith("booking/") for issue in record["issues"] for path in issue["files"])
