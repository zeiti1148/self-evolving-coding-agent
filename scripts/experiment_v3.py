"""V3 three-condition experiments; default is a credential-free, side-effect-free plan."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.core import Agent
from backend.agent.llm import ChatClient
from backend.agent.orchestrator import MultiAgentCoordinator
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.experience.policy import to_model_reference
from backend.projects import load_catalog, validate_project_language
from backend.shared import ToolCall
from backend.tools import ToolService, WorkspaceManager
from backend.tools.workspace import inventory
from scripts.experiment import FrozenSearchStore, code_provenance, file_hash, write_json
from scripts.v3_evaluation import build_plan, paired_comparisons, request_metrics, snapshot_database, summarize
from scripts.v3_references import RequestRecorder, inspect_references, read_references

DEFAULT_CATALOG = ROOT / "benchmarks/evaluation-v3/catalog.json"


def load_cases(catalog=DEFAULT_CATALOG):
    projects = load_catalog(Path(catalog), ROOT)
    if not projects or len({project.repository_id for project in projects.values()}) != len(projects):
        raise ValueError("Evaluation projects must have distinct repository identities.")
    if len({project.source.resolve() for project in projects.values()}) != len(projects):
        raise ValueError("Evaluation projects must use distinct source directories.")
    for project in projects.values():
        if not project.source.resolve().is_relative_to((ROOT / "benchmarks").resolve()) or project.expected_tests is None:
            raise ValueError("Evaluation projects need fixed test counts and sources under benchmarks.")
        validate_project_language(project)
    return projects


def run_trial(spec, project, settings, frozen, allowed_ids, original, checkpoint, *, llm_factory=None, max_steps=20):
    from fastapi.testclient import TestClient
    from backend.api.main import create_app

    started = time.monotonic()
    row = dict(spec, outcome="running", task_id=None, audit={}, comparison_valid=True)
    entries, _ = read_references(frozen)
    representation = "compact" if spec["condition"] == "compact" else "full"
    expected = {identifier: to_model_reference(entries[identifier], representation) for identifier in allowed_ids}
    recorder = RequestRecorder(llm_factory or (lambda: ChatClient(settings)), expected, spec["condition"])
    store = FrozenSearchStore(frozen, ExperienceStore(settings.database_path))
    options = dict(use_experience=spec["condition"] != "none", timeout_seconds=settings.task_timeout,
                   experience_format=representation, retrieval_strategy=spec["retrieval_strategy"])
    app = create_app(settings,
        agent_factory=lambda tools, _: Agent(recorder(), tools.execute_tool, store, **options),
        multi_agent_factory=lambda tools, _: MultiAgentCoordinator(recorder, tools.execute_tool, store, **options))
    try:
        with TestClient(app) as client:
            response = client.post("/api/tasks", json=dict(source_project=project.id, task_description=project.task_example,
                                                           execution_mode=spec["execution_mode"], max_steps=max_steps))
            response.raise_for_status()
            task_id = response.json()["task_id"]
            row.update(task_id=task_id, workspace=str(settings.workspace_root / task_id))
            deadline, previous = started + settings.task_timeout + settings.llm_timeout + 30, None
            while True:
                response = client.get(f"/api/tasks/{task_id}")
                response.raise_for_status()
                status = response.json()
                steps = client.get(f"/api/tasks/{task_id}/steps")
                steps.raise_for_status()
                row.update(status=status, steps=steps.json(), elapsed_ms=round((time.monotonic() - started) * 1000))
                progress = status["status"], len(row["steps"]["steps"])
                if progress != previous:
                    checkpoint(row)
                    previous = progress
                if status["status"] not in {"pending", "running"}:
                    break
                if time.monotonic() >= deadline:
                    row["outcome"] = "polling_timeout"
                    break
                time.sleep(0.1)
        row["audit"] = app.state.tasks.get_audit(task_id)
        row["status"] = app.state.tasks.get(task_id).model_dump(mode="json")
        result = row["status"].get("result") or {}
        final = next((item for item in reversed(result.get("tool_results", [])) if item.get("metadata", {}).get("final_verification")), None)
        row["final_verification"] = final
        counts = (final or {}).get("metadata", {})
        verified = bool(final and final.get("success") and counts.get("tests") == project.expected_tests
                        and counts.get("runner") == ("maven" if project.language == "java" else "pytest")
                        and all(counts.get(key) == 0 for key in ("failures", "errors", "skipped")))
        if row["outcome"] != "polling_timeout":
            terminal = row["status"]["status"]
            row["outcome"] = ("passed" if terminal == "succeeded" and verified and result.get("repair_succeeded") is True
                              and result.get("tests_passed") is True else "invalid_verification" if terminal == "succeeded" else terminal)
        row["experience_saved"] = result.get("experience_saved")
        row["changed_files"] = result.get("changed_files", [])
    except Exception as exc:
        row.update(outcome="execution_error", error=type(exc).__name__)
    finally:
        row["actual_requests"] = recorder.events
        row["reference_received"] = any(event["success"] and event["experience_ids"] for event in recorder.events)
        row["metrics"] = request_metrics(row["audit"])
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        row["original_unchanged"] = inventory(project.source) == original
        row["comparison_valid"] = row["original_unchanged"] and all(event["payload_valid"] for event in recorder.events)
        if not row["original_unchanged"]:
            row["outcome"] = "original_changed"
    return row


def baseline(project, settings, original):
    manager = WorkspaceManager(settings.workspace_root, {project.id: project.source})
    tools = ToolService(manager, settings.maven_home, python_executable=settings.python_executable)
    workspace = manager.create_workspace(project.source, str(uuid4()))
    result = tools.execute_tool(workspace, ToolCall(id=str(uuid4()), name="run_tests"))
    counts = result.metadata
    valid = (result.error_code == "TEST_FAILED" and counts.get("tests") == project.expected_tests
             and counts.get("skipped") == 0 and original == inventory(project.source))
    return dict(valid=valid, result=result.model_dump(mode="json"))


def write_report(directory, report):
    report["summary"] = summarize(report["tasks"])
    report["paired_comparisons"] = paired_comparisons(report["tasks"])
    write_json(directory / "report.json", report)
    lines = ["# V3 对照实验", "", f"状态：{report['status']}；模式：{report['mode']}。", "",
             "成功由独立最终测试判定；失败计入分母。用量缺失不代表零成本。配对成本仅比较双方成功且用量完整的任务。", "",
             "| 模式/检索/经验 | 完成/计划 | 成功 | 成功率 | 完整用量任务 | 平均 token |", "| --- | --- | --- | --- | --- | --- |"]
    for key, group in report["summary"].items():
        rate = f"{group['success_rate']:.1%}" if group["success_rate"] is not None else "—"
        tokens = str(round(group["mean_total_tokens"])) if group["mean_total_tokens"] is not None else "未知"
        lines.append(f"| {key} | {group['completed']}/{group['scheduled']} | {group['passed']} | {rate} | {group['token_complete_trials']} | {tokens} |")
    if report.get("reason"):
        lines.extend(["", f"未运行原因：{report['reason']}"])
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_experiment(*, live=False, reference_database=None, case_ids=None, repetitions=3, modes=("single",),
                   retrievals=("baseline",), output_root=None, settings=None, llm_factory=None, catalog=DEFAULT_CATALOG,
                   max_steps=20):
    projects = load_cases(catalog)
    selected = list(projects) if case_ids is None else list(case_ids)
    if any(identifier not in projects for identifier in selected):
        raise ValueError("Unknown evaluation case.")
    if type(max_steps) is not int or not 1 <= max_steps <= 100:
        raise ValueError("max_steps must be 1..100.")
    plan = build_plan(selected, repetitions, modes, retrievals)
    report = dict(schema_version=1, mode="dry-run", status="planned", planned_tasks=len(plan), plan=plan, tasks=[],
                  max_steps=max_steps, completed=False)
    if not live:
        return report
    settings = settings or Settings.from_env()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    output_base = Path(output_root or ROOT / ".cache/experiments-v3").resolve()
    if output_base.is_relative_to((ROOT / "benchmarks").resolve()):
        raise ValueError("Experiment outputs must remain outside benchmark sources.")
    directory = output_base / f"{stamp}-{uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    report.update(mode="scripted" if llm_factory else "live-model", status="running", directory=str(directory),
        created_at=datetime.now(timezone.utc).isoformat(), requested_model=settings.llm_model if not llm_factory else "test-factory",
        task_timeout_seconds=settings.task_timeout, llm_timeout_seconds=settings.llm_timeout,
        interpretation="Scripted runs validate infrastructure only." if llm_factory else "Small pilots are exploratory; report all failures and usage coverage.")

    def not_run(reason):
        report.update(status="not_run", reason=reason)
        write_report(directory, report)
        return report

    if not llm_factory and not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return not_run("Missing configured model service; no requests made.")
    if reference_database is None:
        return not_run("Pass --experience-db with independently trained and reviewed references.")
    frozen = directory / "references.db"
    try:
        report["frozen_sha256"] = snapshot_database(Path(reference_database), frozen)
        allowed = inspect_references(frozen, [projects[identifier] for identifier in selected],
                                     evaluation_projects=projects.values())
    except (ValueError, OSError, sqlite3.Error) as exc:
        # ValueError messages are program-owned preflight reasons; never print database/provider exceptions.
        return not_run(str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    report.update(reference_database=str(Path(reference_database).resolve()), frozen_database=str(frozen), allowed_references=allowed)
    report["code_version"] = code_provenance()
    report["code_version"]["source_sha256"].update({path.relative_to(ROOT).as_posix(): file_hash(path)
        for folder in (ROOT / "backend/agent", ROOT / "backend/experience") for path in folder.glob("*.py")})
    report["code_version"]["source_sha256"].update({f"scripts/{name}": file_hash(ROOT / "scripts" / name)
        for name in ("experiment_v3.py", "v3_evaluation.py", "v3_references.py")})
    originals = {identifier: inventory(projects[identifier].source) for identifier in selected}
    report["original_hashes"], report["baselines"] = originals, {}
    report["tasks"] = [dict(spec, outcome="pending") for spec in plan]
    write_report(directory, report)
    for identifier in selected:
        try:
            report["baselines"][identifier] = baseline(projects[identifier], replace(settings, workspace_root=directory / "baselines"), originals[identifier])
        except Exception as exc:
            report["baselines"][identifier] = dict(valid=False, error=type(exc).__name__)
        write_report(directory, report)
    for index, spec in enumerate(plan):
        project = projects[spec["project"]]
        # Logical IDs stay in the report; compact directories leave room for
        # Maven's nested output paths on Windows.
        trial_dir = directory / "trials" / f"{index + 1:04d}"
        trial_dir.mkdir(parents=True)

        def checkpoint(row):
            row["trial_directory"] = str(trial_dir)
            report["tasks"][index] = dict(row)
            write_json(trial_dir / "task.json", row)
            write_report(directory, report)

        if not report["baselines"][project.id]["valid"]:
            row = dict(spec, outcome="baseline_invalid", comparison_valid=False)
        elif file_hash(frozen) != report["frozen_sha256"]:
            row = dict(spec, outcome="reference_changed", comparison_valid=False)
        else:
            trial_settings = replace(settings, workspace_root=trial_dir / "workspaces", database_path=trial_dir / "output.db",
                projects={project.id: project.source}, project_definitions={project.id: project})
            checkpoint(dict(spec, outcome="running"))
            try:
                row = run_trial(spec, project, trial_settings, frozen, allowed[project.id], originals[project.id], checkpoint,
                                llm_factory=llm_factory, max_steps=max_steps)
            except Exception as exc:
                row = dict(spec, outcome="execution_error", error=type(exc).__name__, comparison_valid=False)
        checkpoint(row)
        print(f"[{index + 1}/{len(plan)}] {spec['trial_id']}: {row['outcome']}", flush=True)
    report["frozen_unchanged"] = file_hash(frozen) == report["frozen_sha256"]
    report["originals_unchanged"] = all(inventory(projects[identifier].source) == originals[identifier] for identifier in selected)
    report.update(status="completed", completed=True)
    if not report["frozen_unchanged"] or not report["originals_unchanged"]:
        report["status"] = "invalid"
        for row in report["tasks"]:
            row["comparison_valid"] = False
    write_report(directory, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--experience-db", type=Path)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--modes", nargs="+", choices=("single", "multi"), default=["single"])
    parser.add_argument("--retrievals", nargs="+", choices=("baseline", "contextual", "adaptive"), default=["baseline"])
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args(argv)
    report = run_experiment(live=args.live, reference_database=args.experience_db, case_ids=args.cases,
        repetitions=args.repetitions, modes=args.modes, retrievals=args.retrievals, max_steps=args.max_steps,
        output_root=args.output_root, catalog=args.catalog)
    print(json.dumps({key: report[key] for key in ("mode", "status", "planned_tasks", "directory", "reason") if key in report}, ensure_ascii=False))
    return 0 if report["status"] == "planned" or (report["completed"] and report["status"] == "completed"
        and all(row.get("comparison_valid") is True for row in report["tasks"])) else 1


if __name__ == "__main__":
    raise SystemExit(main())
