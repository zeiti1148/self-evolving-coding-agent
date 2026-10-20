from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

import backend.experience as experience
from backend.shared import ApplicationError, ErrorCode, ExperienceCreate


def data(**changes):
    values = dict(task_summary="修复 greeting", error_type="NullPointerException",
                  root_cause="name is null", solution="check argument",
                  verification="mvn test passes", tags=["Java", "null"])
    values.update(changes)
    return ExperienceCreate(**values)


@pytest.fixture
def store(tmp_path):
    return experience.ExperienceStore(tmp_path / "search.db")


def test_keywords_match_across_fields_and_all_are_required(store):
    saved = store.save_experience(data())
    assert hasattr(store, "search_experiences"), "Search is not implemented"
    assert store.search_experiences(" 修复  NAME\tCHECK\npasses ") == [saved]
    assert store.search_experiences("修复 missing") == []


@pytest.mark.parametrize("field", ["task_summary", "error_type", "root_cause", "solution", "verification"])
def test_each_documented_text_field_is_searchable(store, field):
    saved = store.save_experience(data(**{field: "唯一Match"}))
    assert store.search_experiences("唯一match") == [saved]


def test_tags_match_whole_values_casefolded_with_and_semantics(store):
    exact = store.save_experience(data(tags=["Java", "NULL", "Straße"]))
    store.save_experience(data(tags=["javascript", "null"]))
    store.save_experience(data(tags=["java"]))
    assert store.search_experiences("", tags=[" java ", "null", "STRASSE"]) == [exact]
    assert store.search_experiences("", tags=["jav"]) == []


def test_combined_filters_apply_before_limit(store):
    wanted = store.save_experience(data())
    store.save_experience(data(tags=["javascript"]))
    store.save_experience(data(error_type="OtherNullPointerException"))
    store.save_experience(data(task_summary="unrelated"))
    assert store.search_experiences(
        "greeting", tags=["JAVA", "null"], error_type=" nullpointerexception ", limit=1
    ) == [wanted]


def test_empty_filters_and_default_limits(store):
    assert store.search_experiences("") == []
    for i in range(7):
        store.save_experience(data(task_summary=f"task {i}"))
    assert len(store.search_experiences("")) == 5
    assert store.search_experiences("  ", tags=[], limit=20) == store.list_experiences()
    assert store.search_experiences("", limit=2) == store.list_experiences(2)


@pytest.mark.parametrize("literal", ["100%", "a_b", "O'Reilly", "x');DROP", "反斜杠\\", "[abc]"])
def test_keywords_are_literal_not_sql_syntax(store, literal):
    saved = store.save_experience(data(solution=literal))
    store.save_experience(data(solution="1000 axb OReilly x abc"))
    assert store.search_experiences(literal) == [saved]
    assert len(store.list_experiences()) == 2


def test_unicode_casefold_and_optional_error_type(store):
    saved = store.save_experience(data(solution="Straße", error_type=None))
    assert store.search_experiences("STRASSE") == [saved]
    assert store.search_experiences("", error_type="NullPointerException") == []


@pytest.mark.parametrize("kwargs", [
    {"query": None}, {"query": 1}, {"query": []},
    {"tags": "java"}, {"tags": [1]}, {"tags": [" "]},
    {"error_type": 2}, {"error_type": " "},
    {"limit": 0}, {"limit": -1}, {"limit": True}, {"limit": 2**64},
])
def test_invalid_search_arguments_are_validation_errors(store, kwargs):
    arguments = {"query": ""} | kwargs
    with pytest.raises(ApplicationError) as caught:
        store.search_experiences(**arguments)
    assert caught.value.code == ErrorCode.EXPERIENCE_VALIDATION_ERROR
    assert not store.database_path.exists()


def test_public_search_facade(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "api.db").as_posix())
    saved = experience.save_experience(data())
    assert experience.search_experiences("greeting", ["java"], "NullPointerException", 5) == [saved]


@pytest.mark.parametrize("kwargs", [
    {"query": "\ud800"}, {"error_type": "\ud800"}, {"tags": ["\ud800"]},
])
def test_search_rejects_unencodable_text_before_database_io(store, kwargs):
    with pytest.raises(ApplicationError) as caught:
        store.search_experiences(**({"query": ""} | kwargs))
    assert caught.value.code == ErrorCode.EXPERIENCE_VALIDATION_ERROR
    assert not store.database_path.exists()


@pytest.mark.parametrize("tags", ["invalid json", json.dumps([1]), json.dumps({"java": True})])
def test_corrupt_tags_during_filter_are_storage_errors(store, tags):
    store.save_experience(data())
    with closing(sqlite3.connect(store.database_path)) as connection:
        with connection:
            connection.execute("UPDATE experiences SET tags=?", (tags,))
    with pytest.raises(ApplicationError) as caught:
        store.search_experiences("", tags=["java"])
    assert caught.value.code == ErrorCode.EXPERIENCE_STORAGE_ERROR
