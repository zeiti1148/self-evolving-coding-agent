"""Java/Python multi-issue repairs, scoped reuse and merging with actual tools.

Default: deterministic test model. --live: configured ChatClient, never a fallback.
Each run owns a fresh SQLite database and workspace directory.
"""
from __future__ import annotations

import argparse
import copy
import json
import hashlib
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from backend.agent.llm import ChatClient
from backend.api.main import create_app
from backend.config import Settings
from backend.shared import ToolCall
from backend.tools.workspace import inventory


class ScriptedMultiIssueModel:
    """Acceptance fixture with an intentionally premature completion to exercise recovery."""
    def __init__(self, repairs: list[dict], language: str):
        self.requests = []
        self.language = language
        self.repairs = repairs
        self.decisions = 0

    @staticmethod
    def calls(specs):
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"model-{uuid4()}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for name, args in specs]}

    def complete(self, messages, *, tools=None):
        self.requests.append(copy.deepcopy(messages))
        if not tools:
            return {"role": "assistant", "content": json.dumps({
                "task_summary": "Repair multi-issue fulfillment input handling",
                "error_type": self.repairs[0]["error_type"], "root_cause": "Unvalidated independent inputs reach unsafe operations",
                "solution": "Validate each input before its dependent operation and preserve normal behavior",
                "verification": "Provided by independent final verification", "tags": [self.language, "input-validation"],
                "applicability": ["Fulfillment name, quantity and item selection inputs with the existing fallback contract"],
                "issues": [{"error_type": row["error_type"], "root_cause": f"Unchecked input in {row['path']}",
                            "solution": f"Validate the input before use in {row['path']}", "files": [row["path"]]}
                           for row in self.repairs]})}
        self.decisions += 1
        if self.decisions == 1:
            return self.calls([("run_tests", {})])
        if self.decisions == 2:
            return self.calls([("write_file", {k: self.repairs[0][k] for k in ("path", "content")})])
        if self.decisions == 3:
            return {"role": "assistant", "content": "First problem fixed."}
        if self.decisions == 4:
            return self.calls([("write_file", {k: row[k] for k in ("path", "content")}) for row in self.repairs[1:]])
        if self.decisions == 5:
            return self.calls([("run_tests", {})])
        return {"role": "assistant", "content": "All original tests pass after repairing the independent defects."}


class RecordingModel:
    def __init__(self, delegate):
        self.delegate = delegate
        self.requests = []

    def complete(self, messages, *, tools=None):
        self.requests.append(copy.deepcopy(messages))
        return self.delegate.complete(messages, tools=tools)

    def set_deadline(self, deadline):
        if hasattr(self.delegate, "set_deadline"):
            self.delegate.set_deadline(deadline)

    @property
    def audit(self):
        return getattr(self.delegate, "audit", [])

    def close(self):
        if hasattr(self.delegate, "close"):
            self.delegate.close()


def run(live: bool, directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    base = Settings.from_env()
    settings = replace(base, workspace_root=directory / "workspaces", database_path=directory / "experiences.db")
    repairs = json.loads((ROOT / "backend/tests/fixtures/multi_issue_fixes.json").read_text(encoding="utf-8"))
    report = {"mode": "live" if live else "scripted", "created_at": datetime.now(timezone.utc).isoformat(),
              "tasks": [], "baselines": {}, "merge": {}, "passed": False}
    report["code_version"] = {
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip(),
        "dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()),
        "source_sha256": {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sorted((ROOT / "backend").rglob("*.py")) if "tests" not in path.parts},
    }
    active = {"project": "java-fulfillment-multi"}
    models = []

    def factory():
        project = settings.project(active["project"])
        model = RecordingModel(ChatClient(settings) if live else ScriptedMultiIssueModel(repairs[project.id], project.language))
        models.append(model)
        return model

    app = create_app(settings, llm_factory=factory)
    source_hashes = {key: inventory(settings.projects[key]) for key in repairs}

    def save():
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def submit(client, identifier):
        active["project"] = identifier
        project = settings.project(identifier)
        response = client.post("/api/tasks", json={"source_project": identifier,
            "task_description": project.task_example, "max_steps": 25})
        response.raise_for_status()
        task_id = response.json()["task_id"]
        seen_running = False
        deadline = time.monotonic() + settings.task_timeout + 90
        while time.monotonic() < deadline:
            status = client.get(f"/api/tasks/{task_id}").json()
            if status["status"] not in {"pending", "running"}:
                break
            seen_running |= status["status"] == "running" and status["steps_completed"] > 0
            time.sleep(.1)
        else:
            raise RuntimeError("Task did not finish within acceptance timeout")
        result = status.get("result") or {}
        audit = app.state.tasks.get_audit(task_id)
        task = {"project": identifier, "task_id": task_id, "running_steps_observed": seen_running,
                "status": status, "audit": audit}
        report["tasks"].append(task)
        save()
        if status["status"] != "succeeded" or result.get("experience_saved") is not True:
            raise RuntimeError(f"{identifier} did not complete repair and experience saving")
        final = result["tool_results"][-1]
        assert final["metadata"]["final_verification"] and final["metadata"]["tests"] == project.expected_tests
        assert not any(final["metadata"][key] for key in ("errors", "failures", "skipped"))
        saved = app.state.experiences.get_experience(result["experience_id"])
        assert saved.language == project.language and saved.repository_id == project.repository_id
        assert len(saved.issues) >= 3 and saved.evidence[0].tests == project.expected_tests
        task["experience"] = saved.model_dump(mode="json")
        task["model_reference_ids"] = [entry["id"] for entry in json.loads(models[-1].requests[0][1]["content"])["reference_experiences"]]
        if not live:
            test_results = [r for r in result["tool_results"] if r["tool_name"] == "run_tests"]
            assert len(test_results) >= 4 and not test_results[0]["success"] and not test_results[1]["success"]
            assert test_results[-1]["success"] and audit["final_verification_attempts"] == 2
        save()
        print(f"PASS {identifier}: {final['metadata']['tests']} tests, {len(saved.issues)} structured issues", flush=True)
        return saved

    try:
        with TestClient(app) as client:
            catalog = client.get("/api/projects").json()
            assert set(repairs).issubset({entry["id"] for entry in catalog})
            for identifier in repairs:
                copied = app.state.tools.manager.create_workspace(settings.projects[identifier], str(uuid4()))
                baseline = app.state.tools.execute_tool(copied, ToolCall(id=str(uuid4()), name="run_tests", arguments={}))
                report["baselines"][identifier] = baseline.model_dump(mode="json")
                assert not baseline.success and baseline.error_code.value == "TEST_FAILED"
            submit(client, "java-fulfillment-multi")
            experience = submit(client, "python-fulfillment-multi")
            # V3 owns batch/provenance fields. Exercise legacy single-result merging with
            # two independently executed repairs instead of manufacturing component records.
            repeated = submit(client, "python-fulfillment-multi")
            components = [experience, repeated]
            body = {"experience_ids": [entry.id for entry in components]}
            preview = client.post("/api/experiences/merge-preview", json=body)
            assert preview.status_code == 200 and preview.json()["can_merge"]
            response = client.post("/api/experiences/merge", json=body)
            assert response.status_code == 200, response.text
            merged = response.json()
            assert client.post("/api/experiences/merge", json=body).json()["id"] == merged["id"]
            report["merge"] = {"source_task_ids": [entry.source_task_id for entry in components], "independently_generated_sources": True,
                               "source_ids": body["experience_ids"], "result": merged, "idempotent": True}
            if not live:
                submit(client, "python-fulfillment-multi")
                delivered = report["tasks"][-1]["model_reference_ids"]
                assert merged["id"] in delivered and not set(body["experience_ids"]) & set(delivered)
                report["merge"]["delivered_to_followup_task"] = True
            other_repo = app.state.experiences.retrieve_experiences(language="python", repository_id="unrelated-repository", error_types=[], limit=5)
            assert all(item.scope == "global" for item in other_repo)
            report["repository_isolation"] = True
            report["originals_unchanged"] = all(inventory(settings.projects[key]) == value for key, value in source_hashes.items())
            assert report["originals_unchanged"]
            report["passed"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    folder = args.output_dir or ROOT / ".cache/acceptance-v2" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    result = run(args.live, folder)
    print(f"PASS: {result['mode']} acceptance; report: {folder / 'report.json'}")
