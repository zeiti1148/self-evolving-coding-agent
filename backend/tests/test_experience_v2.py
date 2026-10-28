from __future__ import annotations

import sqlite3
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.experience import ExperienceStore
from backend.shared import ApplicationError, ErrorCode, ExperienceCreate, RepairIssue, VerificationEvidence


def issue(error="NullPointerException", *, solution="Check null before access.", files=None, root="Input may be null."):
    return RepairIssue(error_type=error, root_cause=root, solution=solution,
                       files=["src/main/java/Service.java"] if files is None else files)


def evidence(*, language="java", **changes):
    values = dict(source_task_id=str(uuid4()), tool_call_id=str(uuid4()),
                  runner="maven" if language == "java" else "pytest", tests=3)
    return VerificationEvidence(**(values | changes))


def data(**changes):
    values = dict(task_summary="Guard the input", error_type="NullPointerException",
                  root_cause="Input may be null.", solution="Check null before access.",
                  verification="Independent final test passed.", tags=["java"],
                  source_task_id=str(uuid4()), issues=[issue()], evidence=[evidence()])
    values.update(changes)
    return ExperienceCreate(**values)


@pytest.fixture
def store(tmp_path):
    return ExperienceStore(tmp_path / "experiences.db")


def test_migrates_real_nine_column_database_without_rewriting_sources(tmp_path):
    path = tmp_path / "legacy.db"
    identity, task = str(uuid4()), str(uuid4())
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE experiences (id TEXT PRIMARY KEY NOT NULL,
            task_summary TEXT NOT NULL, error_type TEXT, root_cause TEXT NOT NULL,
            solution TEXT NOT NULL, verification TEXT NOT NULL, tags TEXT NOT NULL,
            source_task_id TEXT, created_at TEXT NOT NULL)""")
        connection.execute("INSERT INTO experiences VALUES(?,?,?,?,?,?,?,?,?)", (
            identity, "Original summary", "NullPointerException", "Root", "Solution", "mvn test",
            '["java"]', task, datetime.now(timezone.utc).isoformat()))
    store = ExperienceStore(path)
    old = store.get_experience(identity)
    assert old.id == identity and old.source_task_id == task and old.task_summary == "Original summary"
    assert old.schema_version == 1 and old.language == "java" and old.scope == "global"
    assert old.issues == [] and old.evidence == []
    new = store.save_experience(data(scope="project", repository_id="repo-a"))
    assert store.get_experience(identity) == old
    assert ExperienceStore(path).get_experience(new.id) == new
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT details FROM experiences WHERE id=?", (identity,)).fetchone()[0] == "{}"


def test_complex_record_round_trips_nested_evidence_and_issue_order(store):
    first = issue()
    second = issue("NumberFormatException", solution="Handle blank numeric strings.", root="Blank string was parsed.")
    saved = store.save_experience(data(scope="project", repository_id="checkout", source_revision="abc123",
        applicability=["Trusted checkout repository"], issues=[first, second]))
    loaded = ExperienceStore(store.database_path).get_experience(saved.id)
    assert loaded == saved and loaded.issues == [first, second]
    assert loaded.evidence[0].tool_call_id and loaded.source_revision == "abc123"


@pytest.mark.parametrize("changes", [
    {"scope": "project"}, {"repository_id": "repo-a"}, {"language": "ruby"},
    {"scope": "project", "repository_id": " "}, {"applicability": [" "]},
    {"merged_from": ["not-a-uuid"]},
    {"issues": [{"error_type": "ValueError", "root_cause": "", "solution": "fix"}]},
    {"issues": [dict(error_type="ValueError", root_cause="root", solution="fix", files=["../secret"])]},
    {"issues": [dict(error_type="ValueError", root_cause="root", solution="fix", files=["C:secret"])]},
])
def test_rejects_invalid_complex_records(changes):
    with pytest.raises(ValidationError):
        data(**changes)


@pytest.mark.parametrize("changes", [{"tests": 0}, {"tests": True}, {"failures": 1}, {"skipped": 1}, {"errors": 1}])
def test_evidence_requires_completed_non_skipped_passing_tests(changes):
    with pytest.raises(ValidationError):
        evidence(**changes)


def test_wrong_language_verification_is_rejected():
    with pytest.raises(ValidationError):
        data(language="python")


def test_retrieval_repository_and_language_isolation_and_project_priority(store):
    local = store.save_experience(data(scope="project", repository_id="repo-a"))
    global_rule = store.save_experience(data())
    other = store.save_experience(data(scope="project", repository_id="repo-b"))
    python = store.save_experience(data(language="python", tags=["python"], evidence=[evidence(language="python")]))
    found = store.retrieve_experiences(language="java", repository_id="repo-a", error_types=["NullPointerException"])
    assert [record.id for record in found] == [local.id, global_rule.id]
    assert other.id not in {r.id for r in found} and python.id not in {r.id for r in found}
    assert store.retrieve_experiences(language="java", repository_id=None, error_types=[]) == [global_rule]


def test_retrieval_matches_all_issue_types_and_ranks_overlap_before_recency(store):
    multiple = store.save_experience(data(error_type=None, issues=[issue(), issue("NumberFormatException")]))
    single = store.save_experience(data())
    store.save_experience(data(error_type="ArithmeticException", issues=[issue("ArithmeticException")]))
    found = store.retrieve_experiences(language="java", repository_id=None,
                                       error_types=["NullPointerException", "NumberFormatException"])
    assert found == [multiple, single]
    assert store.retrieve_experiences(language="java", repository_id=None, error_types=["NumberFormatException"]) == [multiple]


def test_filtered_list_applies_filters_before_pagination(store):
    local = [store.save_experience(data(scope="project", repository_id="repo-a")) for _ in range(3)]
    store.save_experience(data(scope="project", repository_id="repo-b"))
    store.save_experience(data())
    assert store.list_experiences(1, 1, repository_id="repo-a", language="java", scope="project") == [local[1]]
    assert store.list_experiences(1, 5, repository_id="repo-a") == []


def test_merge_is_previewed_preserves_sources_and_deduplicates_retrieval(store):
    left = store.save_experience(data())
    right = store.save_experience(data(error_type="NumberFormatException", issues=[issue("NumberFormatException")]))
    before = store.list_experiences()
    plan = store.preview_merge([right.id, left.id])
    assert plan["can_merge"] and not plan["conflicts"]
    assert len(plan["preview"].issues) == 2 and len(plan["preview"].evidence) == 2
    assert store.list_experiences() == before
    merged = store.merge_experiences([left.id, right.id])
    assert merged.merged_from == sorted([left.id, right.id])
    assert merged.source_task_id is None
    assert {item.source_task_id for item in merged.evidence} == {left.evidence[0].source_task_id, right.evidence[0].source_task_id}
    assert store.get_experience(left.id) == left and store.get_experience(right.id) == right
    assert store.retrieve_experiences(language="java", repository_id=None, error_types=["NullPointerException"]) == [merged]
    assert store.list_experiences(active_only=True) == [merged]
    assert store.search_experiences("") == [merged]
    assert len(store.list_experiences()) == 3
    assert store.merge_experiences([right.id, left.id]) == merged
    assert store.preview_merge([left.id, right.id])["preview"].merged_from == merged.merged_from


@pytest.mark.parametrize("changes", [
    {"scope": "project", "repository_id": "repo-b"},
    {"scope": "global", "repository_id": None},
    {"language": "python", "evidence": [], "tags": ["python"]},
    {"applicability": ["Only nonempty input"]},
    {"issues": [issue(solution="Always reject null input.")]},
])
def test_incompatible_sources_never_merge(store, changes):
    left = store.save_experience(data(scope="project", repository_id="repo-a"))
    right = store.save_experience(data(**({"scope": "project", "repository_id": "repo-a"} | changes)))
    plan = store.preview_merge([left.id, right.id])
    assert plan["can_merge"] is False and plan["preview"] is None and plan["conflicts"]
    with pytest.raises(ApplicationError) as caught:
        store.merge_experiences([left.id, right.id])
    assert caught.value.code == ErrorCode.EXPERIENCE_VALIDATION_ERROR
    assert len(store.list_experiences()) == 2


@pytest.mark.parametrize("field,left_text,right_text", [
    ("solution", 'Return the literal "Admin".', 'Return the literal "admin".'),
    ("solution", 'Return the literal "a  b".', 'Return the literal "a b".'),
    ("root_cause", 'The key "UserID" is missing.', 'The key "userid" is missing.'),
])
def test_merge_preserves_case_and_spacing_in_repair_rules(store, field, left_text, right_text):
    def record(text):
        value = issue().model_dump()
        value[field] = text
        return store.save_experience(data(issues=[RepairIssue(**value)]))

    left, right = record(left_text), record(right_text)
    assert not store.preview_merge([left.id, right.id])["can_merge"]
    with pytest.raises(ApplicationError):
        store.merge_experiences([left.id, right.id])
    assert len(store.list_experiences()) == 2
    assert getattr(store.get_experience(left.id).issues[0], field) == left_text
    assert getattr(store.get_experience(right.id).issues[0], field) == right_text


@pytest.mark.parametrize("left_text,right_text", [
    ('role == "Admin"', 'role == "admin"'),
    ('separator == "  "', 'separator == " "'),
])
def test_merge_distinguishes_exact_applicability_conditions(store, left_text, right_text):
    left = store.save_experience(data(applicability=[left_text]))
    right = store.save_experience(data(applicability=[right_text]))
    assert not store.preview_merge([left.id, right.id])["can_merge"]
    with pytest.raises(ApplicationError):
        store.merge_experiences([left.id, right.id])
    assert len(store.list_experiences()) == 2


def test_same_exception_in_different_files_can_preserve_distinct_rules(store):
    left = store.save_experience(data(issues=[issue(files=["A.java"])]))
    right = store.save_experience(data(issues=[issue(files=["B.java"], solution="Use an empty collection.")]))
    assert len(store.merge_experiences([left.id, right.id]).issues) == 2


def test_issue_file_aliases_are_canonicalized_and_deduplicated():
    value = issue(files=["src/service.py", "./src/service.py", "src//service.py", "src/./service.py"])
    assert value.files == ["src/service.py"]


@pytest.mark.parametrize("path", [
    "src/service.py/", "src/.", "src/../service.py", "src /service.py", "src./service.py",
    "src/service.py ", "NUL.py", "src/CON/service.py", "src/a?b.py", "src/evil\x01.py",
])
def test_issue_files_reject_invalid_components(path):
    with pytest.raises(ValidationError):
        issue(files=[path])


@pytest.mark.parametrize("alias", ["./src/service.py", "src//service.py", "src/./service.py", "SRC/service.py"])
def test_merge_conflict_checks_canonical_path_identity(store, alias):
    left = store.save_experience(data(issues=[issue("ValueError", files=["src/service.py"], solution="Reject the value.")]))
    right = store.save_experience(data(issues=[issue("ValueError", files=[alias], solution="Accept the value.")]))
    assert not store.preview_merge([left.id, right.id])["can_merge"]
    with pytest.raises(ApplicationError):
        store.merge_experiences([left.id, right.id])


@pytest.mark.parametrize("alias", ["./src/service.py", "src//service.py"])
def test_preexisting_serialized_alias_cannot_bypass_merge_conflict(store, alias):
    left = store.save_experience(data(issues=[issue("ValueError", files=["src/service.py"], solution="Reject the value.")]))
    right = store.save_experience(data(issues=[issue("ValueError", files=["src/service.py"], solution="Accept the value.")]))
    with sqlite3.connect(store.database_path) as connection:
        details = json.loads(connection.execute("SELECT details FROM experiences WHERE id=?", (right.id,)).fetchone()[0])
        details["issues"][0]["files"] = [alias]
        connection.execute("UPDATE experiences SET details=? WHERE id=?", (json.dumps(details), right.id))
    assert store.get_experience(right.id).issues[0].files == ["src/service.py"]
    assert not store.preview_merge([left.id, right.id])["can_merge"]


def test_retrieval_small_limit_preserves_ranking_among_many_records(store):
    preferred = store.save_experience(data(scope="project", repository_id="repo-a"))
    for index in range(24):
        store.save_experience(data(task_summary=f"Recent general rule {index}"))
    found = store.retrieve_experiences(language="java", repository_id="repo-a", error_types=["NullPointerException"], limit=1)
    assert found == [preferred]


def test_conflicting_repair_order_is_not_silently_reordered(store):
    first, second = issue(), issue("NumberFormatException")
    left = store.save_experience(data(issues=[first, second]))
    right = store.save_experience(data(issues=[second, first]))
    assert "repair orders" in " ".join(store.preview_merge([left.id, right.id])["conflicts"])


def test_merge_can_preserve_a_combined_dependency_order(store):
    first, second, third = issue("FirstError"), issue("SecondError"), issue("ThirdError")
    left = store.save_experience(data(issues=[first, third]))
    right = store.save_experience(data(issues=[second, third]))
    result = store.merge_experiences([left.id, right.id])
    assert result.issues.index(first) < result.issues.index(third)
    assert result.issues.index(second) < result.issues.index(third)


def test_source_already_merged_requires_using_its_current_replacement(store):
    left, right, third = [store.save_experience(data()) for _ in range(3)]
    merged = store.merge_experiences([left.id, right.id])
    assert not store.preview_merge([left.id, third.id])["can_merge"]
    latest = store.merge_experiences([merged.id, third.id])
    assert store.list_experiences(active_only=True) == [latest]
    assert merged.id in latest.merged_from


def test_merge_missing_source_changes_nothing(store):
    existing = store.save_experience(data())
    plan = store.preview_merge([existing.id, str(uuid4())])
    assert not plan["can_merge"] and "not found" in plan["conflicts"][0]
    assert store.list_experiences() == [existing]


def test_failed_merge_link_insert_rolls_back_new_record_and_all_links(store):
    left, right = [store.save_experience(data()) for _ in range(2)]
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("""CREATE TRIGGER fail_merge AFTER INSERT ON experience_merge_sources
            BEGIN SELECT RAISE(ABORT, 'private failure'); END""")
    with pytest.raises(ApplicationError) as caught:
        store.merge_experiences([left.id, right.id])
    assert caught.value.code == ErrorCode.EXPERIENCE_STORAGE_ERROR
    assert "private failure" not in caught.value.message
    assert len(store.list_experiences()) == 2
    assert len(store.list_experiences(active_only=True)) == 2
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM experience_merges").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_merge")
    assert store.merge_experiences([left.id, right.id])


def test_concurrent_duplicate_merge_commits_only_one_result(store):
    left, right = [store.save_experience(data()) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: store.merge_experiences([left.id, right.id]), range(8)))
    assert len({result.id for result in results}) == 1
    assert len(store.list_experiences()) == 3


def test_concurrent_overlapping_merges_cannot_consume_same_source_twice(store):
    left, right, third = [store.save_experience(data()) for _ in range(3)]
    def merge(ids):
        try:
            return store.merge_experiences(ids).id
        except ApplicationError as exc:
            assert exc.code == ErrorCode.EXPERIENCE_VALIDATION_ERROR
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(merge, [[left.id, right.id], [left.id, third.id]]))
    assert sum(result is not None for result in results) == 1
    assert len(store.list_experiences()) == 4


def test_synthetic_merge_provenance_cannot_bypass_merge_checks(store):
    with pytest.raises(ApplicationError):
        store.save_experience(data(merged_from=[str(uuid4()), str(uuid4())]))
    assert not store.database_path.exists()


def test_legacy_sources_keep_provenance_without_fabricating_test_evidence(store):
    left, right = [store.save_experience(data(issues=[], evidence=[])) for _ in range(2)]
    merged = store.merge_experiences([left.id, right.id])
    assert merged.evidence == [] and len(merged.issues) == 1
    assert {store.get_experience(identity).source_task_id for identity in merged.merged_from} == {
        left.source_task_id, right.source_task_id}


@pytest.mark.parametrize("kwargs", [
    {"language": []}, {"language": None}, {"repository_id": " "},
    {"repository_id": "\ud800"}, {"error_types": "ValueError"},
    {"error_types": [""]}, {"query": "\ud800"}, {"limit": True},
])
def test_invalid_retrieval_filters_fail_before_database_io(store, kwargs):
    arguments = {"language": "java", "repository_id": None, "error_types": []} | kwargs
    with pytest.raises(ApplicationError) as caught:
        store.retrieve_experiences(**arguments)
    assert caught.value.code == ErrorCode.EXPERIENCE_VALIDATION_ERROR
    assert not store.database_path.exists()


def test_mutated_issue_is_revalidated_before_persistence(store):
    value = data()
    value.issues[0].files.append("../outside")
    with pytest.raises(ApplicationError):
        store.save_experience(value)
    assert not store.database_path.exists()


@pytest.mark.parametrize("ids", [[], [str(uuid4())], [str(uuid4())] * 2, [str(uuid4()) for _ in range(11)], ["bad", "bad2"]])
def test_invalid_merge_selection_is_rejected_before_database_io(store, ids):
    with pytest.raises(ApplicationError):
        store.preview_merge(ids)
    assert not store.database_path.exists()


@pytest.mark.parametrize("details", ['{"id":"override"}', "[]", "not JSON", '{"issues":[{}]}'])
def test_invalid_details_cannot_override_identity_or_escape_validation(store, details):
    saved = store.save_experience(data())
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE experiences SET details=? WHERE id=?", (details, saved.id))
    with pytest.raises(ApplicationError) as caught:
        store.get_experience(saved.id)
    assert caught.value.code == ErrorCode.EXPERIENCE_STORAGE_ERROR
