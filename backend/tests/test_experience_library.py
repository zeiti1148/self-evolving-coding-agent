import json
from uuid import uuid4

import pytest

from backend.experience.database import connect
from backend.experience.history import enqueue_scan,run_once,validate,Proposal
from backend.experience.library import library
from backend.experience.store import ExperienceStore
from backend.shared import ExperienceCreate
from backend.tests.test_experience_consolidation import record,save


def legacy(store,text='Guard null before trimming; return the guest greeting for null.'):
    return store.save_experience(ExperienceCreate(task_summary='Fix greeting input',root_cause='Null is dereferenced before trimming',
        solution=text,verification='Historical prose only',tags=['java'],source_task_id=str(uuid4())))


class ArchiveModel:
    calls=0
    approve=True
    tamper=None
    def complete(self,messages):
        self.calls+=1;facts=json.loads(messages[1]['content']);sources=facts['sources']
        if facts['agent_role']=='history_grouping':
            return {'content':json.dumps(dict(groups=[dict(source_ids=[s['id'] for s in sources],representative_id=sources[0]['id'],
                bindings=[dict(source_id=s['id'],solution_quote=s['solution']) for s in sources],reason='Equivalent input guard')],reason='Archive duplicates'))}
        if self.tamper:self.tamper()
        return {'content':json.dumps(dict(approved=self.approve,checks=[dict(source_id=s['id'],equivalent_to_representative=self.approve,
            conditions_preserved=self.approve) for s in sources],reason='Independent conditions check'))}


@pytest.fixture
def old_records(tmp_path):
    store=ExperienceStore(tmp_path/'app.db');store.initialize()
    sources=[legacy(store),legacy(store,'Before trimming a name, handle null by returning the guest greeting.')]
    return store,sources


def test_history_consolidates_legacy_without_upgrading_trust_and_preserves_provenance(old_records):
    store,sources=old_records;model=ArchiveModel()
    before=library(store.database_path)
    assert before['summary']['unverified']==2
    first=enqueue_scan(store.database_path)
    assert enqueue_scan(store.database_path)['id']==first['id']
    assert run_once(store.database_path,lambda:model)
    data=library(store.database_path);[card]=data['items']
    assert data['summary']['original_records']==2 and data['summary']['knowledge_cards']==1
    assert card['kind']=='archive' and card['state']=='unverified' and card['evidence_count']==0
    assert set(card['source_ids'])=={s.id for s in sources} and card['source_count']==2
    assert all(store.get_experience(s.id)==s for s in sources)
    assert enqueue_scan(store.database_path)['id']==first['id']
    assert not run_once(store.database_path,lambda:model) and model.calls==2
    store.initialize()
    assert library(store.database_path)['items']==data['items']


def test_rejected_review_and_changed_sources_cannot_hide_original_records(old_records):
    store,sources=old_records;model=ArchiveModel();model.approve=False
    enqueue_scan(store.database_path);run_once(store.database_path,lambda:model)
    assert len(library(store.database_path)['items'])==2
    with connect(store.database_path) as db:
        assert db.execute('SELECT count(*) FROM experience_archive_groups').fetchone()[0]==0


def test_grouped_source_edit_invalidates_view_and_restores_raw_cards(old_records):
    store,sources=old_records;enqueue_scan(store.database_path);run_once(store.database_path,ArchiveModel)
    with connect(store.database_path) as db:db.execute('UPDATE experiences SET solution=? WHERE id=?',('A changed incompatible rule',sources[0].id))
    cards=library(store.database_path)['items']
    assert len(cards)==2 and all(c['kind']=='rule' for c in cards)


def test_history_requires_verbatim_quotes_and_identical_domains(old_records):
    store,sources=old_records;values=[s.model_dump(mode='json') for s in sources]
    proposal=Proposal(groups=[dict(source_ids=[s.id for s in sources],representative_id=sources[0].id,
        bindings=[dict(source_id=s.id,solution_quote=s.solution) for s in sources],reason='Equivalent')],reason='Check')
    validate(proposal,values)
    values[1]['repository_id']='different-context'
    with pytest.raises(ValueError):validate(proposal,values)
    values[1]['repository_id']=values[0]['repository_id'];proposal.groups[0].bindings[0].solution_quote='invented evidence'
    with pytest.raises(ValueError):validate(proposal,values)


def test_history_backfills_verified_jobs_and_library_counts_effective_rules(tmp_path):
    store=ExperienceStore(tmp_path/'app.db');store.initialize()
    first=save(store,record());second=save(store,record())
    with connect(store.database_path) as db:
        db.execute('DELETE FROM experience_auto_jobs')
        db.execute("UPDATE knowledge_rules SET state='ready'")
    scan=enqueue_scan(store.database_path)
    assert scan['scheduled']==2
    data=library(store.database_path)
    assert data['summary']['original_records']==2
    assert data['summary']['knowledge_cards']==1
    assert data['items'][0]['source_count']==2 and data['items'][0]['merged']


def test_library_get_is_read_only_and_keeps_scope_filters(old_records):
    store,sources=old_records
    with connect(store.database_path) as db:
        before=db.execute('SELECT count(*) FROM experience_history_scans').fetchone()[0]
    assert not library(store.database_path,language='python')['items']
    assert len(library(store.database_path,language='java')['items'])==2
    with connect(store.database_path) as db:assert db.execute('SELECT count(*) FROM experience_history_scans').fetchone()[0]==before


def test_failed_history_scan_requires_explicit_retry_and_preserves_attempt(old_records):
    store,sources=old_records
    first=enqueue_scan(store.database_path)
    def fail():raise RuntimeError('controlled unavailable model')
    run_once(store.database_path,fail)
    assert enqueue_scan(store.database_path)['id']==first['id']
    assert not run_once(store.database_path,fail)
    second=enqueue_scan(store.database_path,retry=True)
    assert second['id']!=first['id'] and enqueue_scan(store.database_path,retry=True)['id']==second['id']
    run_once(store.database_path,ArchiveModel)
    with connect(store.database_path) as db:
        assert db.execute('SELECT state FROM experience_history_scans WHERE id=?',(first['id'],)).fetchone()[0]=='failed'
    assert library(store.database_path)['items'][0]['source_count']==2


def test_next_history_batch_advances_past_already_reviewed_sources(tmp_path):
    store=ExperienceStore(tmp_path/'app.db');store.initialize()
    for _ in range(11):legacy(store)
    model=ArchiveModel();model.approve=False
    first=enqueue_scan(store.database_path);assert first['deferred']==1
    run_once(store.database_path,lambda:model)
    second=enqueue_scan(store.database_path)
    assert second['id']!=first['id'] and second['deferred']==0 and second['state']=='succeeded'
    data=library(store.database_path)
    assert data['summary']['original_records']==11
    # Exact immutable rule fingerprints already share references without a model.
    assert len(data['items'])==1 and data['items'][0]['source_count']==11
    assert data['items'][0]['kind']=='rule' and data['items'][0]['state']=='unverified'
    assert '缺少可核对' in data['items'][0]['reason']
