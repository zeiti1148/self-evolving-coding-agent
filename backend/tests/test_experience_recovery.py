"""Recovery uses real pytest in isolated fixtures; model fixtures are not live evidence."""
import json
from pathlib import Path
from uuid import uuid4
import pytest

from backend.experience.automation import AutomaticMergeWorker
from backend.experience.database import connect
from backend.experience.knowledge import digest
from backend.experience.library import library
from backend.experience.recovery import RecoveryService,enqueue,history,active_grants
from backend.experience.retrieval import retrieve
from backend.tests.test_automatic_conflict_resolution import real_sources
from backend.tests.test_cross_upload_conflicts import ConflictModel
from backend.tests.test_experience_library import legacy,ArchiveModel
from backend.experience.history import enqueue_scan,run_once


def prepared(tmp_path):
    from unittest.mock import patch
    from backend.projects import source_revision
    from backend.tests.test_experience_consolidation import record
    def versioned(**values):
        revision=source_revision(tmp_path/('source'+values['repository_id'][-1]))
        return record(source_revision=revision,compatibility_key=revision,**values)
    with patch('backend.tests.test_automatic_conflict_resolution.record',side_effect=versioned):
        store,sources,verifier,manager=real_sources(tmp_path,'separate')
    worker=AutomaticMergeWorker(store.database_path,ConflictModel,verifier)
    assert worker.run_once() and worker.run_once()
    service=RecoveryService(store.database_path,verifier,lambda:None)
    return store,sources,verifier,manager,service


def test_paused_rule_recovers_only_inside_exact_verified_context(tmp_path):
    store,sources,verifier,manager,service=prepared(tmp_path)
    [review]=store.list_conflicts(status='resolved')
    card=next(c for c in library(store.database_path)['items'] if c['state']=='paused')
    job=enqueue(store.database_path,card['id'])
    assert enqueue(store.database_path,card['id'])['id']==job['id']
    assert service.run_once()
    recovered=history(store.database_path,card['id'])[0]
    assert recovered['state']=='succeeded',recovered
    assert len(recovered['runs'])==2 and all(r['outcome']=='passed' for r in recovered['runs'])
    assert store.list_conflicts(status='resolved')[0]==review
    assert all(store.get_experience(s.id)==s for s in sources)
    with connect(store.database_path) as db:
        grant=active_grants(db)[card['id']]
    context={k:grant[k] for k in ('repository_id','source_revision','compatibility_key','runner_hash')}
    assert card['id'] in {r['rule_id'] for r in retrieve(store.database_path,language='python',error_types=[],**context)}
    for change in ({'runner_hash':'0'*64},{'repository_id':'unrelated'},{'source_revision':'new-version'},{'runner_hash':None}):
        assert card['id'] not in {r['rule_id'] for r in retrieve(store.database_path,language='python',error_types=[],**(context|change))}
    assert next(c for c in library(store.database_path)['items'] if c['id']==card['id'])['reuse_mode']=='scoped'
    # A new human review invalidates the previous automatic grant immediately.
    store.reopen_conflict(review.id,review.version,'New contradictory evidence needs review')
    assert card['id'] not in {r['rule_id'] for r in retrieve(store.database_path,language='python',error_types=[],**context)}
    assert history(store.database_path,card['id'])[0]['state']=='invalidated'


def test_missing_legacy_evidence_never_becomes_a_pass_by_clicking(tmp_path):
    from backend.experience import ExperienceStore
    store=ExperienceStore(tmp_path/'app.db');store.initialize()
    a,b=legacy(store),legacy(store,'Before trimming a name, handle null by returning the guest greeting.')
    enqueue_scan(store.database_path);run_once(store.database_path,ArchiveModel)
    card=library(store.database_path)['items'][0]
    service=RecoveryService(store.database_path,None,lambda:None)
    enqueue(store.database_path,card['id']);assert service.run_once()
    latest=history(store.database_path,card['id'])[0]
    assert latest['state']=='needs_evidence' and not latest['can_confirm'] and not latest['runs']
    assert library(store.database_path)['items'][0]['state']=='unverified'


def test_changed_artifact_removes_the_scoped_grant(tmp_path):
    store,sources,verifier,manager,service=prepared(tmp_path)
    card=next(c for c in library(store.database_path)['items'] if c['state']=='paused')
    enqueue(store.database_path,card['id']);service.run_once()
    with connect(store.database_path) as db:grant=active_grants(db)[card['id']]
    Path(grant['artifacts'][0]['path']).write_text('altered evidence')
    context={k:grant[k] for k in ('repository_id','source_revision','compatibility_key','runner_hash')}
    assert not any(r['rule_id']==card['id'] for r in retrieve(store.database_path,language='python',error_types=[],**context))
    assert history(store.database_path,card['id'])[0]['state']=='invalidated'


def test_catalog_facets_search_and_pagination_use_the_whole_library(tmp_path):
    from backend.experience import ExperienceStore
    store=ExperienceStore(tmp_path/'app.db');store.initialize()
    for i in range(23):legacy(store,f'Rule {i}: guard null before converting and keep contract number {i}.')
    page=library(store.database_path,limit=5,offset=5,q='contract',language='java',state='unverified')
    assert len(page['items'])==5 and page['total']==23 and page['next_offset']==10
    assert page['summary']['unverified']==23 and page['facets']['languages']==[dict(value='java',label='java',count=23)]
    assert library(store.database_path,q='absent term',limit=5)['total']==0
    assert library(store.database_path,language='python',limit=5)['items']==[]


class LinkModel:
    def __init__(self, approve=True):self.approve=approve;self.calls=0
    def complete(self,messages):
        self.calls+=1;facts=json.loads(messages[1]['content'])
        return {'content':json.dumps(dict(equivalent=self.approve,new_issue_index=0,
            old_quote=facts['old']['solution'],new_quote=facts['new']['issues'][0]['solution'],
            reason='Unit fixture: compare the concrete tested contract'))}
    def close(self):pass


def archived_with_task(tmp_path,approve=True):
    from backend.shared import ExperienceCreate
    from backend.experience.recovery import attach_task
    store,sources,verifier,manager,service=prepared(tmp_path)
    old=[]
    for text in ('Conversion must return the contract value one.','Return one under the original conversion contract.'):
        old.append(store.save_experience(ExperienceCreate(language='python',task_summary='Old conversion repair',
            root_cause='Wrong contract value',solution=text,verification='Old prose only',tags=['python'],source_task_id=str(uuid4()))))
    enqueue_scan(store.database_path);run_once(store.database_path,ArchiveModel)
    card=next(c for c in library(store.database_path)['items'] if c['kind']=='archive')
    job=enqueue(store.database_path,card['id']);service.run_once()
    assert history(store.database_path,card['id'])[0]['state']=='needs_evidence'
    attach_task(store.database_path,job['id'],sources[0].source_task_id)
    model=LinkModel(approve);service.llm_factory=lambda:model
    assert service.run_once()
    return store,card,service,model,old


@pytest.mark.parametrize('approve',[True,False])
def test_legacy_link_requires_full_tests_and_independent_review(tmp_path,approve):
    store,card,service,model,old=archived_with_task(tmp_path,approve)
    result=history(store.database_path,card['id'])[0]
    assert len(result['runs'])==2 and model.calls==2
    assert result['state']==('succeeded' if approve else 'needs_confirmation')
    if not approve:
        assert result['can_confirm']
        result=service.confirm(result['id'],result['version'],'User checked the original business contract.',True)
        assert result['state']=='succeeded' and result['actor']=='user'
    assert next(c for c in library(store.database_path)['items'] if c['id']==card['id'])['kind']=='validated'
    assert all(store.get_experience(item.id)==item for item in old)
    # Retry retains the attached source task and cannot accidentally bypass linkage.
    retry=enqueue(store.database_path,card['id'],retry=True)
    assert retry['task_id']==result['task_id']
    assert service.run_once()
    assert model.calls==4


def test_confirmation_cannot_use_tampered_evidence(tmp_path):
    from backend.shared import ApplicationError
    store,card,service,model,old=archived_with_task(tmp_path,False)
    result=history(store.database_path,card['id'])[0]
    path=service.verifier.settings.workspace_root.parent/result['runs'][0]['artifact_path']
    path.write_text('tampered')
    with pytest.raises(ApplicationError,match='证据已变化'):
        service.confirm(result['id'],result['version'],'Checked',True)
    with connect(store.database_path) as db:assert not active_grants(db)
