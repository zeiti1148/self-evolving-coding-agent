"""Role event operations using TaskStore's existing transaction ownership."""
from uuid import uuid4

from backend.shared import AgentEvent


SCHEMA = """CREATE TABLE IF NOT EXISTS api_agent_events (
    task_id TEXT NOT NULL REFERENCES api_tasks(id),
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    event_id TEXT NOT NULL UNIQUE,
    event_json TEXT NOT NULL,
    PRIMARY KEY(task_id, sequence)
)"""


def read_events(connection, task_id):
    from .store import TaskStorageError
    try:
        result = []
        for expected, row in enumerate(connection.execute(
                "SELECT * FROM api_agent_events WHERE task_id=? ORDER BY sequence", (task_id,)), 1):
            event = AgentEvent.model_validate_json(row["event_json"])
            if (row["sequence"] != expected or event.sequence != expected or event.task_id != task_id
                    or event.event_id != row["event_id"]):
                raise ValueError("Inconsistent agent event")
            result.append(event)
        return result
    except (TypeError, ValueError):
        raise TaskStorageError() from None


def insert_event(connection, event):
    connection.execute("INSERT INTO api_agent_events VALUES (?, ?, ?, ?)",
                       (event.task_id, event.sequence, event.event_id, event.model_dump_json()))


def recover_roles(connection, task_id, recovered_at):
    events = read_events(connection, task_id)
    latest = {(event.agent_id, event.phase): event for event in events}
    sequence = len(events)
    for event in latest.values():
        if event.status == "started":
            sequence += 1
            insert_event(connection, AgentEvent(**(event.model_dump() | dict(event_id=str(uuid4()), sequence=sequence,
                status="stopped", created_at=max(recovered_at, event.created_at),
                summary="Role interrupted by service restart; task was not resumed."))))
