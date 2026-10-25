from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from backend.config import Settings
from scripts.reuse_evidence import experience_hash, portable_reuse

ROOT = Path(__file__).resolve().parents[2]


def test_followup_fixture_is_registered_and_preserves_formal_corpus():
    case = json.loads((ROOT / "benchmarks/reuse/catalog.json").read_text(encoding="utf-8"))
    path = Settings().projects[case["id"]]
    assert path == ROOT / case["path"]
    assert len(list((path / "src/main").rglob("*.java"))) == 3
    text = "\n".join(p.read_text(encoding="utf-8") for p in (path / "src/test").rglob("*.java"))
    assert len(re.findall(r"@Test\b", text)) == case["expected_test_count"] == 12
    assert "@Disabled" not in text
    assert case["error_type"] in case["task_description"]
    assert len(json.loads((ROOT / "benchmarks/catalog.json").read_text(encoding="utf-8"))) == 6


def evidence():
    source = {"id": "source", "solution": "Validate before mutation"}
    saved = {"id": "new", "source_task_id": "target"}
    event = {"success": True, "http_status": 200, "request_experience_ids": ["source"],
             "request_experience_hashes": [experience_hash(source)]}
    final = {"call": {"name": "run_tests"}, "result": {"success": True, "output": "12 passed", "metadata": {
        "final_verification": True, "tests": 12, "failures": 0, "errors": 0, "skipped": 0}}}
    return {"mode": "recorded-live-reuse", "complete": True, "verified": True, "source_recording_sha256": "recording",
        "source_database_unchanged": True, "source_experience": source, "experiences_before": [source],
        "matched_experiences": [source], "experiences_after": [source, saved], "query": {}, "model": "test", "created_at": "now",
        "captured_decisions": [{"successful": True, "user_content": {"reference_experiences": [dict(source)]}, "audit_events": [event]}],
        "target": {"id": "target", "title": "test", "task_description": "test", "expected_test_count": 12, "task_id": "target",
        "initial_files": {}, "baseline": {"output": "failure"}, "steps": [final], "status": {"status": "succeeded", "result": {"tests_passed": True}},
        "experience": saved, "original_unchanged": True, "tests_unchanged": True, "audit": {}}}


def test_export_links_exact_saved_experience_to_a_successful_request(tmp_path):
    source = evidence()
    path = tmp_path / "report.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    portable = portable_reuse(path, "recording")
    assert portable["request"]["user_content"]["reference_experiences"] == portable["matched_experiences"]
    assert len(portable["experiences_after"]) == len(portable["experiences_before"]) + 1
    assert json.loads(path.read_text(encoding="utf-8")) == source


@pytest.mark.parametrize("damage", ["body", "hash"])
def test_export_rejects_request_content_or_hash_mismatch(tmp_path, damage):
    source = evidence()
    decision = source["captured_decisions"][0]
    if damage == "body":
        decision["user_content"]["reference_experiences"][0]["solution"] = "Different advice"
    else:
        decision["audit_events"][0]["request_experience_hashes"] = ["wrong"]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="body, ID"):
        portable_reuse(path, "recording")


def test_export_rejects_unrelated_source_recording(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(evidence()), encoding="utf-8")
    with pytest.raises(ValueError, match="another source"):
        portable_reuse(path, "different")
