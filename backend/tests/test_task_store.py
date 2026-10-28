from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
from uuid import uuid4

import pytest

from backend.api import store as task_module
from backend.api.store import TaskStore
from backend.experience import ExperienceStore
from backend.shared import AgentResult, ExperienceCreate, TaskCreateRequest, TaskStep, ToolCall, ToolResult


def completed_step(sequence=1):
    call = ToolCall(id=str(uuid4()), name="read_file", arguments={"path": "src/app.py"})
    result = ToolResult(call_id=call.id, tool_name=call.name, success=True,
                        output="def count(): return 1\n", metadata={"details": ["recorded"]})
    return TaskStep(sequence=sequence, call=call, result=result)


def stopped_result(task_id, steps=()):
    return AgentResult(task_id=task_id, status="stopped", summary="Decision budget reached.",
                       repair_succeeded=False, steps=[step.call for step in steps],
                       tool_results=[step.result for step in steps])


@pytest.fixture
def store(tmp_path):
    value = TaskStore(tmp_path / "history.db")
    yield value
    value.close()


def test_terminal_status_steps_request_and_audit_survive_reopen(tmp_path):
    path = tmp_path / "history.db"
    request = TaskCreateRequest(task_description="Fix the count", source_project="demo")
    with closing(TaskStore(path)) as first:
        task_id = first.create(request).task_id
        request.task_description = "Caller changed its copy"
        first.start(task_id)
        step = completed_step()
        first.append(task_id, step)
        first.finish(task_id, stopped_result(task_id, [step]), audit={"model_requests": [{"tokens": 12}]})
        status = first.get(task_id)
        steps = first.get_steps(task_id)
    with closing(TaskStore(path)) as second:
        assert second.get(task_id) == status
        assert second.get_steps(task_id) == steps
        assert second.get_request(task_id).task_description == "Fix the count"
        assert second.get_audit(task_id) == {"model_requests": [{"tokens": 12}]}


def test_restart_stops_only_unfinished_tasks_without_replaying_steps(tmp_path):
    path = tmp_path / "history.db"
    with closing(TaskStore(path)) as first:
        pending, running, finished = [first.create().task_id for _ in range(3)]
        first.start(running)
        step = completed_step()
        first.append(running, step)
        first.put_audit(running, {"decision_rounds": 1})
        first.finish(finished, error="Original scheduling failure.")
        terminal = first.get(finished)
    with closing(TaskStore(path)) as second:
        for task_id in (pending, running):
            value = second.get(task_id)
            assert value.status == "stopped" and value.result is None
            assert value.finished_at >= (value.started_at or value.created_at)
            assert "restart" in value.error_message.lower()
        assert second.get(pending).started_at is None
        assert second.get(running).steps_completed == 1
        assert second.get_steps(running).steps == [step]
        assert second.get_audit(running) == {"decision_rounds": 1}
        assert second.get(finished) == terminal
        interrupted = second.get(running)
    with closing(TaskStore(path)) as third:
        assert third.get(running) == interrupted


def test_constructor_does_not_create_database_until_initialized(tmp_path):
    path = tmp_path / "new-directory" / "history.db"
    value = TaskStore(path)
    assert not path.exists()
    try:
        value.initialize()
        value.initialize()
        assert path.is_file()
        assert value.list_tasks() == []
    finally:
        value.close()


def test_file_snapshots_remain_readable_after_worker_shutdown(tmp_path):
    store = TaskStore(tmp_path / "history.db")
    task_id = store.create().task_id
    store.finish(task_id, stopped_result(task_id), audit={"decision_rounds": 2})
    store.close()
    # Existing experiment scripts collect final audit after FastAPI lifespan exits.
    assert store.get(task_id).status == "stopped"
    assert store.get_audit(task_id) == {"decision_rounds": 2}
    assert store.get_steps(task_id).steps == []
    store.close()


def test_snapshots_do_not_share_mutable_tool_data(store):
    task_id = store.create().task_id
    store.start(task_id)
    step = completed_step()
    store.append(task_id, step)
    step.result.metadata["details"].append("caller mutation")
    returned = store.get_steps(task_id)
    returned.steps[0].call.arguments["path"] = "changed.py"
    returned.steps[0].result.metadata["details"].clear()
    saved = store.get_steps(task_id).steps[0]
    assert saved.call.arguments == {"path": "src/app.py"}
    assert saved.result.metadata == {"details": ["recorded"]}
    store.put_audit(task_id, {"requests": [1]})
    store.get_audit(task_id)["requests"].append(2)
    assert store.get_audit(task_id) == {"requests": [1]}


@pytest.mark.parametrize("change", ["arguments", "output", "metadata"])
def test_final_result_cannot_replace_recorded_tool_evidence(change):
    # The no-argument store also exercises the original in-memory contract.
    store = TaskStore()
    task_id = store.create().task_id
    store.start(task_id)
    step = completed_step()
    store.append(task_id, step)
    forged = step.model_copy(deep=True)
    if change == "arguments":
        forged.call.arguments["path"] = "another.py"
    elif change == "output":
        forged.result.output = "invented output"
    else:
        forged.result.metadata["details"] = ["invented evidence"]
    try:
        with pytest.raises(ValueError, match="trace"):
            store.finish(task_id, stopped_result(task_id, [forged]))
        assert store.get(task_id).status == "running"
        assert store.get_steps(task_id).steps == [step]
    finally:
        store.close()


def test_rejected_transitions_preserve_steps_and_terminal_result(store):
    task_id = store.create().task_id
    with pytest.raises(ValueError):
        store.append(task_id, completed_step())
    store.start(task_id)
    step = completed_step()
    store.append(task_id, step)
    for invalid in (completed_step(3), step.model_copy(update={"sequence": 2})):
        with pytest.raises(ValueError):
            store.append(task_id, invalid)
    assert store.get(task_id).steps_completed == 1
    assert store.get_steps(task_id).steps == [step]
    store.finish(task_id, stopped_result(task_id, [step]))
    terminal = store.get(task_id)
    for action in (lambda: store.start(task_id), lambda: store.append(task_id, completed_step(2)),
                   lambda: store.finish(task_id, error="replacement")):
        with pytest.raises(ValueError):
            action()
    assert store.get(task_id) == terminal


@pytest.mark.parametrize("field,original,changed", [
    ("arguments", {"path": "src/app.py", "start_line": 1}, {"path": "src/app.py", "start_line": True}),
    ("metadata", {"tests": 1}, {"tests": True}),
    ("metadata", {"counts": {"errors": 0}}, {"counts": {"errors": False}}),
])
def test_final_trace_preserves_json_value_types(store, field, original, changed):
    task_id = store.create().task_id
    store.start(task_id)
    step = completed_step()
    setattr(step.call if field == "arguments" else step.result, field, original)
    store.append(task_id, step)
    substituted = step.model_copy(deep=True)
    setattr(substituted.call if field == "arguments" else substituted.result, field, changed)
    with pytest.raises(ValueError, match="trace"):
        store.finish(task_id, stopped_result(task_id, [substituted]))
    assert store.get(task_id).status == "running"
    assert store.get_steps(task_id).steps[0].model_dump_json() == step.model_dump_json()


def test_failed_step_write_rolls_back_step_and_count(tmp_path):
    path = tmp_path / "history.db"
    with closing(TaskStore(path)) as store:
        task_id = store.create().task_id
        store.start(task_id)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_snapshot BEFORE UPDATE ON api_tasks "
                               "BEGIN SELECT RAISE(ABORT, 'injected snapshot write failure'); END")
        with pytest.raises(task_module.TaskStorageError) as error:
            store.append(task_id, completed_step())
        assert "injected" not in str(error.value) and str(path) not in str(error.value)
        assert store.get(task_id).steps_completed == 0
        assert store.get_steps(task_id).steps == []
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DROP TRIGGER reject_snapshot")
        store.append(task_id, completed_step())
        assert store.get(task_id).steps_completed == 1


def test_final_result_and_audit_rollback_together(store):
    task_id = store.create().task_id
    store.start(task_id)
    store.put_audit(task_id, {"before": True})
    with pytest.raises(ValueError):
        store.finish(task_id, stopped_result(task_id), audit={"invalid": float("nan")})
    assert store.get(task_id).status == "running"
    assert store.get_audit(task_id) == {"before": True}
    store.finish(task_id, stopped_result(task_id), audit={"after": True})
    assert store.get_audit(task_id) == {"after": True}


def test_concurrent_tasks_keep_independent_histories_and_paginate(store):
    def run(index):
        task_id = store.create().task_id
        store.start(task_id)
        step = completed_step()
        store.append(task_id, step)
        store.finish(task_id, stopped_result(task_id, [step]), audit={"index": index})
        return task_id
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(run, range(12)))
    first, second = store.list_tasks(5), store.list_tasks(7, 5)
    assert len(first) == 5 and len(second) == 7
    assert {item.task_id for item in first + second} == set(ids)
    assert [(item.created_at, item.task_id) for item in first + second] == sorted(
        [(item.created_at, item.task_id) for item in first + second], reverse=True)
    assert store.list_tasks(20, 12) == []
    for index, task_id in enumerate(ids):
        assert store.get(task_id).status == "stopped"
        assert len(store.get_steps(task_id).steps) == 1
        assert store.get_audit(task_id) == {"index": index}


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (1, -1), (True, 0)])
def test_invalid_pagination_does_not_silently_change_query(store, limit, offset):
    with pytest.raises(ValueError):
        store.list_tasks(limit, offset)


def test_unknown_task_is_distinct_from_storage_failure(store):
    task_id = str(uuid4())
    for read in (store.get, store.get_steps, store.get_request):
        with pytest.raises(KeyError):
            read(task_id)
    assert store.get_audit(task_id) == {}
    with pytest.raises(KeyError):
        store.put_audit(task_id, {"unexpected": True})


def test_corrupt_task_snapshot_is_not_reported_as_missing(tmp_path):
    path = tmp_path / "history.db"
    with closing(TaskStore(path)) as store:
        task_id = store.create().task_id
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE api_tasks SET status_json = ? WHERE id = ?", ("{}", task_id))
        with pytest.raises(task_module.TaskStorageError):
            store.get(task_id)


def test_task_tables_do_not_change_existing_experience_records(tmp_path):
    path = tmp_path / "shared.db"
    experiences = ExperienceStore(path)
    saved = experiences.save_experience(ExperienceCreate(
        task_summary="Legacy repair", root_cause="Missing condition", solution="Check value", verification="Passed"))
    with closing(sqlite3.connect(path)) as connection:
        before = connection.execute("SELECT * FROM experiences").fetchall()
    with closing(TaskStore(path)) as store:
        task_id = store.create().task_id
        store.finish(task_id, error="No model configured.")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT * FROM experiences").fetchall() == before
    assert experiences.get_experience(saved.id) == saved
