"""Exercise the actual HTTP API, Agent, Maven and SQLite with two benchmark tasks.

Default: test-only scripted model. --live uses the configured real model service.
Reports explicitly identify the mode. All workspaces/databases stay under .cache.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.shared import AgentResult, TaskStatusResponse, TaskStepsResponse, ToolCall
from backend.tools.workspace import inventory
from tests.scripted_llm import ScriptedLLM, TASKS


def accept(*, live=False, output: Path | None = None) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = (ROOT / ".cache/acceptance" / stamp).resolve()
    directory.mkdir(parents=True)
    settings = replace(Settings.from_env(), workspace_root=directory / "workspaces", database_path=directory / "experiences.db")
    originals = {key: inventory(path) for key, path in settings.projects.items()}
    models = []
    def make_model():
        model = ScriptedLLM()
        models.append(model)
        return model
    app = create_app(settings, llm_factory=None if live else make_model)
    report = {"mode": "live-model" if live else "scripted-model-real-tools", "created_at": stamp,
              "directory": str(directory), "requested_model": settings.llm_model if live else None,
              "task_timeout_seconds": settings.task_timeout, "tasks": [], "baselines": {}, "passed": False}
    destination = output or directory / "report.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with TestClient(app) as client:
            for project, description in TASKS:
                baseline_workspace = app.state.tools.manager.create_workspace(settings.projects[project], str(uuid4()))
                baseline = app.state.tools.execute_tool(baseline_workspace, ToolCall(id=str(uuid4()), name="run_tests"))
                report["baselines"][project] = baseline.model_dump(mode="json")
                assert baseline.error_code == "TEST_FAILED", "Original benchmark did not reproduce its test failure"
                start = client.post("/api/tasks", json={"source_project": project, "task_description": description, "max_steps": 20})
                assert start.status_code == 202, start.text
                task_id = start.json()["task_id"]
                deadline = time.monotonic() + settings.task_timeout + settings.llm_timeout + 30
                observed_running_steps = False
                while True:
                    status = TaskStatusResponse.model_validate(client.get(f"/api/tasks/{task_id}").json())
                    steps = TaskStepsResponse.model_validate(client.get(f"/api/tasks/{task_id}/steps").json())
                    observed_running_steps |= status.status == "running" and bool(steps.steps)
                    if status.status not in {"pending", "running"}:
                        break
                    if time.monotonic() > deadline:
                        raise AssertionError("Acceptance polling deadline exceeded")
                    time.sleep(0.15)
                steps = TaskStepsResponse.model_validate(client.get(f"/api/tasks/{task_id}/steps").json())
                result = status.result
                record = {"project": project, "task_id": task_id, "observed_running_steps": observed_running_steps,
                          "status": status.model_dump(mode="json"), "steps": steps.model_dump(mode="json"),
                          "audit": app.state.tasks.get_audit(task_id)}
                report["tasks"].append(record)
                (directory / f"{project}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                assert result is not None and result.status == "succeeded", status.model_dump_json()
                AgentResult.model_validate(result.model_dump())
                assert result.tests_passed and result.experience_saved and result.changed_files
                assert result.tool_results[-1].metadata["final_verification"]
                assert result.tool_results[-1].metadata["tests"] == baseline.metadata["tests"]
                assert len(steps.steps) == len(result.steps) and observed_running_steps
                assert originals[project] == inventory(settings.projects[project]), "Original fixture changed"
                if not live:
                    assert any(r.error_code == "TEST_FAILED" for r in result.tool_results), "Missing real initial failure"
                if len(report["tasks"]) == 2:
                    first_id = report["tasks"][0]["status"]["result"]["experience_id"]
                    assert first_id in result.tool_results[-1].metadata["retrieved_experience_ids"]
                    if not live:
                        assert first_id in models[-1].received_experience_ids
                    else:
                        requests = record["audit"]["model_requests"]
                        assert any(event["success"] and event["phase"] == "decision" and
                                   first_id in event["request_experience_ids"] for event in requests), "Experience was not sent in a successful real model request"
                print(f"PASS {project}: tests={result.tool_results[-1].metadata.get('tests')}, experience={result.experience_id}", flush=True)
            experiences = client.get("/api/experiences?limit=20&offset=0").json()
            assert len(experiences) == 2
        reopened = ExperienceStore(settings.database_path).list_experiences()
        assert len(reopened) == 2
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report: {destination}", flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    accept(live=args.live, output=args.output)
