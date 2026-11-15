"""V3 experiments must compare isolated, equally budgeted trials."""
import importlib.util
import json
import sqlite3
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest


def evaluation():
    assert importlib.util.find_spec("scripts.v3_evaluation") is not None, "V3 evaluation module is required"
    from scripts import v3_evaluation
    return v3_evaluation


def runner():
    assert importlib.util.find_spec("scripts.experiment_v3") is not None, "V3 runner is required"
    from scripts import experiment_v3
    return experiment_v3


def test_v3_plan_balances_all_three_conditions_and_keeps_unique_trials():
    plan = evaluation().build_plan([f"case-{i}" for i in range(6)])
    assert len(plan) == len({row["trial_id"] for row in plan}) == 54
    for case in {row["project"] for row in plan}:
        orders = [[row["condition"] for row in plan if row["project"] == case and row["repetition"] == repeat]
                  for repeat in (1, 2, 3)]
        assert all(len(set(order)) == 3 for order in orders)
        assert all({order[position] for order in orders} == {"none", "full", "compact"} for position in range(3))
    matrix = evaluation().build_plan(["python-case"], repetitions=1, modes=("single", "multi"),
                                     retrievals=("baseline", "contextual"))
    assert len(matrix) == 12
    assert Counter((row["execution_mode"], row["retrieval_strategy"]) for row in matrix) == {
        (mode, strategy): 3 for mode in ("single", "multi") for strategy in ("baseline", "contextual")}


@pytest.mark.parametrize("kwargs", [dict(case_ids=[]), dict(case_ids=["same", "same"]),
    dict(case_ids=["../bad"]), dict(case_ids=["ok"], repetitions=True), dict(case_ids=["ok"], repetitions=0),
    dict(case_ids=["ok"], modes=[]), dict(case_ids=["ok"], modes=["unknown"]),
    dict(case_ids=["ok"], retrievals=["baseline", "baseline"])])
def test_v3_plan_rejects_ambiguous_or_unbounded_conditions(kwargs):
    with pytest.raises(ValueError):
        evaluation().build_plan(**kwargs)


def test_v3_metrics_flatten_roles_and_keep_missing_usage_unknown():
    metrics = evaluation().request_metrics({"model_requests": [
        {"role": "diagnosis", "requests": [{"success": True, "usage": {"total_tokens": 7}}]},
        {"role": "repair", "requests": [{"success": False, "error_type": "ReadTimeout"}]},
        {"role": "extractor", "requests": [{"success": True, "usage": {"total_tokens": 3}}]},
    ]})
    assert metrics["model_requests"] == 3
    assert metrics["reported_total_tokens"] == 10 and metrics["usage_reported_requests"] == 2
    assert metrics["total_tokens"] is None
    assert evaluation().request_metrics({})["total_tokens"] is None


def test_v3_summary_retains_failures_and_only_pairs_matching_tasks():
    common = dict(project="a", repetition=1, execution_mode="single", retrieval_strategy="baseline",
                  elapsed_ms=100, metrics={"total_tokens": None}, comparison_valid=True)
    rows = [dict(common, condition="none", outcome="passed"),
            dict(common, condition="full", outcome="failed"),
            dict(common, condition="compact", outcome="running"),
            dict(common, project="b", condition="full", outcome="stopped")]
    summary = evaluation().summarize(rows)
    full = summary["single/baseline/full"]
    assert full["scheduled"] == full["completed"] == 2
    assert full["passed"] == 0 and full["success_rate"] == 0
    assert full["mean_total_tokens"] is None and full["token_complete_trials"] == 0
    pairs = evaluation().paired_comparisons(rows)
    pair = pairs["single/baseline/full-vs-none"]
    assert pair["paired_trials"] == 1 and pair["wins"] == 0 and pair["losses"] == 1
    assert pair["success_rate_delta"] == -1
    assert pair["mean_token_delta"] is None


def test_v3_snapshot_is_read_only_even_for_uninitialized_database(tmp_path):
    module = evaluation()
    source, destination = tmp_path / "source.db", tmp_path / "snapshot.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE sentinel(value TEXT)")
        db.execute("INSERT INTO sentinel VALUES ('do not migrate')")
    before = source.read_bytes()
    digest = module.snapshot_database(source, destination)
    assert source.read_bytes() == before and len(digest) == 64
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT value FROM sentinel").fetchone()[0] == "do not migrate"
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='experiences'").fetchall()
    with pytest.raises(ValueError):
        module.snapshot_database(source, source)
    with pytest.raises(ValueError):
        module.snapshot_database(source, destination)
    with pytest.raises(ValueError):
        module.snapshot_database(tmp_path / "missing.db", tmp_path / "new.db")
    assert not (tmp_path / "missing.db").exists()


def test_v3_dry_run_does_not_load_model_configuration_or_write_files(tmp_path, monkeypatch):
    from backend.config import Settings
    module = runner()
    monkeypatch.setattr(Settings, "from_env", lambda: pytest.fail("dry-run must not read model configuration"))
    report = module.run_experiment(case_ids=["python-booking-transfer"], output_root=tmp_path / "unused",
                                   catalog=module.ROOT / "benchmarks/experience-v3/catalog.json")
    assert report["mode"] == "dry-run" and report["planned_tasks"] == 9
    assert not (tmp_path / "unused").exists()


def published_reference(tmp_path, repository="source-only"):
    from backend.experience import ExperienceStore
    from backend.experience.promotion import preview_promotion, publish_global
    from backend.tests.test_experience_policy import seed
    store = ExperienceStore(tmp_path / "reference.db")
    source = seed(store, repository_id=repository)
    preview = preview_promotion(store.database_path, source.id)
    published = publish_global(store.database_path, source.id, preview.draft)
    from backend.experience.jobs import drain
    drain(store.database_path, seconds=20)
    return store, source, published


def test_v3_reference_preflight_detects_published_target_answers(tmp_path):
    module = runner()
    store, source, published = published_reference(tmp_path, "v3-python-booking")
    projects = module.load_cases(module.ROOT / "benchmarks/experience-v3/catalog.json")
    snapshot = tmp_path / "frozen.db"
    evaluation().snapshot_database(store.database_path, snapshot)
    with pytest.raises(ValueError, match="evaluation"):
        module.inspect_references(snapshot, [projects["python-booking-transfer"]])


@pytest.mark.parametrize("mode", ["single", "multi"])
def test_v3_trial_runs_real_api_tools_with_frozen_references(tmp_path, mode):
    from backend.config import Settings
    from scripts.experiment import file_hash
    from backend.tools.workspace import inventory
    from tests.scripted_multi_agent import ScriptedRoleFactory
    module = runner()
    store, _, published = published_reference(tmp_path)
    project = module.load_cases(module.ROOT / "benchmarks/experience-v3/catalog.json")["python-booking-transfer"]
    snapshot = tmp_path / "frozen.db"
    digest = evaluation().snapshot_database(store.database_path, snapshot)
    settings = replace(Settings(), workspace_root=tmp_path / "workspaces", database_path=tmp_path / "trial.db",
                       projects={project.id: project.source}, project_definitions={project.id: project}, task_timeout=30)
    row = dict(trial_id="fixture", project=project.id, condition="compact", execution_mode=mode,
               retrieval_strategy="baseline", repetition=1)
    result = module.run_trial(row, project, settings, snapshot, [published.id], inventory(project.source),
                              lambda _: None, llm_factory=ScriptedRoleFactory())
    assert result["outcome"] == "passed", result.get("error") or result.get("status")
    assert result["comparison_valid"] and result["reference_received"]
    assert result["final_verification"]["metadata"]["tests"] == 12
    assert result["actual_requests"] and result["metrics"]["total_tokens"] is None
    assert any(published.id in event["experience_ids"] for event in result["actual_requests"])
    assert file_hash(snapshot) == digest
    assert result["original_unchanged"] and Path(result["workspace"]).is_dir()


def test_v3_default_catalog_is_six_distinct_balanced_evaluation_sources():
    projects = runner().load_cases()
    assert len(projects) == 6
    assert Counter(project.language for project in projects.values()) == {"java": 3, "python": 3}
    assert len({project.source.resolve() for project in projects.values()}) == 6
    assert all(project.expected_tests == 12 for project in projects.values())
    assert not any("checkout" in project.id for project in projects.values())


def test_v3_runner_checkpoints_execution_errors_and_keeps_trial_outputs_isolated(tmp_path, monkeypatch):
    from backend.config import Settings
    module = runner()
    source, _, _ = published_reference(tmp_path)
    calls = []
    original_source = source.database_path.read_bytes()
    monkeypatch.setattr(module, "baseline", lambda *args: {"valid": True})

    def trial(spec, project, settings, frozen, allowed, original, checkpoint, **kwargs):
        calls.append((spec["trial_id"], settings.database_path, settings.workspace_root))
        if len(calls) == 2:
            raise RuntimeError("Injected failure must remain in the report")
        return dict(spec, outcome="passed", comparison_valid=True, original_unchanged=True,
                    reference_received=spec["condition"] != "none", metrics={"total_tokens": None})

    monkeypatch.setattr(module, "run_trial", trial)
    result = module.run_experiment(live=True, reference_database=source.database_path,
        case_ids=["python-booking-transfer"], repetitions=1, settings=Settings(), llm_factory=lambda: None,
        output_root=tmp_path / "runs")
    assert result["completed"] and len(calls) == 3
    assert len({call[1] for call in calls}) == len({call[2] for call in calls}) == 3
    # Long human-readable trial IDs used as directories exceed Windows/Maven
    # path limits once a batch directory and Java build outputs are appended.
    assert all(len(call[1].parent.name) <= 12 for call in calls)
    for row, call in zip(result["tasks"], calls):
        assert Path(row["trial_directory"]) == call[1].parent
        sidecar = __import__("json").loads((call[1].parent / "task.json").read_text(encoding="utf-8"))
        assert sidecar["trial_id"] == row["trial_id"]
    assert result["tasks"][1]["outcome"] == "execution_error"
    assert result["frozen_unchanged"] and result["originals_unchanged"]
    assert source.database_path.read_bytes() == original_source
    saved = __import__("json").loads((Path(result["directory"]) / "report.json").read_text(encoding="utf-8"))
    assert saved["summary"]["single/baseline/full"]["failed"] == 1
    assert len(list((Path(result["directory"]) / "trials").glob("*/task.json"))) == 3


def test_live_output_root_cannot_write_into_benchmark_sources(tmp_path, monkeypatch):
    from backend.config import Settings
    module = runner()
    source, _, _ = published_reference(tmp_path)
    project = module.load_cases()["python-inventory-evaluation"]
    # mkdir is forbidden so the red test cannot contaminate the real fixture.
    def forbidden_mkdir(*args, **kwargs):
        pytest.fail("Output validation must happen before any filesystem mutation")
    monkeypatch.setattr(Path, "mkdir", forbidden_mkdir)
    with pytest.raises(ValueError, match="benchmark"):
        module.run_experiment(live=True, reference_database=source.database_path, settings=Settings(),
                              llm_factory=lambda: None, output_root=project.source / "runs")


def test_subset_experiment_rejects_experience_from_unselected_evaluation_source(tmp_path, monkeypatch):
    from backend.config import Settings
    module = runner()
    source, _, _ = published_reference(tmp_path, "v3-python-booking")
    monkeypatch.setattr(module, "baseline", lambda *args: pytest.fail("Evaluation-derived reference must fail preflight"))
    result = module.run_experiment(live=True, reference_database=source.database_path, settings=Settings(),
        llm_factory=lambda: pytest.fail("No model request permitted"), case_ids=["python-inventory-evaluation"],
        output_root=tmp_path / "runs", repetitions=1)
    assert result["status"] == "not_run" and "evaluation" in result["reason"]
    assert result["tasks"] == []


def test_frozen_checked_retrieval_does_not_bypass_pending_rules_or_use_trial_feedback(tmp_path):
    from backend.experience import ExperienceStore
    from backend.tests.test_experience_consolidation import record, save, scan
    from scripts.experiment import FrozenSearchStore, file_hash

    training = ExperienceStore(tmp_path / "training.db")
    source = save(training, record())
    frozen = tmp_path / "frozen.db"
    digest = evaluation().snapshot_database(training.database_path, frozen)
    output = ExperienceStore(tmp_path / "trial.db")
    adapter = FrozenSearchStore(frozen, output)
    context = dict(language="python", repository_id=source.repository_id, error_types=[],
                   compatibility_key=None, source_revision=None, runner_hash=None)
    # The old source-row policy considers this row active; the checked engine
    # correctly waits for maintenance and must not fall back to that policy.
    assert source.reuse_status == "active"
    assert adapter.retrieve_model_references(**context) == []
    feedback = record(task_summary="Trial's new answer")
    adapter.save_experiences([feedback], source_task_id=feedback.source_task_id)
    scan(output)
    scan(training)
    assert training.retrieve_model_references(**context)
    assert output.retrieve_model_references(**context)
    assert adapter.retrieve_model_references(**context) == []
    assert file_hash(frozen) == digest


def published_multi_issue_reference(tmp_path):
    from backend.experience import ExperienceStore
    from backend.experience.promotion import preview_promotion, publish_global
    from backend.tests.test_experience_consolidation import record, save, scan

    store = ExperienceStore(tmp_path / "multi-reference.db")
    source = save(store, record(repository_id="independent-training", issues=[
        dict(error_type="ValueError", root_cause="Unvalidated quantity", solution="Validate the quantity contract", files=["quantity.py"]),
        dict(error_type="IndexError", root_cause="Unvalidated selection", solution="Validate the selection bounds", files=["selection.py"]),
    ]))
    scan(store)
    preview = preview_promotion(store.database_path, source.id)
    assert preview.can_publish
    published = publish_global(store.database_path, source.id, preview.draft)
    scan(store)
    return store, published


@pytest.mark.parametrize("mode", ["single", "multi"])
def test_v3_trial_audits_distinct_rules_from_one_source_without_collapsing_them(tmp_path, mode):
    from backend.config import Settings
    from backend.tools.workspace import inventory
    from scripts.experiment import file_hash
    from tests.scripted_multi_agent import ScriptedRoleFactory

    module = runner()
    store, published = published_multi_issue_reference(tmp_path)
    project = module.load_cases(module.ROOT / "benchmarks/experience-v3/catalog.json")["python-booking-transfer"]
    snapshot = tmp_path / "frozen.db"
    digest = evaluation().snapshot_database(store.database_path, snapshot)
    allowed = module.inspect_references(snapshot, [project])[project.id]
    assert allowed == [published.id]
    expected_rules = {entry["rule_id"] for entry in module.checked_references(snapshot, project)}
    assert len(expected_rules) == 2
    settings = replace(Settings(), workspace_root=tmp_path / "workspaces", database_path=tmp_path / "trial.db",
        projects={project.id: project.source}, project_definitions={project.id: project}, task_timeout=30)
    spec = dict(trial_id="multi-issue", project=project.id, condition="compact", execution_mode=mode,
                retrieval_strategy="baseline", repetition=1)
    result = module.run_trial(spec, project, settings, snapshot, allowed, inventory(project.source),
                              lambda _: None, llm_factory=ScriptedRoleFactory(), max_steps=12)
    assert result["outcome"] == "passed", result.get("error") or result.get("status")
    assert result["comparison_valid"] and result["reference_received"]
    received = [event for event in result["actual_requests"] if event["success"] and event["experience_ids"]]
    assert received and any(set(event["rule_ids"]) == expected_rules for event in received)
    for event in received:
        assert set(event["experience_ids"]) == {published.id}
        assert len(event["rule_ids"]) == len(event["content_sha256"]) == len(event["experience_ids"])
        assert len(event["rule_ids"]) == len(set(event["rule_ids"]))
        assert event["payload_valid"]
    with sqlite3.connect(settings.database_path) as db:
        persisted = db.execute("SELECT request_json FROM api_tasks WHERE id=?", (result["task_id"],)).fetchone()[0]
    assert json.loads(persisted)["max_steps"] == 12
    assert result["audit"]["decision_rounds"] <= 12
    assert file_hash(snapshot) == digest


@pytest.mark.parametrize("mode", ["single", "multi"])
def test_v3_none_condition_uses_same_budget_without_receiving_any_checked_rule(tmp_path, mode):
    from backend.config import Settings
    from backend.tools.workspace import inventory
    from tests.scripted_multi_agent import ScriptedRoleFactory

    module = runner()
    store, _, published = published_reference(tmp_path)
    project = module.load_cases(module.ROOT / "benchmarks/experience-v3/catalog.json")["python-booking-transfer"]
    snapshot = tmp_path / "frozen.db"
    evaluation().snapshot_database(store.database_path, snapshot)
    settings = replace(Settings(), workspace_root=tmp_path / "workspaces", database_path=tmp_path / "trial.db",
        projects={project.id: project.source}, project_definitions={project.id: project}, task_timeout=30)
    spec = dict(trial_id="no-references", project=project.id, condition="none", execution_mode=mode,
                retrieval_strategy="adaptive", repetition=1)
    result = module.run_trial(spec, project, settings, snapshot, [published.id], inventory(project.source),
                              lambda _: None, llm_factory=ScriptedRoleFactory(), max_steps=12)
    assert result["outcome"] == "passed", result.get("error") or result.get("status")
    assert result["comparison_valid"] and not result["reference_received"]
    assert result["actual_requests"] and all(event["experience_ids"] == event["rule_ids"] == []
                                             for event in result["actual_requests"])
    with sqlite3.connect(settings.database_path) as db:
        persisted = db.execute("SELECT request_json FROM api_tasks WHERE id=?", (result["task_id"],)).fetchone()[0]
    assert json.loads(persisted)["max_steps"] == 12 and result["audit"]["decision_rounds"] <= 12
    assert result["audit"]["experience_enabled"] is False


@pytest.mark.parametrize("condition,change", [("compact", "constraint"), ("compact", "identity"), ("none", None)])
def test_request_recorder_rejects_modified_rule_boundaries_before_model_call(tmp_path, condition, change):
    import json
    from backend.experience.policy import format_model_reference
    from scripts.v3_references import RequestRecorder

    module = runner()
    store, _, _ = published_reference(tmp_path)
    project = module.load_cases(module.ROOT / "benchmarks/experience-v3/catalog.json")["python-booking-transfer"]
    snapshot = tmp_path / "frozen.db"
    evaluation().snapshot_database(store.database_path, snapshot)
    reference = format_model_reference(module.checked_references(snapshot, project)[0], "compact")

    class ForbiddenClient:
        def complete(self, *args, **kwargs):
            pytest.fail("A modified or forbidden reference must be rejected before contacting a model")

    recorder = RequestRecorder(ForbiddenClient, {reference["rule_id"]: reference}, condition)
    actual = json.loads(json.dumps(reference))
    if change == "constraint":
        actual["constraints"] = ["Invented business contract"]
    elif change == "identity":
        actual["rule_id"] = "invented-rule"
    messages = [{"role": "system", "content": "fixture"},
                {"role": "user", "content": json.dumps({"reference_experiences": [actual]})}]
    with pytest.raises(ValueError, match="frozen reference controls"):
        recorder().complete(messages, tools=[])
    assert len(recorder.events) == 1 and not recorder.events[0]["payload_valid"]
    assert not recorder.events[0]["success"]
