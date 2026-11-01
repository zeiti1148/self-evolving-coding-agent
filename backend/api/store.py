"""Transactional task snapshots for one API instance, with optional SQLite persistence."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from backend.shared import (
    AgentEvent, AgentResult, TaskCreateRequest, TaskCreateResponse, TaskStatusResponse, TaskStep, TaskStepsResponse,
)
from . import agent_events


class TaskStorageError(Exception):
    """Safe API-facing error; underlying database details stay out of responses."""

    def __init__(self):
        super().__init__("Task storage is unavailable or contains invalid data.")


def now():
    return datetime.now(timezone.utc)


def _trace_json(model) -> str:
    # Python equality equates True/1 and False/0; tool evidence must preserve types.
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, allow_nan=False)


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS api_tasks (
        id TEXT PRIMARY KEY NOT NULL,
        request_json TEXT,
        status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','stopped')),
        created_at TEXT NOT NULL,
        status_json TEXT NOT NULL,
        audit_json TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE TABLE IF NOT EXISTS api_task_steps (
        task_id TEXT NOT NULL REFERENCES api_tasks(id),
        sequence INTEGER NOT NULL CHECK(sequence >= 1),
        call_id TEXT NOT NULL,
        step_json TEXT NOT NULL,
        PRIMARY KEY(task_id, sequence),
        UNIQUE(task_id, call_id)
    )""",
    "CREATE INDEX IF NOT EXISTS api_tasks_created_idx ON api_tasks(created_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS api_tasks_status_idx ON api_tasks(status)",
    agent_events.SCHEMA,
)


class TaskStore:
    def __init__(self, database_path: Path | None = None):
        self.lock = RLock()
        self.path = Path(database_path) if database_path is not None else None
        if self.path is not None and not self.path.is_absolute():
            self.path = Path(__file__).resolve().parents[2] / self.path
        self._connection: sqlite3.Connection | None = None
        self._initialized = False
        self._closed = False

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise TaskStorageError()
        if self._connection is not None:
            return self._connection
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.path) if self.path is not None else ":memory:",
            timeout=5.0, check_same_thread=False,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
        except sqlite3.Error:
            connection.close()
            raise
        if self.path is None:
            self._connection = connection
        return connection

    @contextmanager
    def _transaction(self):
        # Each connection is used only while this lock is held, including reads.
        with self.lock:
            connection = None
            try:
                connection = self._connect()
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    if not self._initialized:
                        self._prepare(connection)
                    yield connection
                self._initialized = True
            except (sqlite3.Error, OSError):
                raise TaskStorageError() from None
            finally:
                if self.path is not None and connection is not None:
                    connection.close()

    def _prepare(self, connection):
        for statement in SCHEMA:
            connection.execute(statement)
        rows = connection.execute("SELECT * FROM api_tasks WHERE status IN ('pending', 'running')").fetchall()
        for row in rows:
            current = self._status(row)
            steps = self._steps(connection, current)
            recovered = TaskStatusResponse(**{
                **current.model_dump(), "status": "stopped",
                "finished_at": max(now(), current.started_at or current.created_at,
                                   *(step.completed_at for step in steps)),
                "error_message": "Task interrupted by a service restart; submit a new task to run again.",
            })
            self._write_status(connection, recovered)
            agent_events.recover_roles(connection, current.task_id, recovered.finished_at)

    def initialize(self) -> None:
        with self._transaction():
            pass

    def append_agent_event(self, task_id: str, event: AgentEvent) -> None:
        event = AgentEvent.model_validate(event.model_dump())
        with self._transaction() as connection:
            current = self._status(self._row(connection, task_id))
            existing = agent_events.read_events(connection, task_id)
            if current.status != "running" or event.task_id != task_id:
                raise ValueError("Role events require their own running task")
            if event.sequence != len(existing)+1 or any(item.event_id == event.event_id for item in existing):
                raise ValueError("Role events must have unique continuous sequence numbers")
            if event.created_at < current.started_at or existing and event.created_at < existing[-1].created_at:
                raise ValueError("Role event timestamps must follow task start and prior events")
            agent_events.insert_event(connection, event)

    def get_agent_events(self, task_id: str, *, after_sequence: int = 0, limit: int = 100) -> list[AgentEvent]:
        if type(after_sequence) is not int or after_sequence < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid event cursor or limit")
        with self._transaction() as connection:
            self._row(connection, task_id)
            return [event for event in agent_events.read_events(connection, task_id) if event.sequence > after_sequence][:limit]

    def close(self) -> None:
        with self.lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            # File-backed operations own short-lived connections. Existing experiment
            # clients can still read durable snapshots after the API worker shutdown.
            self._closed = self.path is None

    @staticmethod
    def _row(connection, task_id):
        row = connection.execute("SELECT * FROM api_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return row

    @staticmethod
    def _status(row) -> TaskStatusResponse:
        try:
            status = TaskStatusResponse.model_validate_json(row["status_json"])
            if (status.task_id != row["id"] or status.status != row["status"]
                    or status.created_at.isoformat() != row["created_at"]):
                raise ValueError("Inconsistent task snapshot")
            return status
        except (ValueError, TypeError):
            raise TaskStorageError() from None

    @staticmethod
    def _steps(connection, status) -> list[TaskStep]:
        rows = connection.execute(
            "SELECT * FROM api_task_steps WHERE task_id = ? ORDER BY sequence", (status.task_id,),
        ).fetchall()
        try:
            result = []
            for sequence, row in enumerate(rows, 1):
                step = TaskStep.model_validate_json(row["step_json"])
                if step.sequence != sequence or row["sequence"] != sequence or step.call.id != row["call_id"]:
                    raise ValueError("Inconsistent step snapshot")
                result.append(step)
            if len(result) != status.steps_completed:
                raise ValueError("Inconsistent step count")
            return result
        except (ValueError, TypeError):
            raise TaskStorageError() from None

    @staticmethod
    def _write_status(connection, status):
        connection.execute("UPDATE api_tasks SET status = ?, status_json = ? WHERE id = ?",
                           (status.status.value, status.model_dump_json(), status.task_id))

    @staticmethod
    def _write_audit(connection, task_id, audit):
        try:
            if not isinstance(audit, dict):
                raise ValueError("Audit must be an object")
            encoded = json.dumps(audit, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError):
            raise ValueError("Audit must contain JSON-compatible data") from None
        connection.execute("UPDATE api_tasks SET audit_json = ? WHERE id = ?", (encoded, task_id))

    def put_audit(self, task_id: str, audit: dict) -> None:
        with self._transaction() as connection:
            self._row(connection, task_id)
            self._write_audit(connection, task_id, audit)

    def get_audit(self, task_id: str) -> dict:
        with self._transaction() as connection:
            try:
                row = self._row(connection, task_id)
            except KeyError:
                return {}
            try:
                audit = json.loads(row["audit_json"])
                if not isinstance(audit, dict):
                    raise ValueError("Invalid audit")
                return audit
            except (ValueError, TypeError):
                raise TaskStorageError() from None

    def create(self, request: TaskCreateRequest | None = None) -> TaskCreateResponse:
        request_json = (TaskCreateRequest.model_validate(request.model_dump()).model_dump_json()
                        if request is not None else None)
        response = TaskCreateResponse(task_id=str(uuid4()))
        status = TaskStatusResponse(task_id=response.task_id, status="pending", created_at=response.created_at)
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO api_tasks (id, request_json, status, created_at, status_json) VALUES (?, ?, ?, ?, ?)",
                (response.task_id, request_json, "pending", response.created_at.isoformat(), status.model_dump_json()),
            )
        return response

    def get(self, task_id: str) -> TaskStatusResponse:
        with self._transaction() as connection:
            return self._status(self._row(connection, task_id))

    def get_request(self, task_id: str) -> TaskCreateRequest | None:
        with self._transaction() as connection:
            data = self._row(connection, task_id)["request_json"]
            try:
                return TaskCreateRequest.model_validate_json(data) if data is not None else None
            except (ValueError, TypeError):
                raise TaskStorageError() from None

    def list_tasks(self, limit: int = 20, offset: int = 0) -> list[TaskStatusResponse]:
        if (type(limit) is not int or not 1 <= limit <= 100
                or type(offset) is not int or not 0 <= offset <= 2**63 - 1):
            raise ValueError("Invalid task history pagination")
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM api_tasks ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?", (limit, offset),
            ).fetchall()
            return [self._status(row) for row in rows]

    def get_steps(self, task_id: str) -> TaskStepsResponse:
        with self._transaction() as connection:
            status = self._status(self._row(connection, task_id))
            return TaskStepsResponse(task_id=task_id, status=status.status, steps=self._steps(connection, status))

    def start(self, task_id: str) -> None:
        with self._transaction() as connection:
            current = self._status(self._row(connection, task_id))
            if current.status != "pending":
                raise ValueError("Task already started")
            self._write_status(connection, TaskStatusResponse(**{
                **current.model_dump(), "status": "running", "started_at": max(now(), current.created_at),
            }))

    def append(self, task_id: str, step: TaskStep) -> None:
        step = TaskStep.model_validate(step.model_dump())
        with self._transaction() as connection:
            current = self._status(self._row(connection, task_id))
            if current.status != "running" or step.sequence != current.steps_completed + 1:
                raise ValueError("Invalid step transition")
            if connection.execute("SELECT 1 FROM api_task_steps WHERE task_id = ? AND call_id = ?",
                                  (task_id, step.call.id)).fetchone():
                raise ValueError("Duplicate tool call")
            connection.execute("INSERT INTO api_task_steps (task_id, sequence, call_id, step_json) VALUES (?, ?, ?, ?)",
                               (task_id, step.sequence, step.call.id, step.model_dump_json()))
            self._write_status(connection, TaskStatusResponse(**{
                **current.model_dump(), "steps_completed": step.sequence,
            }))

    def finish(self, task_id: str, result: AgentResult | None = None, error: str | None = None,
               *, audit: dict | None = None) -> None:
        if result is not None:
            result = AgentResult.model_validate(result.model_dump())
            if error is not None:
                raise ValueError("Specify a result or a scheduling error, not both")
        with self._transaction() as connection:
            current = self._status(self._row(connection, task_id))
            if current.status not in {"pending", "running"}:
                raise ValueError("Task already finished")
            steps = self._steps(connection, current)
            if result is not None:
                calls_match = [_trace_json(step.call) for step in steps] == [_trace_json(call) for call in result.steps]
                results_match = {step.result.call_id: _trace_json(step.result) for step in steps} == {
                    item.call_id: _trace_json(item) for item in result.tool_results}
                if not calls_match or not results_match:
                    raise ValueError("Final trace does not match completed steps")
            self._write_status(connection, TaskStatusResponse(**{
                **current.model_dump(), "status": result.status if result else "failed",
                "finished_at": max(now(), current.started_at or current.created_at,
                                   *(step.completed_at for step in steps)),
                "result": result.model_dump() if result else None,
                "error_message": result.error_message if result else (error or "Task execution failed."),
            }))
            if audit is not None:
                self._write_audit(connection, task_id, audit)
