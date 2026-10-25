"""Record a real follow-up task using the existing stock-repair experience."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from backend.agent.core import ERROR_CLASS
from backend.agent.llm import ChatClient
from backend.api.main import create_app
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.shared import ToolCall
from backend.tools.workspace import inventory
from scripts.demo import source_files, write_json


def content_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class RecordingClient(ChatClient):
    """Observe the actual user-content argument; delegate all inference to ChatClient."""
    def __init__(self, settings):
        super().__init__(settings)
        self.recorded_inputs = []

    def complete(self, messages, *, tools=None):
        entry = None
        start = len(self.audit)
        if tools:
            entry = {"user_content": copy.deepcopy(json.loads(messages[1]["content"])), "successful": False}
            self.recorded_inputs.append(entry)
        try:
            answer = super().complete(messages, tools=tools)
            if entry is not None:
                entry["successful"] = True
            return answer
        finally:
            if entry is not None:
                entry["audit_events"] = copy.deepcopy(self.audit[start:])


def run(source_report: Path) -> Path:
    source_report = source_report.resolve()
    source = json.loads(source_report.read_text(encoding="utf-8"))
    source_case = next(c for c in source["cases"] if c["id"] == "java-stock-transfer")
    if source["mode"] != "recorded-live-model" or not source["complete"] or not source_case["verified"]:
        raise ValueError("A completed stock-repair recording is required")
    source_db = source_report.parent / "experience.db"
    original_db_hash = hashlib.sha256(source_db.read_bytes()).hexdigest()
    directory = ROOT / ".cache/reuse-demo" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True)
    db_path = directory / "experience.db"
    with sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True) as old, sqlite3.connect(db_path) as new:
        old.backup(new)
    settings = replace(Settings.from_env(), workspace_root=directory / "workspaces", database_path=db_path, use_experience=True)
    store = ExperienceStore(db_path)
    before = [e.model_dump(mode="json") for e in store.list_experiences(limit=100)]
    saved_source = next(e for e in before if e["id"] == source_case["experience"]["id"])
    if saved_source != source_case["experience"]:
        raise ValueError("Source experience differs from its recording")
    case = json.loads((ROOT / "benchmarks/reuse/catalog.json").read_text(encoding="utf-8"))
    error_type = ERROR_CLASS.search(case["task_description"]).group()
    query = {"query": "", "tags": ["java"], "error_type": error_type, "limit": 5}
    matches = [e.model_dump(mode="json") for e in store.search_experiences(**query)]
    project = settings.projects[case["id"]]
    originals = inventory(project)
    model = RecordingClient(settings)
    app = create_app(settings, llm_factory=lambda: model)
    report = {"schema_version": 1, "mode": "recorded-live-reuse", "created_at": datetime.now(timezone.utc).isoformat(),
              "model": settings.llm_model, "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "git_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
              "source_recording_sha256": hashlib.sha256(source_report.read_bytes()).hexdigest(),
              "source_database_sha256": original_db_hash, "source_experience": saved_source,
              "experiences_before": before, "query": query, "matched_experiences": matches,
              "target": {**case, "initial_files": source_files(project)}, "complete": False, "verified": False}
    destination = directory / "report.json"
    try:
        with TestClient(app) as client:
            baseline_workspace = app.state.tools.manager.create_workspace(project, str(uuid4()))
            baseline = app.state.tools.execute_tool(baseline_workspace, ToolCall(id=str(uuid4()), name="run_tests"))
            report["target"]["baseline"] = baseline.model_dump(mode="json")
            if baseline.error_code != "TEST_FAILED" or baseline.metadata.get("tests") != 12 or baseline.metadata.get("skipped") != 0:
                raise ValueError("Expected twelve baseline tests")
            print("BASELINE", {k: baseline.metadata.get(k) for k in ["tests", "failures", "errors", "skipped"]}, flush=True)
            write_json(destination, report)
            response = client.post("/api/tasks", json={"source_project": case["id"], "task_description": case["task_description"], "max_steps": 25})
            response.raise_for_status()
            task_id = response.json()["task_id"]
            report["target"]["task_id"] = task_id
            deadline = time.monotonic() + settings.task_timeout + settings.llm_timeout + 30
            seen = 0
            while True:
                status = client.get(f"/api/tasks/{task_id}").json()
                steps = client.get(f"/api/tasks/{task_id}/steps").json()["steps"]
                report["target"].update(status=status, steps=steps)
                for step in steps[seen:]:
                    print("STEP", step["sequence"], step["call"]["name"], step["result"]["success"], flush=True)
                seen = len(steps)
                write_json(destination, report)
                if status["status"] not in {"running", "pending"}:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("Task polling deadline exceeded")
                time.sleep(0.2)
            target = report["target"]
            target["steps"] = client.get(f"/api/tasks/{task_id}/steps").json()["steps"]
            target["audit"] = app.state.tasks.get_audit(task_id)
            target["final_files"] = source_files(settings.workspace_root / task_id)
            target["original_unchanged"] = originals == inventory(project)
            target["tests_unchanged"] = all(target["final_files"].get(k) == v for k,v in target["initial_files"].items() if k.startswith("src/test/"))
            report["captured_decisions"] = model.recorded_inputs
            report["experiences_after"] = [e.model_dump(mode="json") for e in store.list_experiences(limit=100)]
            target["experience"] = next((e for e in report["experiences_after"] if e["source_task_id"] == task_id), None)
            final = next((s["result"] for s in reversed(target["steps"]) if s["result"]["metadata"].get("final_verification")), None)
            expected_hash = content_hash(saved_source)
            delivered = any(d["successful"] and saved_source in d["user_content"]["reference_experiences"] and
                any(e["success"] and any(i == saved_source["id"] and h == expected_hash for i,h in zip(e["request_experience_ids"], e["request_experience_hashes"])) for e in d["audit_events"])
                for d in model.recorded_inputs)
            report["delivery_verified"] = delivered
            report["source_database_unchanged"] = original_db_hash == hashlib.sha256(source_db.read_bytes()).hexdigest()
            report["verified"] = bool(status["status"] == "succeeded" and final and final["success"] and final["metadata"].get("tests") == 12 and
                all(final["metadata"].get(k) == 0 for k in ["failures", "errors", "skipped"]) and delivered and target["original_unchanged"] and target["tests_unchanged"] and target["experience"] and report["source_database_unchanged"])
            report["complete"] = True
            print("RESULT", status["status"], "delivery_verified", delivered, "verified", report["verified"], flush=True)
    except Exception as exc:
        report["recording_error_type"] = type(exc).__name__
        raise
    finally:
        write_json(destination, report)
        print("REPORT", destination, flush=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print("One real-model follow-up task; use --live to run it against a copy of the source experience database.")
    else:
        result = run(args.source_report)
        raise SystemExit(0 if json.loads(result.read_text(encoding="utf-8"))["verified"] else 1)
