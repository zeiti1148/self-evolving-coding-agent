"""Controlled model responses test guards; live model evidence is kept separately."""
import json

import pytest

from backend.experience import ExperienceStore
from backend.experience.automation import AutomaticMergeWorker, _plan, _pair_result, claim, feed, retry
from backend.experience.cross_conflicts import detect
from backend.experience.database import connect
from backend.experience.knowledge import source_status
from backend.experience.principles import consolidate_principle, current_principle
from backend.shared import ConflictResolution
from backend.tests.test_automatic_repair_merge import WorkflowModel
from backend.tests.test_experience_consolidation import record, save, scan


class ConflictModel:
    def __init__(self, mode='conflict', callback=None):
        self.mode, self.callback = mode, callback
        self.audit = []

    def complete(self, messages):
        data = json.loads(messages[1]['content'])
        self.audit.append(dict(phase=data['agent_role'], success=True, response_id='controlled-test'))
        if data['agent_role'] == 'conflict_detection':
            left, right = data['sources']
            claims = [dict(left_issue=0, right_issue=0, overlapping_condition='Both rules govern the same ordered setup contract.',
                left_quote=left['issues'][0]['solution'], right_quote=right['issues'][0]['solution'],
                reason='The two rules prescribe opposite orders under the same conditions.')]
            if self.mode == 'different_conditions':
                claims = []
            if self.mode == 'invented_quote':
                claims[0]['left_quote'] = 'This quotation never appeared in either source.'
            if self.mode == 'wrong_target':
                claims[0]['right_issue'] = 99
            value = dict(conflicts=claims, reason='Independent contract comparison.')
        else:
            assert [c['claim_index'] for c in data['claims']] == list(range(len(data['claims'])))
            if self.callback:
                self.callback()
            if self.mode == 'transport_failure':
                raise RuntimeError('Controlled review failure')
            value = dict(checks=[dict(claim_index=0, overlapping_scope=self.mode != 'disagree',
                mutually_exclusive=True, supported=True, reason='Verified against both complete source contracts.')],
                reason='Independent review completed.')
            if self.mode == 'missing_check':
                value['checks'][0]['claim_index'] = 2
        return {'content': json.dumps(value)}


def source(repository, solution, symbol='engine.py#setup'):
    return record(repository_id=repository, applicability=['Inherited resource initialization'],
        issues=[dict(error_type='AttributeError', root_cause='Initialization order matters.', solution=solution,
                     files=['engine.py'], symbols=[symbol] if symbol else [])])


@pytest.fixture
def prepared(tmp_path):
    store = ExperienceStore(tmp_path/'test.db')
    older = save(store, source('upload/old', 'Initialize child resources before all parent resources.'))
    newer = save(store, source('upload/new', 'Initialize parent resources before dependent child resources.'))
    scan(store)
    worker = AutomaticMergeWorker(store.database_path, lambda: ConflictModel())
    assert worker.run_once()  # No older candidate for the first source.
    return store, older, newer, worker


def running(prepared):
    store, *_ = prepared
    job = claim(store.database_path)
    _plan(store.database_path, job)
    with connect(store.database_path) as connection:
        pair = dict(connection.execute('SELECT * FROM experience_auto_pairs').fetchone())
    assert _pair_result(store.database_path, job, pair, 'running', 'testing')
    return job, pair


def test_normal_queue_publishes_conflict_and_resolution_preserves_originals(prepared):
    store, older, newer, worker = prepared
    assert worker.run_once()
    [review] = store.list_conflicts()
    assert review.relation == 'conflict' and review.status == 'pending'
    assert any(d.message == '独立模型复核' for d in review.details)
    assert source_status(store.database_path, older.id)['effective_status'] == 'paused'
    assert source_status(store.database_path, newer.id)['effective_status'] == 'paused'
    page = feed(store.database_path, experience_id=newer.id)['items'][0]
    assert page['state'] == 'waiting' and [c['kind'] for c in page['changes']] == ['review']
    # Either repository's normal filter can find a cross-upload review.
    assert store.list_conflicts(repository_id=older.repository_id)
    assert store.list_conflicts(repository_id=newer.repository_id)
    store.resolve_conflict(review.id, ConflictResolution(version=1, resolution='prefer_source',
        preferred_source_id=newer.id, note='The independent parent-before-child regression is now verified.'))
    while worker.run_once():
        pass
    assert source_status(store.database_path, older.id)['effective_status'] == 'paused'
    assert source_status(store.database_path, newer.id)['effective_status'] == 'active'
    change = feed(store.database_path, experience_id=newer.id)['items'][0]['changes'][0]
    assert change['state'] == 'resolved' and change['history'][-1]['data']['preferred_source_id'] == newer.id
    assert store.get_experience(older.id) == older and store.get_experience(newer.id) == newer
    with connect(store.database_path) as connection:
        result = json.loads(connection.execute('SELECT result FROM experience_auto_pairs').fetchone()[0])
    assert [a['phase'] for a in result['model_requests']] == ['conflict_detection', 'conflict_review']


@pytest.mark.parametrize('mode', ['different_conditions', 'disagree'])
def test_different_contract_or_review_disagreement_never_publishes(prepared, mode):
    store, *_ = prepared
    job, pair = running(prepared)
    result = detect(store.database_path, job, pair, ConflictModel(mode))
    assert result['conflict_ids'] == [] and store.list_conflicts() == []


@pytest.mark.parametrize('mode', ['invented_quote', 'wrong_target', 'missing_check'])
def test_unsupported_claims_and_incomplete_reviews_fail_closed(prepared, mode):
    store, *_ = prepared
    job, pair = running(prepared)
    with pytest.raises(ValueError):
        detect(store.database_path, job, pair, ConflictModel(mode))
    assert store.list_conflicts() == []


@pytest.mark.parametrize('mutation', ['lease', 'eligibility', 'contents'])
def test_publication_rechecks_snapshot_and_lease(prepared, mutation):
    store, older, _, _ = prepared
    job, pair = running(prepared)
    def mutate():
        with connect(store.database_path) as connection:
            if mutation == 'lease':
                connection.execute("UPDATE experience_auto_jobs SET lease_token='replacement' WHERE id=?", (job['id'],))
            elif mutation == 'eligibility':
                connection.execute("UPDATE knowledge_rules SET state='needs_review'")
            else:
                connection.execute("UPDATE experiences SET task_summary='changed contract' WHERE id=?", (older.id,))
    with pytest.raises(ValueError):
        detect(store.database_path, job, pair, ConflictModel(callback=mutate))
    assert store.list_conflicts() == []


def test_no_exact_grounded_symbol_does_not_call_a_model(tmp_path):
    store = ExperienceStore(tmp_path/'test.db')
    save(store, source('one', 'Initialize parents before children.', symbol=''))
    save(store, source('two', 'Initialize children before parents.', symbol='engine.py#other'))
    scan(store)
    worker = AutomaticMergeWorker(store.database_path, lambda: None)
    worker.run_once()
    job, pair = running((store,))
    model = ConflictModel()
    assert detect(store.database_path, job, pair, model)['state'] == 'not_applicable'
    assert model.audit == []


def test_failed_review_keeps_paid_proposal_and_audit_without_automatic_retry(prepared):
    store, _, newer, worker = prepared
    worker.llm_factory = lambda: ConflictModel('transport_failure')
    assert worker.run_once() and not worker.run_once()
    assert feed(store.database_path, experience_id=newer.id)['items'][0]['state'] == 'failed'
    with connect(store.database_path) as connection:
        result = json.loads(connection.execute('SELECT result FROM experience_auto_pairs').fetchone()[0])
    assert result['conflict_check']['assessment']['conflicts']
    assert len(result['model_requests']) == 2 and store.list_conflicts() == []
    retry(store.database_path, newer.id)
    retry(store.database_path, newer.id)
    with connect(store.database_path) as connection:
        rows = connection.execute('SELECT attempt,result FROM experience_auto_attempts').fetchall()
    assert len(rows) == 1 and rows[0][0] == 1 and json.loads(rows[0][1]) == result


def test_malformed_review_is_preserved_before_rejection(prepared):
    store, *_ = prepared
    job, pair = running(prepared)
    with pytest.raises(ValueError, match='every claim'):
        detect(store.database_path, job, pair, ConflictModel('missing_check'))
    with connect(store.database_path) as connection:
        value = json.loads(connection.execute('SELECT result FROM experience_auto_pairs').fetchone()[0])
    assert value['conflict_check']['review']['checks'][0]['claim_index'] == 2
    assert store.list_conflicts() == []


def test_pending_and_resolved_conflict_invalidate_old_shared_principle(prepared):
    store, older, newer, _ = prepared
    third = save(store, record(repository_id='third', issues=[dict(error_type='TypeError', root_cause='Other context',
        solution='Initialize child resources before parent resources.', files=['another.py'])]))
    scan(store)
    principle = consolidate_principle(store.database_path, [older.id, third.id], WorkflowModel(), namespace='test')
    assert current_principle(store.database_path, principle['id'])
    job, pair = running(prepared)
    detect(store.database_path, job, pair, ConflictModel())
    assert current_principle(store.database_path, principle['id']) is None
    [review] = store.list_conflicts()
    store.resolve_conflict(review.id, ConflictResolution(version=1, resolution='prefer_source',
        preferred_source_id=newer.id, note='A new verified counterexample refutes the old broad ordering rule.'))
    assert current_principle(store.database_path, principle['id']) is None
