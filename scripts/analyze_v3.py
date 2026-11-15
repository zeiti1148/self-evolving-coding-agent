"""Read-only accounting and paired diagnostics for V3 experiment reports."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import mean
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.v3_evaluation import UNFINISHED, paired_comparisons, request_metrics, summarize

CONFIGURATION = ("mode", "requested_model", "max_steps", "task_timeout_seconds", "llm_timeout_seconds", "frozen_sha256")


def _events(row):
    events = []
    for event in row.get("audit", {}).get("model_requests", []):
        events.extend(event["requests"] if isinstance(event.get("requests"), list) else [event])
    return events


def _known(value):
    return type(value) is int and value >= 0


def _mean(values):
    values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return mean(values) if values else None


def phase_metrics(row):
    groups = {"repair": [], "extraction": [], "unknown": []}
    for event in _events(row):
        phase = event.get("phase")
        groups["extraction" if phase == "extraction" else "repair" if phase else "unknown"].append(event)
    result = {}
    for phase, events in groups.items():
        totals = [event.get("usage", {}).get("total_tokens") for event in events]
        result[phase] = dict(requests=len(events), successful_requests=sum(event.get("success") is True for event in events),
            usage_complete_requests=sum(_known(value) for value in totals),
            reported_total_tokens=sum(value for value in totals if _known(value)))
        for field in ("total_tokens", "prompt_tokens", "completion_tokens"):
            values = [event.get("usage", {}).get(field) for event in events]
            result[phase][field] = sum(values) if values and all(_known(value) for value in values) else None
    return result


def _configuration(reports):
    configurations = {tuple(report.get(key) for key in CONFIGURATION) for report in reports}
    if len(configurations) != 1:
        raise ValueError("Reports must use the same experiment configuration and frozen references.")
    return dict(zip(CONFIGURATION, configurations.pop()))


def analyze_reports(reports):
    reports = list(reports)
    configuration = _configuration(reports)
    rows, allowed, seen, conditions, sources = [], {}, set(), set(), {}
    for report in reports:
        allowed.update(report.get("allowed_references", {}))
        for row in report["tasks"]:
            original = report.get("original_hashes", {}).get(row["project"])
            if not isinstance(original, dict) or not original:
                raise ValueError("Missing target source fingerprints for an experimental case.")
            if row["project"] in sources and sources[row["project"]] != original:
                raise ValueError("Conflicting target source fingerprints for the same case.")
            sources[row["project"]] = dict(original)
            if row["trial_id"] in seen:
                raise ValueError("duplicate trial_id in reports")
            identity = tuple(row[key] for key in ("project", "repetition", "execution_mode", "retrieval_strategy", "condition"))
            if identity in conditions:
                raise ValueError("duplicate experimental condition/repetition in reports")
            conditions.add(identity)
            seen.add(row["trial_id"])
            # Recompute model accounting from the audit rather than copying a
            # partial checkpoint's stale derived metrics.
            rows.append(dict(row, metrics=request_metrics(row.get("audit", {}))))

    details, failures = [], []
    by_language, by_case, grouped = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        language = row.get("audit", {}).get("language", "unknown")
        by_language[language].append(row)
        by_case[row["project"]].append(row)
        key = f"{row['execution_mode']}/{row['retrieval_strategy']}/{row['condition']}"
        actual = [event for event in row.get("actual_requests", []) if event.get("role") != "extractor"]
        identities = [event.get("experience_ids", []) for event in actual]
        steps = row.get("steps", {}).get("steps", [])
        test_positions = [index for index, step in enumerate(steps) if step.get("call", {}).get("name") == "run_tests"]
        writes = [index for index, step in enumerate(steps) if step.get("call", {}).get("name") == "write_file"]
        tests = [steps[index].get("result", {}) for index in test_positions]
        detail = {key: row.get(key) for key in ("trial_id", "project", "repetition", "execution_mode", "retrieval_strategy",
                  "condition", "outcome", "comparison_valid", "elapsed_ms")}
        detail.update(language=language, metrics=row["metrics"], phases=phase_metrics(row),
            eligible_references=len(allowed.get(row["project"], [])),
            initial_reference_ids=identities[0] if identities else None,
            reference_requests=sum(bool(ids) for ids in identities),
            distinct_reference_sets=len({tuple(sorted(ids)) for ids in identities if ids}),
            reference_bytes_sent=sum(event.get("reference_bytes", 0) for event in actual),
            test_runs=len(tests), failed_test_runs=sum(test.get("error_code") == "TEST_FAILED" for test in tests),
            first_test_failed=tests[0].get("error_code") == "TEST_FAILED" if tests else None,
            write_before_first_test=min(writes) < min(test_positions) if writes and test_positions else None)
        details.append(detail)
        if row["outcome"] not in UNFINISHED:
            grouped[key].append(detail)
        if row["outcome"] not in UNFINISHED | {"passed"}:
            failures.append(dict(trial_id=row["trial_id"], outcome=row["outcome"], comparison_valid=row.get("comparison_valid"),
                http_statuses=sorted({event["http_status"] for event in _events(row) if "http_status" in event}),
                error_types=sorted({event["error_type"] for event in _events(row) if "error_type" in event}),
                task_error=(row.get("status") or {}).get("error_message") or row.get("error")))

    summary = summarize(rows)
    for key, group in summary.items():
        completed = grouped[key]
        group.update(mean_repair_tokens=_mean(d["phases"]["repair"]["total_tokens"] for d in completed),
            repair_complete_trials=sum(_known(d["phases"]["repair"]["total_tokens"]) for d in completed),
            extraction_complete_trials=sum(_known(d["phases"]["extraction"]["total_tokens"]) for d in completed),
            mean_extraction_tokens=_mean(d["phases"]["extraction"]["total_tokens"] for d in completed),
            mean_repair_prompt_tokens=_mean(d["phases"]["repair"]["prompt_tokens"] for d in completed),
            mean_repair_completion_tokens=_mean(d["phases"]["repair"]["completion_tokens"] for d in completed),
            mean_reference_bytes_sent=_mean(d["reference_bytes_sent"] for d in completed),
            mean_decision_rounds=_mean(d["metrics"]["decision_rounds"] for d in completed),
            mean_tool_calls=_mean(d["metrics"]["tool_calls"] for d in completed),
            mean_elapsed_ms=_mean(d.get("elapsed_ms") for d in completed),
            tasks_with_failed_tests=sum(d["failed_test_runs"] > 0 for d in completed),
            tasks_writing_before_tests=sum(d["write_before_first_test"] is True for d in completed))

    index = {(d["project"], d["repetition"], d["execution_mode"], d["condition"], d["retrieval_strategy"]): d for d in details}
    pairing = dict(pairs=0, same_initial_membership=0, changed_initial_order=0)
    for key, after in index.items():
        if key[-1] != "contextual" or after["condition"] == "none":
            continue
        before = index.get((*key[:-1], "baseline"))
        if not before or any(d["outcome"] in UNFINISHED or d.get("comparison_valid") is not True for d in (before, after)):
            continue
        a, b = before["initial_reference_ids"], after["initial_reference_ids"]
        if a is None or b is None:
            continue
        pairing["pairs"] += 1
        pairing["same_initial_membership"] += set(a) == set(b)
        pairing["changed_initial_order"] += a != b and set(a) == set(b)
    return dict(configuration=configuration, original_hashes=sources, summary=summary,
        by_language={key: summarize(value) for key, value in by_language.items()},
        by_case={key: summarize(value) for key, value in by_case.items()},
        paired_comparisons=paired_comparisons(rows), reference_pairing=pairing, failures=failures, trials=details)


def compare_reports(before_reports, after_reports, *, before_strategy="contextual", after_strategy="adaptive"):
    before, after = analyze_reports(before_reports), analyze_reports(after_reports)
    if before["configuration"] != after["configuration"]:
        raise ValueError("Before/after configuration differs beyond retrieval implementation.")
    for project in before["original_hashes"].keys() & after["original_hashes"].keys():
        if before["original_hashes"][project] != after["original_hashes"][project]:
            raise ValueError("Before/after target source fingerprints differ.")
    def indexed(analysis, strategy):
        return {(d["project"], d["repetition"], d["execution_mode"], d["condition"]): d for d in analysis["trials"]
                if d["retrieval_strategy"] == strategy and d["outcome"] not in UNFINISHED and d.get("comparison_valid") is True}
    a, b = indexed(before, before_strategy), indexed(after, after_strategy)
    result = {}
    for condition in ("none", "full", "compact"):
        pairs = [(a[key], b[key]) for key in a.keys() & b.keys() if key[-1] == condition]
        changes = [int(right["outcome"] == "passed") - int(left["outcome"] == "passed") for left, right in pairs]
        passed = [(left, right) for left, right in pairs if left["outcome"] == right["outcome"] == "passed"]
        complete = [(left, right) for left, right in passed if all(_known(x["metrics"]["total_tokens"]) for x in (left, right))]
        deltas = [right["metrics"]["total_tokens"] - left["metrics"]["total_tokens"] for left, right in complete]
        case_deltas = defaultdict(list)
        for (left, _), delta in zip(complete, deltas):
            case_deltas[left["project"]].append(delta)
        result[condition] = dict(pairs=len(pairs), wins=changes.count(1), losses=changes.count(-1), ties=changes.count(0),
            success_rate_delta=_mean(changes), both_passed=len(passed), complete_success_token_pairs=len(complete),
            mean_total_token_delta=_mean(deltas),
            paired_before_mean_tokens=_mean(left["metrics"]["total_tokens"] for left, _ in complete),
            paired_after_mean_tokens=_mean(right["metrics"]["total_tokens"] for _, right in complete),
            mean_elapsed_delta_ms=_mean(right["elapsed_ms"] - left["elapsed_ms"] for left, right in passed
                if left.get("elapsed_ms") is not None and right.get("elapsed_ms") is not None),
            case_mean_token_delta={key: mean(values) for key, values in case_deltas.items()},
            unpaired_before=sum(key[-1] == condition and key not in b for key in a),
            unpaired_after=sum(key[-1] == condition and key not in a for key in b))
        for phase in ("repair", "extraction"):
            measured = [(left, right) for left, right in passed
                        if all(_known(x["phases"][phase]["total_tokens"]) for x in (left, right))]
            result[condition].update({
                f"complete_{phase}_pairs": len(measured),
                f"mean_{phase}_token_delta": _mean(right["phases"][phase]["total_tokens"] - left["phases"][phase]["total_tokens"] for left, right in measured),
                f"paired_before_mean_{phase}_tokens": _mean(left["phases"][phase]["total_tokens"] for left, _ in measured),
                f"paired_after_mean_{phase}_tokens": _mean(right["phases"][phase]["total_tokens"] for _, right in measured),
            })
    return result


def reports_from_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    paths = []
    for case in manifest["cases"].values():
        candidates = [Path(case["report"])] if case.get("report") else list(Path(case["output_root"]).glob("*/report.json"))
        if len(candidates) != 1:
            raise ValueError("Each case must have exactly one report before analysis.")
        paths.extend(candidates)
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--before-manifest", type=Path)
    parser.add_argument("--after-strategy", default="adaptive")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = reports_from_manifest(args.manifest)
    result = analyze_reports(reports)
    if args.before_manifest:
        result["before_after"] = compare_reports(reports_from_manifest(args.before_manifest), reports, after_strategy=args.after_strategy)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"trials": len(result["trials"]), "failures": len(result["failures"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
