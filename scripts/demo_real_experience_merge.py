"""Replay a real successful repair and observe production automatic deduplication.

No model requests. Three independent workspace/test runs reuse ONE unmodified
issue extracted by the earlier real model. This isolates exact-rule consolidation,
not semantic paraphrase accuracy or new autonomous repair performance.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["KNOWLEDGE_MODELS_ENABLED"] = "false"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from backend.config import Settings
from backend.experience import ExperienceStore
from backend.experience.database import read_only
from backend.experience.jobs import MaintenanceWorker
from backend.experience.knowledge import provenance_page
from backend.projects import source_revision
from backend.shared import ExperienceCreate, ToolCall
from backend.tools import ToolService, WorkspaceManager
from backend.tools.workspace import inventory


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    saved = ROOT / ".cache/knowledge-live-experiment"
    history = json.loads((saved / "report.json").read_text(encoding="utf-8"))
    records = json.loads((saved / "training-raw.json").read_text(encoding="utf-8"))
    original = next(x for x in records if x["id"] == "12a8d375-8c61-4415-bb79-70cb57472ffa")
    trial = next(x for x in history["tasks"] if x["task_id"] == original["source_task_id"])
    assert trial["visible_passed"] and trial["heldout_passed"]
    case = next(x for x in history["catalog"] if x["id"] == trial["case"])
    source = Path(case["path"])
    fixed = (Path(trial["workspace"]) / "rules.py").read_text(encoding="utf-8")
    before_code = (source / "rules.py").read_text(encoding="utf-8")
    source_before = inventory(source)
    issue = original["issues"][0]
    assert issue["error_type"] == "AttributeError" and not issue["depends_on"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = ROOT / ".cache/knowledge-real-merge-demo" / (stamp + "-" + uuid4().hex[:6])
    directory.mkdir(parents=True, exist_ok=False)
    manager = WorkspaceManager(directory / "workspaces", {case["id"]: source})
    tools = ToolService(manager, Settings().maven_home, python_executable=sys.executable)
    store = ExperienceStore(directory / "experiences.db")
    store.initialize()
    worker = MaintenanceWorker(store.database_path, semantic_enabled=False)
    report = dict(created_at=datetime.now(timezone.utc).isoformat(), completed=False,
                  kind="real_test_execution_with_fixed_previously_extracted_rule",
                  original_trial=trial["trial_id"], original_experience_id=original["id"],
                  original_task_id=original["source_task_id"], selected_issue_index=0,
                  original_model=history["model"], new_model_requests=0,
                  note="使用此前真实模型修复的完整源码；每次独立重跑原始测试。经验正文固定复用原提炼结果中的第 1 条判空规则，不重新调用模型。",
                  source_project=str(source), database=str(store.database_path),
                  code_before=before_code, code_after=fixed, rule=issue,
                  constraints=original["constraints"], runs=[], snapshots=[])

    def references():
        return store.retrieve_model_references("label None strip AttributeError guest",
            language="python", repository_id=original["repository_id"], error_types=["AttributeError"],
            compatibility_key=original["compatibility_key"])

    def snapshot(stage):
        with read_only(store.database_path) as connection:
            counts = {name: connection.execute("SELECT count(*) FROM " + name).fetchone()[0]
                      for name in ["experiences", "knowledge_rules", "knowledge_evidence", "knowledge_sources", "knowledge_decisions"]}
            rules = [dict(x) for x in connection.execute("SELECT id,state,fingerprint FROM knowledge_rules ORDER BY id")]
            jobs = [dict(x) for x in connection.execute("SELECT id,state,attempts,examined FROM knowledge_jobs ORDER BY id")]
        result = dict(stage=stage, counts=counts, rules=rules, jobs=jobs, references=references())
        report["snapshots"].append(result)
        write_json(directory / "report.json", report)
        return result

    def await_ready():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with read_only(store.database_path) as connection:
                remaining = connection.execute("SELECT count(*) FROM knowledge_jobs WHERE state!='succeeded'").fetchone()[0]
            if remaining == 0 and len(references()) == 1:
                return
            time.sleep(.05)
        raise RuntimeError("Background consolidation did not reach the expected ready state")

    snapshot("empty")
    originals = []
    started = False
    try:
        for index in range(1, 4):
            task_id = str(uuid4())
            workspace = manager.create_workspace(source, task_id)
            baseline_call = ToolCall(id=str(uuid4()), name="run_tests")
            baseline = tools.execute_tool(workspace, baseline_call)
            assert baseline.error_code == "TEST_FAILED" and baseline.metadata["tests"] == 11
            apply_call = ToolCall(id=str(uuid4()), name="write_file", arguments={"path": "rules.py", "content": fixed})
            applied = tools.execute_tool(workspace, apply_call)
            assert applied.success
            test_call = ToolCall(id=str(uuid4()), name="run_tests")
            verified = tools.execute_tool(workspace, test_call)
            assert verified.success and verified.metadata["tests"] == 11
            assert not any(verified.metadata.get(k, 0) for k in ["failures", "errors", "skipped"])
            value = ExperienceCreate(schema_version=4, language="python", scope="project",
                repository_id=original["repository_id"], compatibility_key=original["compatibility_key"],
                source_revision=source_revision(source, source_before), source_task_id=task_id,
                task_summary=f"独立复现 {index}：label(None) 判空后返回 guest。",
                root_cause=issue["root_cause"], solution=issue["solution"], issues=[issue],
                constraints=original["constraints"], applicability=original["applicability"],
                required_capabilities=original["required_capabilities"], tags=original["tags"],
                verification=f"本次独立 pytest 运行：11 项通过，调用编号 {test_call.id}。",
                evidence=[dict(source_task_id=task_id, tool_call_id=test_call.id, runner="pytest", tests=11)])
            entry = store.save_experiences([value], source_task_id=task_id)[0]
            originals.append(entry)
            report["runs"].append(dict(index=index, task_id=task_id, experience_id=entry.id,
                workspace=str(workspace), baseline_call=baseline_call.model_dump(mode="json"),
                baseline=baseline.model_dump(mode="json"), test_call=test_call.model_dump(mode="json"),
                verification=verified.model_dump(mode="json"), experience=entry.model_dump(mode="json")))
            snapshot(f"saved_{index}")
            if not started:
                worker.start()
                started = True
            await_ready()
            current = snapshot(f"ready_{index}")
            assert current["counts"]["knowledge_rules"] == 1
            assert current["counts"]["experiences"] == index
            assert current["counts"]["knowledge_evidence"] == index
            assert current["references"][0]["evidence_summary"]["verified_runs"] == index
            print(f"Run {index}: 11 tests passed; originals={index}, rules=1, evidence={index}, model references=1", flush=True)
        retried = store.save_experiences([value], source_task_id=task_id)[0]
        assert retried.id == entry.id
        final = snapshot("same_save_retried")
        reference = final["references"][0]
        assert final["counts"]["experiences"] == final["counts"]["knowledge_evidence"] == 3
        assert set(reference["source_ids"]) == {v.id for v in originals}
        assert all(store.get_experience(v.id) == v for v in originals)
        assert inventory(source) == source_before
        report.update(completed=True, original_project_unchanged=True, original_records_unchanged=True,
                      manual_merge_calls=0, manual_review_decisions=final["counts"]["knowledge_decisions"],
                      final_reference=reference, provenance=provenance_page(store.database_path, reference["rule_id"]))
        report["label_before"] = ast.get_source_segment(before_code, next(n for n in ast.parse(before_code).body if isinstance(n, ast.FunctionDef) and n.name == "label"))
        report["label_after"] = ast.get_source_segment(fixed, next(n for n in ast.parse(fixed).body if isinstance(n, ast.FunctionDef) and n.name == "label"))
        write_json(directory / "report.json", report)
        destination = ROOT / "docs/reports/knowledge-remediation-2026-11-14/real-merge-example.json"
        write_json(destination, report)
        print(json.dumps({"completed": True, "report": str(destination), "rule_id": reference["rule_id"], "database": str(store.database_path)}, ensure_ascii=False))
    finally:
        if started:
            worker.close()


if __name__ == "__main__":
    main()
