"""Recovery and review invariants across operation sequences and schema upgrades."""
import json
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from backend.experience import ExperienceStore
from backend.experience.database import connect
from backend.experience.jobs import claim, fail, process_page, retry
from backend.experience.knowledge import effective_rules
from backend.shared import ConflictResolution, RepairIssue
from backend.tests.test_experience_consolidation import record, alternative, save, scan, retrieve, EvidenceClassifier, decision


class ReviewMachine(RuleBasedStateMachine):
    @initialize()
    def start(self):
        self.directory=tempfile.TemporaryDirectory()
        self.store=ExperienceStore(Path(self.directory.name)/'rules.db')
        self.originals={}

    @rule(other=st.booleans())
    def append(self,other):
        entry=save(self.store,alternative() if other else record())
        self.originals[entry.id]=entry.model_dump_json()

    @rule()
    def work(self): scan(self.store,EvidenceClassifier())

    @rule(action=st.sampled_from(['prefer_source','keep_separate','confirm_compatible','confirm_equivalent']))
    def decide(self,action):
        for review in self.store.list_conflicts(limit=100)[:1]:
            self.store.resolve_conflict(review.id,ConflictResolution(version=review.version,resolution=action,
                preferred_source_id=review.sources[0].id if action=='prefer_source' else None,note='State machine decision'))

    @rule()
    def undo(self):
        for review in self.store.list_conflicts(status='resolved',limit=100)[:1]:
            self.store.reopen_conflict(review.id,review.version,'Recheck the source contract')

    @invariant()
    def immutable_and_checked(self):
        assert {k:self.store.get_experience(k).model_dump_json() for k in self.originals}==self.originals
        refs=retrieve(self.store)
        with connect(self.store.database_path) as connection:
            blocked=effective_rules(connection)
            assert not {v['rule_id'] for v in refs} & blocked
            assert connection.execute('PRAGMA foreign_key_check').fetchall()==[]
            assert connection.execute('SELECT count(*) FROM experiences').fetchone()[0]==len(self.originals)
            assert not connection.execute('SELECT review_id,version FROM knowledge_decisions GROUP BY review_id,version HAVING count(*)>1').fetchall()

    def teardown(self): self.directory.cleanup()


TestReviewMachine=ReviewMachine.TestCase
TestReviewMachine.settings=settings(max_examples=20,stateful_step_count=20,deadline=None,derandomize=True)


def test_expired_failure_cannot_fail_the_new_owner(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db');save(store,record())
    first=claim(store.database_path)
    with connect(store.database_path) as connection:
        connection.execute('UPDATE knowledge_jobs SET lease_until=0')
    second=claim(store.database_path);process_page(store.database_path,second)
    fail(store.database_path,first|{'attempts':4},RuntimeError())
    assert len(retrieve(store))==1


def test_candidate_and_verified_identical_text_do_not_share_eligibility(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db')
    candidate=store.save_experience(record(scope='global',repository_id=None,reuse_status='candidate'))
    from backend.experience.store import _insert,_new_experience
    verified=_new_experience(record(scope='global',repository_id=None,reuse_status='active'))
    with connect(store.database_path) as connection:
        _insert(connection,verified)
    scan(store)
    assert retrieve(store)[0]['id']==verified.id
    assert store.experience_maintenance(candidate.id)['current_rule_ids']==[]


def test_evidence_collision_cannot_be_bypassed_by_retry(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db');first=record();save(store,first);scan(store)
    changed=first.model_copy(deep=True)
    changed.evidence[0].tests+=1
    # Use the legacy single-save path to exercise two independent stored payloads with one evidence key.
    store.save_experience(changed)
    with connect(store.database_path) as connection:
        job=connection.execute('SELECT id FROM knowledge_jobs').fetchone()[0]
    retry(store.database_path,job);scan(store)
    assert retrieve(store)==[]


def test_dependencies_with_a_different_error_are_included(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db')
    value=record(issues=[dict(error_type='TypeError',root_cause='Normalize input',solution='Normalize first',files=['normalize.py']),
                        dict(error_type='ValueError',root_cause='Validate input',solution='Validate after normalization',files=['validate.py'],depends_on=[0])])
    save(store,value);scan(store)
    refs=store.retrieve_model_references(language='python',repository_id='team/repository',error_types=['ValueError'])
    assert [v['error_type'] for v in refs]==['TypeError','ValueError']


def test_cross_source_dependency_cycle_requires_review(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db')
    a=dict(error_type='TypeError',root_cause='A',solution='Do A',files=['a.py'])
    b=dict(error_type='ValueError',root_cause='B',solution='Do B',files=['b.py'])
    save(store,record(issues=[a,b|{'depends_on':[0]}]))
    save(store,record(issues=[a|{'depends_on':[1]},b]))
    scan(store)
    assert retrieve(store)==[]


def test_publication_rechecks_source_rules_and_tracks_later_pause(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db');source=save(store,record());scan(store)
    preview=store.preview_promotion(source.id)
    published=store.publish_global(source.id,preview.draft);scan(store)
    assert store.experience_maintenance(published.id)['current_rule_ids']
    cross_repo=store.retrieve_model_references(language='python',repository_id='another/repository',error_types=[])
    assert [v['id'] for v in cross_repo]==[published.id]
    assert 'source_ids' not in cross_repo[0] and 'files' not in cross_repo[0]['issues'][0]
    save(store,alternative());scan(store,EvidenceClassifier())
    assert not store.experience_maintenance(published.id)['current_rule_ids']
    assert not store.preview_promotion(source.id).can_publish
    assert store.retrieve_model_references(language='python',repository_id='another/repository',error_types=[])==[]


def test_whole_record_merge_cannot_revive_a_paused_subrule(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db')
    a=dict(error_type='ValueError',root_cause='A',solution='Reject missing input',files=['a.py'])
    b=dict(error_type='TypeError',root_cause='B',solution='Normalize input',files=['b.py'])
    source=save(store,record(issues=[a,b]));other=save(store,record(issues=[b]))
    save(store,record(issues=[a|{'solution':'Use a fallback'}]));scan(store,EvidenceClassifier())
    assert retrieve(store)  # The independent B rule survives.
    assert not store.preview_merge([source.id,other.id])['can_merge']


def test_index_upgrade_backfills_preexisting_rules(tmp_path):
    from backend.experience.maintenance_schema import migrate
    store=ExperienceStore(tmp_path/'rules.db');save(store,record());scan(store)
    with connect(store.database_path) as connection:
        connection.execute('DELETE FROM knowledge_targets')
        migrate(connection)
        assert connection.execute('SELECT count(*) FROM knowledge_targets').fetchone()[0]==1


def test_legacy_decision_can_be_reopened_with_history(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db');a=save(store,record());b=save(store,alternative());scan(store)
    identifier=str(uuid4());left,right=sorted([a.id,b.id])
    details=json.dumps([dict(code='rule_overlap',message='Old overlap',source_ids=[left,right],field='issues',suggestion='Inspect contracts')])
    with connect(store.database_path) as connection:
        connection.execute('INSERT INTO experience_conflicts(id,left_id,right_id,details,created_at) VALUES(?,?,?,?,?)',
                           (identifier,left,right,details,a.created_at.isoformat()))
    resolved=store.resolve_conflict(identifier,decision(a.id))
    reopened=store.reopen_conflict(identifier,resolved.version,'New source evidence')
    assert reopened.status=='pending'
    assert [v['action'] for v in store.review_history(identifier)]==['resolve','reopen']
    assert store.reopen_conflict(identifier,resolved.version,'New source evidence')==reopened
    # Both stores are paginated together, with no vanished legacy rows.
    assert len(store.list_conflicts(limit=100))==2


def test_equivalence_chain_never_overrides_unknown_endpoints(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db')
    for text in ['Alpha','Beta','Gamma']:
        save(store,record(issues=[dict(error_type='ValueError',root_cause=text,solution=text,files=['app.py'])]))
    scan(store)
    with connect(store.database_path) as connection:
        ids=[r[0] for r in connection.execute('SELECT id FROM knowledge_rules ORDER BY id')]
        connection.execute("UPDATE knowledge_relations SET kind='equivalent' WHERE left_rule=? OR right_rule=?",(ids[1],ids[1]))
        assert set(ids)<=effective_rules(connection)
    assert retrieve(store)==[]
    from backend.experience.library import library
    assert all(card['state']=='paused' for card in library(store.database_path)['items'])


def test_invalid_symbol_hint_keeps_the_file_scope_and_original_rule(tmp_path):
    from backend.experience.grounding import ground_issues
    (tmp_path/'Rules.java').write_text('class Rules { static String label(String s) { return s; } }')
    issue=RepairIssue(error_type='IllegalArgumentException',root_cause='Missing guard',solution='Validate input',
                      files=['Rules.java'],symbols=['Rules.java#dev.example.Rules.label'])
    ground_issues([issue],tmp_path,'java')
    assert issue.symbols==[] and issue.files==['Rules.java'] and issue.solution=='Validate input'


@pytest.mark.parametrize('left,right', [('Accept x >= 10','Accept x > 10'),('Reject x == 0','Reject x != 0'),
    ('Reject x < 0 or y > 10','Reject x > 0 or y < 10'),('Accept x > 0 and y > 0','Accept x > 0 or y > 0')])
def test_overconfident_nli_cannot_change_comparisons(left,right):
    from scripts.knowledge_semantic_experiment import corpus
    from copy import deepcopy
    from backend.experience.relations import classify
    a=corpus()[0]['left'];b=deepcopy(a)
    a['issue']['solution']=left;b['issue']['solution']=right
    assert classify(a,b,EvidenceClassifier())[0]=='unknown'


def test_experiment_context_is_an_immutable_per_call_snapshot():
    import threading
    from scripts.knowledge_live_experiment import Memory
    class Store:
        def retrieve_model_references(self,*args,**kwargs): return []
    memory=Memory(None,Store(),'new',threading.Lock())
    observed=['ValueError'];memory.retrieve_model_references('input',error_types=observed)
    observed.append('TypeError')
    assert memory.calls[0]['context']['error_types']==['ValueError']


def test_manual_merge_preserves_distinct_symbols_and_declared_constraints(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db');sources=[]
    for symbol in ['app.py#first','app.py#second']:
        sources.append(save(store,record(constraints=['Readonly mode only'],issues=[dict(error_type='ValueError',
            root_cause='Missing input',solution='Reject missing input',files=['app.py'],symbols=[symbol])])))
    scan(store)
    merged=store.merge_experiences([v.id for v in sources]);scan(store)
    assert len(merged.issues)==2
    refs=retrieve(store)
    assert len(refs)==2 and all('Readonly mode only' in v['constraints'] for v in refs)
    assert {v['issues'][0]['symbols'][0] for v in refs}=={'app.py#first','app.py#second'}


def test_unknown_source_version_does_not_match_a_known_target(tmp_path):
    store=ExperienceStore(tmp_path/'rules.db');save(store,record());scan(store)
    assert store.retrieve_model_references(language='python',repository_id='team/repository',error_types=[],source_revision='tree:known')==[]
