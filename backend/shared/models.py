"""Pydantic v2 models shared by the API, Agent, tools, and experience modules."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ErrorCode


MAX_TEXT_LENGTH = 50_000
MAX_OUTPUT_LENGTH = 200_000
MAX_STEPS = 100


def _canonical_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("must be a valid UUID string") from exc


def _require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("must include timezone information")
    return value


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        cleaned = tag.strip()
        if not cleaned:
            raise ValueError("tags must not contain blank values")
        key = cleaned.casefold()
        if key not in seen:
            normalized.append(cleaned)
            seen.add(key)
    return normalized


def _validate_relative_file(path: str) -> str:
    if "\\" in path:
        raise ValueError("must use a workspace-relative POSIX path")
    parsed = PurePosixPath(path)
    if path in {"", "."} or parsed.is_absolute() or ".." in parsed.parts:
        raise ValueError("must be a file path relative to the workspace")
    return path


class SharedModel(BaseModel):
    """Strict defaults for all public data contracts."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class ToolName(str, Enum):
    LIST_FILES = "list_files"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    RUN_COMMAND = "run_command"
    RUN_TESTS = "run_tests"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"


class ToolCall(SharedModel):
    id: str
    name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id")(_canonical_uuid)


class ToolResult(SharedModel):
    call_id: str
    tool_name: ToolName
    success: bool
    output: str = Field(default="", max_length=MAX_OUTPUT_LENGTH)
    error_code: ErrorCode | None = None
    error_message: str | None = Field(default=None, min_length=1, max_length=2_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_call_id = field_validator("call_id")(_canonical_uuid)

    @model_validator(mode="after")
    def validate_success_and_error(self) -> ToolResult:
        has_error = self.error_code is not None or self.error_message is not None
        if self.success and has_error:
            raise ValueError("successful tool results must not contain error fields")
        if not self.success and (
            self.error_code is None or self.error_message is None
        ):
            raise ValueError(
                "failed tool results require both error_code and error_message"
            )
        return self


class ExperienceCreate(SharedModel):
    task_summary: str = Field(min_length=1, max_length=1_000)
    error_type: str | None = Field(default=None, min_length=1, max_length=200)
    root_cause: str = Field(min_length=1, max_length=5_000)
    solution: str = Field(min_length=1, max_length=10_000)
    verification: str = Field(min_length=1, max_length=5_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_task_id: str | None = None

    _validate_source_task_id = field_validator("source_task_id")(
        lambda value: _canonical_uuid(value) if value is not None else value
    )
    _normalize_experience_tags = field_validator("tags")(_normalize_tags)


class Experience(ExperienceCreate):
    id: str
    created_at: datetime

    _validate_id = field_validator("id")(_canonical_uuid)
    _validate_created_at = field_validator("created_at")(_require_aware_datetime)


class AgentRequest(SharedModel):
    task_id: str
    task_description: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    workspace: Path
    max_steps: int = Field(default=20, ge=1, le=MAX_STEPS)

    _validate_task_id = field_validator("task_id")(_canonical_uuid)

    @field_validator("workspace")
    @classmethod
    def validate_workspace(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("workspace must be an absolute managed path")
        return value


class AgentResult(SharedModel):
    task_id: str
    status: TaskStatus
    summary: str = Field(min_length=1, max_length=10_000)
    steps: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    repair_succeeded: bool
    tests_passed: bool
    changed_files: list[str] = Field(default_factory=list)
    experience_saved: bool | None = None
    experience_id: str | None = None
    experience_save_error: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_000,
    )
    error_message: str | None = Field(default=None, min_length=1, max_length=5_000)

    _validate_task_id = field_validator("task_id")(_canonical_uuid)
    _validate_experience_id = field_validator("experience_id")(
        lambda value: _canonical_uuid(value) if value is not None else value
    )
    _validate_changed_files = field_validator("changed_files")(
        lambda paths: [_validate_relative_file(path) for path in paths]
    )

    @model_validator(mode="after")
    def validate_result_state(self) -> AgentResult:
        if self.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            raise ValueError("AgentResult status must be terminal")

        succeeded = self.status is TaskStatus.SUCCEEDED
        if succeeded != self.repair_succeeded or succeeded != self.tests_passed:
            raise ValueError(
                "succeeded status, repair_succeeded, and tests_passed must agree"
            )

        if succeeded and self.experience_saved is None:
            raise ValueError(
                "successful repairs must report whether experience saving succeeded"
            )
        if not succeeded and self.experience_saved is not None:
            raise ValueError(
                "experience saving must not be attempted for unsuccessful repairs"
            )

        if self.experience_saved is True:
            if self.experience_id is None or self.experience_save_error is not None:
                raise ValueError(
                    "saved experience requires experience_id and no save error"
                )
        elif self.experience_saved is False:
            if self.experience_id is not None or self.experience_save_error is None:
                raise ValueError(
                    "failed experience saving requires an error and no experience_id"
                )
        elif self.experience_id is not None or self.experience_save_error is not None:
            raise ValueError(
                "experience fields must be empty when saving was not attempted"
            )

        call_ids = [call.id for call in self.steps]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool call ids must be unique")
        result_ids = [result.call_id for result in self.tool_results]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("tool result call ids must be unique")
        if set(result_ids) != set(call_ids):
            raise ValueError("tool calls and results must have matching ids")
        call_names = {call.id: call.name for call in self.steps}
        if any(
            call_names[result.call_id] is not result.tool_name
            for result in self.tool_results
        ):
            raise ValueError("tool calls and results must have matching names")
        return self


class TaskStep(SharedModel):
    sequence: int = Field(ge=1)
    call: ToolCall
    result: ToolResult
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    _validate_completed_at = field_validator("completed_at")(
        _require_aware_datetime
    )

    @model_validator(mode="after")
    def validate_call_result_pair(self) -> TaskStep:
        if self.call.id != self.result.call_id:
            raise ValueError("step call and result ids must match")
        if self.call.name is not self.result.tool_name:
            raise ValueError("step call and result tool names must match")
        return self


class TaskCreateRequest(SharedModel):
    task_description: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    source_project: str = Field(min_length=1, max_length=1_000)
    max_steps: int = Field(default=20, ge=1, le=MAX_STEPS)


class TaskCreateResponse(SharedModel):
    task_id: str
    status: Literal[TaskStatus.PENDING] = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    _validate_task_id = field_validator("task_id")(_canonical_uuid)
    _validate_created_at = field_validator("created_at")(_require_aware_datetime)


class TaskStatusResponse(SharedModel):
    task_id: str
    status: TaskStatus
    steps_completed: int = Field(default=0, ge=0)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: AgentResult | None = None
    error_message: str | None = Field(default=None, min_length=1, max_length=5_000)

    _validate_task_id = field_validator("task_id")(_canonical_uuid)
    _validate_timestamps = field_validator(
        "created_at", "started_at", "finished_at"
    )(lambda value: _require_aware_datetime(value) if value is not None else value)

    @model_validator(mode="after")
    def validate_status_timeline(self) -> TaskStatusResponse:
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at must not precede created_at")
        if self.finished_at is not None:
            lower_bound = self.started_at or self.created_at
            if self.finished_at < lower_bound:
                raise ValueError("finished_at must not precede task start")

        if self.status is TaskStatus.PENDING:
            if any((self.started_at, self.finished_at, self.result)):
                raise ValueError("pending tasks cannot contain execution state")
        elif self.status is TaskStatus.RUNNING:
            if self.started_at is None or self.finished_at is not None or self.result:
                raise ValueError("running tasks require only started_at")
        else:
            if self.finished_at is None:
                raise ValueError("terminal tasks require finished_at")
            if self.status is TaskStatus.SUCCEEDED and self.result is None:
                raise ValueError("succeeded tasks require an AgentResult")

        if self.result is not None:
            if self.result.task_id != self.task_id:
                raise ValueError("task response and result ids must match")
            if self.result.status is not self.status:
                raise ValueError("task response and result statuses must match")
        return self


class TaskStepsResponse(SharedModel):
    task_id: str
    status: TaskStatus
    steps: list[TaskStep] = Field(default_factory=list)

    _validate_task_id = field_validator("task_id")(_canonical_uuid)

    @model_validator(mode="after")
    def validate_step_order(self) -> TaskStepsResponse:
        sequences = [step.sequence for step in self.steps]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("steps must have unique ascending sequence numbers")
        return self
