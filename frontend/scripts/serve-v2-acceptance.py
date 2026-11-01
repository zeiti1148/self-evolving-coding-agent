"""Local Vue acceptance: scripted model, real V2 API/tools/SQLite; no live LLM.

Run from the repository root: python frontend/scripts/serve-v2-acceptance.py
Only the two supported multi-issue fixtures are registered. This is not a
production entry point and must not be used as evidence of live-model ability.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn
from backend.api.main import create_app
from backend.config import Settings
from scripts.acceptance_v2 import ScriptedMultiIssueModel


def acceptance_app(directory: Path):
    base = Settings.from_env()
    fixes = json.loads((ROOT / "backend/tests/fixtures/multi_issue_fixes.json").read_text(encoding="utf-8"))
    projects = {key: base.project(key) for key in fixes}
    settings = replace(base, database_path=directory / "experiences.db",
                       workspace_root=directory / "workspaces",
                       projects={key: project.source for key, project in projects.items()},
                       project_definitions=projects)

    class PerTaskModel:
        def __init__(self):
            self.delegate = None

        def complete(self, messages, *, tools=None):
            if self.delegate is None:
                language = json.loads(messages[1]["content"])["language"]
                project = next(value for value in projects.values() if value.language == language)
                self.delegate = ScriptedMultiIssueModel(fixes[project.id], language)
            return self.delegate.complete(messages, tools=tools)

    return create_app(settings, llm_factory=PerTaskModel)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".cache/frontend-v2-acceptance")
    args = parser.parse_args()
    directory = args.data_dir.resolve()
    print(f"SCRIPTED MODEL ONLY; real tools and SQLite. Data: {directory}", flush=True)
    uvicorn.run(acceptance_app(directory), host="127.0.0.1", port=args.port)
