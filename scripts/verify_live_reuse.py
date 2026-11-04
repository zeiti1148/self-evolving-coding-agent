"""Partial live publication/reuse verification from a historical successful task.

Default: read-only preflight. --live backs up the entire source SQLite database,
checks incompatible merge rejection, publishes a reviewed text-input rule, and
runs exactly one new real-model target task. It does not rerun the source repair,
does not replace acceptance_v3.py, and makes no efficiency comparison.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.agent.extraction import verification_passed
from backend.agent.llm import ChatClient
from backend.api.main import create_app
from backend.config import Settings
from backend.shared import Experience, PromotionDraft, ToolCall, ToolResult
from backend.tools.workspace import inventory
from scripts.acceptance_v3 import RecordingClient, inspect_requests
from scripts.backup_database import backup


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def read_source(path: Path, task_id: str, settings: Settings):
    """Read historical evidence without opening the migrating application store."""
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as database:
        database.row_factory = sqlite3.Row
        row = database.execute("SELECT request_json,status_json,audit_json FROM api_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("Source task not found")
        request, status, audit = (json.loads(row[key]) for key in ("request_json", "status_json", "audit_json"))
        result = status.get("result") or {}
        if status["status"] != "succeeded" or not result.get("repair_succeeded") or not result.get("tests_passed"):
            raise ValueError("Source task must have an independently verified successful repair")
        if not result.get("experience_saved") or len(result.get("experience_ids", [])) != 3:
            raise ValueError("This verification requires the historical task's three saved experiences")
        project = settings.project(request["source_project"])
        calls = [ToolCall.model_validate(item) for item in result["steps"]]
        results = [ToolResult.model_validate(item) for item in result["tool_results"]]
        if (not calls or len(calls) != len(results)
                or any(call.id != response.call_id for call, response in zip(calls, results))):
            raise ValueError("Historical tool calls and results do not match")
        final = results[-1]
        if (calls[-1].name != "run_tests" or not final.metadata.get("final_verification")
                or not verification_passed(final, project.language)
                or final.metadata.get("tests") != project.expected_tests):
            raise ValueError("Historical independent final verification does not match the registered source")
        experiences = []
        for identifier in result["experience_ids"]:
            row = database.execute("SELECT * FROM experiences WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise ValueError("A source experience is missing")
            record = {key: row[key] for key in ("id", "task_summary", "error_type", "root_cause", "solution",
                                               "verification", "source_task_id", "created_at")}
            record.update(tags=json.loads(row["tags"]), **json.loads(row["details"]))
            experience = Experience.model_validate(record)
            if (experience.scope != "project" or experience.reuse_status != "active"
                    or experience.repository_id != project.repository_id or experience.language != project.language
                    or experience.source_task_id != task_id or not experience.evidence
                    or any(e.source_task_id != task_id or e.tool_call_id != final.call_id
                           or e.tests != final.metadata["tests"] for e in experience.evidence)):
                raise ValueError("Source experience provenance or final evidence is inconsistent")
            experiences.append(experience.model_dump(mode="json"))
        steps = [json.loads(row[0]) for row in database.execute(
            "SELECT step_json FROM api_task_steps WHERE task_id=? ORDER BY sequence", (task_id,))]
        agents = [json.loads(row[0]) for row in database.execute(
            "SELECT event_json FROM api_agent_events WHERE task_id=? ORDER BY sequence", (task_id,))]
    text_sources = [item for item in experiences if set(item["required_capabilities"]) == {"text-input"}]
    if len(text_sources) != 1:
        raise ValueError("Expected exactly one historical experience requiring only text-input")
    workspace = path.parent / "workspaces" / task_id
    if not workspace.is_dir():
        raise ValueError("Historical workspace must remain available for source evidence")
    snapshot = {"request": request, "status": status, "audit": audit,
                "steps": steps, "agents": agents, "experiences": experiences}
    return {"snapshot": snapshot, "snapshot_sha256": digest(snapshot), "text_source": text_sources[0],
            "source_project": project.id, "workspace": str(workspace), "final_call_id": final.call_id,
            "tests": final.metadata["tests"]}


def reviewed_text_draft(source):
    """Explicitly reviewed publication draft, independent of source defaults."""
    return PromotionDraft(task_summary="按目标业务合同处理缺失与空白文本",
        language=source["language"], error_type=source["error_type"],
        root_cause="在字符串操作前未检查缺失输入，且未单独核对空白文本的处理要求。",
        solution="先检查输入是否缺失，再根据目标项目原始测试决定是否去除首尾空白及如何处理空白结果。"
                 "回退值和拒绝策略必须来自目标原始测试；保留正常输入的既有行为，并运行目标的全部原始测试。",
        tags=[source["language"], "文本输入", "输入校验"],
        applicability=["目标使用与来源相同的编程语言，并登记了 text-input 能力。",
                       "仅参考缺失或空白文本的检查顺序；回退值、空白处理和拒绝策略均以目标原始测试为准。"],
        issues=[{"error_type": source["issues"][0]["error_type"],
                 "root_cause": "缺失文本可能在字符串操作时触发异常，空白文本也需要独立核对业务要求。",
                 "solution": "在操作前检查缺失输入，并按目标原始测试分别处理空白文本和正常文本。", "files": []}],
        required_capabilities=["text-input"])


def configured(settings):
    return bool(settings.llm_api_key and settings.llm_api_key != "replace_with_your_api_key"
        and settings.llm_model and settings.llm_model != "example-model"
        and settings.llm_base_url and "api.example.com" not in settings.llm_base_url)


def run(source_db, source_task_id, directory, *, live=False, task_timeout=None, max_steps=24):
    source_db, directory = Path(source_db).resolve(), Path(directory).resolve()
    source_task_id = str(UUID(source_task_id))
    if directory.exists():
        raise ValueError("Output directory must not exist; previous evidence will not be overwritten")
    settings = Settings.from_env()
    protected_roots = [source_db.parent / "workspaces", *settings.projects.values()]
    if any(directory.is_relative_to(Path(root).resolve()) for root in protected_roots):
        raise ValueError("Output directory must be outside source workspaces and registered project fixtures")
    if task_timeout is not None:
        if not math.isfinite(task_timeout) or task_timeout <= 0:
            raise ValueError("Task timeout must be positive and finite")
        settings = replace(settings, task_timeout=task_timeout)
    if not 1 <= max_steps <= 100:
        raise ValueError("max_steps must be between 1 and 100")
    source = read_source(source_db, source_task_id, settings)
    target = settings.project("java-booking-transfer")
    if target.language != source["text_source"]["language"] or "text-input" not in target.capabilities or target.expected_tests != 12:
        raise ValueError("Target registration must be same-language Java booking with text-input and 12 expected tests")
    draft = reviewed_text_draft(source["text_source"]).model_dump(mode="json")
    preflight = {"status": "preflight_passed", "live_request_made": False,
        "full_v3_acceptance": False, "source_repair_rerun": False, "efficiency_comparison": False,
        "source_task_id": source_task_id, "historical_source_tests": source["tests"],
        "historical_source_experience_ids": [item["id"] for item in source["snapshot"]["experiences"]],
        "source_workspace_preserved_at": source["workspace"], "target_project": target.id,
        "expected_target_tests": target.expected_tests, "reviewed_publication_draft": draft,
        "model_configured": configured(settings), "request_timeout_seconds": settings.llm_timeout,
        "task_timeout_seconds": settings.task_timeout, "output_directory": str(directory)}
    if not live:
        return preflight
    if not configured(settings):
        raise ValueError("Live model configuration is missing or contains placeholders")
    directory.mkdir(parents=True, exist_ok=False)
    report_path = directory / "report.json"
    report = {**preflight, "status": "running", "run_id": str(uuid4()),
        "purpose": "partial_live_publication_and_cross_repository_reuse",
        "source_database": str(source_db), "source_snapshot_sha256": source["snapshot_sha256"],
        "source_evidence": source["snapshot"], "merge_checks": [], "checks": {},
        "publication_draft_review": "assistant-reviewed from historical evidence; not a recorded user UI action",
        "created_at": datetime.now(timezone.utc).isoformat(), "new_target_task_count": 0,
        "code_version": {"commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, check=True).stdout.strip(),
            "sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in ("scripts/verify_live_reuse.py", "backend/agent/extraction.py", "backend/agent/llm.py")}}}
    def save():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    def http(client, method, url, **kwargs):
        response = getattr(client, method)(url, **kwargs)
        if not response.is_success:
            raise AssertionError(f"{method.upper()} {url} returned HTTP {response.status_code}")
        return response.json()
    requests = []
    settings = replace(settings, database_path=directory / "experiences.db", workspace_root=directory / "workspaces")
    def factory():
        return RecordingClient(ChatClient(settings), requests)
    def forbid_model():
        raise AssertionError("Read-only evidence inspection must not invoke a model")
    def visible_files(client, task_id):
        changes = http(client, "get", f"/api/tasks/{task_id}/code-changes")
        tests = http(client, "get", f"/api/tasks/{task_id}/test-sources")
        assert changes["files"] and all(item["available"] for item in changes["files"]), "Task code-change evidence is not fully readable"
        assert tests["files"], "Task has no readable original test sources"
        return {"code_changes": changes, "test_sources": tests,
            "code_diffs": [http(client, "get", f"/api/tasks/{task_id}/code-diff", params={"path": item["path"]})
                           for item in changes["files"]],
            "test_source_files": [http(client, "get", f"/api/tasks/{task_id}/test-source", params={"path": item["path"]})
                                  for item in tests["files"]]}
    target_id, target_status, target_agents, target_steps, published = None, None, None, None, None
    target_source_inventory = inventory(target.source)
    source_workspace_inventory = inventory(Path(source["workspace"]))
    original_workspaces = source_db.parent / "workspaces"
    original_workspaces_inventory = inventory(original_workspaces)
    def capture_latest_target():
        if not target_id or not settings.database_path.is_file():
            return
        with sqlite3.connect(settings.database_path.as_uri() + "?mode=ro", uri=True) as database:
            row = database.execute("SELECT status_json,audit_json FROM api_tasks WHERE id=?", (target_id,)).fetchone()
            if row:
                report["target_status"], report["target_audit"] = (json.loads(value) for value in row)
                report["target_agents"] = [json.loads(item[0]) for item in database.execute(
                    "SELECT event_json FROM api_agent_events WHERE task_id=? ORDER BY sequence", (target_id,))]
                report["target_steps"] = {"task_id": target_id, "steps": [json.loads(item[0]) for item in database.execute(
                    "SELECT step_json FROM api_task_steps WHERE task_id=? ORDER BY sequence", (target_id,))]}
    save()
    try:
        report["backup"] = backup(source_db, settings.database_path)
        save()
        # The baseline API uses the copied DB and only reads the untouched original
        # workspaces. Task ownership manifests retain their original project paths.
        with TestClient(create_app(replace(settings, workspace_root=original_workspaces), llm_factory=forbid_model)) as original_view:
            source_visible = visible_files(original_view, source_task_id)
        shutil.copytree(original_workspaces, settings.workspace_root, symlinks=True)
        assert inventory(settings.workspace_root) == original_workspaces_inventory, "Workspace evidence copy changed files or manifests"
        report["workspaces_backup"] = {"source": str(original_workspaces), "copy": str(settings.workspace_root),
            "source_manifest_paths_preserved": True, "inventory_sha256": digest(original_workspaces_inventory)}
        report["source_visible_files"] = source_visible
        save()
        app = create_app(settings, llm_factory=factory)
        with TestClient(app) as client:
            assert http(client, "get", f"/api/tasks/{source_task_id}") == source["snapshot"]["status"], "Backup changed source task"
            records = [http(client, "get", f"/api/experiences/{item['id']}") for item in source["snapshot"]["experiences"]]
            assert records == source["snapshot"]["experiences"], "Backup changed source experience evidence"
            assert visible_files(client, source_task_id) == source_visible, "Copied source task code/test views differ from original evidence"
            report["checks"]["copied_source_code_and_tests_identical"] = True
            for left, right in itertools.combinations(records, 2):
                body = {"experience_ids": [left["id"], right["id"]]}
                preview = http(client, "post", "/api/experiences/merge-preview", json=body)
                check = {"experience_ids": body["experience_ids"], "preview": preview}
                report["merge_checks"].append(check); save()
                assert preview["can_merge"] is False, "Historical independent conditions unexpectedly became mergeable"
                rejected = client.post("/api/experiences/merge", json=body)
                check["merge_http_status"] = rejected.status_code
                check["rejection"] = rejected.json()
                save()
                assert rejected.status_code == 409, "Incompatible merge was not rejected"
            report["checks"]["all_three_incompatible_pairs_rejected"] = True
            source_id = source["text_source"]["id"]
            preview = http(client, "post", "/api/experiences/promotion-preview", json={"source_id": source_id})
            report["promotion_preview"] = preview; save()
            assert preview["can_publish"], "Verified source cannot be published directly"
            body = {"source_id": source_id, "draft": draft, "supersedes_id": preview["current_global_id"]}
            published = http(client, "post", "/api/experiences/publish-global", json=body)
            report["publication"] = published; save()
            assert http(client, "post", "/api/experiences/publish-global", json=body) == published, "Publication was not idempotent"
            assert published["scope"] == "global" and published["required_capabilities"] == ["text-input"]
            assert http(client, "get", f"/api/experiences/{source_id}") == source["text_source"], "Publication rewrote the source"
            report["checks"].update(publication_idempotent=True, source_project_scope_and_record_preserved=True)
            started = time.monotonic()
            created = http(client, "post", "/api/tasks", json={"source_project": target.id,
                "task_description": target.task_example, "execution_mode": "multi", "max_steps": max_steps})
            target_id = created["task_id"]
            report.update(live_request_made=True, new_target_task_count=1, target_task_id=target_id)
            save()
            polling_deadline = started + settings.task_timeout + 90
            while True:
                target_status = http(client, "get", f"/api/tasks/{target_id}")
                report["target_status"] = target_status
                if target_status["status"] not in {"pending", "running"}:
                    break
                if time.monotonic() >= polling_deadline:
                    save()
                    raise AssertionError("Single target task exceeded its polling deadline; no replacement task was submitted")
                time.sleep(.25)
            target_agents = http(client, "get", f"/api/tasks/{target_id}/agents")
            target_steps = http(client, "get", f"/api/tasks/{target_id}/steps")
            result = target_status.get("result") or {}
            target_experiences = [http(client, "get", f"/api/experiences/{identifier}") for identifier in result.get("experience_ids", [])]
            report.update(target_elapsed_seconds=round(time.monotonic()-started, 3),
                target_audit=app.state.tasks.get_audit(target_id), target_agents=target_agents,
                target_steps=target_steps, target_experiences=target_experiences)
            save()
            injections = inspect_requests(requests, language=target.language,
                repository_id=target.repository_id, capabilities=target.capabilities)
            report["actual_injections"] = injections
            save()
            assert target_status["status"] == "succeeded", f"Target task ended as {target_status['status']}"
            assert result["repair_succeeded"] and result["tests_passed"], "Target repair was not independently verified"
            final = result["tool_results"][-1]
            assert (final["tool_name"] == "run_tests" and final["success"] and final["metadata"].get("final_verification")
                and final["metadata"].get("runner") == "maven" and final["metadata"].get("tests") == 12
                and all(final["metadata"].get(key) == 0 for key in ("failures", "errors", "skipped"))), "Target final verification must be 12 passing Maven tests"
            assert result["experience_saved"] is True and target_experiences, "Target experience extraction/save failed"
            assert all(item["source_task_id"] == target_id and item["evidence"]
                and all(e["source_task_id"] == target_id and e["tool_call_id"] == final["call_id"] and e["tests"] == 12
                        for e in item["evidence"]) for item in target_experiences), "Target experiences lack their own final evidence"
            relevant = [entry for entry in injections if entry["role"] in {"diagnosis", "repair", "review"}]
            assert sum(entry["role"] == "diagnosis" for entry in relevant) >= 2, "Two independent diagnosis requests were not observed"
            assert {entry["role"] for entry in relevant} == {"diagnosis", "repair", "review"}, "A required role request is missing"
            assert all(published["id"] in entry["experience_ids"] for entry in relevant), "A target role did not actually receive the published rule"
            historical_ids = {item["id"] for item in records}
            assert all(not historical_ids.intersection(entry["experience_ids"]) for entry in injections), "Private source experience was injected cross-repository"
            report["target_visible_files"] = visible_files(client, target_id)
            report["checks"].update(target_12_tests_passed=True, target_experiences_saved=True,
                every_target_role_received_published_rule=True, source_private_records_not_injected=True)
            save()
        with TestClient(create_app(settings, llm_factory=forbid_model)) as restarted:
            assert http(restarted, "get", f"/api/tasks/{source_task_id}") == source["snapshot"]["status"]
            assert http(restarted, "get", f"/api/tasks/{target_id}") == target_status
            assert http(restarted, "get", f"/api/tasks/{target_id}/agents") == target_agents
            assert http(restarted, "get", f"/api/tasks/{target_id}/steps") == target_steps
            assert http(restarted, "get", f"/api/experiences/{published['id']}") == published
            for item in [*source["snapshot"]["experiences"], *report["target_experiences"]]:
                assert http(restarted, "get", f"/api/experiences/{item['id']}") == item
            assert visible_files(restarted, source_task_id) == source_visible, "Source code/test views changed after restart"
            assert visible_files(restarted, target_id) == report["target_visible_files"], "Target code/test views changed after restart"
        report["checks"]["restart_preserved_source_target_and_publication"] = True
        report["checks"]["restart_preserved_source_and_target_code_test_views"] = True
        report["status"] = "partial_live_reuse_passed"
    except Exception as exc:
        report.update(status="partial_live_reuse_failed", error_type=type(exc).__name__)
        # Assertions are controlled checks above; arbitrary provider/storage error text is not recorded.
        if isinstance(exc, AssertionError):
            report["failed_check"] = str(exc)
    finally:
        try:
            capture_latest_target()
            report["live_request_made"] = bool(requests)
            if requests and "actual_injections" not in report:
                try:
                    report["actual_injections"] = inspect_requests(requests, language=target.language,
                        repository_id=target.repository_id, capabilities=target.capabilities)
                except AssertionError as exc:
                    report.update(status="partial_live_reuse_failed", injection_validation_error=str(exc))
            report["checks"]["original_source_evidence_unchanged"] = read_source(source_db, source_task_id, settings)["snapshot_sha256"] == source["snapshot_sha256"]
            report["checks"]["original_source_workspace_unchanged"] = inventory(Path(source["workspace"])) == source_workspace_inventory
            report["checks"]["original_workspace_tree_unchanged"] = inventory(original_workspaces) == original_workspaces_inventory
            report["checks"]["original_target_fixture_unchanged"] = inventory(target.source) == target_source_inventory
            if not all(report["checks"][key] for key in ("original_source_evidence_unchanged", "original_source_workspace_unchanged", "original_workspace_tree_unchanged", "original_target_fixture_unchanged")):
                report.update(status="partial_live_reuse_failed", failed_check="Original source evidence or fixture changed")
        except Exception as exc:
            report.update(status="partial_live_reuse_failed", integrity_check_error=type(exc).__name__)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--source-task-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-timeout-seconds", type=float)
    parser.add_argument("--max-steps", type=int, default=24)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    result = run(args.source_db, args.source_task_id, args.output_dir, live=args.live,
                 task_timeout=args.task_timeout_seconds, max_steps=args.max_steps)
    if not args.live:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({"status": result["status"], "full_v3_acceptance": False,
        "target_task_id": result.get("target_task_id"), "new_target_task_count": result["new_target_task_count"],
        "checks": result["checks"], "failed_check": result.get("failed_check"),
        "report": str(args.output_dir.resolve() / "report.json")}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "partial_live_reuse_passed" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"Preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
