"""Experiment isolation and bookkeeping tests: no model service or Maven calls."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.experience import ExperienceStore
from backend.shared import AgentResult, ExperienceCreate, TaskStep, ToolCall, ToolResult
from scripts import experiment


def experience_data(label="training", error_type="NullPointerException"):
    return ExperienceCreate(task_summary=label, error_type=error_type,
                            root_cause="Missing boundary handling.", solution="Validate the boundary.",
                            verification="Verified against original tests.", tags=["java"], source_task_id=str(uuid4()))


def test_frozen_search_never_reads_evaluation_feedback_or_later_training(tmp_path):
    training = ExperienceStore(tmp_path / "training.db")
    seed = training.save_experience(experience_data())
    frozen_path = tmp_path / "frozen.db"
    digest = experiment.freeze_database(training.database_path, frozen_path)
    output = ExperienceStore(tmp_path / "output.db")
    isolated = experiment.FrozenSearchStore(frozen_path, output)
    feedback = isolated.save_experience(experience_data("evaluation answer"))
    training.save_experience(experience_data("late training answer"))

    found = isolated.search_experiences("boundary", tags=["JAVA"], error_type="nullpointerexception", limit=5)
    assert [entry.id for entry in found] == [seed.id]
    assert [entry.id for entry in output.list_experiences()] == [feedback.id]
    assert isolated.search_experiences("evaluation") == []
    assert isolated.search_experiences("", error_type="NumberFormatException") == []
    assert experiment.file_hash(frozen_path) == digest
    with pytest.raises(ValueError, match="different databases"):
        experiment.FrozenSearchStore(frozen_path, ExperienceStore(frozen_path))
    with pytest.raises(ValueError, match="new, separate"):
        experiment.freeze_database(training.database_path, frozen_path)


def test_plan_is_fixed_and_counterbalances_formal_conditions():
    training = [{"id": f"train-{index}"} for index in range(3)]
    evaluation = [{"id": f"eval-{index}"} for index in range(6)]
    plan = experiment.build_plan(training, evaluation, "all")
    assert len(plan) == len({row["trial_id"] for row in plan}) == 31
    assert [row["use_experience"] for row in plan[:3]] == [False] * 3
    pilot = [row for row in plan if row["stage"] == "pilot"]
    assert len(pilot) == 4 and {row["project"] for row in pilot} == {"eval-0", "eval-1"}
    formal = [row for row in plan if row["stage"] == "formal"]
    for case in evaluation:
        assert [(row["repetition"], row["use_experience"]) for row in formal if row["project"] == case["id"]] == [
            (1, False), (1, True), (2, True), (2, False)]
    assert len(experiment.build_plan(training, evaluation, "pilot")) == 7
    assert len(experiment.build_plan(training, evaluation, "formal")) == 27


def test_dry_run_does_not_load_credentials_create_files_or_call_models(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Dry run must not load settings or call a model.")
    monkeypatch.setattr(Settings, "from_env", forbidden)
    monkeypatch.setattr(experiment, "ChatClient", forbidden)
    root = tmp_path / "runs"
    report = experiment.run_experiment(output_root=root)
    assert report["mode"] == "dry-run" and report["planned_tasks"] == 31
    assert not root.exists()


def test_failed_and_stopped_tasks_stay_in_statistics_and_missing_usage_is_explicit():
    metrics = experiment.request_metrics({"model_requests": [
        {"success": True, "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}},
        {"success": False, "error_type": "ReadTimeout"},
    ]})
    assert metrics["model_requests"] == 2 and metrics["successful_model_requests"] == 1
    assert metrics["reported_tokens"]["total_tokens"] == 7
    assert metrics["token_reporting_requests"]["total_tokens"] == 1
    rows = [{"stage": "formal", "use_experience": False, "outcome": outcome,
             "metrics": metrics, "elapsed_ms": 100} for outcome in
            ("passed", "failed", "stopped", "execution_error", "baseline_invalid", "running")]
    summary = experiment.summarize(rows)["formal/without-experience"]
    assert summary["scheduled"] == 6 and summary["completed"] == 5
    assert summary["passed"] == 1 and summary["failed"] == 4 and summary["success_rate"] == 0.2
    assert summary["outcomes"]["stopped"] == 1


@pytest.mark.parametrize("terminal,tests,expected", [("succeeded", 3, "passed"),
                                                     ("succeeded", 0, "invalid_verification"),
                                                     ("stopped", 3, "stopped")])
def test_trial_uses_api_callback_and_requires_terminal_final_verification(
        monkeypatch, tmp_path, terminal, tests, expected):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pom.xml").write_text("<project />", encoding="utf-8")
    settings = replace(Settings(), workspace_root=tmp_path / "workspaces", database_path=tmp_path / "output.db",
                       projects={"sample": source}, task_timeout=2)

    class FakeAgent:
        def __init__(self, _llm, execute, store, *, use_experience, timeout_seconds):
            assert not use_experience and timeout_seconds == 2
            self.last_run_audit = {"experience_enabled": False, "retrieved_experience_ids": [],
                                   "model_requests": [{"phase": "decision", "success": True, "request_experience_ids": []}]}

        def run(self, request, *, on_step):
            call = ToolCall(id=str(uuid4()), name="run_tests")
            result = ToolResult(call_id=call.id, tool_name="run_tests", success=True,
                                metadata={"final_verification": True, "tests": tests, "failures": 0, "errors": 0, "skipped": 0})
            on_step(TaskStep(sequence=1, call=call, result=result))
            return AgentResult(task_id=request.task_id, status=terminal, summary="Fake verification",
                               steps=[call], tool_results=[result], repair_succeeded=True, tests_passed=True,
                               experience_saved=False if terminal == "succeeded" else None,
                               experience_save_error="Test deliberately omits extraction." if terminal == "succeeded" else None)

    monkeypatch.setattr(experiment, "Agent", FakeAgent)
    monkeypatch.setattr(experiment, "ChatClient", lambda _: object())
    snapshots = []
    spec = {"trial_id": "formal-sample-without-experience-r1", "stage": "formal", "project": "sample",
            "use_experience": False, "repetition": 1}
    row = experiment.run_trial(spec, {"id": "sample", "task_description": "Fix boundary", "expected_test_count": 3},
                               settings, None, experiment.inventory(source), lambda record: snapshots.append(dict(record)))
    assert row["outcome"] == expected
    assert row["task_id"] and Path(row["workspace"]).is_dir()
    assert len(row["steps"]["steps"]) == 1 and row["metrics"]["model_requests"] == 1
    assert row["original_unchanged"] and row["comparison_valid"]
    assert snapshots


def test_all_stage_writes_incremental_failure_report_and_never_retrieves_target_answers(monkeypatch, tmp_path):
    calls = []
    checkpoints_seen = []
    seeds = []

    def fake_baseline(case, tools, original, source):
        return {"valid": True, "original_unchanged": True,
                "result": {"error_code": "TEST_FAILED", "metadata": {"tests": case["expected_test_count"]}}}

    def fake_trial(spec, case, settings, store, original, checkpoint):
        calls.append(spec["trial_id"])
        report_path = settings.workspace_root.parent / "report.json"
        checkpoints_seen.append(json.loads(report_path.read_text(encoding="utf-8"))["tasks"][-1]["outcome"])
        if spec["stage"] == "training":
            assert spec["use_experience"] is False
            seeds.append(store.save_experience(experience_data(spec["project"], case["error_type"])).id)
        else:
            assert isinstance(store, experiment.FrozenSearchStore)
            assert {entry.id for entry in store.search_experiences("", limit=20)} == set(seeds)
            store.save_experience(experience_data("target answer", case["error_type"]))
        if len(calls) == 6:
            raise RuntimeError("Synthetic execution failure; it must not trigger a retry.")
        row = {**spec, "outcome": "failed" if len(calls) == 5 else "passed", "elapsed_ms": 1,
               "original_unchanged": True, "comparison_valid": True}
        checkpoint(row)
        return row

    monkeypatch.setattr(experiment, "run_baseline", fake_baseline)
    monkeypatch.setattr(experiment, "run_trial", fake_trial)
    monkeypatch.setattr(experiment, "ChatClient", lambda _: pytest.fail("No real model is allowed in this test."))
    report = experiment.run_experiment(live=True, settings=Settings(), output_root=tmp_path / "runs")
    assert report["completed"] and not report["all_tasks_passed"]
    assert len(calls) == len(set(calls)) == 31 and checkpoints_seen == ["running"] * 31
    assert len(report["baselines"]) == 9 and report["originals_unchanged"]
    assert len(report["training_experiences"]) == 3 and all(report["training_coverage"].values())
    assert len(ExperienceStore(Path(report["output_database"])).list_experiences(limit=50)) == 28
    assert experiment.file_hash(Path(report["frozen_database"])) == report["frozen_sha256"]
    directory = Path(report["directory"])
    assert len(list((directory / "tasks").glob("*.json"))) == 31
    saved = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert saved["tasks"][4]["outcome"] == "failed"
    assert saved["tasks"][5]["outcome"] == "execution_error"
    assert sum(group["failed"] for group in saved["summary"].values()) == 2


def test_environment_baseline_error_is_reported_once_and_skips_model_tasks(monkeypatch, tmp_path):
    baselines = []

    def broken_baseline(case, tools, original, source):
        baselines.append(case["id"])
        raise OSError("Synthetic missing Maven installation")

    monkeypatch.setattr(experiment, "run_baseline", broken_baseline)
    monkeypatch.setattr(experiment, "run_trial", lambda *args: pytest.fail("Invalid baseline must not call a model."))
    report = experiment.run_experiment(live=True, stage="pilot", settings=Settings(), output_root=tmp_path)
    assert report["completed"] and not report["all_tasks_passed"]
    assert len(baselines) == len(set(baselines)) == 5
    assert len(report["tasks"]) == 7
    assert all(row["outcome"] == "baseline_invalid" for row in report["tasks"])
    assert all(row["error"] == "OSError" for row in report["baselines"].values())
    assert report["training_experiences"] == []
