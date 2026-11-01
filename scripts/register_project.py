"""Register a trusted local Java/Python repository in a private server catalog."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.projects import ProjectDefinition, load_catalog, validate_project_language
from backend.tools.workspace import WorkspaceManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True)
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--language", required=True, choices=["java", "python"])
    parser.add_argument("--repository", required=True, help="Stable repository ID shared by its registered projects")
    parser.add_argument("--label")
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--example", default="修复项目中的异常并通过全部测试，保持正常行为。")
    parser.add_argument("--registry", type=Path, default=ROOT / "runtime/projects.json")
    args = parser.parse_args()
    project = ProjectDefinition(id=args.id, label=args.label or args.id, language=args.language,
                                repository_id=args.repository, source=args.path.absolute(), task_example=args.example,
                                capabilities=args.capability)
    WorkspaceManager(ROOT / "runtime/workspaces", {project.id: project.source}).validate_source(project.source)
    try:
        validate_project_language(project)
    except ValueError as exc:
        parser.error(str(exc))
    registry = args.registry.absolute()
    entries = load_catalog(registry, ROOT) if registry.exists() else {}
    entries[project.id] = project
    registry.parent.mkdir(parents=True, exist_ok=True)
    candidate = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=registry.parent, delete=False) as stream:
            candidate = Path(stream.name)
            json.dump([entry.model_dump(mode="json") for entry in entries.values()], stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(candidate, registry)
    finally:
        if candidate is not None and candidate.exists():
            candidate.unlink()
    print(f"Registered {project.id} ({project.language}) in {registry}")
    print("Set PROJECT_REGISTRY to this catalog in the local .env and restart the backend.")


if __name__ == "__main__":
    main()
