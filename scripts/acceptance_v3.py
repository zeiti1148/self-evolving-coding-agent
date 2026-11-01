"""V3 acceptance: scripted models (default) or --live; real FastAPI, SQLite, tools and tests.

Every invocation owns a new output directory. Never modifies the source benchmarks or app.db.
Reports retrieved IDs, actual request payload hashes and independent test outcomes separately.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import sqlite3
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
from backend.shared import ExperienceCreate
from backend.tools.workspace import inventory
from tests.scripted_multi_agent import ScriptedRoleFactory


def inspect_requests(requests, *, language, repository_id, capabilities=()):
    """Check the actual messages sent to the model, independent of retrieval's own audit."""
    inspected = []
    for messages in requests:
        for message in messages:
            if message.get("role") != "user":
                continue
            try:
                payload = json.loads(message.get("content", ""))
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict) or "reference_experiences" not in payload:
                continue
            references = payload["reference_experiences"]
            for entry in references:
                assert entry["language"] == language, "Cross-language injection"
                assert entry["reuse_status"] == "active", "Unreviewed/candidate injection"
                assert set(entry.get("required_capabilities", [])).issubset(capabilities), "Capability mismatch"
                if entry["scope"] == "project":
                    assert entry["repository_id"] == repository_id, "Cross-repository project injection"
                else:
                    assert entry["scope"] == "global"
                    assert not set(entry) & {"repository_id", "source_task_id", "source_revision", "verification", "evidence", "derived_from", "merged_from", "supersedes_id", "batch_id"}, "Private global payload"
                    assert all("files" not in issue for issue in entry.get("issues", [])), "Private source paths"
            inspected.append({"role": payload.get("agent_role", "single"),
                "experience_ids": [entry["id"] for entry in references],
                "content_sha256": [hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()).hexdigest() for entry in references],
                "references": references})
    return inspected


class RecordingClient:
    def __init__(self, delegate, requests):
        self.delegate, self.requests = delegate, requests

    def complete(self, messages, *, tools=None):
        self.requests.append(copy.deepcopy(messages))
        return self.delegate.complete(messages, tools=tools)

    def set_deadline(self, deadline):
        if hasattr(self.delegate, "set_deadline"):
            self.delegate.set_deadline(deadline)

    def close(self):
        if hasattr(self.delegate, "close"):
            self.delegate.close()

    @property
    def audit(self):
        return getattr(self.delegate, "audit", [])


def run(live: bool, directory: Path) -> dict:
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    settings = replace(Settings.from_env(), workspace_root=directory / "workspaces", database_path=directory / "experiences.db")
    report = {"run_id": str(uuid4()), "mode": "live" if live else "scripted", "model": settings.llm_model if live else "ScriptedRoleFactory",
              "created_at": datetime.now(timezone.utc).isoformat(), "tasks": [], "merges": [], "publications": [],
              "checks": {}, "status": "running", "passed": False,
              "interpretation": "Scripted responses validate orchestration only. No controlled efficiency comparison was performed."}

    def save():
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if live and not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        report.update(status="not_run", passed=None, reason="Missing configured LLM_API_BASE_URL, LLM_API_KEY or LLM_MODEL; no model calls made.")
        save()
        return report
    report["code_version"] = {
        "base_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip(),
        "dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()),
        "source_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sorted((ROOT / "backend").rglob("*.py")) if "tests" not in path.parts},
    }
    # A target with a deliberately missing capability exercises filtering inside actual role requests.
    target = settings.project("python-booking-transfer")
    restricted = target.model_copy(update={"id": "python-booking-restricted", "repository_id": "v3-python-restricted", "capabilities": []})
    settings = replace(settings, project_definitions={**settings.project_definitions, restricted.id: restricted},
                       projects={**settings.projects, restricted.id: restricted.source})
    original = {key: inventory(settings.projects[key]) for key in ("java-checkout-complex", "python-checkout-complex", "java-booking-transfer", "python-booking-transfer")}
    requests = []
    active = {"factory": ScriptedRoleFactory()}

    def factory():
        return RecordingClient(ChatClient(settings) if live else active["factory"](), requests)

    app = create_app(settings, llm_factory=factory)

    def http(client, method, url, **kwargs):
        response = getattr(client, method)(url, **kwargs)
        response.raise_for_status()
        return response.json()

    def submit(client, identifier, *, label, mode="multi", max_steps=24, fault=None, expect="succeeded", save_ok=True):
        active["factory"] = ScriptedRoleFactory(partial_first="checkout" in identifier, fail_role=fault,
                                                repository_cases={restricted.repository_id: target.id})
        start_index, started = len(requests), time.monotonic()
        project = settings.project(identifier)
        created = http(client, "post", "/api/tasks", json={"source_project": identifier, "task_description": project.task_example,
                       "execution_mode": mode, "max_steps": max_steps})
        deadline = started + settings.task_timeout + 90
        while True:
            status = http(client, "get", f"/api/tasks/{created['task_id']}")
            if status["status"] not in {"pending", "running"}:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("Acceptance polling deadline exceeded")
            time.sleep(.1)
        task_id, result = created["task_id"], status.get("result") or {}
        audit = app.state.tasks.get_audit(task_id)
        injections = inspect_requests(requests[start_index:], language=project.language, repository_id=project.repository_id, capabilities=project.capabilities)
        events = http(client, "get", f"/api/tasks/{task_id}/agents")
        task = {"label": label, "project": identifier, "task_id": task_id, "status": status,
                "elapsed_seconds": round(time.monotonic()-started, 3), "audit": audit, "role_events": events,
                "retrieved_ids": audit.get("retrieved_experience_ids", []), "actual_injections": injections,
                "actual_injected_ids": sorted({identifier for entry in injections for identifier in entry["experience_ids"]})}
        report["tasks"].append(task); save()
        assert status["status"] == expect, f"{label}: expected {expect}, got {status['status']}"
        assert audit["decision_rounds"] <= max_steps
        if expect == "succeeded":
            assert result["repair_succeeded"] and result["tests_passed"]
            assert result["experience_saved"] is save_ok
            final = result["tool_results"][-1]
            assert final["success"] and final["metadata"]["final_verification"]
            assert final["metadata"]["tests"] == project.expected_tests
            assert all(final["metadata"][key] == 0 for key in ("errors", "failures", "skipped"))
            records = [http(client, "get", f"/api/experiences/{identifier}") for identifier in result["experience_ids"]]
            if save_ok:
                assert 3 <= len(records) <= 5 and all(len(row["issues"]) >= 2 for row in records[:3])
                assert len({row["task_summary"] for row in records}) == len(records)
                assert all(row["source_task_id"] == task_id and row["evidence"][0]["tool_call_id"] == final["call_id"] for row in records)
            else:
                assert not records and result["experience_save_error"]
            task["experiences"] = records
            tests = [step for step in result["tool_results"] if step["tool_name"] == "run_tests"]
            task["test_progression"] = [{"passed": step["success"], **step["metadata"]} for step in tests]
            if not live and "checkout" in identifier and mode == "multi":
                assert len(tests) >= 3 and not tests[0]["success"] and not tests[1]["success"]
            if mode == "multi":
                assert {"diagnosis", "repair", "review", "coordinator", "extractor"}.issubset({e["role"] for e in events})
                assert len({e["agent_id"] for e in events if e["role"] == "diagnosis"}) == 2
        else:
            assert not result.get("experience_ids")
        save()
        print(f"PASS {label}: status={status['status']}, injected={len(task['actual_injected_ids'])}, saved={len(result.get('experience_ids', []))}", flush=True)
        return task

    try:
        with TestClient(app) as client:
            sources = {}
            for language in ("java", "python"):
                source = submit(client, f"{language}-checkout-complex", label=f"{language}-source")
                sources[language] = source
                compatible = None
                for pair in itertools.combinations(source["experiences"], 2):
                    body = {"experience_ids": [row["id"] for row in pair]}
                    preview = http(client, "post", "/api/experiences/merge-preview", json=body)
                    if preview["can_merge"]:
                        compatible = body
                        break
                assert compatible is not None, "No compatible naturally generated pair; do not fabricate merge inputs"
                merged = http(client, "post", "/api/experiences/merge", json=compatible)
                assert http(client, "post", "/api/experiences/merge", json=compatible)["id"] == merged["id"]
                graph = http(client, "get", f"/api/experiences/{merged['id']}/provenance")
                assert set(compatible["experience_ids"]).issubset({node["id"] for node in graph["nodes"]})
                source["merge"] = {"inputs": compatible["experience_ids"], "result": merged}
                report["merges"].append(source["merge"])
                again = submit(client, f"{language}-checkout-complex", label=f"{language}-after-merge")
                assert merged["id"] in again["actual_injected_ids"]
                assert not set(compatible["experience_ids"]) & set(again["actual_injected_ids"])
            incompatible = {"experience_ids": [sources[lang]["merge"]["result"]["id"] for lang in ("java", "python")]}
            assert not http(client, "post", "/api/experiences/merge-preview", json=incompatible)["can_merge"]
            assert client.post("/api/experiences/merge", json=incompatible).status_code == 409
            for language in ("java", "python"):
                before = submit(client, f"{language}-booking-transfer", label=f"{language}-before-publication")
                assert not before["actual_injected_ids"], "Unpublished source injected into target"
            for language, source in sources.items():
                source_id = source["merge"]["result"]["id"]
                preview = http(client, "post", "/api/experiences/promotion-preview", json={"source_id": source_id})
                assert preview["can_publish"]
                draft = preview["draft"]
                draft.update(task_summary="Validate input preconditions while preserving the target business contract",
                    solution="Validate missing and blank text, integer syntax and range, and collection/index preconditions separately. "
                             "Choose fallback values and numeric rejection rules from the target's original tests; never copy source defaults.",
                    required_capabilities=["text-input", "integer-input", "indexed-selection"])
                body = {"source_id": source_id, "draft": draft, "supersedes_id": preview["current_global_id"]}
                published = http(client, "post", "/api/experiences/publish-global", json=body)
                assert http(client, "post", "/api/experiences/publish-global", json=body)["id"] == published["id"]
                assert http(client, "get", f"/api/experiences/{source_id}")["scope"] == "project"
                report["publications"].append(published)
            for language in ("java", "python"):
                after = submit(client, f"{language}-booking-transfer", label=f"{language}-after-publication")
                global_id = next(row["id"] for row in report["publications"] if row["language"] == language)
                assert global_id in after["actual_injected_ids"]
                for role in ("diagnosis", "repair", "review"):
                    assert any(global_id in entry["experience_ids"] for entry in after["actual_injections"] if entry["role"] == role)
            report["checks"].update(merge_idempotent=True, merge_conflict_rejected=True, natural_multi_experience_batches=True,
                source_isolation_in_every_request=True, same_language_only=True, target_business_contracts=True)
            if not live:
                store = app.state.experiences
                # Candidate and old globals are deliberately untrusted; these are negative fixtures, not repair outputs.
                for schema in (2, 3):
                    store.save_experience(ExperienceCreate(schema_version=schema, language="python", task_summary="Unpublished negative fixture",
                        root_cause="Untrusted", solution="Never inject", verification="No execution", error_type="ValueError"))
                restricted_task = submit(client, restricted.id, label="capability-mismatch")
                assert not restricted_task["actual_injected_ids"]
                submit(client, "python-booking-transfer", label="single-compatibility", mode="single")
                submit(client, "python-booking-transfer", label="shared-budget-exhaustion", max_steps=2, expect="stopped")
                submit(client, "python-booking-transfer", label="role-exception", fault="review", expect="failed")
                with sqlite3.connect(settings.database_path) as db:
                    db.execute("CREATE TRIGGER fail_batch BEFORE INSERT ON experience_batch_items WHEN NEW.ordinal=1 BEGIN SELECT RAISE(ABORT, 'acceptance failure'); END")
                try:
                    failed_save = submit(client, "python-booking-transfer", label="atomic-save-failure", save_ok=False)
                finally:
                    with sqlite3.connect(settings.database_path) as db:
                        db.execute("DROP TRIGGER fail_batch")
                assert not http(client, "get", "/api/experiences", params={"source_task_id": failed_save["task_id"]})
                with sqlite3.connect(settings.database_path) as db:
                    assert db.execute("SELECT COUNT(*) FROM experience_batches WHERE source_task_id=?", (failed_save["task_id"],)).fetchone()[0] == 0
                report["checks"].update(capability_rejected=True, candidate_and_legacy_not_injected=True,
                    single_compatibility=True, shared_budget_stopped=True, role_failure_explicit=True, batch_failure_atomic=True)
            saved_events = {task["task_id"]: task["role_events"] for task in report["tasks"]}
        restarted = create_app(settings, llm_factory=factory)
        with TestClient(restarted) as client:
            for task in report["tasks"]:
                assert http(client, "get", f"/api/tasks/{task['task_id']}") == task["status"]
                assert http(client, "get", f"/api/tasks/{task['task_id']}/agents") == saved_events[task["task_id"]]
                for identifier in task["status"].get("result", {}).get("experience_ids", []):
                    assert http(client, "get", f"/api/experiences/{identifier}")["id"] == identifier
            for row in report["publications"]:
                assert http(client, "get", f"/api/experiences/{row['id']}/provenance")["nodes"]
        report["checks"]["restart_history"] = True
        assert all(inventory(settings.projects[key]) == value for key, value in original.items())
        report["checks"]["original_fixtures_unchanged"] = True
        report.update(status="passed", passed=True)
    except Exception as exc:
        report.update(status="failed", passed=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    directory = args.output_dir or ROOT / ".cache/acceptance-v3" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    result = run(args.live, directory)
    print(f"{result['status']}: {directory / 'report.json'}")
    sys.exit(0 if result["passed"] else 2 if result["status"] == "not_run" else 1)
