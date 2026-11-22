"""Deterministic injection-side cost of each retrieval budget. No model calls.

Task-side cost needs the live comparison harness, which requires the frozen
snapshots from the primary sequence. This script measures only what the budget
change does to the payload: how many rules and characters reach the model.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.experience import ExperienceStore
from backend.experience.jobs import drain
from backend.experience.policy import RETRIEVAL_BUDGETS
from backend.experience.retrieval import retrieve
from backend.shared import ExperienceCreate
from uuid import uuid4

CASES = ROOT / 'benchmarks' / 'missed-merge' / 'cases.json'


def build(store, spec):
    task = str(uuid4())
    value = ExperienceCreate(**dict(
        schema_version=4, language='python', scope='project', repository_id=spec['repository_id'],
        task_summary=spec['task_summary'], root_cause=spec['root_cause'], solution=spec['solution'],
        constraints=[], verification='Original tests passed', source_task_id=task,
        applicability=['Missing text input'],
        issues=[dict(error_type=spec['error_type'], root_cause=spec['root_cause'],
                     solution=spec['solution'], files=spec['files'])],
        evidence=[dict(source_task_id=task, tool_call_id=str(uuid4()), runner='pytest', tests=3)]))
    return store.save_experiences([value], source_task_id=task)[0]


def synthetic(store, *, count, padding, repository_id):
    """Rules that are eligible for the same source: same repository and error type."""
    for index in range(count):
        spec = dict(repository_id=repository_id, task_summary=f'Parser rule {index}',
                    root_cause=f'Missing optional value {index} cannot be converted',
                    solution=('Convert optional input only after checking for absence. ' * max(1, padding // 56)).strip(),
                    error_type='ValueError', files=['app.py'])
        build(store, spec)
    return dict(repository_id=repository_id, task_summary='Parser rule source',
                root_cause='Missing optional value 0 cannot be converted',
                solution='Convert optional input only after checking for absence.',
                error_type='ValueError', files=['app.py'])


def measure(path, source, strategy):
    limit, budget = RETRIEVAL_BUDGETS[strategy]
    query = f"{source.task_summary}\n{source.issues[0].root_cause}"
    references = retrieve(path, query, language=source.language, repository_id=source.repository_id,
                          error_types=[source.issues[0].error_type], limit=limit,
                          character_budget=budget)
    characters = sum(len(json.dumps(reference, ensure_ascii=False)) for reference in references)
    return dict(strategy=strategy, limit=limit, character_budget=budget,
                rules=len(references), characters=characters,
                rule_ids=[reference['rule_id'] for reference in references])


def scenarios(cases):
    """Documented cases plus two dense libraries that actually reach the caps."""
    values = [(case['id'], case['source'], [case['partner'], *case['distractors']]) for case in cases]
    dense = dict(repository_id='benchmark/dense-library')
    long_rules = dict(repository_id='benchmark/long-library')
    values.append(('dense-8-rules', dense, None))
    values.append(('long-6-rules', long_rules, None))
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', default=str(CASES))
    parser.add_argument('--json', help='write raw rows here')
    options = parser.parse_args()
    cases = json.loads(Path(options.cases).read_text(encoding='utf-8'))['cases']
    rows = []
    with tempfile.TemporaryDirectory() as root:
        for name, source_spec, others in scenarios(cases):
            store = ExperienceStore(Path(root) / f"{name}.db")
            if others is None:
                padding = 3000 if name.startswith('long') else 120
                count = 6 if name.startswith('long') else 8
                source = build(store, synthetic(store, count=count, padding=padding,
                                                repository_id=source_spec['repository_id']))
            else:
                source = build(store, source_spec)
                for spec in others:
                    build(store, spec)
            drain(store.database_path, seconds=20)
            for strategy in ('contextual', 'adaptive'):
                rows.append(dict(case=name, **measure(store.database_path, source, strategy)))
    print('| 场景 | 策略 | 上限（条/字符） | 实际注入规则 | 实际字符 |')
    print('| --- | --- | --- | ---: | ---: |')
    for row in rows:
        print(f"| {row['case']} | {row['strategy']} | {row['limit']} / {row['character_budget']} | "
              f"{row['rules']} | {row['characters']} |")
    print()
    for strategy in ('contextual', 'adaptive'):
        subset = [row for row in rows if row['strategy'] == strategy]
        print(f"{strategy}: 平均 {sum(r['rules'] for r in subset)/len(subset):.2f} 条 / "
              f"{sum(r['characters'] for r in subset)/len(subset):.0f} 字符，"
              f"合计 {sum(r['rules'] for r in subset)} 条 / {sum(r['characters'] for r in subset)} 字符")
    if options.json:
        Path(options.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
