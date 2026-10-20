from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.shared.errors import ErrorCode
from backend.shared.models import (
    AgentRequest,
    AgentResult,
    Experience,
    ExperienceCreate,
    TaskStatus,
    TaskStatusResponse,
    TaskStep,
    TaskStepsResponse,
    ToolCall,
    ToolName,
    ToolResult,
)


TASK_ID = "12345678-1234-5678-1234-567812345678"
CALL_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EXPERIENCE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def make_call() -> ToolCall:
    return ToolCall(
        id=CALL_ID,
        name=ToolName.READ_FILE,
        arguments={"path": "src/Main.java"},
    )


def make_success_result() -> ToolResult:
    return ToolResult(
        call_id=CALL_ID,
        tool_name=ToolName.READ_FILE,
        success=True,
        output="class Main {}",
        metadata={"truncated": False},
    )


def test_normal_models_are_created_with_defaults() -> None:
    request = AgentRequest(
        task_id=TASK_ID,
        task_description="Fix a null dereference",
        workspace=Path("/tmp/tasks/example"),
    )
    experience = ExperienceCreate(
        task_summary="Fix greeting",
        error_type="NullPointerException",
        root_cause="The argument is dereferenced without a null check.",
        solution="Handle null before trimming the argument.",
        verification="Run the Maven test suite.",
        tags=["Java", "java", " null "],
        source_task_id=TASK_ID,
    )

    assert request.max_steps == 20
    assert experience.tags == ["Java", "null"]
    assert make_call().arguments["path"] == "src/Main.java"


def test_required_fields_and_bounds_are_validated() -> None:
    with pytest.raises(ValidationError):
        ToolCall(name=ToolName.READ_FILE)  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        AgentRequest(
            task_id=TASK_ID,
            task_description=" ",
            workspace=Path("relative/path"),
            max_steps=0,
        )


@pytest.mark.parametrize(
    "values",
    [
        {
            "success": True,
            "error_code": ErrorCode.FILE_NOT_FOUND,
            "error_message": "unexpected error",
        },
        {"success": False, "error_code": None, "error_message": None},
        {
            "success": False,
            "error_code": ErrorCode.FILE_NOT_FOUND,
            "error_message": None,
        },
    ],
)
def test_tool_result_success_and_error_fields_must_agree(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ToolResult(
            call_id=CALL_ID,
            tool_name=ToolName.READ_FILE,
            output="",
            **values,
        )


def test_failed_tool_result_is_valid_when_error_is_complete() -> None:
    result = ToolResult(
        call_id=CALL_ID,
        tool_name=ToolName.READ_FILE,
        success=False,
        error_code=ErrorCode.FILE_NOT_FOUND,
        error_message="File does not exist.",
    )

    assert result.success is False
    assert result.error_code is ErrorCode.FILE_NOT_FOUND


def test_json_serialization_round_trip() -> None:
    experience = Experience(
        id=EXPERIENCE_ID,
        task_summary="Fix greeting",
        error_type="NullPointerException",
        root_cause="Missing null handling.",
        solution="Check for null before trim.",
        verification="Maven tests pass.",
        tags=["java", "null"],
        source_task_id=TASK_ID,
        created_at=datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc),
    )

    restored = Experience.model_validate_json(experience.model_dump_json())

    assert restored == experience
    assert restored.created_at.tzinfo is not None


def test_agent_result_rejects_nonterminal_or_inconsistent_status() -> None:
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID,
            status=TaskStatus.RUNNING,
            summary="Still working",
            repair_succeeded=False,
            tests_passed=False,
        )

    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID,
            status=TaskStatus.SUCCEEDED,
            summary="Inconsistent result",
            repair_succeeded=True,
            tests_passed=False,
            experience_saved=True,
            experience_id=EXPERIENCE_ID,
        )


def test_agent_result_requires_one_matching_result_per_call() -> None:
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID,
            status=TaskStatus.FAILED,
            summary="A tool call did not produce a result.",
            steps=[make_call()],
            repair_succeeded=False,
            tests_passed=False,
        )


def test_experience_save_failure_keeps_successful_repair() -> None:
    result = AgentResult(
        task_id=TASK_ID,
        status=TaskStatus.SUCCEEDED,
        summary="Code fixed and tests passed; experience storage failed.",
        repair_succeeded=True,
        tests_passed=True,
        experience_saved=False,
        experience_save_error="SQLite database is unavailable.",
        changed_files=["src/main/java/GreetingService.java"],
    )

    assert result.status is TaskStatus.SUCCEEDED
    assert result.repair_succeeded is True
    assert result.tests_passed is True
    assert result.experience_saved is False
    assert result.experience_id is None


def test_experience_save_status_fields_are_consistent() -> None:
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID,
            status=TaskStatus.SUCCEEDED,
            summary="Invalid experience state",
            repair_succeeded=True,
            tests_passed=True,
            experience_saved=False,
            experience_id=EXPERIENCE_ID,
            experience_save_error="Storage failed.",
        )

    saved = AgentResult(
        task_id=TASK_ID,
        status=TaskStatus.SUCCEEDED,
        summary="Everything succeeded.",
        repair_succeeded=True,
        tests_passed=True,
        experience_saved=True,
        experience_id=EXPERIENCE_ID,
    )
    assert saved.experience_id == EXPERIENCE_ID


def test_completed_task_step_requires_matching_call_and_result() -> None:
    step = TaskStep(sequence=1, call=make_call(), result=make_success_result())
    response = TaskStepsResponse(
        task_id=TASK_ID,
        status=TaskStatus.RUNNING,
        steps=[step],
    )
    assert response.steps[0].sequence == 1

    with pytest.raises(ValidationError):
        TaskStep(
            sequence=1,
            call=make_call(),
            result=make_success_result().model_copy(
                update={"call_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"}
            ),
        )


def test_task_status_response_validates_lifecycle() -> None:
    now = datetime.now(timezone.utc)
    running = TaskStatusResponse(
        task_id=TASK_ID,
        status=TaskStatus.RUNNING,
        created_at=now,
        started_at=now,
    )
    assert running.result is None

    with pytest.raises(ValidationError):
        TaskStatusResponse(
            task_id=TASK_ID,
            status=TaskStatus.PENDING,
            created_at=now,
            started_at=now,
        )
