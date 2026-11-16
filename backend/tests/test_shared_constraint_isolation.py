"""Temporary rule/grant fixtures exercise isolation, never live experiment data."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from backend.experience import ExperienceStore
from backend.experience.database import connect
from backend.experience.knowledge import canonical,digest,effective_rules,now,source_status
from backend.experience.library import library
from backend.experience.recovery import (active_grants,related_versions,rule_snapshot,shared_context_proof,source)
from backend.experience.retrieval import retrieve
from backend.tests.test_experience_consolidation import record,save,scan,decision
from backend.tools.workspace import inventory


CONSTRAINT='For class marks preserve the C,A,B MRO order.'


def rules(store,entry):
    with connect(store.database_path) as db:
        return [r[0] for r in db.execute('SELECT rule_id FROM knowledge_sources WHERE experience_id=? ORDER BY ordinal',(entry.id,))]


def reference(store,repository='pytest',**changes):
    return retrieve(store.database_path,language='python',repository_id=repository,error_types=[],**changes)


def relation(store,left,right,kind='conflict'):
    left,right=sorted((left,right));identifier=str(uuid4())
    with connect(store.database_path) as db:
        previous=db.execute('SELECT id FROM knowledge_relations WHERE left_rule=? AND right_rule=?',(left,right)).fetchone()
        if previous:
            identifier=previous[0]
            db.execute("UPDATE knowledge_relations SET kind=?,status='pending',version=1,resolution=NULL WHERE id=?",(kind,identifier))
        else:
            db.execute('INSERT INTO knowledge_relations(id,left_rule,right_rule,kind,details,model_version,created_at) VALUES(?,?,?,?,?,?,?)',
                       (identifier,left,right,kind,'{}','unit-boundary',now()))
    return identifier


@pytest.fixture
def bundle(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db')
    value=record(repository_id='pytest',source_revision='version-a',compatibility_key='contract-a',constraints=[CONSTRAINT],issues=[
        dict(error_type='AssertionError',root_cause='A generator violates the list API',solution='Return a concrete list',files=['marks.py'],symbols=['marks.py#unpack']),
        dict(error_type='AssertionError',root_cause='Multiple bases were not collected',solution='Collect marks in C,A,B order',files=['marks.py'],symbols=['marks.py#order'])])
    original=save(store,value)
    opposing=save(store,record(repository_id='pytest-other-version',issues=[dict(error_type='AssertionError',root_cause='Fixture order changed',
        solution='Collect marks in B,A,C order',files=['marks.py'],symbols=['marks.py#order'])]))
    unrelated=save(store,record(repository_id='sphinx',issues=[dict(error_type='AttributeError',root_cause='Attribute docs missing',
        solution='Find inherited attribute documentation',files=['autodoc.py'],symbols=['autodoc.py#members'])]))
    scan(store)
    return store,original,opposing,unrelated


def test_paused_sibling_cannot_leak_shared_constraints_and_sources_are_unchanged(bundle):
    store,original,opposing,unrelated=bundle
    safe,order=rules(store,original);opposite=rules(store,opposing)[0]
    assert any(v['rule_id']==safe and CONSTRAINT in v['constraints'] for v in reference(store))
    review=relation(store,order,opposite)
    store.resolve_conflict(review,decision())
    assert reference(store)==[]
    cards={c['id']:c for c in library(store.database_path)['items']}
    assert cards[safe]['state']==cards[order]['state']=='paused'
    assert source_status(store.database_path,original.id)['effective_status']=='paused'
    assert reference(store,'sphinx')[0]['id']==unrelated.id
    assert cards[rules(store,unrelated)[0]]['state']=='active'
    assert all(store.get_experience(item.id)==item for item in (original,opposing,unrelated))
    assert cards[safe]['constraints']==[CONSTRAINT]


@pytest.mark.parametrize('inactive',['retired','merged','replaced'])
def test_only_current_unknown_review_blocks_shared_context(bundle,inactive):
    store,original,opposing,unrelated=bundle
    safe,order=rules(store,original);opposite=rules(store,opposing)[0]
    relation(store,order,opposite,'unknown')
    assert reference(store)==[]
    assert all(c['state']=='paused' for c in library(store.database_path)['items'] if c['id'] in {safe,order})
    with connect(store.database_path) as db:
        if inactive=='merged':
            db.execute('INSERT INTO experience_merge_sources VALUES(?,?)',(opposing.id,unrelated.id))
        elif inactive=='replaced':
            db.execute('INSERT INTO experience_publication_revisions VALUES(?,?)',(opposing.id,unrelated.id))
        else:
            identifier=str(uuid4());left,right=sorted((opposing.id,unrelated.id))
            db.execute("INSERT INTO experience_conflicts(id,left_id,right_id,details,status,created_at) VALUES(?,?,?,'[]','resolved',?)",
                       (identifier,left,right,now()))
            db.execute('INSERT INTO experience_retirements VALUES(?,?)',(opposing.id,identifier))
    assert {v['rule_id'] for v in reference(store)}=={safe,order}
    assert all(c['state']=='active' for c in library(store.database_path)['items'] if c['id'] in {safe,order})
    assert library(store.database_path)['summary']['pending_reviews']==0


def test_pause_on_other_side_makes_pending_review_obsolete(bundle):
    store,original,opposing,unrelated=bundle
    safe,order=rules(store,original);opposite=rules(store,opposing)[0]
    relation(store,order,opposite,'unknown')
    other_review=relation(store,opposite,rules(store,unrelated)[0])
    with connect(store.database_path) as db:
        db.execute('INSERT INTO knowledge_pauses VALUES(?,?)',(opposite,other_review))
    assert {v['rule_id'] for v in reference(store)}=={safe,order}


def test_shared_context_and_dependencies_propagate_to_fixed_point(bundle):
    store,original,opposing,_=bundle
    safe,order=rules(store,original)
    child=save(store,record(repository_id='downstream',constraints=['Keep both downstream checks'],issues=[
        dict(error_type='ValueError',root_cause='Reader depends on marks',solution='Use the validated reader',files=['read.py']),
        dict(error_type='TypeError',root_cause='Writer reuses the same contract',solution='Use the validated writer',files=['write.py'])]))
    scan(store);first,second=rules(store,child)
    with connect(store.database_path) as db:
        db.execute('INSERT INTO knowledge_dependencies VALUES(?,?)',(safe,first))
    review=relation(store,order,rules(store,opposing)[0]);store.resolve_conflict(review,decision())
    with connect(store.database_path) as db:
        assert {safe,order,first,second}<=effective_rules(db)
    assert reference(store,'downstream')==[]


def test_retired_source_cannot_connect_an_independently_reverified_projection(bundle):
    store,original,opposing,_=bundle
    safe,order=rules(store,original)
    fresh=save(store,record(repository_id=original.repository_id,source_revision=original.source_revision,
        compatibility_key=original.compatibility_key,constraints=list(original.constraints),issues=[original.issues[0]]))
    scan(store)
    review=relation(store,order,rules(store,opposing)[0]);store.resolve_conflict(review,decision())
    assert reference(store)==[]
    with connect(store.database_path) as db:
        db.execute('INSERT INTO experience_merge_sources VALUES(?,?)',(original.id,fresh.id))
    assert [v['rule_id'] for v in reference(store)]==[safe]


def install_grant(store,entry,rule_id,tmp_path,*,new=False):
    """Persist a structural authorization fixture; real grant issuance has separate pytest tests."""
    work=tmp_path/f'verified-{rule_id}';work.mkdir(exist_ok=True)
    (work/'module.py').write_text('VALUE = 1\n')
    artifact=tmp_path/f'unit-artifact-{rule_id}.txt';artifact.write_text('Unit evidence fixture, not live engineering evidence')
    identifier=str(uuid4())
    with connect(store.database_path) as db:
        grant=dict(rule_id=rule_id,card_id=rule_id,recovery_id=identifier,rule_hash=digest(rule_snapshot(db,rule_id)),
            source_hashes={entry.id:digest(source(db,entry.id))},review_versions=related_versions(db,rule_id),
            workspace=str(work),inventory_hash=digest(inventory(work)),
            artifacts=[dict(path=str(artifact),sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())],
            project_id='pytest-upload',project_label='Pytest source',repository_id=entry.repository_id,
            compatibility_key=entry.compatibility_key,source_revision=entry.source_revision,runner_hash='a'*64)
        if new:grant['shared_context_hash']=shared_context_proof(db,rule_id)
        data=dict(scope={key:grant[key] for key in ('project_id','project_label','repository_id','compatibility_key','source_revision','runner_hash')},
                  reason='Unit scoped authorization',runs=[{},{}],after=dict(state='active',reuse_mode='scoped'))
        db.execute("INSERT INTO knowledge_recoveries(id,card_id,state,version,data,created_at,updated_at) VALUES(?,?,'succeeded',1,?,?,?)",
                   (identifier,rule_id,canonical(data),now(),now()))
        db.execute('INSERT OR REPLACE INTO knowledge_reuse_grants VALUES(?,?,?)',(rule_id,identifier,canonical(grant)))
    return grant


def test_legacy_grant_preserved_unless_another_shared_rule_is_blocked(bundle,tmp_path):
    store,original,opposing,_=bundle
    safe,order=rules(store,original)
    grant=install_grant(store,original,safe,tmp_path)
    with connect(store.database_path) as db:assert safe in active_grants(db)
    review=relation(store,order,rules(store,opposing)[0]);store.resolve_conflict(review,decision())
    with connect(store.database_path) as db:assert safe not in active_grants(db)
    context={key:grant[key] for key in ('compatibility_key','source_revision','runner_hash')}
    assert reference(store,**context)==[]
    assert next(c for c in library(store.database_path)['items'] if c['id']==safe)['state']=='paused'


def test_explicit_shared_context_grant_is_scoped_and_not_a_broad_sibling_restore(bundle,tmp_path):
    store,original,opposing,_=bundle
    safe,order=rules(store,original)
    review=relation(store,order,rules(store,opposing)[0]);store.resolve_conflict(review,decision())
    grant=install_grant(store,original,safe,tmp_path,new=True)
    context={key:grant[key] for key in ('compatibility_key','source_revision','runner_hash')}
    refs=reference(store,**context)
    assert [r['rule_id'] for r in refs]==[safe]
    assert refs[0]['reuse_boundary']['runner_hash']==grant['runner_hash']
    assert CONSTRAINT in refs[0]['constraints']
    for change in ({'runner_hash':'b'*64},{'runner_hash':None},{'source_revision':'other'},{'compatibility_key':'other'}):
        assert reference(store,**(context|change))==[]
    assert reference(store,'other',**context)==[]
    assert next(c for c in library(store.database_path)['items'] if c['id']==order)['state']=='paused'
    # A sibling's new/reopened review is not in the target's old direct review map.
    store.reopen_conflict(review,2,'New evidence changes the sibling boundary')
    with connect(store.database_path) as db:assert safe not in active_grants(db)
    assert reference(store,**context)==[]


def test_legacy_target_grant_keeps_only_necessary_safe_prerequisites_in_scope(bundle,tmp_path):
    store,original,opposing,_=bundle
    safe,order=rules(store,original)
    with connect(store.database_path) as db:
        db.execute('INSERT INTO knowledge_prerequisites VALUES(?,?)',(safe,order))
        db.execute('INSERT INTO knowledge_dependencies VALUES(?,?)',(safe,order))
    review=relation(store,order,rules(store,opposing)[0]);store.resolve_conflict(review,decision())
    grant=install_grant(store,original,order,tmp_path)
    context={key:grant[key] for key in ('compatibility_key','source_revision','runner_hash')}
    with connect(store.database_path) as db:assert order in active_grants(db)
    refs=reference(store,**context)
    assert {r['rule_id'] for r in refs}=={safe,order}
    prerequisite=next(r for r in refs if r['rule_id']==safe)
    assert prerequisite['reuse_boundary']['prerequisite_for']==order
    assert reference(store,**(context|{'runner_hash':'b'*64}))==[]
    cards={c['id']:c for c in library(store.database_path)['items']}
    assert cards[safe]['reuse_mode']==cards[order]['reuse_mode']=='scoped'
    with connect(store.database_path) as db:
        db.execute("UPDATE knowledge_rules SET state='needs_review',version=version+1 WHERE id=?",(safe,))
    with connect(store.database_path) as db:assert order not in active_grants(db)
    assert reference(store,**context)==[]


def test_shared_context_grant_rejects_changed_dependency_provenance(bundle,tmp_path):
    store,original,opposing,unrelated=bundle
    safe,order=rules(store,original);parent=rules(store,unrelated)[0]
    review=relation(store,order,rules(store,opposing)[0]);store.resolve_conflict(review,decision())
    with connect(store.database_path) as db:
        db.execute('INSERT INTO knowledge_dependencies VALUES(?,?)',(parent,order))
    grant=install_grant(store,original,safe,tmp_path,new=True)
    with connect(store.database_path) as db:assert safe in active_grants(db)
    with connect(store.database_path) as db:
        db.execute('INSERT INTO experience_merge_sources VALUES(?,?)',(unrelated.id,opposing.id))
    with connect(store.database_path) as db:assert safe not in active_grants(db)


def test_new_pause_between_retrieval_snapshots_cannot_leak_a_sibling(bundle):
    store,original,opposing,_=bundle
    safe,order=rules(store,original);review=relation(store,order,rules(store,opposing)[0],'compatible')
    class PauseDuringEmbedding:
        available=True
        version='unit-race'
        def embed(self,*args,**kwargs):
            with connect(store.database_path) as db:
                db.execute("UPDATE knowledge_relations SET kind='conflict' WHERE id=?",(review,))
            store.resolve_conflict(review,decision())
            return None
    with connect(store.database_path) as db:
        db.execute('INSERT INTO knowledge_embeddings VALUES(?,?,?)',(safe,'unit-race',b''))
    assert retrieve(store.database_path,'marks',language='python',repository_id=original.repository_id,
                    error_types=[],semantic=PauseDuringEmbedding())==[]


def test_dependency_inheritance_cannot_override_a_separately_restricted_parent(bundle,tmp_path):
    store,original,opposing,_=bundle
    safe,order=rules(store,original)
    with connect(store.database_path) as db:
        db.execute('INSERT INTO knowledge_prerequisites VALUES(?,?)',(safe,order))
        db.execute('INSERT INTO knowledge_dependencies VALUES(?,?)',(safe,order))
    review=relation(store,order,rules(store,opposing)[0]);store.resolve_conflict(review,decision())
    target=install_grant(store,original,order,tmp_path)
    parent=install_grant(store,original,safe,tmp_path,new=True)
    # A separately scoped parent is not implicitly reauthorized by its child.
    with connect(store.database_path) as db:
        parent['runner_hash']='b'*64
        db.execute('UPDATE knowledge_reuse_grants SET data=? WHERE rule_id=?',(canonical(parent),safe))
    with connect(store.database_path) as db:assert order not in active_grants(db)
    context={key:target[key] for key in ('compatibility_key','source_revision','runner_hash')}
    assert reference(store,**context)==[]


def test_scoped_prerequisite_is_never_injected_without_its_authorized_target(bundle,tmp_path):
    store,original,opposing,_=bundle
    value=record(repository_id='scoped',source_revision='version-a',compatibility_key='contract-a',constraints=[CONSTRAINT],
        issues=[original.issues[0].model_copy(update={'error_type':'TypeError'}),
                original.issues[1].model_copy(update={'error_type':'ValueError','depends_on':[0]})])
    selected=save(store,value);scan(store);parent,target=rules(store,selected)
    review=relation(store,target,rules(store,opposing)[0]);store.resolve_conflict(review,decision())
    grant=install_grant(store,selected,target,tmp_path)
    context={key:grant[key] for key in ('repository_id','compatibility_key','source_revision','runner_hash')}
    assert retrieve(store.database_path,language='python',error_types=['TypeError'],**context)==[]
    refs=retrieve(store.database_path,language='python',error_types=['ValueError'],**context)
    assert {r['rule_id'] for r in refs}=={parent,target}
    assert next(r for r in refs if r['rule_id']==parent)['reuse_boundary']['prerequisite_for']==target


def test_sibling_scope_restriction_propagates_without_a_prerequisite_edge(bundle,tmp_path):
    store,original,_,_=bundle
    safe,order=rules(store,original)
    grant=install_grant(store,original,order,tmp_path)
    context={key:grant[key] for key in ('compatibility_key','source_revision','runner_hash')}
    assert [r['rule_id'] for r in reference(store,**context)]==[order]
    assert reference(store,**(context|{'runner_hash':'b'*64}))==[]
    assert next(c for c in library(store.database_path)['items'] if c['id']==safe)['state']=='paused'
    with connect(store.database_path) as db:
        db.execute("UPDATE knowledge_recoveries SET state='running' WHERE id=?",(grant['recovery_id'],))
    assert reference(store,**context)==[]


@pytest.mark.parametrize('change',['recovery','artifact'])
def test_new_context_proof_rechecks_sibling_authorization_state(bundle,tmp_path,change):
    store,original,_,_=bundle
    safe,order=rules(store,original)
    parent=install_grant(store,original,order,tmp_path)
    grant=install_grant(store,original,safe,tmp_path,new=True)
    context={key:grant[key] for key in ('compatibility_key','source_revision','runner_hash')}
    with connect(store.database_path) as db:assert safe in active_grants(db)
    assert {r['rule_id'] for r in reference(store,**context)}=={safe,order}
    if change=='recovery':
        with connect(store.database_path) as db:
            db.execute("UPDATE knowledge_recoveries SET state='running',version=version+1 WHERE id=?",(parent['recovery_id'],))
    else:
        Path(parent['artifacts'][0]['path']).write_text('Changed sibling evidence')
    with connect(store.database_path) as db:
        assert safe not in active_grants(db)
    assert reference(store,**context)==[]


def test_new_context_proof_is_checked_even_when_the_other_blocker_disappears(bundle,tmp_path):
    from backend.experience.knowledge import blocked_without_seeds
    store,original,opposing,_=bundle
    safe,order=rules(store,original)
    review=relation(store,order,rules(store,opposing)[0]);store.resolve_conflict(review,decision())
    grant=install_grant(store,original,safe,tmp_path,new=True)
    store.reopen_conflict(review,2,'Review new source evidence')
    store.resolve_conflict(review,decision(version=3,resolution='confirm_compatible'))
    with connect(store.database_path) as db:
        assert not blocked_without_seeds(db,{safe})
        assert safe not in active_grants(db)


def test_same_scope_parent_and_child_grants_do_not_invalidate_each_other(bundle,tmp_path):
    store,original,opposing,_=bundle
    safe,order=rules(store,original)
    with connect(store.database_path) as db:
        db.execute('INSERT INTO knowledge_prerequisites VALUES(?,?)',(safe,order))
        db.execute('INSERT INTO knowledge_dependencies VALUES(?,?)',(safe,order))
    review=relation(store,order,rules(store,opposing)[0]);store.resolve_conflict(review,decision())
    parent=install_grant(store,original,safe,tmp_path,new=True)
    child=install_grant(store,original,order,tmp_path,new=True)
    context={key:child[key] for key in ('compatibility_key','source_revision','runner_hash')}
    with connect(store.database_path) as db:
        assert {safe,order}<=active_grants(db).keys()
    assert {r['rule_id'] for r in reference(store,**context)}=={safe,order}
    replacement=install_grant(store,original,safe,tmp_path,new=True)
    assert replacement['recovery_id']!=parent['recovery_id']
    with connect(store.database_path) as db:assert {safe,order}<=active_grants(db).keys()
    with connect(store.database_path) as db:
        db.execute("UPDATE knowledge_recoveries SET state='running' WHERE id=?",(replacement['recovery_id'],))
    assert reference(store,**context)==[]


def test_two_expired_peer_grants_recover_sequentially_without_premature_reuse(bundle,tmp_path):
    from backend.experience.recovery import history
    store,original,_,_=bundle
    safe,order=rules(store,original)
    first=install_grant(store,original,safe,tmp_path,new=True)
    second=install_grant(store,original,order,tmp_path,new=True)
    context={key:first[key] for key in ('compatibility_key','source_revision','runner_hash')}
    for grant in (first,second):Path(grant['artifacts'][0]['path']).write_text('Expired evidence')
    assert reference(store,**context)==[]
    install_grant(store,original,safe,tmp_path,new=True)
    assert reference(store,**context)==[]
    pending=history(store.database_path,safe)[0]
    assert pending['state']=='invalidated' and pending['after']['state']=='paused'
    assert '本条补充验证已通过' in pending['reason'] and '无需重复验证本条' in pending['reason']
    install_grant(store,original,order,tmp_path,new=True)
    assert {r['rule_id'] for r in reference(store,**context)}=={safe,order}
    restored=history(store.database_path,safe)[0]
    assert restored['state']=='succeeded' and restored['after']['state']=='active'
    assert '已恢复复用' in restored['reason']
    assert next(c for c in library(store.database_path)['items'] if c['id']==safe)['state']=='active'


@pytest.mark.parametrize('new',[False,True])
def test_single_rule_grant_does_not_override_blocked_provenance_ancestor(bundle,tmp_path,new):
    store,original,opposing,_=bundle
    safe,order=rules(store,original)
    derived=save(store,record(repository_id='derived',source_revision='derived-v1',compatibility_key='derived-v1',
        issues=[dict(error_type='ValueError',root_cause='Derived implementation',solution='Retain its verified source behavior',files=['derived.py'])]))
    scan(store);target=rules(store,derived)[0]
    with connect(store.database_path) as db:
        db.execute('INSERT INTO knowledge_dependencies VALUES(?,?)',(safe,target))
    grant=install_grant(store,derived,target,tmp_path,new=new)
    context={key:grant[key] for key in ('compatibility_key','source_revision','runner_hash')}
    assert [r['rule_id'] for r in reference(store,'derived',**context)]==[target]
    review=relation(store,order,rules(store,opposing)[0]);store.resolve_conflict(review,decision())
    assert reference(store,'derived',**context)==[]
    if new:assert grant['shared_context_hash'] is not None
    # Even fresh target evidence cannot override an unavailable source ancestor.
    install_grant(store,derived,target,tmp_path,new=True)
    assert reference(store,'derived',**context)==[]


@pytest.mark.parametrize('new',[False,True])
def test_scoped_ancestor_source_quarantine_also_blocks_derived_authorization(bundle,tmp_path,new):
    store,original,opposing,_=bundle
    parent=rules(store,original)[0]
    derived=save(store,record(repository_id=original.repository_id,source_revision=original.source_revision,
        compatibility_key=original.compatibility_key,issues=[dict(error_type='ValueError',root_cause='Derived source rule',
            solution='Preserve the checked source behavior',files=['derived.py'])]))
    scan(store);target=rules(store,derived)[0]
    with connect(store.database_path) as db:
        db.execute('INSERT INTO knowledge_dependencies VALUES(?,?)',(parent,target))
    install_grant(store,original,parent,tmp_path,new=new)
    grant=install_grant(store,derived,target,tmp_path,new=new)
    context={key:grant[key] for key in ('compatibility_key','source_revision','runner_hash')}
    assert target in {r['rule_id'] for r in reference(store,**context)}
    with connect(store.database_path) as db:
        left,right=sorted((original.id,opposing.id))
        db.execute("INSERT INTO experience_conflicts(id,left_id,right_id,details,status,created_at) VALUES(?,?,?,'[]','pending',?)",
                   (str(uuid4()),left,right,now()))
        assert not ({parent,target}&active_grants(db).keys())
    assert reference(store,**context)==[]


@pytest.mark.parametrize('new',[False,True])
def test_equivalent_bridge_grant_cannot_restore_unreviewed_endpoints(tmp_path,new):
    store=ExperienceStore(tmp_path/'rules.db');entries={}
    for text in ('Alpha','Beta','Gamma'):
        entry=save(store,record(repository_id='pytest',source_revision='version-a',compatibility_key='contract-a',
            issues=[dict(error_type='ValueError',root_cause=text,solution=text,files=['app.py'])]))
        entries[rules(store,entry)[0]]=entry
    scan(store);left,bridge,right=sorted(entries)
    with connect(store.database_path) as db:
        reviews=[dict(row) for row in db.execute('SELECT * FROM knowledge_relations')]
    endpoint_review=None
    for review in reviews:
        is_endpoint={review['left_rule'],review['right_rule']}=={left,right}
        resolved=store.resolve_conflict(review['id'],decision(resolution='confirm_compatible' if is_endpoint else 'confirm_equivalent'))
        if is_endpoint:endpoint_review=resolved
    grant=install_grant(store,entries[bridge],bridge,tmp_path,new=new)
    context={key:grant[key] for key in ('compatibility_key','source_revision','runner_hash')}
    assert bridge in {ref['rule_id'] for ref in reference(store,**context)}
    store.reopen_conflict(endpoint_review.id,endpoint_review.version,'Endpoint behavior requires new review')
    with connect(store.database_path) as db:
        assert bridge not in active_grants(db)
        assert set(entries)<=effective_rules(db)
    assert reference(store,**context)==[]
    assert all(card['state']=='paused' for card in library(store.database_path)['items'])


@pytest.mark.parametrize('view',['library','history'])
def test_each_view_checks_files_once_per_grant_and_never_caches_across_requests(bundle,tmp_path,monkeypatch,view):
    from backend.experience import recovery
    store,original,_,_=bundle
    safe,order=rules(store,original)
    install_grant(store,original,safe,tmp_path,new=True)
    install_grant(store,original,safe,tmp_path,new=True)  # Also retain an older history row.
    other=install_grant(store,original,order,tmp_path,new=True)
    checked=[];validate=recovery.grant_files_valid
    def inspect(grant):
        checked.append(grant['rule_id'])
        return validate(grant)
    monkeypatch.setattr(recovery,'grant_files_valid',inspect)
    def read():
        if view=='history':return recovery.history(store.database_path,safe)[0]
        return next(card for card in library(store.database_path)['items'] if card['id']==safe)
    initial=read()
    assert initial['state']==('succeeded' if view=='history' else 'active')
    assert sorted(checked)==sorted([safe,order])
    Path(other['artifacts'][0]['path']).write_text('Evidence changed between two actual requests')
    checked.clear();changed=read()
    assert changed['state']==('invalidated' if view=='history' else 'paused')
    assert sorted(checked)==sorted([safe,order])
