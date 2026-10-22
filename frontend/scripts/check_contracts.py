"""Test-only bridge: validate frontend samples and exercise an isolated SQLite DB."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.experience import ExperienceStore
from backend.shared import Experience, ExperienceCreate
from backend.shared.models import TaskCreateRequest, TaskCreateResponse, TaskStatusResponse, TaskStepsResponse


def verify(samples: list[dict]) -> dict:
    expected = {"success", "tests-failed", "stopped", "save-failure", "schedule-error"}
    assert len(samples) == 5 and {item["scenario"] for item in samples} == expected
    checked = 0
    # Each run owns its temporary directory. Never uses DATABASE_URL or app.db.
    temp_root = ROOT / ".cache" / "integration-checks"
    temp_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="sqlite-", dir=temp_root) as temporary:
        db = Path(temporary) / "experiences.db"
        store = ExperienceStore(db)
        saved = []
        for sample in samples:
            TaskCreateRequest.model_validate(sample["request"])
            created = TaskCreateResponse.model_validate(sample["created"])
            states = [TaskStatusResponse.model_validate(value) for value in sample["states"]]
            steps = TaskStepsResponse.model_validate(sample["steps"])
            records = [Experience.model_validate(value) for value in sample["experiences"]]
            checked += 3 + len(states) + len(records)
            assert len(states) == 3
            assert states[0].status.value == "pending" and states[1].status.value == "running"
            assert all(state.task_id == created.task_id for state in states)
            final = states[-1]
            assert steps.task_id == final.task_id and steps.status == final.status
            assert len(steps.steps) == final.steps_completed
            if final.result is not None:
                assert [step.call for step in steps.steps] == final.result.steps
                assert [step.result for step in steps.steps] == final.result.tool_results
                if final.result.experience_saved:
                    source = next(item for item in records if item.id == final.result.experience_id)
                    assert source.source_task_id == created.task_id
                    payload = ExperienceCreate.model_validate(source.model_dump(exclude={"id", "created_at"}))
                    saved.append(store.save_experience(payload))
        assert len(saved) == 1, "Only the successful save scenario may persist an experience"
        # More rows exercise the frontend's five visible rows + one-row lookahead.
        for number in range(6):
            saved.append(store.save_experience(ExperienceCreate(
                task_summary=f"integration sample {number}", error_type="NullPointerException",
                root_cause="name is null", solution="check null before trim",
                verification="contract fixture, not an actual Maven run", tags=["Java", "null"],
            )))
        reopened = ExperienceStore(db)
        ordered = reopened.list_experiences(100)
        assert {item.id for item in ordered} == {item.id for item in saved}
        assert ordered == sorted(ordered, key=lambda item: (item.created_at, item.id), reverse=True)
        assert len(reopened.list_experiences(6, 0)) == 6
        assert len(reopened.list_experiences(6, 5)) == 2
        found = reopened.search_experiences("integration sample", tags=["JAVA"], error_type="nullpointerexception", limit=100)
        assert len(found) == 6
        assert reopened.search_experiences("no-such-contract-keyword") == []
        serialized = [item.model_dump(mode="json") for item in ordered]
    return {"checked_objects": checked, "scenarios": 5, "sqlite_records": serialized}


if __name__ == "__main__":
    try:
        samples = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        output = verify(samples)
    except Exception as error:
        # Do not print fixture content, local paths or SQL on failure.
        print(f"Contract/SQLite acceptance failed: {type(error).__name__}", file=sys.stderr)
        sys.exit(1)
    sys.stdout.buffer.write(json.dumps(output, ensure_ascii=False).encode("utf-8"))
