"""Pure V3 experiment planning, accounting and immutable SQLite snapshots."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from statistics import mean, median

CONDITIONS = ("none", "full", "compact")
MODES = ("single", "multi")
RETRIEVALS = ("baseline", "contextual", "adaptive")
UNFINISHED = {"pending", "running"}


def build_plan(case_ids, repetitions=3, modes=("single",), retrievals=("baseline",)):
    case_ids, modes, retrievals = list(case_ids), list(modes), list(retrievals)
    if (not case_ids or len(set(case_ids)) != len(case_ids)
            or any(not isinstance(case, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", case) for case in case_ids)
            or type(repetitions) is not int or not 1 <= repetitions <= 100):
        raise ValueError("Unique project IDs and 1..100 repetitions are required.")
    for values, allowed in ((modes, MODES), (retrievals, RETRIEVALS)):
        if not values or len(set(values)) != len(values) or not set(values).issubset(allowed):
            raise ValueError("Invalid or repeated execution mode/retrieval strategy.")
    plan = []
    for repeat in range(repetitions):
        # Rotate all three conditions; alternate strategy/mode order as well.
        for index, case in enumerate(case_ids):
            offset = (repeat + index) % len(CONDITIONS)
            conditions = CONDITIONS[offset:] + CONDITIONS[:offset]
            for mode in modes[::(-1 if repeat % 2 else 1)]:
                for retrieval in retrievals[::(-1 if repeat % 2 else 1)]:
                    for condition in conditions:
                        plan.append(dict(trial_id=f"{case}-{mode}-{retrieval}-{condition}-r{repeat + 1}",
                            project=case, repetition=repeat + 1, condition=condition,
                            execution_mode=mode, retrieval_strategy=retrieval))
    return plan


def snapshot_database(source: Path, destination: Path) -> str:
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or destination.exists() or source == destination:
        raise ValueError("Reference database must exist; snapshot must be a new separate file.")
    # Do not use ExperienceStore here: its connection path initializes/migrates schemas.
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as origin:
        origin.execute("PRAGMA query_only=ON")
        with closing(sqlite3.connect(destination)) as target:
            origin.backup(target)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def request_metrics(audit: dict) -> dict:
    events = []
    for event in audit.get("model_requests", []):
        events.extend(event["requests"] if isinstance(event.get("requests"), list) else [event])
    values = [event.get("usage", {}).get("total_tokens") for event in events]
    reported = [value for value in values if type(value) is int and value >= 0]
    return dict(model_requests=len(events), successful_model_requests=sum(event.get("success") is True for event in events),
                reported_total_tokens=sum(reported), usage_reported_requests=len(reported),
                total_tokens=sum(reported) if events and len(reported) == len(events) else None,
                decision_rounds=audit.get("decision_rounds"), tool_calls=audit.get("tool_calls"))


def summarize(records: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in records:
        groups[f"{row['execution_mode']}/{row['retrieval_strategy']}/{row['condition']}"].append(row)
    summary = {}
    for key, rows in groups.items():
        completed = [row for row in rows if row.get("outcome", "pending") not in UNFINISHED]
        tokens = [row.get("metrics", {}).get("total_tokens") for row in completed]
        known = [value for value in tokens if type(value) is int]
        times = [row["elapsed_ms"] for row in completed if isinstance(row.get("elapsed_ms"), (int, float))]
        passed = sum(row["outcome"] == "passed" for row in completed)
        summary[key] = dict(scheduled=len(rows), completed=len(completed), passed=passed,
            failed=len(completed) - passed, success_rate=passed / len(completed) if completed else None,
            outcomes=dict(Counter(row.get("outcome", "pending") for row in rows)),
            valid_trials=sum(row.get("comparison_valid") is True for row in completed),
            reference_received_trials=sum(row.get("reference_received") is True for row in completed),
            token_complete_trials=len(known), mean_total_tokens=mean(known) if known else None,
            median_elapsed_ms=median(times) if times else None)
    return summary


def paired_comparisons(records: list[dict]) -> dict:
    index = {(row["project"], row["repetition"], row["execution_mode"], row["retrieval_strategy"], row["condition"]): row
             for row in records if row.get("outcome", "pending") not in UNFINISHED and row.get("comparison_valid") is True}
    pairs = defaultdict(list)
    for (project, repeat, mode, retrieval, condition), row in index.items():
        if condition != "none":
            baseline = index.get((project, repeat, mode, retrieval, "none"))
            if baseline:
                pairs[f"{mode}/{retrieval}/{condition}-vs-none"].append((baseline, row))
        if retrieval == "contextual":
            baseline = index.get((project, repeat, mode, "baseline", condition))
            if baseline:
                pairs[f"{mode}/{condition}/contextual-vs-baseline"].append((baseline, row))
    result = {}
    for key, rows in pairs.items():
        changes = [int(after["outcome"] == "passed") - int(before["outcome"] == "passed") for before, after in rows]
        successful = [(before, after) for before, after in rows if before["outcome"] == after["outcome"] == "passed"]
        tokens = [after["metrics"]["total_tokens"] - before["metrics"]["total_tokens"] for before, after in successful
                  if all(type(row.get("metrics", {}).get("total_tokens")) is int for row in (before, after))]
        times = [after["elapsed_ms"] - before["elapsed_ms"] for before, after in successful
                 if all(isinstance(row.get("elapsed_ms"), (int, float)) for row in (before, after))]
        result[key] = dict(paired_trials=len(rows), wins=changes.count(1), losses=changes.count(-1),
            ties=changes.count(0), success_rate_delta=mean(changes), both_passed=len(successful),
            token_complete_pairs=len(tokens), mean_token_delta=mean(tokens) if tokens else None,
            mean_elapsed_delta_ms=mean(times) if times else None)
    return result
