"""Record two real-model, real-Maven demonstrations for offline interactive replay.

Without --live only list the planned work. Never silently retry a task or replace
a failed recording with a scripted success. Raw evidence stays under .cache/demo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
from backend.api.main import create_app
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.shared import TaskStatusResponse, TaskStepsResponse, ToolCall
from backend.tools.workspace import inventory

CATALOG = ROOT / "benchmarks/demo/catalog.json"


def write_json(path: Path, value) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def source_files(path: Path) -> dict[str, str]:
    return {file.relative_to(path).as_posix(): file.read_text(encoding="utf-8")
            for file in sorted((path / "src").rglob("*.java"))}


def record(*, projects: list[str] | None = None) -> Path:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    selected = [case for case in catalog if not projects or case["id"] in projects]
    if not selected or (projects and set(projects) - {case["id"] for case in catalog}):
        raise ValueError("Unknown demo project")
    base = Settings.from_env()
    if not all((base.llm_base_url, base.llm_api_key, base.llm_model)):
        raise ValueError("Configure the local model settings before --live")
    directory = ROOT / ".cache/demo" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True)
    settings = replace(base, workspace_root=directory / "workspaces", database_path=directory / "experience.db",
                       use_experience=False)
    report = {"schema_version": 1, "mode": "recorded-live-model", "created_at": datetime.now(timezone.utc).isoformat(),
              "model": settings.llm_model, "use_experience": False,
              "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "git_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                  ["backend/agent/core.py", "backend/tools/service.py", "backend/config.py", "scripts/demo.py"]},
              "purpose": "Intermediate-case demonstration; not part of the earlier 24-task comparison.",
              "cases": [], "complete": False}
    destination = directory / "report.json"
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            for case in selected:
                project = settings.projects[case["id"]]
                original = inventory(project)
                before = source_files(project)
                baseline_workspace = app.state.tools.manager.create_workspace(project, str(uuid4()))
                baseline = app.state.tools.execute_tool(baseline_workspace, ToolCall(id=str(uuid4()), name="run_tests"))
                entry = {**case, "initial_files": before, "baseline": baseline.model_dump(mode="json"),
                         "status": None, "steps": [], "final_files": {}, "experience": None, "audit": {}, "verified": False}
                report["cases"].append(entry)
                write_json(destination, report)
                counts = baseline.metadata
                if baseline.error_code != "TEST_FAILED" or counts.get("tests") != case["expected_test_count"] or counts.get("skipped") != 0:
                    raise RuntimeError("Baseline did not reproduce the complete expected test suite")
                print(f"BASELINE {case['id']}: {counts['tests']} tests; failures={counts['failures']}, errors={counts['errors']}", flush=True)
                response = client.post("/api/tasks", json={"source_project": case["id"], "task_description": case["task_description"], "max_steps": 25})
                response.raise_for_status()
                task_id = response.json()["task_id"]
                entry["task_id"] = task_id
                seen = 0
                deadline = time.monotonic() + settings.task_timeout + settings.llm_timeout + 30
                while True:
                    status = TaskStatusResponse.model_validate(client.get(f"/api/tasks/{task_id}").json())
                    steps = TaskStepsResponse.model_validate(client.get(f"/api/tasks/{task_id}/steps").json())
                    entry["status"] = status.model_dump(mode="json")
                    entry["steps"] = [step.model_dump(mode="json") for step in steps.steps]
                    for step in steps.steps[seen:]:
                        print(f"STEP {case['id']} {step.sequence}: {step.call.name} success={step.result.success}", flush=True)
                    seen = len(steps.steps)
                    write_json(destination, report)
                    if status.status not in {"pending", "running"}:
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError("Demo polling exceeded its bounded waiting period")
                    time.sleep(0.2)
                # Fetch completed steps again after observing the terminal state.
                entry["steps"] = client.get(f"/api/tasks/{task_id}/steps").json()["steps"]
                entry["audit"] = app.state.tasks.get_audit(task_id)
                workspace = settings.workspace_root / task_id
                entry["final_files"] = source_files(workspace)
                entry["original_unchanged"] = original == inventory(project)
                entry["tests_unchanged"] = all(entry["final_files"].get(name) == content for name, content in before.items() if name.startswith("src/test/"))
                result = status.result
                final = next((r for r in reversed(result.tool_results) if r.metadata.get("final_verification")), None) if result else None
                entry["verified"] = bool(result and result.status == "succeeded" and result.tests_passed is True and final and
                    final.success and final.metadata.get("tests") == case["expected_test_count"] and
                    all(final.metadata.get(key) == 0 for key in ("failures", "errors", "skipped")) and
                    entry["original_unchanged"] and entry["tests_unchanged"])
                if result and result.experience_id:
                    saved = ExperienceStore(settings.database_path).list_experiences(limit=100)
                    entry["experience"] = next((e.model_dump(mode="json") for e in saved if e.id == result.experience_id), None)
                write_json(destination, report)
                print(f"RESULT {case['id']}: {status.status}; verified={entry['verified']}; changed={result.changed_files if result else []}", flush=True)
            report["complete"] = True
    except Exception as exc:
        report["recording_error_type"] = type(exc).__name__
        raise
    finally:
        write_json(destination, report)
        print(f"REPORT {destination}", flush=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--project", action="append")
    args = parser.parse_args()
    if not args.live:
        cases = json.loads(CATALOG.read_text(encoding="utf-8"))
        for case in cases:
            print(f"{case['id']}: {case['expected_test_count']} tests; {case['title']}")
        print("Use --live to call the configured real model, Maven and SQLite.")
    else:
        result_path = record(projects=args.project)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        raise SystemExit(0 if all(case["verified"] for case in result["cases"]) else 1)
