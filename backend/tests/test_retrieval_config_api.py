from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from backend.api import main
from backend.config import Settings
from backend.shared import ApplicationError, ErrorCode


@pytest.mark.parametrize("mode", ["single", "multi"])
def test_api_wires_configured_retrieval_policy_to_both_runtimes(tmp_path, monkeypatch, mode):
    options = []

    class ObservedAgent:
        def __init__(self, *args, **kwargs):
            options.append(kwargs)

        def run(self, *args, **kwargs):
            raise ApplicationError(ErrorCode.LLM_ERROR, "Stop after checking runtime options")

    monkeypatch.setattr(main, "Agent" if mode == "single" else "MultiAgentCoordinator", ObservedAgent)
    settings = replace(Settings(), workspace_root=tmp_path / "work", database_path=tmp_path / "tasks.db",
                       experience_retrieval_strategy="adaptive")
    with TestClient(main.create_app(settings)) as client:
        response = client.post("/api/tasks", json={"source_project": "java-null-greeting",
            "task_description": "Check options", "execution_mode": mode})
        assert response.status_code == 202
    assert options and options[0]["retrieval_strategy"] == "adaptive"
