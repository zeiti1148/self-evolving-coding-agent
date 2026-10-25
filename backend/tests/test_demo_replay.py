"""Offline replay preserves evidence, safely embeds text, and includes separate demos."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.config import Settings
from scripts.export_replay import export

ROOT = Path(__file__).resolve().parents[2]


def test_demo_cases_are_registered_without_changing_formal_corpus():
    demos = json.loads((ROOT / "benchmarks/demo/catalog.json").read_text(encoding="utf-8"))
    formal = json.loads((ROOT / "benchmarks/catalog.json").read_text(encoding="utf-8"))
    assert len(formal) == 6
    assert len(demos) == 2
    assert {c["id"] for c in demos}.isdisjoint(c["id"] for c in formal)
    for case in demos:
        project = ROOT / case["path"]
        assert Settings().projects[case["id"]] == project
        assert len(list((project / "src/main").rglob("*.java"))) == 3
        tests = "\n".join(p.read_text(encoding="utf-8") for p in (project / "src/test").rglob("*.java"))
        assert len(re.findall(r"@Test\b", tests)) == case["expected_test_count"] == 12
        assert "@Disabled" not in tests


def report_fixture():
    payload = "</script><script>alert('injection')</script>\u2028"
    return {
        "mode": "recorded-live-model", "complete": True, "created_at": "2026-10-25T00:00:00Z",
        "model": "test-fixture", "git_head": "abc", "git_dirty": False, "purpose": "unit test only",
        "secret": "must-not-export", "cases": [{
            "id": "case", "title": "Case", "difficulty": "test", "task_description": payload,
            "expected_test_count": 1, "learning_points": [], "flow": [], "initial_files": {},
            "baseline": {"output": "path C:\\local\\workspaces\\123\\target\nassertion failed"},
            "status": {"status": "failed", "result": {"steps": ["duplicate"], "tool_results": []}},
            "steps": [], "experience": None, "verified": False, "original_unchanged": True,
            "tests_unchanged": True, "task_id": "task", "audit": {"model_requests": [{"success": False}]},
        }],
    }


def test_export_preserves_failed_case_and_escapes_script_breakout(tmp_path):
    report = report_fixture()
    source = tmp_path / "report.json"
    source.write_text(json.dumps(report), encoding="utf-8")
    output = export(source, tmp_path / "replay.html").read_text(encoding="utf-8")
    assert "</script><script>alert" not in output
    assert "must-not-export" not in output
    assert "C:\\\\local" not in output
    assert "/*__REPLAY_DATA__*/" not in output and "/*__REPLAY_MODEL__*/" not in output
    assert "connect-src 'none'" in output
    data = json.loads(output.split("const recording = ", 1)[1].split(";\n", 1)[0])
    case = data["cases"][0]
    assert case["task_description"] == report["cases"][0]["task_description"]
    assert case["verified"] is False and case["status"]["status"] == "failed"
    assert case["baseline"]["output"] == "assertion failed"
    assert case["request_counts"] == {"total": 1, "success": 0}
    assert "steps" not in case["status"]["result"]
    assert json.loads(source.read_text(encoding="utf-8")) == report


@pytest.mark.parametrize("change", [{"mode": "scripted"}, {"complete": False}])
def test_export_rejects_scripted_or_partial_recording(tmp_path, change):
    report = report_fixture() | change
    source = tmp_path / "report.json"
    source.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="completed real-model"):
        export(source, tmp_path / "replay.html")
