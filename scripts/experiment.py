"""Live, isolated experience ablation; no model requests without --live.

Each invocation creates a new directory. It never resumes or retries whole tasks.
all = 3 training tasks + 4 pilot tasks + 24 formal tasks. Formal repetitions
reverse the condition order to reduce a simple execution-order confound.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.core import Agent
from backend.agent.llm import ChatClient
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.experience.store import (
    COLUMNS, ORDER, _matches_keywords, _matches_tags, _same_text,
    decode_row, validate_integer, validation_error,
)
from backend.shared import ApplicationError, TaskStatusResponse, TaskStepsResponse, ToolCall
from backend.tools import ToolService, WorkspaceManager
from backend.tools.workspace import inventory


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    """Replace checkpoints atomically so an interruption preserves valid JSON."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def freeze_database(source: Path, destination: Path) -> str:
    """Take a consistent SQLite snapshot, including an empty training database."""
    if destination.exists() or source.resolve() == destination.resolve():
        raise ValueError("The frozen database must be a new, separate file.")
    ExperienceStore(source).list_experiences(limit=1)
    with closing(sqlite3.connect(source)) as origin, closing(sqlite3.connect(destination)) as target:
        origin.backup(target)
    return file_hash(destination)


class FrozenSearchStore:
    """Search only the frozen training DB; save only to the evaluation output DB.

    The normal store initializes its schema on every connection, so frozen reads
    deliberately use SQLite's read-only mode and the existing search predicates.
    """

    def __init__(self, frozen: Path, output: ExperienceStore):
        self.frozen = frozen.resolve(strict=True)
        self.output = output
        output_path = output.database_path.resolve()
        if self.frozen == output_path or (output_path.exists() and self.frozen.samefile(output_path)):
            raise ValueError("Frozen references and output must use different databases.")

    def save_experience(self, data):
        return self.output.save_experience(data)

    def search_experiences(self, query, tags=None, error_type=None, limit=5):
        validate_integer(limit, "limit", 1)
        if not isinstance(query, str):
            raise validation_error("query must be a string.")
        if tags is not None and (not isinstance(tags, list) or
                                any(not isinstance(tag, str) or not tag.strip() for tag in tags)):
            raise validation_error("tags must be a list of nonblank strings.")
        if error_type is not None and (not isinstance(error_type, str) or not error_type.strip()):
            raise validation_error("error_type must be a nonblank string or None.")
        conditions, parameters = [], []
        if query.strip():
            conditions.append("matches_keywords(?, task_summary, error_type, root_cause, solution, verification)")
            parameters.append(query.casefold())
        if tags:
            conditions.append("matches_tags(tags, ?)")
            parameters.append(json.dumps(sorted({tag.strip().casefold() for tag in tags})))
        if error_type is not None:
            conditions.append("same_text(error_type, ?)")
            parameters.append(error_type.strip().casefold())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        parameters.append(limit)
        with closing(sqlite3.connect(self.frozen.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.create_function("matches_keywords", 6, _matches_keywords, deterministic=True)
            connection.create_function("matches_tags", 2, _matches_tags, deterministic=True)
            connection.create_function("same_text", 2, _same_text, deterministic=True)
            rows = connection.execute(
                f"SELECT {COLUMNS} FROM experiences{where}{ORDER} LIMIT ?", parameters,
            ).fetchall()
            return [decode_row(row) for row in rows]


def load_catalog(path: Path) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Benchmark catalog must be a nonempty list.")
    for case in cases:
        if set(case) != {"id", "path", "task_description", "error_type", "expected_test_count"}:
            raise ValueError("Unexpected catalog fields.")
        if not all(isinstance(case[key], str) and case[key].strip()
                   for key in ("id", "path", "task_description", "error_type")):
            raise ValueError("Invalid catalog text.")
        source = (ROOT / case["path"]).resolve()
        if not source.is_relative_to(ROOT / "benchmarks") or not (source / "pom.xml").is_file():
            raise ValueError("Catalog source must be a project under benchmarks.")
        if type(case["expected_test_count"]) is not int or case["expected_test_count"] < 1:
            raise ValueError("Invalid expected test count.")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Catalog contains duplicate case IDs.")
    return cases


def build_plan(training: list[dict], evaluation: list[dict], stage: str) -> list[dict]:
    if stage not in {"all", "pilot", "formal"}:
        raise ValueError("stage must be all, pilot or formal.")
    plan = []

    def add(phase, cases, repetition, modes):
        for case in cases:
            for enabled in modes:
                condition = "with-experience" if enabled else "without-experience"
                plan.append({"trial_id": f"{phase}-{case['id']}-{condition}-r{repetition}",
                             "stage": phase, "project": case["id"], "use_experience": enabled,
                             "repetition": repetition})

    add("training", training, 1, [False])
    if stage in {"all", "pilot"}:
        add("pilot", evaluation[:2], 1, [False, True])
    if stage in {"all", "formal"}:
        add("formal", evaluation, 1, [False, True])
        add("formal", evaluation, 2, [True, False])
    return plan


def request_metrics(audit: dict) -> dict:
    events = audit.get("model_requests", [])
    tokens = {key: 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    coverage = {key: 0 for key in tokens}
    for event in events:
        for key in tokens:
            value = event.get("usage", {}).get(key)
            if type(value) is int and value >= 0:
                tokens[key] += value
                coverage[key] += 1
    return {"model_requests": len(events), "successful_model_requests": sum(bool(e.get("success")) for e in events),
            "reported_tokens": tokens, "token_reporting_requests": coverage,
            "decision_rounds": audit.get("decision_rounds"), "tool_calls": audit.get("tool_calls")}


def summarize(records: list[dict]) -> dict:
    groups = {}
    for row in records:
        key = row["stage"] + ("/with-experience" if row["use_experience"] else "/without-experience")
        group = groups.setdefault(key, {"scheduled": 0, "completed": 0, "passed": 0, "failed": 0,
                                        "outcomes": {}, "elapsed_ms": 0, "model_requests": 0,
                                        "reported_total_tokens": 0, "token_reporting_requests": 0,
                                        "valid_comparison_tasks": 0})
        group["scheduled"] += 1
        outcome = row.get("outcome", "running")
        group["outcomes"][outcome] = group["outcomes"].get(outcome, 0) + 1
        if outcome not in {"running", "pending"}:
            group["completed"] += 1
            group["passed" if outcome == "passed" else "failed"] += 1
        metrics = row.get("metrics", {})
        group["elapsed_ms"] += row.get("elapsed_ms", 0)
        group["model_requests"] += metrics.get("model_requests", 0)
        group["reported_total_tokens"] += metrics.get("reported_tokens", {}).get("total_tokens", 0)
        group["token_reporting_requests"] += metrics.get("token_reporting_requests", {}).get("total_tokens", 0)
        group["valid_comparison_tasks"] += row.get("comparison_valid") is True
    for group in groups.values():
        group["success_rate"] = group["passed"] / group["completed"] if group["completed"] else None
    return groups


def run_baseline(case: dict, tools: ToolService, original: dict, source: Path) -> dict:
    started = time.monotonic()
    workspace = tools.manager.create_workspace(source, str(uuid4()))
    result = tools.execute_tool(workspace, ToolCall(id=str(uuid4()), name="run_tests"))
    unchanged = original == inventory(source)
    valid = (result.error_code == "TEST_FAILED" and result.metadata.get("tests") == case["expected_test_count"]
             and result.metadata.get("skipped") == 0 and unchanged)
    return {"workspace": str(workspace), "result": result.model_dump(mode="json"),
            "valid": valid, "original_unchanged": unchanged,
            "elapsed_ms": round((time.monotonic() - started) * 1000)}


def run_trial(spec: dict, case: dict, settings: Settings, store, original: dict, checkpoint) -> dict:
    # Importing the API module constructs its default app, so defer it until an
    # explicitly enabled run (the dry-run path never loads local model settings).
    from fastapi.testclient import TestClient
    from backend.api.main import create_app

    record = {**spec, "outcome": "running", "task_id": None, "status": None, "steps": None, "audit": {}}
    started = time.monotonic()
    expected_ids = []
    if spec["use_experience"]:
        expected_ids = [entry.id for entry in store.search_experiences(
            "", tags=["java"], error_type=case["error_type"], limit=5)]
    record["expected_reference_ids"] = expected_ids

    def make_agent(tools, _api_store):
        return Agent(ChatClient(settings), tools.execute_tool, store,
                     use_experience=spec["use_experience"], timeout_seconds=settings.task_timeout)

    app = create_app(settings, agent_factory=make_agent)
    try:
        with TestClient(app) as client:
            response = client.post("/api/tasks", json={"source_project": case["id"],
                                   "task_description": case["task_description"], "max_steps": 20})
            if response.status_code != 202:
                raise RuntimeError(f"Task API returned HTTP {response.status_code}.")
            task_id = response.json()["task_id"]
            record.update(task_id=task_id, workspace=str(settings.workspace_root / task_id))
            deadline = time.monotonic() + settings.task_timeout + settings.llm_timeout + 30
            previous = None
            while True:
                status_response = client.get(f"/api/tasks/{task_id}")
                steps_response = client.get(f"/api/tasks/{task_id}/steps")
                status_response.raise_for_status()
                steps_response.raise_for_status()
                status = TaskStatusResponse.model_validate(status_response.json())
                steps = TaskStepsResponse.model_validate(steps_response.json())
                record.update(status=status.model_dump(mode="json"), steps=steps.model_dump(mode="json"))
                progress = (status.status, len(steps.steps))
                if progress != previous:
                    record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
                    checkpoint(record)
                    previous = progress
                if status.status not in {"pending", "running"}:
                    break
                if time.monotonic() >= deadline:
                    record["outcome"] = "polling_timeout"
                    break
                time.sleep(0.15)
        # Lifespan shutdown joins the bounded Agent worker before collecting audit.
        record["audit"] = app.state.tasks.get_audit(task_id)
        record["status"] = app.state.tasks.get(task_id).model_dump(mode="json")
        record["steps"] = app.state.tasks.get_steps(task_id).model_dump(mode="json")
        result = record["status"].get("result") or {}
        verification = next((tool for tool in reversed(result.get("tool_results", []))
                             if tool.get("metadata", {}).get("final_verification")), None)
        record["final_verification"] = verification
        record["changed_files"] = result.get("changed_files", [])
        record["experience_saved"] = result.get("experience_saved")
        record["experience_id"] = result.get("experience_id")
        record["experience_save_error"] = result.get("experience_save_error")
        metadata = (verification or {}).get("metadata", {})
        record["test_counts"] = {key: metadata.get(key) for key in ("tests", "failures", "errors", "skipped")}
        verified = (verification is not None and verification["success"] and
                    metadata.get("tests") == case["expected_test_count"] and
                    metadata.get("failures") == metadata.get("errors") == metadata.get("skipped") == 0)
        if record["outcome"] != "polling_timeout":
            terminal = record["status"]["status"]
            record["outcome"] = ("passed" if verified and result.get("tests_passed") is True and
                                 result.get("repair_succeeded") is True and terminal == "succeeded" else
                                 "invalid_verification" if terminal == "succeeded" else terminal)
        requested = {reference for event in record["audit"].get("model_requests", [])
                     if event.get("phase") == "decision" for reference in event.get("request_experience_ids", [])}
        received = {reference for event in record["audit"].get("model_requests", [])
                    if event.get("phase") == "decision" and event.get("success")
                    for reference in event.get("request_experience_ids", [])}
        retrieved = set(record["audit"].get("retrieved_experience_ids", []))
        record["reference_isolation_valid"] = requested <= set(expected_ids) and retrieved <= set(expected_ids)
        record["reference_received"] = bool(expected_ids) and set(expected_ids) <= received
        record["comparison_valid"] = (record["audit"].get("experience_enabled") is spec["use_experience"] and
                                      record["reference_isolation_valid"] and
                                      (record["reference_received"] if spec["use_experience"] else not requested and not retrieved))
    except Exception as exc:
        record["outcome"] = "execution_error"
        record["error"] = exc.message if isinstance(exc, ApplicationError) else type(exc).__name__
        if record["task_id"]:
            record["audit"] = app.state.tasks.get_audit(record["task_id"])
            record["status"] = app.state.tasks.get(record["task_id"]).model_dump(mode="json")
            record["steps"] = app.state.tasks.get_steps(record["task_id"]).model_dump(mode="json")
    finally:
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        record["metrics"] = request_metrics(record["audit"])
        record["original_unchanged"] = inventory(settings.projects[case["id"]]) == original
        if not record["original_unchanged"]:
            record["outcome"] = "original_changed"
            record["comparison_valid"] = False
    return record


def run_experiment(*, live=False, stage="all", output_root: Path | None = None,
                   settings: Settings | None = None) -> dict:
    training = load_catalog(ROOT / "benchmarks/training/catalog.json")
    evaluation = load_catalog(ROOT / "benchmarks/catalog.json")
    if len(training) != 3 or len(evaluation) != 6:
        raise ValueError("The fixed experiment requires 3 training and 6 evaluation projects.")
    cases = {case["id"]: case for case in training + evaluation}
    paths = [(ROOT / case["path"]).resolve() for case in training + evaluation]
    if len(cases) != 9 or len(set(paths)) != 9:
        raise ValueError("Training and evaluation must use disjoint IDs and project directories.")
    plan = build_plan(training, evaluation, stage)
    counts = dict(Counter(row["stage"] for row in plan))
    print(f"Plan: training={counts.get('training', 0)}, pilot={counts.get('pilot', 0)}, "
          f"formal={counts.get('formal', 0)}; total={len(plan)} independent tasks; no whole-task retries.", flush=True)
    if not live:
        print("Dry run only. Pass --live to use the configured real model and Maven.", flush=True)
        return {"mode": "dry-run", "planned_tasks": len(plan), "counts": counts, "plan": plan}

    settings = settings or Settings.from_env()
    if not all(math.isfinite(value) and value > 0 for value in (settings.task_timeout, settings.llm_timeout)):
        raise ValueError("Task and model timeouts must be finite positive seconds.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = ((output_root or ROOT / ".cache/experiments") / f"{stamp}-{uuid4().hex[:8]}").resolve()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "tasks").mkdir()
    settings = replace(settings, workspace_root=directory / "workspaces",
                       projects={key: ROOT / case["path"] for key, case in cases.items()})
    training_path, frozen_path, output_path = (directory / name for name in
                                             ("training.db", "frozen-training.db", "evaluation-output.db"))
    originals = {key: inventory(source) for key, source in settings.projects.items()}
    report = {"mode": "live-model", "created_at": stamp, "directory": str(directory), "stage": stage,
              "requested_model": settings.llm_model, "task_timeout_seconds": settings.task_timeout,
              "planned_tasks": len(plan), "counts": counts, "plan": plan, "tasks": [], "baselines": {},
              "original_hashes": originals, "training_database": str(training_path),
              "frozen_database": str(frozen_path), "output_database": str(output_path),
              "run_status": "running", "completed": False}
    destination = directory / "report.json"

    def persist():
        report["summary"] = summarize(report["tasks"])
        write_json(destination, report)

    persist()
    print(f"Report: {destination}", flush=True)
    tools = ToolService(WorkspaceManager(settings.workspace_root, settings.projects), settings.maven_home)
    training_store = ExperienceStore(training_path)
    frozen_store = None
    try:
        for index, spec in enumerate(plan, 1):
            case = cases[spec["project"]]
            if spec["stage"] != "training" and frozen_store is None:
                report["frozen_sha256"] = freeze_database(training_path, frozen_path)
                frozen_store = FrozenSearchStore(frozen_path, ExperienceStore(output_path))
                seeds = frozen_store.search_experiences("", limit=100)
                report["training_experiences"] = [seed.model_dump(mode="json") for seed in seeds]
                report["training_coverage"] = {kind: any(seed.error_type == kind for seed in seeds)
                                               for kind in {case["error_type"] for case in evaluation}}
                persist()
            record = {**spec, "outcome": "running"}
            report["tasks"].append(record)
            task_path = directory / "tasks" / (spec["trial_id"] + ".json")

            def checkpoint(value):
                report["tasks"][-1] = dict(value)
                write_json(task_path, value)
                persist()

            checkpoint(record)
            started = time.monotonic()
            try:
                project = spec["project"]
                if project not in report["baselines"]:
                    try:
                        report["baselines"][project] = run_baseline(case, tools, originals[project], settings.projects[project])
                    except Exception as exc:
                        report["baselines"][project] = {
                            "valid": False, "error": exc.message if isinstance(exc, ApplicationError) else type(exc).__name__,
                            "original_unchanged": inventory(settings.projects[project]) == originals[project],
                        }
                    persist()
                if not report["baselines"][project]["valid"]:
                    record.update(outcome="baseline_invalid", error="Original fixture did not produce the expected test failure.")
                else:
                    store = training_store if spec["stage"] == "training" else frozen_store
                    task_settings = replace(settings, database_path=training_path if spec["stage"] == "training" else output_path)
                    record = run_trial(spec, case, task_settings, store, originals[project], checkpoint)
                record["original_unchanged"] = inventory(settings.projects[project]) == originals[project]
                if not record["original_unchanged"]:
                    record.update(outcome="original_changed", comparison_valid=False)
                if frozen_store is not None:
                    record["frozen_unchanged"] = file_hash(frozen_path) == report["frozen_sha256"]
                    if not record["frozen_unchanged"]:
                        record.update(outcome="frozen_database_changed", comparison_valid=False)
            except Exception as exc:
                record.update(outcome="execution_error", error=exc.message if isinstance(exc, ApplicationError) else type(exc).__name__)
            record.setdefault("elapsed_ms", round((time.monotonic() - started) * 1000))
            checkpoint(record)
            print(f"[{index}/{len(plan)}] {spec['trial_id']}: {record['outcome']}; "
                  f"tests={record.get('test_counts', {}).get('tests', '?')}; "
                  f"requests={record.get('metrics', {}).get('model_requests', 0)}; "
                  f"elapsed_ms={record['elapsed_ms']}", flush=True)
            if record.get("original_unchanged") is False or record.get("frozen_unchanged") is False:
                raise RuntimeError("Source or frozen reference integrity changed; remaining tasks were not started.")
        report["completed"] = True
        report["run_status"] = "completed"
    except BaseException as exc:
        report["run_status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "aborted"
        report["error"] = exc.message if isinstance(exc, ApplicationError) else type(exc).__name__
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["originals_unchanged"] = all(originals[key] == inventory(path) for key, path in settings.projects.items())
        report["all_tasks_passed"] = report["completed"] and all(row.get("outcome") == "passed" for row in report["tasks"])
        persist()
        print(f"Report: {destination}", flush=True)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly enable real model requests.")
    parser.add_argument("--stage", choices=("all", "pilot", "formal"), default="all")
    parser.add_argument("--output-root", type=Path, help="Parent folder; each invocation gets a new child directory.")
    args = parser.parse_args(argv)
    report = run_experiment(live=args.live, stage=args.stage, output_root=args.output_root)
    return 0 if report["mode"] == "dry-run" or report.get("all_tasks_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
