import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from backend.experience import ExperienceStore
from backend.shared import ApplicationError, ExperienceCreate


def batch(task_id=None):
    task_id = task_id or str(uuid4())
    evidence = dict(source_task_id=task_id, tool_call_id=str(uuid4()), runner="pytest", tests=18)
    return [ExperienceCreate(task_summary=f"Rule {i}", root_cause=f"Cause {i}", solution=f"Solution {i}",
                             verification="18 final tests passed", schema_version=3, language="python",
                             scope="project", repository_id="A", source_task_id=task_id,
                             applicability=[f"Condition {i}"], evidence=[evidence],
                             issues=[dict(error_type="ValueError", root_cause=f"Cause {i}",
                                          solution=f"Solution {i}", files=[f"app/rule{i}.py"])])
            for i in range(3)]


def save(store, items):
    method = getattr(store, "save_experiences", None)
    assert method is not None, "atomic batch API is required"
    return method(items, source_task_id=items[0].source_task_id)


def test_batch_three_records_share_task_and_evidence(tmp_path):
    store = ExperienceStore(tmp_path / "batch.db")
    items = batch()
    saved = save(store, items)
    assert len({item.id for item in saved}) == 3
    assert len({item.batch_id for item in saved}) == 1 and saved[0].batch_id
    assert [item.task_summary for item in saved] == ["Rule 0", "Rule 1", "Rule 2"]
    assert all(item.evidence == items[0].evidence for item in saved)
    reopened = ExperienceStore(store.database_path)
    assert [item.id for item in save(reopened, items)] == [item.id for item in saved]


def test_invalid_second_record_writes_nothing(tmp_path):
    store = ExperienceStore(tmp_path / "batch.db")
    items = batch()
    items[1].issues[0].files.append("../private.py")
    with pytest.raises(ApplicationError):
        save(store, items)
    assert store.list_experiences() == []


def test_insert_failure_rolls_back_batch(tmp_path):
    store = ExperienceStore(tmp_path / "batch.db")
    store.list_experiences()
    with sqlite3.connect(store.database_path) as db:
        db.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON experiences
                      WHEN NEW.task_summary = 'Rule 1' BEGIN SELECT RAISE(ABORT, 'injected'); END""")
    with pytest.raises(ApplicationError):
        save(store, batch())
    with sqlite3.connect(store.database_path) as db:
        for table in ("experiences", "experience_batches", "experience_batch_items"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_concurrent_same_batch_returns_same_ids(tmp_path):
    store = ExperienceStore(tmp_path / "batch.db")
    items = batch()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: save(store, items), range(4)))
    assert all([x.id for x in saved] == [x.id for x in results[0]] for saved in results)
    assert len(store.list_experiences()) == 3


def test_same_task_changed_batch_conflicts(tmp_path):
    store = ExperienceStore(tmp_path / "batch.db")
    items = batch()
    saved = save(store, items)
    items[0].solution = "Different knowledge"
    with pytest.raises(ApplicationError, match="另一批不同的经验"):
        save(store, items)
    assert {x.id for x in store.list_experiences()} == {x.id for x in saved}


def test_title_only_duplicates_and_forged_provenance_are_rejected(tmp_path):
    store = ExperienceStore(tmp_path / "batch.db")
    items = batch()
    duplicate = items[0].model_copy(update={"task_summary": "Another title"})
    with pytest.raises(ApplicationError):
        save(store, [items[0], duplicate])
    items[1].derived_from = [str(uuid4())]
    with pytest.raises(ApplicationError):
        save(store, items)
    with pytest.raises(ApplicationError):
        store.save_experience(items[1])
    assert store.list_experiences() == []


def test_batch_rejects_foreign_task_evidence_and_mixed_repository(tmp_path):
    store = ExperienceStore(tmp_path / "batch.db")
    items = batch()
    items[1].evidence[0].source_task_id = str(uuid4())
    with pytest.raises(ApplicationError):
        save(store, items)
    items = batch()
    items[1].repository_id = "B"
    with pytest.raises(ApplicationError):
        save(store, items)
    assert store.list_experiences() == []
