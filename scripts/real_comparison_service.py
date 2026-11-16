"""Host an isolated comparison arm using the product's ordinary HTTP routes.

Example: python scripts/real_comparison_service.py --arm-dir .cache/.../before1
         --mode before --label "更新前 · 第1轮" --group comparison-20261115 --port 8011
No task/model request is submitted by starting this service.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def knowledge_snapshot(database):
    """Observe rows without creating/migrating a database or modifying rules."""
    database = Path(database).resolve()
    if not database.exists():
        return {"experiences": 0, "experience_ids": [], "rules": 0, "rule_states": {}, "relations": {}}
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=15) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        source_ids = [row[0] for row in db.execute("SELECT id FROM experiences ORDER BY id")] if "experiences" in tables else []
        rule_states = dict(db.execute("SELECT state,count(*) FROM knowledge_rules GROUP BY state")) if "knowledge_rules" in tables else {}
        relations = {kind + "/" + state: count for kind, state, count in
            db.execute("SELECT kind,status,count(*) FROM knowledge_relations GROUP BY kind,status")} if "knowledge_relations" in tables else {}
        return {"experiences": len(source_ids), "experience_ids": source_ids,
                "rules": sum(rule_states.values()), "rule_states": rule_states, "relations": relations}


class ComparisonAuditAgent:
    """Attach measurement metadata to actual kernel results, never fabricated data."""
    def __init__(self, delegate, database, metadata):
        self.delegate, self.database, self.metadata = delegate, database, metadata
        self.last_run_audit = {}

    def run(self, request, **callbacks):
        before = knowledge_snapshot(self.database)
        started = datetime.now(timezone.utc).isoformat()
        try:
            return self.delegate.run(request, **callbacks)
        finally:
            self.last_run_audit = dict(getattr(self.delegate, "last_run_audit", {}))
            metadata = dict(self.metadata)
            base = metadata.pop("task_public_base_url", "")
            metadata.update(task_public_url=base.rstrip("/") + "/#/tasks/" + request.task_id if base else None,
                            task_id=request.task_id, max_steps=request.max_steps,
                            started_at=started, finished_at=datetime.now(timezone.utc).isoformat())
            self.last_run_audit.update(comparison_meta=metadata, before_snapshot=before,
                                      after_repair_snapshot=knowledge_snapshot(self.database))


def create_comparison_app(*, arm_dir, mode, label, group, registry=None, public_base_url="",
                          old_code=None, reference_database=None, task_timeout=900, llm_timeout=90):
    arm_dir = Path(arm_dir).resolve()
    cache = (ROOT / ".cache").resolve()
    if not arm_dir.is_relative_to(cache) or arm_dir == cache:
        raise ValueError("Comparison arm data must be under the project's .cache directory")
    if mode not in {"before", "after", "contextual"}:
        raise ValueError("Unknown comparison mode")
    if reference_database is not None:
        reference_database = Path(reference_database).resolve(strict=True)
        if mode == "before" or not reference_database.is_relative_to(cache):
            raise ValueError("Frozen retrieval is available only for current-kernel comparisons under .cache")
    arm_dir.mkdir(parents=True, exist_ok=True)
    database = arm_dir / "app.db"
    workspace = arm_dir / "workspaces"
    # Even main.py's unused module-level default app must not point at production.
    os.environ.update(DATABASE_URL="sqlite:///" + str(database), WORKSPACE_ROOT=str(workspace),
                      LLM_CONFIG_SOURCE="project", LLM_CONFIG_FILE=str(ROOT / ".env"))
    from backend.config import Settings
    from backend.agent.core import Agent
    from backend.agent.llm import ChatClient
    from backend.api.main import create_app
    from scripts.compare_legacy_bridge import LegacyAgentBridge
    from scripts.compare_legacy_worker import OLD_REVISION, kernel_identity

    if "knowledge_automation_enabled" not in Settings.__dataclass_fields__:
        raise RuntimeError("Comparison requires the explicit per-instance automation setting")
    configured = Settings.from_env(llm_env_file=ROOT / ".env")
    settings_kwargs = dict(database_path=database, workspace_root=workspace, projects={}, project_definitions={},
        task_timeout=task_timeout, llm_timeout=llm_timeout, use_experience=True,
        llm_trust_env=False, knowledge_automation_enabled=mode != "before",
        enable_knowledge_models=configured.enable_knowledge_models if mode != "before" else False,
        experience_retrieval_strategy="contextual" if mode == "contextual" else "adaptive")
    if registry is not None:
        settings_kwargs["task_metrics_registry"] = Path(registry).resolve(strict=True)
    settings = replace(configured, **settings_kwargs)
    old_code = Path(old_code or ROOT / ".cache/real-update-comparison/before").resolve()
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                          encoding="utf-8", check=True).stdout.strip()
    if mode == "before":
        identity = kernel_identity(old_code)
    else:
        identity = {"git_head": head, "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("backend/agent/core.py", "backend/experience/retrieval.py", "backend/experience/recovery.py",
                         "backend/experience/automation.py", "backend/experience/automatic_resolution.py")}}
    metadata = dict(runtime_label=label, code_revision=OLD_REVISION if mode == "before" else head,
                    comparison_group=group, variant=mode, task_public_base_url=public_base_url,
                    adapter="ordinary-upload-and-identical-tools-json-rpc-v1" if mode == "before" else None,
                    initial_baseline="ordinary-upload-preflight", initial_baseline_in_model_context=mode != "before")
    if reference_database is not None:
        metadata.update(reference_database=str(reference_database),
                        reference_sha256=hashlib.sha256(reference_database.read_bytes()).hexdigest(),
                        reference_access="read-only frozen snapshot; writes go to arm output database")
    provenance = dict(metadata, kernel=identity, database=str(database), workspace_root=str(workspace),
        model_config_file=settings.llm_config_file, model=settings.llm_model, base_url=settings.llm_base_url,
        task_timeout=task_timeout, llm_timeout=llm_timeout, automatic_knowledge_workers=mode != "before",
        service_started_at=datetime.now(timezone.utc).isoformat())
    (arm_dir / "service-provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")

    def factory(tools, store):
        if mode == "before":
            delegate = LegacyAgentBridge(settings, tools, old_code=old_code, project_root=ROOT, output_root=arm_dir)
        else:
            if reference_database is not None:
                from scripts.experiment import FrozenSearchStore
                store = FrozenSearchStore(reference_database, store)
            delegate = Agent(ChatClient(settings), tools.execute_tool, store, use_experience=True,
                             timeout_seconds=settings.task_timeout, verify_before=True,
                             retrieval_strategy=settings.experience_retrieval_strategy)
        return ComparisonAuditAgent(delegate, settings.database_path, metadata)

    def multi_factory(*args):
        raise ValueError("The registered before/after comparison uses single-agent tasks")

    app = create_app(settings, agent_factory=factory, multi_agent_factory=multi_factory)
    app.state.comparison_metadata = metadata
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("before", "after", "contextual"), required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--public-base-url", default="")
    parser.add_argument("--old-code", type=Path)
    parser.add_argument("--reference-database", type=Path)
    parser.add_argument("--task-timeout", type=float, default=900)
    parser.add_argument("--llm-timeout", type=float, default=90)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    import uvicorn
    options = vars(args)
    port = options.pop("port")
    uvicorn.run(create_comparison_app(**options), host="127.0.0.1", port=port, workers=1)


if __name__ == "__main__":
    main()
