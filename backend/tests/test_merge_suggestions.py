from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest

from backend.experience import ExperienceStore
from backend.shared import ExperienceCreate, RepairIssue, VerificationEvidence


def issue(error="ValueError", solution="校验输入后保留正常行为", files=None):
    return RepairIssue(error_type=error, root_cause="未验证外部输入", solution=solution,
                       files=["src/service.py"] if files is None else files)


def record(store, **changes):
    task = str(uuid4())
    values = dict(schema_version=3, task_summary="处理输入", root_cause="未验证输入", solution="先验证",
        verification="原始测试全部通过", language="python", scope="project", repository_id="repo-a",
        source_revision="rev-1", source_task_id=task, reuse_status="active", tags=["输入"],
        applicability=["外部文本输入"], required_capabilities=["text-input"], issues=[issue()],
        evidence=[VerificationEvidence(source_task_id=task, tool_call_id=str(uuid4()), runner="pytest", tests=3)])
    values.update(changes)
    return store.save_experience(ExperienceCreate(**values))


@pytest.fixture
def store(tmp_path):
    return ExperienceStore(tmp_path / "suggestions.db")


def result(store, **filters):
    return store.suggest_merges(**filters).model_dump(mode="json")


def test_suggestions_are_read_only_and_reuse_real_merge_preview(store):
    a, b = record(store), record(store)
    digest = hashlib.sha256(store.database_path.read_bytes()).hexdigest()
    response = result(store)
    assert response["eligible_count"] == 2 and response["checked_pairs"] == 1
    suggestion, = response["suggestions"]
    assert suggestion["source_ids"] == sorted([a.id, b.id])
    assert suggestion["shared_issue_count"] == suggestion["issue_count"] == 1
    assert suggestion["verification_count"] == 2
    assert suggestion["reasons"] == ["shared_rules"] and not suggestion["revision_warning"]
    assert store.preview_merge(suggestion["source_ids"])["can_merge"]
    assert hashlib.sha256(store.database_path.read_bytes()).hexdigest() == digest
    assert len(store.list_experiences()) == 2
    assert result(store) == response


def test_same_file_complementary_rules_are_suggested_with_revision_warning(store):
    record(store)
    record(store, issues=[issue("TypeError")], source_revision=None)
    suggestion, = result(store)["suggestions"]
    assert suggestion["reasons"] == ["shared_files"]
    assert suggestion["issue_count"] == 2 and suggestion["shared_issue_count"] == 0
    assert suggestion["revision_warning"]


@pytest.mark.parametrize("changes,reason", [
    ({"applicability": ['role == "Admin"']}, "conditions"),
    ({"applicability": ['外部文本输入', '另一条件']}, "conditions"),
    ({"required_capabilities": ["integer-input"]}, "capabilities"),
    ({"issues": [issue(files=["src/other.py"])]}, "unrelated"),
    ({"issues": [issue(solution="使用不同的默认值")]}, "conflict"),
    ({"task_summary": "很" * 1000}, "conflict"),
])
def test_rejected_candidates_explain_why_without_writes(store, changes, reason):
    record(store)
    record(store, **changes)
    response = result(store)
    assert response["suggestions"] == []
    assert response["rejected"] == [{"reason": reason, "count": 1}]
    assert len(store.list_experiences()) == 2


def test_significant_case_and_internal_space_in_conditions_remain_distinct(store):
    record(store, applicability=['role == "Admin"', 'separator == "  "'])
    record(store, applicability=['role == "admin"', 'separator == " "'])
    assert result(store)["rejected"] == [{"reason": "conditions", "count": 1}]


def test_order_and_shared_evidence_conflicts_are_not_recommended(store):
    a = record(store, issues=[issue(), issue("TypeError")])
    record(store, issues=[issue("TypeError"), issue()])
    assert result(store)["rejected"] == [{"reason": "conflict", "count": 1}]
    other = ExperienceStore(store.database_path.parent / "evidence.db")
    record(other, evidence=a.evidence)
    changed = a.evidence[0].model_copy(update={"tests": 4})
    record(other, evidence=[changed])
    assert result(other)["rejected"] == [{"reason": "conflict", "count": 1}]


def test_same_test_call_is_counted_once(store):
    a = record(store)
    record(store, evidence=a.evidence)
    assert result(store)["suggestions"][0]["verification_count"] == 1


def test_scopes_filters_and_superseded_sources_are_respected(store):
    a, b = record(store), record(store)
    record(store, repository_id="repo-b")
    record(store, reuse_status="candidate")
    record(store, applicability=[])
    record(store, schema_version=2)
    assert result(store, repository_id="repo-b")["suggestions"] == []
    assert result(store, language="java")["eligible_count"] == 0
    assert result(store, scope="global")["eligible_count"] == 0
    assert result(store, reuse_status="candidate")["eligible_count"] == 0
    assert result(store)["eligible_count"] == 3
    merged = store.merge_experiences([a.id, b.id])
    assert result(store, repository_id="repo-a")["eligible_count"] == 1
    assert result(store)["suggestions"] == []
    assert store.get_experience(merged.id)


def test_missing_and_legacy_databases_are_not_created_or_migrated(tmp_path):
    missing = tmp_path / "missing" / "db.sqlite"
    assert result(ExperienceStore(missing))["scanned_count"] == 0
    assert not missing.parent.exists()
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as connection:
        connection.execute("CREATE TABLE experiences (id TEXT PRIMARY KEY, task_summary TEXT)")
    before = legacy.read_bytes()
    assert result(ExperienceStore(legacy))["suggestions"] == []
    assert legacy.read_bytes() == before


def test_wal_commits_are_visible_without_changing_knowledge_or_schema(store):
    # SQLite can maintain shared-memory sidecars during a read. immutable=1
    # would avoid them but could miss committed WAL rows from a live writer.
    store.list_experiences()  # Initialize the empty test schema before enabling WAL.
    with closing(sqlite3.connect(store.database_path)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("BEGIN")
        writer.execute("SELECT count(*) FROM experiences").fetchone()
        a, b = record(store), record(store)
        writer.commit()
        assert store.database_path.with_name(store.database_path.name + "-wal").stat().st_size > 32
        before = list(writer.iterdump())
        assert result(store)["suggestions"][0]["source_ids"] == sorted([a.id, b.id])
        assert list(writer.iterdump()) == before


def test_closed_legacy_wal_database_is_not_migrated_or_given_business_records(tmp_path):
    path = tmp_path / "legacy-wal.db"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE experiences (id TEXT PRIMARY KEY, task_summary TEXT)")
        writer.commit()
    before = path.read_bytes()
    assert result(ExperienceStore(path))["suggestions"] == []
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as reader:
        assert [row[1] for row in reader.execute("PRAGMA table_info(experiences)")] == ['id', 'task_summary']
        assert reader.execute("SELECT count(*) FROM experiences").fetchone()[0] == 0


def test_result_limit_reports_truncation_and_stable_priority(store):
    for _ in range(4):
        record(store)
    response = result(store, limit=2)
    assert len(response["suggestions"]) == 2
    assert response["truncation_reasons"] == ["result_limit"]
    assert response == result(store, limit=2)


def test_scan_and_candidate_caps_are_explicit(store, monkeypatch):
    from backend.experience import suggestions
    # Lower the named limits to exercise the same truncation paths with a small real DB.
    monkeypatch.setattr(suggestions, "SCAN_LIMIT", 6)
    monkeypatch.setattr(suggestions, "CANDIDATE_LIMIT", 3)
    for index in range(8):
        record(store, repository_id=f"repo-{index}")
    response = result(store)
    assert response["scanned_count"] == 6 and response["eligible_count"] == 3
    assert response["truncation_reasons"] == ["scan_limit", "candidate_limit"]
    assert response["checked_pairs"] == 0


@pytest.mark.parametrize("filters", [{"limit": 0}, {"limit": True}, {"limit": 21}, {"language": "ruby"}, {"reuse_status": "bogus"}])
def test_invalid_options_are_rejected(store, filters):
    from backend.shared import ApplicationError
    with pytest.raises(ApplicationError):
        result(store, **filters)
