"""Cross-business grouping controls; model effectiveness is measured by the live demo."""
import json

import pytest

from backend.experience import ExperienceStore
from backend.experience.database import connect
from backend.experience.principles import consolidate_principle, retrieve_principles
from backend.tests.test_experience_consolidation import record, save, scan


class Responses:
    def __init__(self,*values,callback=None):
        self.values=list(values);self.calls=0;self.callback=callback
    def complete(self,messages):
        self.calls+=1
        if self.callback and self.calls==2:
            self.callback()
        value=self.values.pop(0)
        if isinstance(value,Exception):
            raise value
        return {'content':json.dumps(value,ensure_ascii=False)}


@pytest.fixture
def setup(tmp_path):
    store=ExperienceStore(tmp_path/'experiences.db')
    a=record(repository_id='shop',constraints=['Money precision: 0.01'],
             issues=[dict(error_type='AssertionError',root_cause='Money rounding is imprecise',
                          solution='Construct decimal prices from original strings before arithmetic.',files=['invoice.py'])])
    b=record(repository_id='school',constraints=['Grade precision: 0.1; threshold 60'],
             issues=[dict(error_type='AssertionError',root_cause='The weighted mark rounds incorrectly',
                          solution='Read scores as decimal strings and apply the school rounding contract.',files=['grades.py'])])
    sources=[save(store,a),save(store,b)];scan(store)
    proposal=dict(decision='shared_principle',title='Decimal contracts',
        principle='Keep decimal inputs exact; use the precision and rounding specified by each business.',
        applicability=['The business contract specifies decimal arithmetic.'],boundaries=['Precision and thresholds remain source-specific.'],
        bindings=[dict(source_id=s.id,supports=[dict(issue_index=0,field='solution',quote=s.issues[0].solution)],
                       business_parameters={'precision':s.constraints[0]}) for s in sources],reason='Both sources require decimal semantics.')
    review=dict(approved=True,checks=[dict(source_id=s.id,supported=True,business_conditions_preserved=True,
                 reason='Its contract remains attached.') for s in sources],unsafe_generalizations=[],reason='Conditional shared principle.')
    return store,sources,proposal,review


def run(setup,model):
    store,sources,_,_=setup
    return consolidate_principle(store.database_path,[s.id for s in sources],model,namespace='owner/demo')


def test_shared_principle_preserves_business_instances_and_is_idempotent(setup):
    store,sources,proposal,review=setup
    model=Responses(proposal,review)
    result=run(setup,model)
    assert result['state']=='succeeded'
    [principle]=retrieve_principles(store.database_path,namespace='owner/demo')
    assert {i['experience_id']:i['constraints'] for i in principle['instances']}=={s.id:s.constraints for s in sources}
    assert principle['evidence_summary']['verified_runs']==2
    assert all(store.get_experience(s.id)==s for s in sources)
    assert not retrieve_principles(store.database_path,namespace='different-owner')
    assert run(setup,model)['reused'] and model.calls==2


def test_catalog_read_does_not_deadlock_a_concurrent_writer(setup,monkeypatch):
    import sqlite3
    import threading
    from backend.experience import principles
    from backend.experience.library import library
    store,_,proposal,review=setup
    assert run(setup,Responses(proposal,review))['state']=='succeeded'
    started=threading.Event();errors=[]
    def write():
        try:
            with sqlite3.connect(store.database_path,timeout=2) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE knowledge_principles SET data=data')
                started.set()
                db.commit()
        except Exception as exc:errors.append(exc);started.set()
    writer=threading.Thread(target=write)
    original=principles.current_principle
    def concurrent_read(*args,**kwargs):
        writer.start();assert started.wait(2)
        return original(*args,**kwargs)
    monkeypatch.setattr(principles,'current_principle',concurrent_read)
    data=library(store.database_path)
    writer.join(timeout=3)
    assert not writer.is_alive() and not errors
    assert any(c['kind']=='principle' for c in data['items'])


def test_fabricated_quotation_fails_before_review(setup):
    store,_,proposal,review=setup
    proposal['bindings'][0]['supports'][0]['quote']='Invented business rule not found in the source.'
    model=Responses(proposal,review)
    with pytest.raises(ValueError,match='verbatim'):
        run(setup,model)
    assert model.calls==1 and retrieve_principles(store.database_path,namespace='owner/demo')==[]
    assert run(setup,model)['state']=='failed' and model.calls==1


@pytest.mark.parametrize('change',['reject','missing_source','unsafe','false_condition'])
def test_review_cannot_be_overridden(setup,change):
    store,_,proposal,review=setup
    if change=='reject':review['approved']=False
    if change=='missing_source':review['checks'][0]['source_id']='wrong-source'
    if change=='unsafe':review['unsafe_generalizations']=['Rounding precision was copied between businesses.']
    if change=='false_condition':review['checks'][0]['business_conditions_preserved']=False
    assert run(setup,Responses(proposal,review))['state']=='rejected'
    assert retrieve_principles(store.database_path,namespace='owner/demo')==[]


def test_distinct_principles_never_get_a_forced_merge(setup):
    store,_,proposal,_=setup
    proposal['decision']='different_principles';proposal['bindings']=[]
    model=Responses(proposal)
    assert run(setup,model)['state']=='rejected' and model.calls==1
    assert not retrieve_principles(store.database_path,namespace='owner/demo')


def test_source_pause_during_review_prevents_commit(setup):
    store,sources,proposal,review=setup
    def pause():
        with connect(store.database_path) as connection:
            connection.execute("UPDATE knowledge_rules SET state='needs_review' WHERE id IN (SELECT rule_id FROM knowledge_sources WHERE experience_id=?)",(sources[0].id,))
    with pytest.raises(ValueError,match='verified'):
        run(setup,Responses(proposal,review,callback=pause))
    assert not retrieve_principles(store.database_path,namespace='owner/demo')


def test_source_pause_after_commit_hides_the_derived_principle(setup):
    store,sources,proposal,review=setup
    run(setup,Responses(proposal,review))
    with connect(store.database_path) as connection:
        connection.execute("UPDATE knowledge_rules SET state='failed' WHERE id IN (SELECT rule_id FROM knowledge_sources WHERE experience_id=?)",(sources[0].id,))
    assert not retrieve_principles(store.database_path,namespace='owner/demo')


def test_transport_failure_is_durable_without_automatic_paid_retries(setup):
    store,_,_,_=setup
    model=Responses(RuntimeError('test unavailable'))
    with pytest.raises(RuntimeError):run(setup,model)
    assert run(setup,model)['state']=='failed' and model.calls==1
    assert not retrieve_principles(store.database_path,namespace='owner/demo')


def test_same_repository_is_not_a_cross_business_demonstration(tmp_path):
    store=ExperienceStore(tmp_path/'experiences.db')
    a=save(store,record());b=save(store,record());scan(store)
    model=Responses()
    with pytest.raises(ValueError,match='different source repositories'):
        consolidate_principle(store.database_path,[a.id,b.id],model,namespace='demo')
    assert model.calls==0
