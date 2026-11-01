"""Read-only, task-bound code comparison contracts."""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from .models import SharedModel, TaskStatus, _canonical_uuid

CodeText = Annotated[str, StringConstraints(strip_whitespace=False, max_length=200_000)]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class CodeChangeInfo(SharedModel):
    path: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=1000)]
    change_type: Literal["modified", "added", "deleted"]
    before_sha256: Digest | None
    after_sha256: Digest | None
    available: bool
    unavailable_reason: str | None = None


class StoredCodeChange(CodeChangeInfo):
    before_content: CodeText | None
    after_content: CodeText | None


class CodeSnapshot(SharedModel):
    task_id: str
    files: list[StoredCodeChange] = Field(max_length=100)
    omitted_files: int = Field(default=0, ge=0)

    _id = field_validator("task_id")(_canonical_uuid)


class CodeChangesIndex(SharedModel):
    task_id: str
    status: TaskStatus
    mode: Literal["snapshot", "recorded_writes"]
    notice: str
    files: list[CodeChangeInfo] = Field(max_length=100)
    omitted_files: int = Field(default=0, ge=0)

    _id = field_validator("task_id")(_canonical_uuid)


class DiffLine(SharedModel):
    kind: Literal["context", "added", "removed"]
    old_line: int | None = Field(default=None, ge=1)
    new_line: int | None = Field(default=None, ge=1)
    content: CodeText


class DiffHunk(SharedModel):
    old_start: int = Field(ge=0)
    old_lines: int = Field(ge=0)
    new_start: int = Field(ge=0)
    new_lines: int = Field(ge=0)
    lines: list[DiffLine]


class CodeDiffFile(StoredCodeChange):
    task_id: str
    mode: Literal["snapshot", "recorded_writes"]
    notice: str
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    format_only: bool
    hunks: list[DiffHunk]

    _id = field_validator("task_id")(_canonical_uuid)
