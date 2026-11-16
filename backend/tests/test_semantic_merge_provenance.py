"""Semantic grouping must preserve evidence and reject context-dominated scores."""
from copy import deepcopy

from backend.experience import ExperienceStore
from backend.experience.database import connect
from backend.experience.equivalence import equivalent_groups
from backend.experience.knowledge import provenance_page
from backend.experience.relations import classify
from backend.tests.test_experience_consolidation import EvidenceClassifier, decision, record, retrieve, save, scan


def projected(value):
    return dict(language=value.language, repository_id=value.repository_id, scope=value.scope,
                compatibility_key='', required_capabilities=[], applicability=value.applicability,
                constraints=value.constraints, issue=value.issues[0].model_dump(exclude={'depends_on'}))


def test_shared_conditions_cannot_hide_different_actions():
    left=projected(record())
    right=deepcopy(left)
    left['issue']['solution']='read 模式下 None 返回 guest。'
    right['issue']['solution']='write 模式下 None 抛出 ValueError。'
    class ContextDominated(EvidenceClassifier):
        def infer(self,a,b):
            return dict(entailment=.999 if '\n' in a else .15,neutral=.85,contradiction=0)
    kind,details=classify(left,right,ContextDominated())
    assert kind=='unknown'
    assert details['scores']['entailment']==.999 and details['action_scores']['entailment']==.15


def test_global_literals_cannot_mask_a_different_action_literal():
    left=projected(record(constraints=['Return "guest" for read; return "blocked" for write.']))
    right=deepcopy(left)
    left['issue']['solution']='Return "guest".'
    right['issue']['solution']='Return "blocked".'
    assert classify(left,right,EvidenceClassifier())[0]=='unknown'


def test_same_context_and_score_do_not_merge_different_error_types():
    left=projected(record());right=deepcopy(left)
    right['issue'].update(error_type='TypeError',solution='Reject the missing input')
    assert classify(left,right,EvidenceClassifier())[0]=='unknown'


def test_semantic_reference_and_provenance_pool_distinct_sources(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db')
    first=save(store,record())
    second=record();second.issues[0].solution='Reject the missing input'
    second=save(store,second)
    scan(store,EvidenceClassifier())
    [ref]=retrieve(store)
    assert set(ref['source_ids'])=={first.id,second.id}
    assert ref['evidence_summary']['verified_runs']==2
    page=provenance_page(store.database_path,ref['rule_id'],limit=1)
    following=provenance_page(store.database_path,ref['rule_id'],offset=page['next_offset'])
    assert set(page['source_ids']+following['source_ids'])=={first.id,second.id}
    assert page['evidence_count']==2
    assert store.get_experience(first.id)==first and store.get_experience(second.id)==second


def test_shared_evidence_is_counted_once(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db')
    value=record();save(store,value)
    other=value.model_copy(deep=True);other.issues[0].solution='Reject the missing input'
    store.save_experience(other)
    scan(store,EvidenceClassifier())
    [ref]=retrieve(store)
    assert len(ref['source_ids'])==2
    assert ref['evidence_summary']['verified_runs']==1
    assert provenance_page(store.database_path,ref['rule_id'])['evidence_count']==1


def test_error_filter_cannot_bypass_an_unreviewed_unknown_relation(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db')
    source=save(store,record())
    other=record();other.issues[0].error_type='TypeError';other.issues[0].solution='Normalize first'
    save(store,other);scan(store)
    assert retrieve(store)==[]
    refs=store.retrieve_model_references(language='python',repository_id='team/repository',error_types=['ValueError'])
    assert refs==[]
    [review]=store.list_conflicts()
    assert review.relation=='unknown'
    store.resolve_conflict(review.id,decision(resolution='confirm_compatible'))
    refs=store.retrieve_model_references(language='python',repository_id='team/repository',error_types=['ValueError'])
    assert [r['id'] for r in refs]==[source.id]


def test_prerequisite_unknown_is_still_relevant(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db')
    child=record(issues=[dict(error_type='TypeError',root_cause='Input',solution='Normalize first',files=['app.py']),
                        dict(error_type='ValueError',root_cause='Missing',solution='Reject missing input',files=['app.py'],depends_on=[0])])
    save(store,child)
    other=record();other.issues[0].solution='Reject the missing input'
    save(store,other);scan(store)
    assert store.retrieve_model_references(language='python',repository_id='team/repository',error_types=['ValueError'])==[]


def test_equivalence_requires_complete_edges_and_equal_dependencies():
    relations={('a','b'):'equivalent',('b','c'):'equivalent'}
    assert equivalent_groups('abc',relations,{})=={v:{v} for v in 'abc'}
    assert equivalent_groups('ab',relations,{'b':{'p'}})=={'a':{'a'},'b':{'b'}}
    assert equivalent_groups('ab',relations,{})=={'a':{'a','b'},'b':{'a','b'}}


def test_paused_member_does_not_contribute_evidence(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db')
    first=save(store,record());value=record();value.issues[0].solution='Reject the missing input'
    second=save(store,value);scan(store,EvidenceClassifier())
    with connect(store.database_path) as connection:
        rule=connection.execute('SELECT rule_id FROM knowledge_sources WHERE experience_id=?',(second.id,)).fetchone()[0]
        connection.execute("UPDATE knowledge_rules SET state='needs_review' WHERE id=?",(rule,))
    [ref]=retrieve(store)
    assert ref['source_ids']==[first.id]
    assert ref['evidence_summary']['verified_runs']==1
