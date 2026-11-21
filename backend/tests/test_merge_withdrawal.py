"""A confirmed equivalence can be withdrawn; raw records and history are never rewritten."""
from backend.experience import ExperienceStore
from backend.shared import ConflictResolution
from backend.tests.test_experience_consolidation import alternative, record, retrieve, save, scan


def test_reopening_an_equivalent_decision_withdraws_the_merged_rule(tmp_path):
    store = ExperienceStore(tmp_path / 'merge.db')
    first = save(store, record())
    second = save(store, alternative())
    scan(store)
    [review] = store.list_conflicts()
    resolved = store.resolve_conflict(review.id, ConflictResolution(
        version=review.version, resolution='confirm_equivalent',
        note='Original tests and the business contract agree on the same boundary behaviour.'))
    merged = retrieve(store)
    # The two sources are now one rule.
    assert len(merged) == 1
    # Raw records stay readable and unchanged.
    assert store.get_experience(first.id) and store.get_experience(second.id)
    reopened = store.reopen_conflict(review.id, resolved.version,
                                     'A new regression contradicts the earlier equivalence.')
    assert reopened.status == 'pending'
    # Withdrawing the decision removes the merged rule from model input immediately.
    assert retrieve(store) == []
    # Raw records survive the withdrawal.
    assert store.get_experience(first.id) and store.get_experience(second.id)
    assert [item['action'] for item in store.review_history(review.id)] == ['resolve', 'reopen']


def test_equivalence_is_not_restored_without_a_new_decision(tmp_path):
    store = ExperienceStore(tmp_path / 'restore.db')
    save(store, record()); save(store, alternative()); scan(store)
    [review] = store.list_conflicts()
    resolved = store.resolve_conflict(review.id, ConflictResolution(
        version=review.version, resolution='confirm_equivalent',
        note='Original tests and the business contract agree on the same boundary behaviour.'))
    store.reopen_conflict(review.id, resolved.version, 'New evidence requires another review.')
    assert retrieve(store) == []
    # Reopening is not a toggle: only a new decision restores reuse.
    assert [item['action'] for item in store.review_history(review.id)] == ['resolve', 'reopen']
