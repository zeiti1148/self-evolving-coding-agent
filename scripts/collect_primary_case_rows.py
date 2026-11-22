"""Collect case rows for the primary sequence: two authentic plus one substitute."""
import json
from pathlib import Path

import pyarrow.parquet as pq

CACHE = Path('.cache/swebench')
VERIFIED = Path('.cache/engineering-repair-research/verified.json')
OUT = Path('.cache/real-update-comparison/cases/rows.json')
WANTED = {
    'pytest-10356': 'pytest-dev__pytest-10356',
    'sphinx-8548': 'sphinx-doc__sphinx-8548',
    'pytest-10482': 'pytest-dev__pytest-10482',
}
FIELDS = ['instance_id', 'repo', 'base_commit', 'version', 'problem_statement',
          'test_patch', 'FAIL_TO_PASS', 'PASS_TO_PASS']


def from_verified():
    rows = json.loads(VERIFIED.read_text(encoding='utf-8'))
    return {row['instance_id']: row for row in rows}


def from_parquet(instance):
    table = pq.read_table(CACHE / 'test-0000.parquet', columns=FIELDS)
    ids = table.column('instance_id').to_pylist()
    if instance not in ids:
        raise SystemExit(f'{instance} not in the SWE-bench test split')
    index = ids.index(instance)
    return {name: table.column(name)[index].as_py() for name in FIELDS}


def main():
    verified = from_verified()
    collected = {}
    for key, instance in WANTED.items():
        row = verified.get(instance) or from_parquet(instance)
        collected[key] = {name: row[name] for name in FIELDS}
        files = sorted({value.split('::')[0] for value in json.loads(row['FAIL_TO_PASS'])
                        + json.loads(row['PASS_TO_PASS'])})
        collected[key]['test_files'] = files
        print(key, row['repo'], row['base_commit'][:10], 'test files:', len(files))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(collected, ensure_ascii=False, indent=2), encoding='utf-8')
    print('written', OUT)


if __name__ == '__main__':
    main()
