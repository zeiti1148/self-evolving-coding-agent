from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.shared.errors import ErrorCode
from backend.shared.models import (
    KNOWN_TOOL_NAMES,
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
TEST_CALL_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


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


def make_final_test_call() -> ToolCall:
    return ToolCall(id=TEST_CALL_ID, name="run_tests")


def make_final_test_result() -> ToolResult:
    return ToolResult(call_id=TEST_CALL_ID, tool_name="run_tests", success=True)


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


@pytest.mark.parametrize(
    "output",
    ["    return value;", "return value;\n", " \t\n  ",
     "    public String greet() {\n        return value;\n    }\n"],
)
def test_tool_output_preserves_original_whitespace(output: str) -> None:
    result = ToolResult(
        call_id=CALL_ID, tool_name="read_file", success=True, output=output
    )
    assert result.output == output
    assert ToolResult.model_validate_json(result.model_dump_json()).output == output


def test_unknown_tool_names_are_preserved_in_calls_results_and_steps() -> None:
    # Independently decoded strings must compare by value, not object identity.
    name = "custom_unknown_tool_name"
    other_name = bytearray(name, "utf-8").decode("utf-8")
    assert name is not other_name
    call = ToolCall(id=CALL_ID, name=name, arguments={"command": ["forbidden"]})
    result = ToolResult(
        call_id=CALL_ID,
        tool_name=other_name,
        success=False,
        error_code=ErrorCode.INVALID_TOOL_CALL,
        error_message="Unknown tool; no operation was executed.",
    )
    step = TaskStep(sequence=1, call=call, result=result)
    assert call.name == result.tool_name == name
    assert name not in KNOWN_TOOL_NAMES
    assert TaskStep.model_validate_json(step.model_dump_json()) == step
    terminal = AgentResult(
        task_id=TASK_ID, status="failed", summary="Rejected an unknown tool.",
        repair_succeeded=False, steps=[call], tool_results=[result],
    )
    assert AgentResult.model_validate_json(terminal.model_dump_json()) == terminal


@pytest.mark.parametrize("name", ["", " ", "\t\n"])
def test_tool_names_reject_empty_or_blank_strings(name: str) -> None:
    with pytest.raises(ValidationError):
        ToolCall(id=CALL_ID, name=name)
    with pytest.raises(ValidationError):
        ToolResult(call_id=CALL_ID, tool_name=name, success=True)


def test_known_tool_names_still_form_the_dispatch_allowlist() -> None:
    assert KNOWN_TOOL_NAMES == {
        "list_files", "read_file", "write_file", "run_command", "run_tests"
    }
    assert make_call().name in KNOWN_TOOL_NAMES


def test_unknown_tool_name_is_not_normalized_into_a_known_tool() -> None:
    call = ToolCall(id=CALL_ID, name=" run_tests ")
    assert call.name == " run_tests "
    assert call.name not in KNOWN_TOOL_NAMES


def test_tool_call_ids_still_require_internal_uuids() -> None:
    with pytest.raises(ValidationError):
        ToolCall(id="model_call_123", name="read_file")
    with pytest.raises(ValidationError):
        ToolResult(call_id="model_call_123", tool_name="read_file", success=True)


def test_other_text_fields_remain_validated() -> None:
    with pytest.raises(ValidationError):
        ToolResult(
            call_id=CALL_ID, tool_name="read_file", success=False,
            error_code=ErrorCode.FILE_NOT_FOUND, error_message=" \t\n",
        )
    with pytest.raises(ValidationError):
        ExperienceCreate(
            task_summary=" ", root_cause="cause", solution="fix",
            verification="mvn test",
        )


def test_tool_output_length_limit_is_still_enforced() -> None:
    with pytest.raises(ValidationError):
        ToolResult(
            call_id=CALL_ID, tool_name="read_file", success=True,
            output=" " * 200_001,
        )


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
        steps=[make_final_test_call()],
        tool_results=[make_final_test_result()],
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
        steps=[make_final_test_call()],
        tool_results=[make_final_test_result()],
    )
    assert saved.experience_id == EXPERIENCE_ID


@pytest.mark.parametrize("tests_passed", [False, None])
def test_succeeded_requires_passing_final_tests(tests_passed: bool | None) -> None:
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID, status="succeeded", summary="Invalid success.",
            repair_succeeded=True, tests_passed=tests_passed,
            experience_saved=True, experience_id=EXPERIENCE_ID,
            steps=[make_final_test_call()], tool_results=[make_final_test_result()],
        )


def test_succeeded_requires_repair_success_even_if_tests_pass() -> None:
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID, status="succeeded", summary="No successful repair.",
            repair_succeeded=False, tests_passed=True,
            experience_saved=True, experience_id=EXPERIENCE_ID,
            steps=[make_final_test_call()], tool_results=[make_final_test_result()],
        )


@pytest.mark.parametrize("status", ["failed", "stopped"])
@pytest.mark.parametrize("tests_passed", [True, False, None])
def test_non_success_status_preserves_test_facts(
    status: str, tests_passed: bool | None,
) -> None:
    result = AgentResult(
        task_id=TASK_ID, status=status, summary="Record observed test facts.",
        repair_succeeded=False, tests_passed=tests_passed,
    )
    assert result.tests_passed is tests_passed
    assert result.experience_saved is None
    assert AgentResult.model_validate_json(result.model_dump_json()) == result


def test_unfinished_final_verification_defaults_to_none() -> None:
    result = AgentResult(
        task_id=TASK_ID, status="stopped", summary="No final verification.",
        repair_succeeded=False,
    )
    assert result.tests_passed is None


@pytest.mark.parametrize("status", ["succeeded", "failed", "stopped"])
@pytest.mark.parametrize("tests_passed", [False, None])
def test_repair_success_requires_passing_tests_in_every_status(
    status: str, tests_passed: bool | None,
) -> None:
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID, status=status, summary="Unverified repair.",
            repair_succeeded=True, tests_passed=tests_passed,
            experience_saved=False if status == "succeeded" else None,
            experience_save_error="Extraction failed." if status == "succeeded" else None,
        )


def test_repair_success_requires_recorded_final_test_evidence() -> None:
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID, status="succeeded", summary="Missing test evidence.",
            repair_succeeded=True, tests_passed=True,
            experience_saved=True, experience_id=EXPERIENCE_ID,
        )


@pytest.mark.parametrize("tool_name", ["write_file", "run_command"])
def test_modification_after_tests_invalidates_success_evidence(tool_name: str) -> None:
    write_call = ToolCall(id=CALL_ID, name=tool_name)
    write_result = ToolResult(call_id=CALL_ID, tool_name=tool_name, success=True)
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID, status="succeeded", summary="Outdated test evidence.",
            repair_succeeded=True, tests_passed=True,
            experience_saved=True, experience_id=EXPERIENCE_ID,
            steps=[make_final_test_call(), write_call],
            tool_results=[make_final_test_result(), write_result],
        )


@pytest.mark.parametrize("tool_name", ["read_file", "list_files"])
def test_readonly_tools_do_not_invalidate_final_evidence(tool_name: str) -> None:
    call = ToolCall(id=CALL_ID, name=tool_name)
    tool_result = ToolResult(call_id=CALL_ID, tool_name=tool_name, success=True)
    result = AgentResult(
        task_id=TASK_ID, status="succeeded", summary="Verified repair inspected.",
        repair_succeeded=True, tests_passed=True,
        experience_saved=True, experience_id=EXPERIENCE_ID,
        steps=[make_final_test_call(), call],
        tool_results=[make_final_test_result(), tool_result],
    )
    assert result.repair_succeeded


def test_reverification_after_writing_restores_valid_evidence() -> None:
    write_call = ToolCall(id=CALL_ID, name="write_file")
    write_result = ToolResult(call_id=CALL_ID, tool_name="write_file", success=True)
    result = AgentResult(
        task_id=TASK_ID, status="succeeded", summary="Modified code reverified.",
        repair_succeeded=True, tests_passed=True,
        experience_saved=True, experience_id=EXPERIENCE_ID,
        steps=[write_call, make_final_test_call()],
        tool_results=[write_result, make_final_test_result()],
    )
    assert result.repair_succeeded


def test_failed_final_test_is_not_success_evidence() -> None:
    failed_test = ToolResult(
        call_id=TEST_CALL_ID, tool_name="run_tests", success=False,
        error_code=ErrorCode.TEST_FAILED, error_message="One test failed.",
    )
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID, status="succeeded", summary="Incorrect test fact.",
            repair_succeeded=True, tests_passed=True,
            experience_saved=True, experience_id=EXPERIENCE_ID,
            steps=[make_final_test_call()], tool_results=[failed_test],
        )


def test_earlier_passing_tests_do_not_override_later_failed_tests() -> None:
    later_test = ToolCall(id=CALL_ID, name="run_tests")
    later_result = ToolResult(
        call_id=CALL_ID, tool_name="run_tests", success=False,
        error_code=ErrorCode.TEST_FAILED, error_message="Regression detected.",
    )
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID, status="succeeded", summary="Old evidence cannot win.",
            repair_succeeded=True, tests_passed=True,
            experience_saved=True, experience_id=EXPERIENCE_ID,
            steps=[make_final_test_call(), later_test],
            tool_results=[make_final_test_result(), later_result],
        )


def test_stopped_can_preserve_verified_repair_without_saving_experience() -> None:
    result = AgentResult(
        task_id=TASK_ID, status="stopped", summary="Stopped at decision budget.",
        repair_succeeded=True, tests_passed=True,
        steps=[make_final_test_call()], tool_results=[make_final_test_result()],
    )
    assert result.status is TaskStatus.STOPPED
    assert result.tests_passed is True
    assert result.experience_saved is None


@pytest.mark.parametrize(
    "experience_fields",
    [
        {"experience_saved": True},
        {"experience_saved": False},
        {"experience_saved": None},
        {"experience_saved": True, "experience_id": EXPERIENCE_ID,
         "experience_save_error": "Contradictory error."},
    ],
)
def test_successful_task_requires_consistent_experience_outcome(
    experience_fields: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID, status="succeeded", summary="Invalid experience outcome.",
            repair_succeeded=True, tests_passed=True,
            steps=[make_final_test_call()], tool_results=[make_final_test_result()],
            **experience_fields,
        )


def test_successful_result_json_round_trip_with_final_evidence() -> None:
    result = AgentResult(
        task_id=TASK_ID, status="succeeded", summary="Verified repair.",
        repair_succeeded=True, tests_passed=True,
        experience_saved=False, experience_save_error="Extraction validation failed.",
        steps=[make_final_test_call()], tool_results=[make_final_test_result()],
    )
    assert AgentResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("status", ["failed", "stopped"])
def test_non_successful_task_cannot_save_successful_experience(status: str) -> None:
    with pytest.raises(ValidationError):
        AgentResult(
            task_id=TASK_ID, status=status, summary="No success experience allowed.",
            repair_succeeded=False, tests_passed=True,
            experience_saved=True, experience_id=EXPERIENCE_ID,
        )


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
