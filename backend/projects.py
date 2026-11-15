"""Server-owned catalog of trusted local projects and repository identities."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from backend.languages import ProjectLanguage, MARKERS, detect_language


class RunnerConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    python_executable: str | None = None
    python_version: str | None = Field(default=None, pattern=r'^3\.\d+$')
    requirements_file: str | None = None
    python_paths: list[str] = Field(default_factory=lambda: ['.', 'src'], max_length=10)
    test_targets: list[str] = Field(default_factory=list, max_length=100)
    deselect: list[str] = Field(default_factory=list, max_length=100)
    pytest_plugins: list[str] = Field(default_factory=list, max_length=10)
    timeout_seconds: int = Field(default=300, ge=10, le=900)

    @field_validator('python_paths', 'test_targets', 'deselect')
    @classmethod
    def relative_paths(cls, values):
        for value in values:
            path = value.split('::', 1)[0]
            if (not value or len(value) > 500 or value.startswith('-') or '\\' in value
                    or ':' in path or Path(path).is_absolute() or '..' in Path(path).parts
                    or any(ord(c) < 32 for c in value)):
                raise ValueError('Test paths must be relative project paths, not shell commands')
        return values

    @field_validator('pytest_plugins')
    @classmethod
    def plugin_names(cls, values):
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*', value) for value in values):
            raise ValueError('Invalid pytest plugin name')
        return values

    @field_validator('python_executable')
    @classmethod
    def interpreter(cls, value):
        if value and (not Path(value).is_absolute() or not re.fullmatch(r'python(?:\d+(?:\.\d+)*)?(?:\.exe)?', Path(value).name, re.I)):
            raise ValueError('Select an absolute Python interpreter path')
        return value

    @field_validator('requirements_file')
    @classmethod
    def requirements_path(cls, value):
        if value:
            cls.relative_paths([value])
        return value


class ProjectDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    label: str = Field(min_length=1, max_length=200)
    language: ProjectLanguage
    repository_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    source: Path
    task_example: str = Field(default="", max_length=50_000)
    experience_contract: str | None = Field(default=None, min_length=1, max_length=200)
    expected_tests: int | None = Field(default=None, ge=1)
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    runner_config: RunnerConfig = Field(default_factory=RunnerConfig)
    origin: Literal['registered', 'upload'] = 'registered'
    source_digest: str | None = None

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values):
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value) for value in values):
            raise ValueError("capabilities must be exact registered identifiers")
        return list(dict.fromkeys(values))

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
    if detect_language(project.source) != project.language:
        raise ValueError("Registered language must match one unambiguous project build marker")


_SOURCE_REVISION = re.compile(r"(?:git:(?:[a-f0-9]{40}|[a-f0-9]{64});)?tree:([a-f0-9]{64})")


def same_source_revision(left: str | None, right: str | None) -> bool:
    """Match immutable source content without rewriting historical Git provenance.

    Git metadata can change without changing the verified project files. Only
    complete, generated revision strings share this compatibility rule; named
    business contracts and other opaque revisions still require exact equality.
    """
    if not isinstance(left, str) or not isinstance(right, str) or not left or not right:
        return False
    if left == right:
        return True
    first, second = _SOURCE_REVISION.fullmatch(left), _SOURCE_REVISION.fullmatch(right)
    return bool(first and second and first.group(1) == second.group(1))


def source_revision(path: Path, baseline: dict[str, str] | None = None) -> str:
    # The file digest identifies dirty working trees and repositories without Git too.
    from backend.tools.workspace import inventory
    fingerprint = hashlib.sha256(json.dumps(inventory(path) if baseline is None else baseline, sort_keys=True).encode()).hexdigest()
    try:
        process = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel", "HEAD"],
                                 capture_output=True, text=True, encoding="utf-8", timeout=5, check=False)
        lines = process.stdout.strip().splitlines()
        # Git walks up parent directories. An uploaded ZIP or monorepo child
        # must not inherit the hosting application's unrelated commit identity.
        if (process.returncode == 0 and len(lines) == 2
                and Path(lines[0]).resolve() == path.resolve()
                and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", lines[1])):
            return f"git:{lines[1]};tree:{fingerprint}"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return f"tree:{fingerprint}"


def public_project(project: ProjectDefinition, revision: str | None = None) -> dict:
    return {"id": project.id, "label": project.label, "language": project.language,
            "repository_id": project.repository_id, "source_revision": revision,
            "task_example": project.task_example, "capabilities": project.capabilities,
            "origin": project.origin, "source_digest": project.source_digest,
            "runner_config": project.runner_config.model_dump()}
