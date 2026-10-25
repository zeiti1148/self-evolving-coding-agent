"""Build one offline HTML from recorded evidence; embeds no keys or model endpoints."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export(report_path: Path, output: Path) -> Path:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("mode") != "recorded-live-model" or not report.get("complete"):
        raise ValueError("Only completed real-model recordings can be exported; failures remain visible")
    cases = []
    for case in report["cases"]:
        if not case.get("status") or not isinstance(case.get("steps"), list):
            raise ValueError("Recording lacks terminal state or steps")
        entry = {key: case[key] for key in ("id", "title", "difficulty", "task_description", "expected_test_count",
                 "learning_points", "flow", "initial_files", "baseline", "status", "steps", "experience", "verified",
                 "original_unchanged", "tests_unchanged", "task_id")}
        entry["metrics"] = {key: case["audit"].get(key) for key in ("decision_rounds", "tool_calls", "elapsed_ms")}
        requests = case["audit"].get("model_requests", [])
        entry["request_counts"] = {"total": len(requests), "success": sum(e.get("success") is True for e in requests)}
        # Hide local machine paths from the portable presentation, while retaining raw evidence locally.
        for result in [entry["baseline"], *(s["result"] for s in entry["steps"])]:
            text = result.get("output", "")
            # Maven output can include resource/target paths; exact code and relative paths are retained.
            lines = [line for line in text.splitlines() if not ("\\workspaces\\" in line or "/workspaces/" in line)]
            result["output"] = "\n".join(lines)
        # The terminal result duplicates complete tool payloads; the replay already has the ordered steps.
        if entry["status"].get("result"):
            entry["status"]["result"].pop("steps", None)
            entry["status"]["result"].pop("tool_results", None)
        cases.append(entry)
    public = {"mode": report["mode"], "created_at": report["created_at"], "model": report["model"],
              "git_head": report["git_head"], "git_dirty": report["git_dirty"], "purpose": report["purpose"], "cases": cases}
    payload = json.dumps(public, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (ROOT / "docs/demo/replay.html").read_text(encoding="utf-8")
    model = (ROOT / "docs/demo/replay-model.mjs").read_text(encoding="utf-8").replace("export function ", "function ")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace("/*__REPLAY_MODEL__*/", model).replace("/*__REPLAY_DATA__*/", payload), encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(export(args.report, args.output))
