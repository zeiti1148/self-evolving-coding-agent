from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

import backend.experience as experience
from backend.shared import ApplicationError, ErrorCode, Experience, ExperienceCreate


def make_data(**overrides) -> ExperienceCreate:
    values = dict(
        task_summary="修复 Java 空指针",
        error_type="NullPointerException",
        root_cause="输入为 null，直接调用 trim()。",
        solution="先检查 null。\n    return guest;",
        verification="mvn test: 2 tests passed",
        tags=["Java", "java", " null "],
        source_task_id="12345678-1234-5678-1234-567812345678",
    )
    values.update(overrides)
    return ExperienceCreate(**values)


@pytest.fixture
def store(tmp_path):
    assert hasattr(experience, "ExperienceStore"), "ExperienceStore is not implemented"
    return experience.ExperienceStore(tmp_path / "nested" / "experiences.db")


def execute_sql(store, sql, parameters=()):
    with closing(sqlite3.connect(store.database_path)) as connection:
        with connection:
            connection.execute(sql, parameters)


def test_save_survives_new_store_and_generates_identity(store):
    before = datetime.now(timezone.utc)
    original = make_data()
    saved = store.save_experience(original)
    assert isinstance(saved, Experience)
    assert UUID(saved.id).version == 4
    assert before <= saved.created_at <= datetime.now(timezone.utc)
    assert saved.created_at.utcoffset().total_seconds() == 0
    assert saved.tags == ["Java", "null"]
    assert saved.solution == "先检查 null。\n    return guest;"
    assert saved.source_task_id == original.source_task_id
    reopened = experience.ExperienceStore(store.database_path)
    assert reopened.list_experiences() == [saved]
    assert original.model_dump() == make_data().model_dump()
    second = reopened.save_experience(original)
    assert second.id != saved.id
    assert len(store.list_experiences()) == 2


def test_empty_database_and_optional_fields(store):
    assert store.list_experiences() == []
    saved = store.save_experience(make_data(error_type=None, tags=[], source_task_id=None))
    assert store.list_experiences() == [saved]
    assert saved.error_type is None
    assert saved.tags == []


def test_pagination_orders_dates_and_breaks_ties_by_id(store):
    saved = [store.save_experience(make_data(task_summary=str(i))) for i in range(3)]
    for row, date in zip(saved, ["2026-01-01", "2026-01-03", "2026-01-02"]):
        execute_sql(store, "UPDATE experiences SET created_at=? WHERE id=?",
                    (date + "T00:00:00+00:00", row.id))
    assert [row.id for row in store.list_experiences(2)] == [saved[1].id, saved[2].id]
    assert [row.id for row in store.list_experiences(2, 2)] == [saved[0].id]
    assert store.list_experiences(2, 3) == []
    execute_sql(store, "UPDATE experiences SET created_at=?", ("2026-01-01T00:00:00+00:00",))
    assert [row.id for row in store.list_experiences()] == sorted(
        [row.id for row in saved], reverse=True
    )


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": -1}, {"limit": True}, {"limit": 1.5},
    {"limit": "5"}, {"offset": -1}, {"offset": True}, {"offset": "0"},
    {"offset": 2**64}, {"limit": 2**64},
])
def test_invalid_pagination_has_stable_error(store, kwargs):
    with pytest.raises(ApplicationError) as caught:
        store.list_experiences(**kwargs)
    assert caught.value.code == ErrorCode.EXPERIENCE_VALIDATION_ERROR
    assert not store.database_path.exists()


def test_save_revalidates_mutated_model_before_creating_database(store):
    data = make_data()
    data.tags.append("   ")  # In-place mutation bypasses assignment validation.
    with pytest.raises(ApplicationError) as caught:
        store.save_experience(data)
    assert caught.value.code == ErrorCode.EXPERIENCE_VALIDATION_ERROR
    assert not store.database_path.exists()


def test_save_rejects_untyped_data(store):
    with pytest.raises(ApplicationError) as caught:
        store.save_experience({"task_summary": "invalid"})
    assert caught.value.code == ErrorCode.EXPERIENCE_VALIDATION_ERROR


@pytest.mark.parametrize("method", ["list", "search"])
def test_excessively_nested_stored_json_is_storage_error(store, method):
    store.save_experience(make_data())
    execute_sql(store, "UPDATE experiences SET tags=?", ("[" * 2000 + "]" * 2000,))
    with pytest.raises(ApplicationError) as caught:
        if method == "list":
            store.list_experiences()
        else:
            store.search_experiences("")
    assert caught.value.code == ErrorCode.EXPERIENCE_STORAGE_ERROR


def test_module_facade_uses_database_url(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "configured.db").as_posix())
    assert hasattr(experience, "save_experience"), "Public save function is missing"
    saved = experience.save_experience(make_data())
    assert experience.list_experiences() == [saved]
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "other.db").as_posix())
    assert experience.list_experiences() == []


def test_relative_config_resolves_against_repo_not_cwd(monkeypatch, tmp_path):
    assert hasattr(experience, "ExperienceStore"), "ExperienceStore is not implemented"
    from backend.experience.database import configured_database_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/app.db")
    assert configured_database_path() == Path(__file__).resolve().parents[2] / "data/app.db"
    monkeypatch.delenv("DATABASE_URL")
    assert configured_database_path() == Path(__file__).resolve().parents[2] / "data/app.db"


@pytest.mark.parametrize("url", ["", "sqlite:///", "sqlite:///:memory:", "postgres://user:secret@host/db"])
def test_bad_database_config_returns_safe_storage_error(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    assert hasattr(experience, "list_experiences"), "Public list function is missing"
    with pytest.raises(ApplicationError) as caught:
        experience.list_experiences()
    assert caught.value.code == ErrorCode.EXPERIENCE_STORAGE_ERROR
    assert "secret" not in caught.value.message


def test_database_io_failure_is_safe(tmp_path):
    assert hasattr(experience, "ExperienceStore"), "ExperienceStore is not implemented"
    blocker = tmp_path / "private-location"
    blocker.write_text("not a directory", encoding="utf-8")
    broken = experience.ExperienceStore(blocker / "db.sqlite")
    with pytest.raises(ApplicationError) as caught:
        broken.save_experience(make_data())
    assert caught.value.code == ErrorCode.EXPERIENCE_STORAGE_ERROR
    assert str(blocker) not in caught.value.message


def test_failed_write_rolls_back_and_next_write_works(store):
    existing = store.save_experience(make_data())
    execute_sql(store, """CREATE TRIGGER reject_insert AFTER INSERT ON experiences
        BEGIN SELECT RAISE(ABORT, 'private database detail'); END""")
    with pytest.raises(ApplicationError) as caught:
        store.save_experience(make_data(task_summary="must not remain"))
    assert caught.value.code == ErrorCode.EXPERIENCE_STORAGE_ERROR
    assert "private database detail" not in caught.value.message
    assert store.list_experiences() == [existing]
    execute_sql(store, "DROP TRIGGER reject_insert")
    store.save_experience(make_data())
    assert len(store.list_experiences()) == 2


@pytest.mark.parametrize("column,value", [
    ("tags", "not json"), ("tags", '[" "]'), ("tags", '{}'),
    ("created_at", "2026-01-01T00:00:00"), ("id", "invalid-uuid"),
])
def test_corrupt_rows_are_storage_errors(store, column, value):
    store.save_experience(make_data())
    execute_sql(store, f"UPDATE experiences SET {column}=?", (value,))
    with pytest.raises(ApplicationError) as caught:
        store.list_experiences()
    assert caught.value.code == ErrorCode.EXPERIENCE_STORAGE_ERROR


def test_corrupt_database_is_storage_error(store):
    store.database_path.parent.mkdir(parents=True)
    store.database_path.write_bytes(b"not a SQLite database" * 30)
    with pytest.raises(ApplicationError) as caught:
        store.list_experiences()
    assert caught.value.code == ErrorCode.EXPERIENCE_STORAGE_ERROR


def test_parallel_calls_use_independent_connections(store):
    def save_and_read(index):
        saved = store.save_experience(make_data(task_summary=f"task {index}"))
        assert saved.id in [row.id for row in store.list_experiences(limit=50)]
        return saved.id

    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(save_and_read, range(12)))
    assert len(set(ids)) == 12
    assert {row.id for row in store.list_experiences(50)} == set(ids)
    # On Windows an open SQLite handle would prevent renaming/removing this file.
    moved = store.database_path.with_suffix(".moved")
    store.database_path.rename(moved)
    moved.unlink()
