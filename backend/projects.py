"""Server-owned catalog of trusted local projects and repository identities."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    label: str = Field(min_length=1, max_length=200)
    language: Literal["java", "python"]
    repository_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    source: Path
    task_example: str = Field(default="", max_length=50_000)
    expected_tests: int | None = Field(default=None, ge=1)

    @field_validator("source")
    @classmethod
    def local_path(cls, value: Path) -> Path:
        if not str(value).strip() or "\x00" in str(value):
            raise ValueError("source must be a local project directory")
        return value


def load_catalog(path: Path, root: Path) -> dict[str, ProjectDefinition]:
    if path.stat().st_size > 1_000_000:
        raise ValueError("Project catalog is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) > 100:
        raise ValueError("Project catalog must be an array of at most 100 projects")
    result = {}
    for item in data:
        project = ProjectDefinition.model_validate(item)
        if project.id in result:
            raise ValueError("Project IDs must be unique")
        source = project.source if project.source.is_absolute() else root / project.source
        result[project.id] = project.model_copy(update={"source": source.absolute()})
    return result


def validate_project_language(project: ProjectDefinition) -> None:
    marker = "pom.xml" if project.language == "java" else "pyproject.toml"
    other = "pyproject.toml" if project.language == "java" else "pom.xml"
    if not (project.source / marker).is_file() or (project.source / other).is_file():
        raise ValueError("Registered language must match one unambiguous project build marker")


def source_revision(path: Path, baseline: dict[str, str] | None = None) -> str:
    # The file digest identifies dirty working trees and repositories without Git too.
    from backend.tools.workspace import inventory
    fingerprint = hashlib.sha256(json.dumps(inventory(path) if baseline is None else baseline, sort_keys=True).encode()).hexdigest()
    try:
        process = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=5, check=False)
        head = process.stdout.strip()
        if process.returncode == 0 and re.fullmatch(r"[a-f0-9]{40,64}", head):
            return f"git:{head};tree:{fingerprint}"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return f"tree:{fingerprint}"


def public_project(project: ProjectDefinition, revision: str | None = None) -> dict:
    return {"id": project.id, "label": project.label, "language": project.language,
            "repository_id": project.repository_id, "source_revision": revision,
            "task_example": project.task_example}
