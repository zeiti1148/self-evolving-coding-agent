"""Deterministic candidate-ranking evaluation over documented missed-merge cases.

Only the production ranking function runs; the ablation disables the lexical
signal by patching the retrieval helper, so no reimplementation can drift from
the code path that actually plans comparisons. No model is called.
"""
import argparse
import json
import sys
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.experience import ExperienceStore
from backend.experience.automation import MAX_CANDIDATES, candidate_ranking
from backend.experience.database import connect
from backend.experience.jobs import drain
from backend.shared import ExperienceCreate

CASES = ROOT / 'benchmarks' / 'missed-merge' / 'cases.json'


def load(path=CASES):
    return json.loads(Path(path).read_text(encoding='utf-8'))['cases']


def build(store, spec):
    """Same verified-experience shape the repair pipeline saves."""
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


def scan(store):
    return drain(store.database_path, seconds=20)


def rank(path, source, *, lexical=True):
    import backend.experience.retrieval as retrieval
    patch = (mock.patch.object(retrieval, 'lexical_candidates', lambda *args, **kwargs: [])
             if not lexical else nullcontext())
    with patch, connect(path) as connection:
        connection.execute('BEGIN')
        return candidate_ranking(connection, source)


def evaluate(cases, root):
    rows = []
    for case in cases:
        store = ExperienceStore(Path(root) / f"{case['id']}.db")
        partner = build(store, case['partner'])
        for spec in case['distractors']:
            build(store, spec)
        source = build(store, case['source'])
        scan(store)
        # Same error type or not: the two groups answer different questions.
        group = 'same-type' if case['source']['error_type'] == case['partner']['error_type'] else 'cross-type'
        for strategy, lexical in (('production', True), ('no-lexical', False)):
            started = time.perf_counter()
            order = [item.id for item in rank(store.database_path, source, lexical=lexical)]
            elapsed = (time.perf_counter() - started) * 1000
            rows.append(dict(case=case['id'], group=group, strategy=strategy, candidates=len(order),
                             rank=order.index(partner.id) + 1 if partner.id in order else None,
                             expected_top=case['expect'].get('top'), ms=round(elapsed, 1)))
    return rows


def summary(rows):
    """Per group and strategy: mixed averages hide the effect of the error-type signal."""
    result = {}
    for group in sorted({row['group'] for row in rows}):
        for strategy in sorted({row['strategy'] for row in rows}):
            subset = [row for row in rows if row['strategy'] == strategy and row['group'] == group]
            if not subset:
                continue
            hit = [row for row in subset if row['rank'] is not None]
            result[f'{group}/{strategy}'] = dict(
                group=group, strategy=strategy, cases=len(subset), eligible=len(hit),
                recall_at_1=sum(row['rank'] <= 1 for row in hit),
                recall_at_5=sum(row['rank'] <= min(5, MAX_CANDIDATES) for row in hit),
                mean_rank=round(sum(row['rank'] for row in hit) / len(hit), 2) if hit else None,
                median_ms=round(sorted(row['ms'] for row in subset)[len(subset) // 2], 1))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', default=str(CASES))
    parser.add_argument('--json', help='write the raw rows here')
    options = parser.parse_args()
    cases = load(options.cases)
    with tempfile.TemporaryDirectory() as root:
        rows = evaluate(cases, root)
    print('| 案例 | 分组 | 策略 | 候选数 | 真实伙伴名次 | 期望上限 | 排序耗时(ms) |')
    print('| --- | --- | --- | ---: | ---: | ---: | ---: |')
    for row in rows:
        print(f"| {row['case']} | {row['group']} | {row['strategy']} | {row['candidates']} | "
              f"{row['rank'] if row['rank'] is not None else '未纳入'} | "
              f"{row['expected_top'] if row['expected_top'] is not None else '不限'} | {row['ms']} |")
    print()
    print('| 分组 | 策略 | 案例数 | 仍可比较 | recall@1 | recall@5 | 平均名次 | 中位耗时(ms) |')
    print('| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |')
    for value in summary(rows).values():
        print(f"| {value['group']} | {value['strategy']} | {value['cases']} | {value['eligible']} | "
              f"{value['recall_at_1']} | {value['recall_at_5']} | {value['mean_rank']} | {value['median_ms']} |")
    if options.json:
        Path(options.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
