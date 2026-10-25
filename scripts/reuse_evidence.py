"""Portable, verified evidence for the experience-reuse presentation."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def experience_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def portable_reuse(path: Path, source_recording_hash: str) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("mode") != "recorded-live-reuse" or not report.get("complete") or not report.get("verified"):
        raise ValueError("Completed verified reuse evidence is required")
    if report.get("source_recording_sha256") != source_recording_hash:
        raise ValueError("Reuse evidence belongs to another source recording")
    source = report["source_experience"]
    sent = None
    for decision in report["captured_decisions"]:
        references = decision["user_content"]["reference_experiences"]
        if not decision["successful"] or source not in references:
            continue
        for event in decision["audit_events"]:
            if event["success"] and any(i == source["id"] and h == experience_hash(source)
                    for i,h in zip(event["request_experience_ids"], event["request_experience_hashes"])):
                sent = {"user_content": decision["user_content"], "event": {k: event.get(k) for k in
                    ["http_status", "success", "phase", "response_id", "returned_model", "request_experience_ids", "request_experience_hashes"]}}
                break
        if sent:
            break
    if sent is None:
        raise ValueError("Experience body, ID and successful request hash must agree")
    if source not in report["experiences_before"] or source not in report["matched_experiences"]:
        raise ValueError("Source experience is absent from storage or retrieval")
    raw_target = report["target"]
    final = next((s["result"] for s in reversed(raw_target["steps"]) if s["call"]["name"] == "run_tests" and s["result"]["metadata"].get("final_verification")), None)
    if (raw_target["status"]["status"] != "succeeded" or not raw_target["status"]["result"]["tests_passed"] or
        not final or not final["success"] or final["metadata"].get("tests") != raw_target["expected_test_count"] or
        any(final["metadata"].get(k) != 0 for k in ["failures", "errors", "skipped"]) or
        not raw_target["original_unchanged"] or not raw_target["tests_unchanged"] or not report["source_database_unchanged"]):
        raise ValueError("Target verification or source integrity is incomplete")
    if (raw_target["experience"] not in report["experiences_after"] or
        len(report["experiences_after"]) != len(report["experiences_before"]) + 1 or
        any(e not in report["experiences_after"] for e in report["experiences_before"])):
        raise ValueError("Experience growth does not match the saved task")
    target = copy.deepcopy({k: report["target"][k] for k in ["id", "title", "task_description", "expected_test_count", "task_id",
        "initial_files", "baseline", "steps", "status", "experience", "original_unchanged", "tests_unchanged"]})
    for result in [target["baseline"], *(s["result"] for s in target["steps"])]:
        result["output"] = "\n".join(line for line in result.get("output", "").splitlines()
            if "\\workspaces\\" not in line and "/workspaces/" not in line)
    if target["status"].get("result"):
        target["status"]["result"].pop("steps", None)
        target["status"]["result"].pop("tool_results", None)
    return {"mode": report["mode"], "created_at": report["created_at"], "model": report["model"],
        "source_experience": source, "experiences_before": report["experiences_before"],
        "experiences_after": report["experiences_after"], "query": report["query"],
        "matched_experiences": report["matched_experiences"], "request": sent, "target": target,
        "metrics": {k: report["target"]["audit"].get(k) for k in ["decision_rounds", "tool_calls", "elapsed_ms"]}}
