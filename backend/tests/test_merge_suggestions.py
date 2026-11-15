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


def record(store, *, review=True, **changes):
    task = str(uuid4())
    runner = {"java": "maven", "python": "pytest", "go": "go", "cpp": "ctest"}[changes.get("language", "python")]
    values = dict(schema_version=3, task_summary="处理输入", root_cause="未验证输入", solution="先验证",
        verification="原始测试全部通过", language="python", scope="project", repository_id="repo-a",
        source_revision="rev-1", source_task_id=task, reuse_status="active", tags=["输入"],
        applicability=["外部文本输入"], required_capabilities=["text-input"], issues=[issue()],
        evidence=[VerificationEvidence(source_task_id=task, tool_call_id=str(uuid4()), runner=runner, tests=3)])
    values.update(changes)
    saved = store.save_experience(ExperienceCreate(**values))
    if review:
        # Normal saves enqueue maintenance; reading suggestions must never run it.
        from backend.experience.jobs import drain
        drain(store.database_path, seconds=20, semantic=None)
    return saved


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
    record(store, compatibility_key="input-contract-v1")
    record(store, issues=[issue("TypeError")], source_revision=None, compatibility_key="input-contract-v1")
    suggestion, = result(store)["suggestions"]
    assert suggestion["reasons"] == ["shared_files"]
    assert suggestion["issue_count"] == 2 and suggestion["shared_issue_count"] == 0
    assert suggestion["revision_warning"]


def test_legacy_host_git_and_tree_sources_keep_one_verified_merge_boundary(store):
    tree = 'tree:' + 'a' * 64
    previous = 'git:' + '1' * 40 + ';' + tree
    a = record(store, source_revision=previous, compatibility_key=previous)
    b = record(store, source_revision=tree, compatibility_key=tree)
    with closing(sqlite3.connect(store.database_path)) as db:
        before = list(db.iterdump())
    [suggestion] = result(store)['suggestions']
    assert not suggestion['revision_warning']
    preview = store.preview_merge([a.id, b.id])
    assert preview['can_merge'], preview
    assert preview['preview'].source_revision == tree
    with closing(sqlite3.connect(store.database_path)) as db:
        assert list(db.iterdump()) == before
    merged = store.merge_experiences([a.id, b.id])
    assert merged.source_revision == tree
    assert store.get_experience(a.id) == a and store.get_experience(b.id) == b


@pytest.mark.parametrize('left_contract,right_contract', [
    ('business-v1', 'business-v2'), ('business-v1', None), (None, 'business-v1'),
])
def test_same_source_content_does_not_override_explicit_contracts(store, left_contract, right_contract):
    tree = 'tree:' + 'a' * 64
    previous = 'git:' + '1' * 40 + ';' + tree
    a = record(store, source_revision=previous, compatibility_key=left_contract)
    b = record(store, source_revision=tree, compatibility_key=right_contract)
    assert result(store)['suggestions'] == []
    preview = store.preview_merge([a.id, b.id])
    assert not preview['can_merge']
    assert 'version_mismatch' in {detail['code'] for detail in preview['conflict_details']}


def test_same_git_host_does_not_hide_real_source_tree_changes(store):
    previous = 'git:' + '1' * 40 + ';tree:' + 'a' * 64
    current = 'git:' + '1' * 40 + ';tree:' + 'b' * 64
    a, b = record(store, source_revision=previous), record(store, source_revision=current)
    assert result(store)['suggestions'] == []
    assert not store.preview_merge([a.id, b.id])['can_merge']


@pytest.mark.parametrize("changes,reason", [
    ({"issues": [issue(files=["src/other.py"])]}, "unrelated"),
    ({"source_revision": "rev-2"}, "conflict"),
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
    a = record(store, applicability=['role == "Admin"', 'separator == "  "'])
    b = record(store, applicability=['role == "admin"', 'separator == " "'])
    # Differing conditions are first held for review by the normal rule worker.
    assert result(store)["eligible_count"] == 0
    from backend.experience.store import build_merge_preview
    assert "applicability_mismatch" in {detail["code"] for detail in build_merge_preview([a, b])["conflict_details"]}


def test_order_and_shared_evidence_conflicts_are_not_recommended(store):
    a = record(store, schema_version=4, issues=[issue(), issue("TypeError").model_copy(update={"depends_on": [0]})])
    b = record(store, schema_version=4, issues=[issue("TypeError"), issue().model_copy(update={"depends_on": [0]})])
    assert result(store)["suggestions"] == []
    from backend.experience.store import build_merge_preview
    assert "order_cycle" in {detail["code"] for detail in build_merge_preview([a, b])["conflict_details"]}
    other = ExperienceStore(store.database_path.parent / "evidence.db")
    record(other, evidence=a.evidence)
    changed = a.evidence[0].model_copy(update={"tests": 4})
    record(other, evidence=[changed])
    assert result(other)["eligible_count"] == 0


def test_same_test_call_is_counted_once(store):
    a = record(store)
    record(store, evidence=a.evidence)
    assert result(store)["suggestions"][0]["verification_count"] == 1


@pytest.mark.parametrize("language", ["java", "python", "go", "cpp"])
def test_schema_four_all_languages_keep_normal_auto_jobs_and_read_only_suggestions(store, language):
    a, b = record(store, schema_version=4, language=language), record(store, schema_version=4, language=language)
    with closing(sqlite3.connect(store.database_path)) as db:
        before = list(db.iterdump())
        assert db.execute("SELECT count(*) FROM experience_auto_jobs WHERE state='pending'").fetchone()[0] == 2
        [suggestion] = result(store, language=language)["suggestions"]
        assert suggestion["language"] == language and suggestion["source_ids"] == sorted([a.id, b.id])
        assert list(db.iterdump()) == before


def test_new_sources_wait_for_normal_maintenance_without_read_side_effects(store):
    record(store, review=False)
    record(store, review=False)
    with closing(sqlite3.connect(store.database_path)) as db:
        before = list(db.iterdump())
        assert result(store)["eligible_count"] == 0
        assert list(db.iterdump()) == before
    from backend.experience.jobs import drain
    drain(store.database_path, seconds=20)
    assert len(result(store)["suggestions"]) == 1


@pytest.mark.parametrize("changes", [
    {"applicability": ['role == "Admin"']},
    {"applicability": ['外部文本输入', '另一条件']},
    {"required_capabilities": ["integer-input"]},
    {"issues": [issue(solution="使用不同的默认值")]},
])
def test_unresolved_rule_relations_are_not_suggested_from_raw_active_status(store, changes):
    record(store)
    record(store, **changes)
    assert store.list_conflicts()
    assert all(item.reuse_status == "active" for item in store.list_experiences())
    assert result(store)["eligible_count"] == 0


def test_one_paused_subrule_blocks_whole_record_suggestion_and_preview(store):
    rules = [issue(), issue("TypeError", files=["src/other.py"])]
    a, b = record(store, schema_version=4, issues=rules), record(store, schema_version=4, issues=rules)
    assert len(result(store)["suggestions"]) == 1
    record(store, schema_version=4, issues=[issue(solution="使用不同的默认值")])
    [review] = store.list_conflicts()
    from backend.shared import ConflictResolution
    store.resolve_conflict(review.id, ConflictResolution(
        version=review.version, resolution="keep_separate", note="分别保留，等待边界测试补证"))
    assert len(store.experience_maintenance(a.id)["current_rule_ids"]) == 1
    assert result(store)["suggestions"] == []
    assert not store.preview_merge([a.id, b.id])["can_merge"]


def test_retired_sources_are_excluded_even_if_their_raw_status_is_active(store):
    a, b = record(store), record(store)
    with sqlite3.connect(store.database_path) as db:
        db.execute("INSERT INTO experience_retirements VALUES(?,?)", (a.id, str(uuid4())))
    assert result(store)["suggestions"] == []
    assert result(store)["eligible_count"] == 1
    assert store.get_experience(a.id).reuse_status == "active"
    assert not store.preview_merge([a.id, b.id])["can_merge"]


def test_missing_rule_projection_is_not_inferred_from_raw_evidence(store):
    a, b = record(store), record(store)
    with sqlite3.connect(store.database_path) as db:
        db.execute("DELETE FROM knowledge_sources WHERE experience_id=?", (a.id,))
    assert result(store)["eligible_count"] == 1
    assert result(store)["suggestions"] == []
    assert not store.preview_merge([a.id, b.id])["can_merge"]


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
