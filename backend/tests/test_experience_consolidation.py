"""Observable invariants of durable rule maintenance (replaces synchronous V1 expectations)."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.main import create_app
from backend.experience import ExperienceStore
from backend.experience.database import connect
from backend.experience.jobs import drain,claim,process_page,list_jobs,retry
from backend.experience.knowledge import provenance_page
from backend.shared import ApplicationError,ConflictResolution,ExperienceCreate,RepairIssue
from backend.tests.test_project_api import configured


def record(**updates):
    task=str(uuid4())
    values=dict(schema_version=4,language='python',scope='project',repository_id='team/repository',
        task_summary='Input rule',root_cause='Missing input',solution='Reject missing input',constraints=[],
        verification='Original tests passed',source_task_id=task,applicability=['Missing text input'],
        issues=[dict(error_type='ValueError',root_cause='Missing input',solution='Reject missing input',files=['app.py'])],
        evidence=[dict(source_task_id=task,tool_call_id=str(uuid4()),runner='pytest',tests=3)])
    return ExperienceCreate(**(values|updates))


class EvidenceClassifier:
    """Controlled classifier for transaction tests; real model accuracy has a separate experiment."""
    version='test-classifier'
    available=False
    def threshold(self,kind): return .99
    def embed(self,*args,**kwargs): return None
    def infer(self,left,right):
        conflict=('Reject missing input' in left and 'Use a fallback' in right or
                  'Reject missing input' in right and 'Use a fallback' in left)
        return dict(entailment=0 if conflict else 1,neutral=0,contradiction=1 if conflict else 0)


def scan(store,semantic=None,**kwargs):
    return drain(store.database_path,seconds=20,semantic=semantic,**kwargs)


def save(store,value):
    return store.save_experiences([value],source_task_id=value.source_task_id)[0]


def retrieve(store,**kwargs):
    return store.retrieve_model_references(language='python',repository_id='team/repository',error_types=[],**kwargs)


def alternative():
    return record(issues=[dict(error_type='ValueError',root_cause='Missing input',solution='Use a fallback',files=['app.py'])])


def decision(source=None,**updates):
    return ConflictResolution(**(dict(version=1,resolution='prefer_source' if source else 'keep_separate',
        preferred_source_id=source,note='Checked original tests and business contract')|updates))


@pytest.fixture
def store(tmp_path): return ExperienceStore(tmp_path/'experiences.db')


def test_raw_save_is_durable_pending_and_batch_retry_keeps_ids(store):
    data=record();saved=save(store,data)
    assert store.get_experience(saved.id)==saved
    assert retrieve(store)==[]
    assert save(store,data)==saved
    assert len(list_jobs(store.database_path))==1
    scan(store)
    assert retrieve(store)[0]['id']==saved.id


def test_twenty_repeats_keep_flat_sources_and_unique_evidence(store):
    originals=[save(store,record()) for _ in range(20)]
    scan(store)
    [reference]=retrieve(store)
    assert reference['evidence_summary']['verified_runs']==20
    with sqlite3.connect(store.database_path) as db:
        assert db.execute('SELECT count(*) FROM experiences').fetchone()[0]==20
        assert db.execute('SELECT count(*) FROM knowledge_rules').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM knowledge_evidence').fetchone()[0]==20
    first=provenance_page(store.database_path,reference['rule_id'],limit=7)
    second=provenance_page(store.database_path,reference['rule_id'],offset=first['next_offset'],limit=100)
    assert set(first['source_ids']+second['source_ids'])=={v.id for v in originals}


def test_large_group_cannot_skip_contradictory_newcomer(store):
    for _ in range(61): save(store,record())
    save(store,alternative())
    assert retrieve(store)==[]
    result=scan(store,EvidenceClassifier())
    assert result.pending_jobs==0 and len(result.conflict_ids)==1
    assert retrieve(store)==[] and len(store.list_experiences(limit=100))==62


def test_unknown_is_a_suggestion_not_a_confirmed_conflict(store):
    save(store,record());save(store,alternative());result=scan(store)
    [review]=store.list_conflicts()
    assert review.relation=='unknown' and not result.conflict_ids
    assert result.unknown_pairs==1 and retrieve(store)==[]
    resolved=store.resolve_conflict(review.id,decision(resolution='confirm_compatible'))
    assert resolved.relation=='compatible' and len(retrieve(store))==2


def test_rule_scope_preserves_other_independent_rules_on_conflict(store):
    value=record();value.issues[0].symbols=['app.py#name']
    value.issues.append(RepairIssue(error_type='ValueError',root_cause='Age',solution='Reject negative age',files=['app.py'],symbols=['app.py#age']))
    save(store,value)
    other=alternative();other.issues[0].symbols=['app.py#name'];save(store,other)
    scan(store,EvidenceClassifier())
    assert [v['solution'] for v in retrieve(store)]==['Reject negative age']


def test_case_sensitive_paths_and_versions_do_not_collapse(store):
    left=record(compatibility_key='v1');left.issues[0].files=['Service.py']
    right=record(compatibility_key='v1');right.issues[0].files=['service.py']
    save(store,left);save(store,right)
    changed=alternative();changed.compatibility_key='v2';save(store,changed)
    scan(store,EvidenceClassifier())
    assert len(retrieve(store,compatibility_key='v1'))==2
    assert len(retrieve(store,compatibility_key='v2'))==1
    assert not store.list_conflicts()


def test_legacy_top_level_constraints_are_never_lost(store):
    save(store,record(schema_version=3,solution='Only use this rule for read-only requests.'))
    scan(store)
    [value]=retrieve(store)
    assert 'Only use this rule for read-only requests.' in value['constraints']


def test_evidence_identity_disagreement_pauses_affected_rules(store):
    first=record();save(store,first);scan(store)
    second=record(evidence=[first.evidence[0].model_copy(update={'tests':9})],source_task_id=first.source_task_id)
    store.save_experience(second)
    assert retrieve(store)==[]
    assert len(store.list_experiences())==2


def test_derivation_failure_preserves_original_and_durable_retry(store):
    save(store,record());scan(store)
    with sqlite3.connect(store.database_path) as db:
        db.execute("CREATE TRIGGER fail_derived BEFORE INSERT ON knowledge_relations BEGIN SELECT RAISE(ABORT,'fail'); END")
    original=save(store,alternative());scan(store)
    assert store.get_experience(original.id)==original and len(store.list_experiences())==2
    [job]=[v for v in list_jobs(store.database_path) if v['attempts']]
    assert job['state']=='pending'
    with sqlite3.connect(store.database_path) as db: db.execute('DROP TRIGGER fail_derived')
    retry(store.database_path,job['id']);scan(store)
    assert not any(v['state']=='failed' for v in list_jobs(store.database_path))


def test_expired_lease_recovers_and_fences_old_owner(store):
    save(store,record());old=claim(store.database_path)
    with sqlite3.connect(store.database_path) as db: db.execute('UPDATE knowledge_jobs SET lease_until=0')
    new=claim(store.database_path)
    assert new['lease_token']!=old['lease_token']
    process_page(store.database_path,old)
    assert retrieve(store)==[]
    process_page(store.database_path,new)
    assert len(retrieve(store))==1


def test_cursor_progress_is_persistent_and_unchecked_rules_stay_excluded(store,monkeypatch):
    for i in range(6):
        value=record();value.issues[0].root_cause=f'Cause {i}';save(store,value)
    monkeypatch.setattr('backend.experience.jobs.PAGE_SIZE',2)
    first=scan(store,max_pages=1)
    assert first.pending_jobs==6 and retrieve(store)==[]
    scan(ExperienceStore(store.database_path))
    assert len(retrieve(store,limit=20))==6


def test_rule_decision_is_versioned_reversible_and_preserves_other_pauses(store):
    left=save(store,record());right=save(store,alternative());scan(store,EvidenceClassifier())
    [review]=store.list_conflicts();choice=decision(left.id,version=review.version)
    resolved=store.resolve_conflict(review.id,choice)
    assert store.resolve_conflict(review.id,choice)==resolved
    assert [r['id'] for r in retrieve(store)]==[left.id]
    reopened=store.reopen_conflict(review.id,resolved.version,'New contract evidence')
    assert reopened.status=='pending' and retrieve(store)==[]
    assert store.reopen_conflict(review.id,resolved.version,'New contract evidence')==reopened
    assert [r['action'] for r in store.review_history(review.id)]==['resolve','reopen']
    assert store.get_experience(right.id)==right
    with pytest.raises(ApplicationError): store.resolve_conflict(review.id,choice)


def test_concurrent_opposite_decisions_commit_once(store):
    save(store,record());save(store,alternative());scan(store,EvidenceClassifier())
    [review]=store.list_conflicts()
    def resolve(source):
        try: return store.resolve_conflict(review.id,decision(source.id,version=review.version))
        except ApplicationError: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(resolve,review.sources))
    assert sum(r is not None for r in results)==1 and len(store.review_history(review.id))==1


def test_resolution_failure_rolls_back_pause_and_audit(store):
    save(store,record());save(store,alternative());scan(store,EvidenceClassifier())
    [review]=store.list_conflicts()
    with sqlite3.connect(store.database_path) as db:
        db.execute("CREATE TRIGGER fail_pause BEFORE INSERT ON knowledge_pauses BEGIN SELECT RAISE(ABORT,'fail'); END")
    with pytest.raises(ApplicationError): store.resolve_conflict(review.id,decision(version=review.version))
    assert store.list_conflicts()==[review] and not store.review_history(review.id)


def test_reads_do_not_acquire_reserved_write_lock(store):
    saved=save(store,record());done=threading.Event()
    with sqlite3.connect(store.database_path) as writer:
        writer.execute('BEGIN IMMEDIATE')
        thread=threading.Thread(target=lambda:(store.get_experience(saved.id),done.set()));thread.start()
        try: assert done.wait(1), 'read waited for an unrelated reserved writer'
        finally: writer.rollback();thread.join()


def test_explicit_dependencies_only_and_cycles_rejected(store):
    first=record();extra=RepairIssue(error_type='TypeError',root_cause='Type',solution='Check type',files=['type.py'])
    first.issues.append(extra);save(store,first)
    second=record(issues=list(reversed(first.issues)));save(store,second);scan(store)
    assert not store.list_conflicts()
    with pytest.raises(ValidationError):
        record(issues=[first.issues[0].model_copy(update={'depends_on':[1]}),extra.model_copy(update={'depends_on':[0]})])


def test_concurrent_duplicate_ingestion_is_flat_and_idempotent(store):
    with ThreadPoolExecutor(max_workers=4) as pool: originals=list(pool.map(lambda _:save(store,record()),range(8)))
    scan(store)
    assert len(retrieve(store))==1 and len({r.id for r in originals})==8


@pytest.mark.parametrize('changes',[dict(version=True),dict(version=0),dict(note=' '),dict(resolution='prefer_source'),dict(preferred_source_id=str(uuid4()))])
def test_resolution_contract_rejects_ambiguous_choices(changes):
    with pytest.raises(ValidationError): decision(**changes)


def test_api_progress_review_and_reopen(tmp_path,monkeypatch):
    from backend.experience.jobs import MaintenanceWorker
    # Route versioning is isolated from the worker's intentional policy-upgrade rescan.
    monkeypatch.setattr(MaintenanceWorker,'start',lambda self:None)
    monkeypatch.setattr(MaintenanceWorker,'close',lambda self:None)
    app=create_app(configured(tmp_path));store=app.state.experiences
    left=save(store,record());save(store,alternative());scan(store,EvidenceClassifier())
    with TestClient(app) as client:
        assert client.post('/api/experiences/jobs',json={}).status_code==422
        assert client.get('/api/experiences/jobs').status_code==200
        assert client.get(f'/api/experiences/{left.id}/maintenance').json()['effective_status']=='paused'
        [review]=client.get('/api/experiences/conflicts').json()
        url=f"/api/experiences/conflicts/{review['id']}"
        result=client.post(url+'/resolve',json=decision(left.id,version=review['version']).model_dump())
        assert result.status_code==200
        assert client.post(url+'/reopen',json={'version':result.json()['version'],'note':'Recheck'}).status_code==200
        assert len(client.get(url+'/history').json())==2
