"""Documented missed merges replay through the same deterministic ranking production uses."""
import json
from pathlib import Path

import pytest

from backend.experience import ExperienceStore
from backend.experience.automation import MAX_CANDIDATES, candidate_ranking
from backend.experience.database import connect
from backend.tests.test_experience_consolidation import record, save, scan

CASES = Path(__file__).resolve().parents[2] / 'benchmarks' / 'missed-merge' / 'cases.json'


def load():
    return json.loads(CASES.read_text(encoding='utf-8'))['cases']


def build(store, spec):
    return save(store, record(
        repository_id=spec['repository_id'], task_summary=spec['task_summary'],
        root_cause=spec['root_cause'], solution=spec['solution'],
        issues=[dict(error_type=spec['error_type'], root_cause=spec['root_cause'],
                     solution=spec['solution'], files=spec['files'])]))


@pytest.mark.parametrize('case', load(), ids=lambda case: case['id'])
def test_documented_case_stays_reachable(case, tmp_path):
    store = ExperienceStore(tmp_path/f"{case['id']}.db")
    partner = build(store, case['partner'])
    for spec in case['distractors']:
        build(store, spec)
    source = build(store, case['source'])
    scan(store)
    with connect(store.database_path) as connection:
        connection.execute('BEGIN')
        ranking = candidate_ranking(connection, source)
    order = [item.id for item in ranking]
    # Batching made "never compared" a defect; every true partner stays eligible.
    assert partner.id in order, '真实合并伙伴必须仍在可比较范围内'
    rank = order.index(partner.id) + 1
    top = case['expect'].get('top')
    if top is not None:
        assert rank <= min(top, MAX_CANDIDATES), f'该来源排在 {rank} / {len(order)}，未进入首批'
