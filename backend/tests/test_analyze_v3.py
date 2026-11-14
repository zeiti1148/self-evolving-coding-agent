import copy
import importlib
import importlib.util

import pytest

from scripts.v3_evaluation import request_metrics


def analyzer():
    assert importlib.util.find_spec("scripts.analyze_v3") is not None
    return importlib.import_module("scripts.analyze_v3")


def event(phase="decision", tokens=10, success=True):
    value = dict(phase=phase, success=success, request_experience_ids=[], http_status=200 if success else 500)
    if tokens is not None:
        value["usage"] = dict(total_tokens=tokens, prompt_tokens=tokens - 2, completion_tokens=2)
    return value


def row(identifier="case-single-contextual-full-r1", *, condition="full", strategy="contextual", outcome="passed", events=None):
    audit = dict(language="python", decision_rounds=1, tool_calls=3,
                 model_requests=events if events is not None else [event(), event("extraction", 6)])
    return dict(trial_id=identifier, project="case", repetition=1, execution_mode="single",
                retrieval_strategy=strategy, condition=condition, outcome=outcome, comparison_valid=True,
                elapsed_ms=100, audit=audit, metrics=request_metrics(audit), actual_requests=[])


def report(*rows):
    return dict(mode="live-model", status="completed", requested_model="fixture-model", max_steps=12,
                task_timeout_seconds=180, llm_timeout_seconds=60, frozen_sha256="frozen",
                tasks=list(rows), allowed_references={"case": ["a", "b"]},
                original_hashes={"case": {"app.py": "source-hash", "test_app.py": "test-hash"}})


def test_analysis_rejects_duplicate_trials_instead_of_double_counting():
    module = analyzer()
    with pytest.raises(ValueError, match="duplicate"):
        module.analyze_reports([report(row()), report(row())])


def test_analysis_preserves_failures_and_unknown_usage():
    module = analyzer()
    failed = row("failed", outcome="failed", events=[event(tokens=None, success=False)])
    failed["repetition"] = 2
    result = module.analyze_reports([report(row(), failed)])
    group = result["summary"]["single/contextual/full"]
    assert group["scheduled"] == group["completed"] == 2
    assert group["passed"] == 1 and group["success_rate"] == 0.5
    assert group["token_complete_trials"] == 1
    assert result["failures"][0]["trial_id"] == "failed"
    assert result["failures"][0]["http_statuses"] == [500]


def test_phase_metrics_keep_retry_usage_unknown_and_split_extraction():
    module = analyzer()
    task = row(events=[dict(requests=[event(tokens=None, success=False), event(tokens=12)]), event("extraction", 8)])
    phases = module.phase_metrics(task)
    assert phases["repair"]["requests"] == 2
    assert phases["repair"]["total_tokens"] is None
    assert phases["repair"]["reported_total_tokens"] == 12
    assert phases["repair"]["usage_complete_requests"] == 1
    assert phases["extraction"]["total_tokens"] == 8


def test_analysis_reports_reference_repetition_from_actual_requests():
    module = analyzer()
    task = row()
    task["actual_requests"] = [
        dict(role="single", experience_ids=["a", "b"], reference_bytes=120, success=True),
        dict(role="single", experience_ids=["b", "a"], reference_bytes=120, success=True),
        dict(role="extractor", experience_ids=[], reference_bytes=0, success=True),
    ]
    detail = module.analyze_reports([report(task)])["trials"][0]
    assert detail["reference_bytes_sent"] == 240
    assert detail["reference_requests"] == 2
    assert detail["distinct_reference_sets"] == 1
    assert detail["eligible_references"] == 2
    assert detail["phases"]["repair"]["total_tokens"] == 10
    assert detail["phases"]["extraction"]["total_tokens"] == 6


def test_analysis_distinguishes_reordering_from_different_reference_membership():
    module = analyzer()
    before = row("baseline", strategy="baseline")
    after = row("contextual", strategy="contextual")
    for task, ids in ((before, ["a", "b"]), (after, ["b", "a"])):
        task["actual_requests"] = [dict(role="single", experience_ids=ids, reference_bytes=120, success=True)]
    result = module.analyze_reports([report(before, after)])
    assert result["reference_pairing"] == dict(pairs=1, same_initial_membership=1, changed_initial_order=1)


def test_analysis_does_not_mix_model_or_frozen_reference_configurations():
    module = analyzer()
    first = report(row("first"))
    second = report(row("second"))
    second["frozen_sha256"] = "different"
    with pytest.raises(ValueError, match="configuration"):
        module.analyze_reports([first, second])


def test_unfinished_trials_stay_scheduled_without_a_failure_or_zero_cost():
    module = analyzer()
    task = row(outcome="running")
    task.pop("audit")
    task.pop("metrics")
    result = module.analyze_reports([report(task)])
    group = result["summary"]["single/contextual/full"]
    assert group["scheduled"] == 1 and group["completed"] == 0
    assert group["mean_total_tokens"] is None
    assert result["failures"] == []


def test_before_after_comparison_pairs_cases_repeats_and_includes_losses():
    module = analyzer()
    before = row("before")
    after = row("after", strategy="adaptive", outcome="failed", events=[event(tokens=None, success=False)])
    comparison = module.compare_reports([report(before)], [report(after)], after_strategy="adaptive")
    group = comparison["full"]
    assert group["pairs"] == 1 and group["losses"] == 1 and group["wins"] == 0
    assert group["complete_success_token_pairs"] == 0 and group["mean_total_token_delta"] is None


def test_before_after_cost_comparison_requires_complete_paired_successes():
    module = analyzer()
    before = row("before")
    after = row("after", strategy="adaptive", events=[event(tokens=8), event("extraction", 4)])
    before2, after2 = copy.deepcopy(before), copy.deepcopy(after)
    before2.update(trial_id="before2", repetition=2)
    after2.update(trial_id="after2", repetition=2)
    after2["audit"]["model_requests"][0].pop("usage")
    after2["metrics"] = request_metrics(after2["audit"])
    group = module.compare_reports([report(before, before2)], [report(after, after2)], after_strategy="adaptive")["full"]
    assert group["pairs"] == 2 and group["both_passed"] == 2
    assert group["complete_success_token_pairs"] == 1
    assert group["mean_total_token_delta"] == -4


def test_analysis_rejects_duplicate_conditions_even_with_different_trial_ids():
    module = analyzer()
    with pytest.raises(ValueError, match="duplicate"):
        module.analyze_reports([report(row("one"), row("two"))])


def test_repair_cost_remains_measurable_when_extraction_usage_is_missing():
    module = analyzer()
    before = row("before")
    after = row("after", strategy="adaptive", events=[event(tokens=8), event("extraction", None, False)])
    analysis = module.analyze_reports([report(after)])
    summary = analysis["summary"]["single/adaptive/full"]
    assert summary["repair_complete_trials"] == 1
    assert summary["extraction_complete_trials"] == 0
    group = module.compare_reports([report(before)], [report(after)], after_strategy="adaptive")["full"]
    assert group["complete_success_token_pairs"] == 0
    assert group["complete_repair_pairs"] == 1
    assert group["mean_repair_token_delta"] == -2


@pytest.mark.parametrize("changed_file", ["app.py", "test_app.py"])
def test_before_after_rejects_changed_target_source_or_tests(changed_file):
    module = analyzer()
    before = report(row("before"))
    after = report(row("after", strategy="adaptive"))
    after["original_hashes"]["case"][changed_file] = "changed"
    with pytest.raises(ValueError, match="source"):
        module.compare_reports([before], [after])


def test_analysis_rejects_conflicting_sources_for_same_case_across_reports():
    module = analyzer()
    first = report(row("first"))
    later = row("later")
    later["repetition"] = 2
    second = report(later)
    second["original_hashes"]["case"]["app.py"] = "changed"
    with pytest.raises(ValueError, match="source"):
        module.analyze_reports([first, second])


def test_before_after_rejects_missing_target_identity_evidence():
    module = analyzer()
    after = report(row("after", strategy="adaptive"))
    after.pop("original_hashes")
    with pytest.raises(ValueError, match="source"):
        module.compare_reports([report(row("before"))], [after])
