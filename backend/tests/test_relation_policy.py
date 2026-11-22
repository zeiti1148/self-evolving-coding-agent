"""Relation policy migration: a code change must actually re-check old verdicts."""
import sqlite3
from dataclasses import replace

from backend.config import Settings
from backend.experience.jobs import reschedule_relations
from backend.experience.relations import RELATION_POLICY_VERSION, classify
from backend.tests.test_experience_consolidation import record, save, scan


def rule(error_type, solution, files=('app.py',), conditions=('Optional numeric input',)):
    return dict(language='python', repository_id='team/repository', scope='project',
                compatibility_key='tree:' + 'a' * 64, required_capabilities=[],
                applicability=list(conditions), constraints=[],
                issue=dict(error_type=error_type, solution=solution, root_cause='cause', files=list(files), symbols=[]),
                task_summary='summary', root_cause='cause', solution=solution, tags=[])


def test_different_error_types_in_the_same_scope_stay_for_review():
    kind, details = classify(rule('ValueError', 'Reject missing input'), rule('IndexError', 'Clamp the page offset'))
    assert kind == 'unknown'
    assert '文字差异不构成矛盾证据' in details['reason']


def test_same_error_type_stays_ambiguous_without_a_model():
    kind, details = classify(rule('ValueError', 'Reject missing input'), rule('ValueError', 'Use the default value'))
    assert kind == 'unknown'


def test_paused_card_explains_what_is_unresolved(tmp_path):
    from backend.experience import ExperienceStore
    from backend.experience.library import library
    store = ExperienceStore(tmp_path / 'p.db')
    save(store, record())
    save(store, record(solution='Use the default value',
                       issues=[dict(error_type='ValueError', root_cause='Missing input',
                                    solution='Use the default value', files=['app.py'])]))
    scan(store)
    cards = library(store.database_path)['items']
    paused = [card for card in cards if card['kind'] == 'rule' and card['state'] == 'paused']
    assert paused, 'the same-type pair should block both rules'
    assert '关系尚未判定' in paused[0]['reason']
    store = __import__('backend.experience', fromlist=['ExperienceStore']).ExperienceStore(tmp_path / 'm.db')
    save(store, record()); save(store, record(repository_id='other')); scan(store)
    before = sqlite3.connect(store.database_path).execute(
        "SELECT count(*) FROM knowledge_jobs WHERE state='succeeded'").fetchone()[0]
    assert before >= 1
    assert reschedule_relations(store.database_path) is True
    pending = sqlite3.connect(store.database_path).execute(
        "SELECT count(*) FROM knowledge_jobs WHERE state='pending'").fetchone()[0]
    assert pending >= 1
    # Running again under the same policy changes nothing.
    assert reschedule_relations(store.database_path) is False
    stored = sqlite3.connect(store.database_path).execute(
        "SELECT value FROM knowledge_meta WHERE key='relation_policy'").fetchone()[0]
    assert stored == RELATION_POLICY_VERSION
