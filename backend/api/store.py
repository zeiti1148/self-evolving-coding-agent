"""Thread-safe, single-process task snapshots. No task recovery across restarts."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4
from copy import deepcopy

from backend.shared import AgentResult, TaskCreateResponse, TaskStatusResponse, TaskStep, TaskStepsResponse


def now():
    return datetime.now(timezone.utc)


class TaskStore:
    def __init__(self):
        self.lock = RLock()
        self.statuses: dict[str, TaskStatusResponse] = {}
        self.steps: dict[str, list[TaskStep]] = {}
        self.audits: dict[str, dict] = {}

    def put_audit(self, task_id: str, audit: dict) -> None:
        with self.lock:
            self.audits[task_id] = deepcopy(audit)

    def get_audit(self, task_id: str) -> dict:
        with self.lock:
            return deepcopy(self.audits.get(task_id, {}))

    def create(self) -> TaskCreateResponse:
        response = TaskCreateResponse(task_id=str(uuid4()))
        with self.lock:
            self.statuses[response.task_id] = TaskStatusResponse(task_id=response.task_id, status="pending", created_at=response.created_at)
            self.steps[response.task_id] = []
        return response

    def get(self, task_id: str) -> TaskStatusResponse:
        with self.lock:
            return self.statuses[task_id].model_copy(deep=True)

    def get_steps(self, task_id: str) -> TaskStepsResponse:
        with self.lock:
            return TaskStepsResponse(task_id=task_id, status=self.statuses[task_id].status,
                                     steps=[s.model_copy(deep=True) for s in self.steps[task_id]])

    def start(self, task_id: str) -> None:
        with self.lock:
            current = self.statuses[task_id]
            if current.status != "pending":
                raise ValueError("Task already started")
            self.statuses[task_id] = TaskStatusResponse(**{**current.model_dump(), "status": "running", "started_at": now()})

    def append(self, task_id: str, step: TaskStep) -> None:
        with self.lock:
            current = self.statuses[task_id]
            if current.status != "running" or step.sequence != len(self.steps[task_id]) + 1:
                raise ValueError("Invalid step transition")
            if any(s.call.id == step.call.id for s in self.steps[task_id]):
                raise ValueError("Duplicate tool call")
            self.steps[task_id].append(TaskStep.model_validate(step.model_dump()))
            self.statuses[task_id] = TaskStatusResponse(**{**current.model_dump(), "steps_completed": len(self.steps[task_id])})

    def finish(self, task_id: str, result: AgentResult | None = None, error: str | None = None) -> None:
        with self.lock:
            current = self.statuses[task_id]
            if current.status not in {"pending", "running"}:
                raise ValueError("Task already finished")
            if result is not None and [step.call.id for step in self.steps[task_id]] != [call.id for call in result.steps]:
                raise ValueError("Final trace does not match completed steps")
            self.statuses[task_id] = TaskStatusResponse(**{**current.model_dump(),
                "status": result.status if result else "failed", "finished_at": now(),
                "result": result.model_dump() if result else None,
                "error_message": result.error_message if result else error})
