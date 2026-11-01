import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.experience import ExperienceStore
from backend.shared import ApplicationError, PromotionDraft
from backend.tests.test_experience_batches import batch, save


def source(store):
    return save(store, batch())[0]


def draft(**updates):
    return PromotionDraft(**(dict(task_summary="Validate missing and blank text", language="python",
        error_type="ValueError", root_cause="Missing and blank text need explicit policy",
        solution="Check missing input, then normalize and apply the target fallback or rejection policy",
        applicability=["Target must define missing and blank text behavior"], required_capabilities=["text-input"],
        issues=[dict(error_type="ValueError", root_cause="Missing text bypasses validation",
                     solution="Validate missing text before normalization")]) | updates))


def preview(store, identifier):
    method = getattr(store, "preview_promotion", None)
    assert method is not None, "promotion preview is required"
    return method(identifier)


def publish(store, identifier, value=None, **kwargs):
    method = getattr(store, "publish_global", None)
    assert method is not None, "explicit global publication is required"
    return method(identifier, value or draft(), **kwargs)


def test_preview_is_read_only_and_global_payload_excludes_source_private_data(tmp_path):
    store = ExperienceStore(tmp_path / "publish.db")
    original = source(store)
    count = len(store.list_experiences())
    plan = preview(store, original.id)
    assert plan.can_publish and plan.current_global_id is None
    assert len(store.list_experiences()) == count
    text = json.dumps(plan.injection_preview)
    assert original.id not in text and "app/rule0.py" not in text and original.source_task_id not in text
    assert all(not issue.files for issue in plan.draft.issues)


def test_publication_preserves_source_and_only_eligible_targets_receive_global(tmp_path):
    store = ExperienceStore(tmp_path / "publish.db")
    original = source(store)
    published = publish(store, original.id)
    assert published.scope == "global" and published.reuse_status == "active" and published.schema_version == 3
    assert published.derived_from == [original.id] and published.evidence == original.evidence
    assert store.get_experience(original.id) == original
    assert store.retrieve_experiences(language="python", repository_id="B", error_types=[], capabilities=["text-input"]) == [published]
    assert store.retrieve_experiences(language="python", repository_id="B", error_types=[]) == []
    assert store.retrieve_experiences(language="java", repository_id="B", error_types=[], capabilities=["text-input"]) == []


def test_concurrent_duplicate_publication_is_idempotent(tmp_path):
    store = ExperienceStore(tmp_path / "publish.db")
    original = source(store)
    with ThreadPoolExecutor(max_workers=3) as pool:
        values = list(pool.map(lambda _: publish(store, original.id), range(3)))
    assert len({value.id for value in values}) == 1
    assert len(store.list_experiences(scope="global")) == 1


def test_revision_requires_current_head_and_excludes_previous_injection(tmp_path):
    store = ExperienceStore(tmp_path / "publish.db")
    original = source(store)
    first = publish(store, original.id)
    updated = draft(solution="Reject missing text when the target forbids a fallback")
    assert preview(store, original.id).current_global_id == first.id
    with pytest.raises(ApplicationError):
        publish(store, original.id, updated)
    second = publish(store, original.id, updated, supersedes_id=first.id)
    assert second.supersedes_id == first.id and store.get_experience(first.id) == first
    assert publish(store, original.id, updated, supersedes_id=first.id) == second
    with pytest.raises(ApplicationError):
        publish(store, original.id, draft(solution="Another correction"), supersedes_id=first.id)
    assert store.retrieve_experiences(language="python", repository_id="B", error_types=[], capabilities=["text-input"]) == [second]


def test_concurrent_different_revisions_only_one_wins(tmp_path):
    store = ExperienceStore(tmp_path / "publish.db")
    original = source(store)
    first = publish(store, original.id)
    def update(i):
        try:
            return publish(store, original.id, draft(solution=f"Target policy {i}"), supersedes_id=first.id)
        except ApplicationError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, range(2)))
    assert sum(value is not None for value in results) == 1
    assert len(store.list_experiences(scope="global")) == 2


def test_publication_failure_rolls_back_record_head_and_links(tmp_path):
    store = ExperienceStore(tmp_path / "publish.db")
    original = source(store)
    preview(store, original.id)
    with sqlite3.connect(store.database_path) as db:
        db.execute("""CREATE TRIGGER fail_publication BEFORE INSERT ON experience_publication_heads
                      BEGIN SELECT RAISE(ABORT, 'injected'); END""")
    with pytest.raises(ApplicationError):
        publish(store, original.id)
    assert store.list_experiences(scope="global") == []
    with sqlite3.connect(store.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM experience_promotions").fetchone()[0] == 0


@pytest.mark.parametrize("invalid", ["language", "path", "repository", "file"])
def test_publication_rejects_private_paths_identifiers_and_wrong_language(tmp_path, invalid):
    store = ExperienceStore(tmp_path / "publish.db")
    original = source(store)
    value = draft()
    if invalid == "language": value.language = "java"
    if invalid == "path": value.solution = "Read app/rule0.py"
    if invalid == "repository": value.solution = "Use repository A defaults"
    if invalid == "file": value.issues[0].files = ["app/rule0.py"]
    with pytest.raises(ApplicationError):
        publish(store, original.id, value)
    assert not store.list_experiences(scope="global")


def test_no_evidence_legacy_merge_stays_unreviewed_and_cannot_publish(tmp_path):
    from backend.shared import ExperienceCreate
    store = ExperienceStore(tmp_path / "publish.db")
    data = dict(task_summary="Old", root_cause="Old cause", solution="Old rule", verification="Unproven")
    left = store.save_experience(ExperienceCreate(**data))
    right = store.save_experience(ExperienceCreate(**data))
    merged = store.merge_experiences([left.id, right.id])
    assert merged.schema_version == 3 and merged.reuse_status == "legacy_unreviewed" and not merged.evidence
    assert store.retrieve_experiences(language="java", repository_id="B", error_types=[]) == []
    assert not preview(store, merged.id).can_publish
    with pytest.raises(ApplicationError):
        publish(store, merged.id)


def test_merge_preserves_related_rules_within_each_multi_issue_source(tmp_path):
    from backend.shared import RepairIssue
    store = ExperienceStore(tmp_path / "related.db")
    records = batch()
    for index, item in enumerate(records):
        item.applicability = ["Same tested input contract"]
        item.issues = [RepairIssue(error_type="ValueError", root_cause=f"Group {index} missing", solution="Guard missing", files=[f"part{index}.py"]),
                       RepairIssue(error_type="ValueError", root_cause=f"Group {index} blank", solution="Guard normalized blank", files=[f"part{index}.py"])]
    saved = save(store, records)
    plan = store.preview_merge([saved[0].id, saved[1].id])
    assert plan["can_merge"], plan["conflicts"]
    assert len(store.merge_experiences([saved[0].id, saved[1].id]).issues) == 4


def test_merged_global_is_republished_by_new_record_and_old_versions_stay_excluded(tmp_path):
    store = ExperienceStore(tmp_path / "publish.db")
    left, right = source(store), source(store)
    first, second = publish(store, left.id), publish(store, right.id)
    merged = store.merge_experiences([first.id, second.id])
    with pytest.raises(ApplicationError):
        publish(store, left.id, draft(solution="Old head changed"), supersedes_id=first.id)
    revised = publish(store, merged.id, draft(solution="Merged target-specific policy"))
    assert revised.supersedes_id == merged.id and revised.derived_from == [merged.id]
    assert store.retrieve_experiences(language="python", repository_id="B", error_types=[], capabilities=["text-input"]) == [revised]
    assert preview(store, merged.id).can_publish
    again = publish(store, merged.id, draft(solution="Further clarification"), supersedes_id=revised.id)
    assert again.supersedes_id == revised.id
    assert store.retrieve_experiences(language="python", repository_id="B", error_types=[], capabilities=["text-input"]) == [again]


@pytest.mark.parametrize("field,value", [("reuse_status", "candidate"), ("required_capabilities", ["different"])])
def test_merge_checks_publication_status_and_capabilities(tmp_path, field, value):
    store = ExperienceStore(tmp_path / "publish.db")
    left = source(store)
    right = batch()[0]
    setattr(right, field, value)
    right = store.save_experience(right)
    assert not store.preview_merge([left.id, right.id])["can_merge"]
    with pytest.raises(ApplicationError):
        store.merge_experiences([left.id, right.id])
