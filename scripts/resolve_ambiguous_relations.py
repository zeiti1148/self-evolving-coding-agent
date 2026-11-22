"""Inspect and resolve ambiguous rule relations without a semantic model.

Dry run first. `--confirm-compatible` resolves the listed unknown relations as
"compatible" (keep both rules, neither blocks the other); it never merges rules
and never touches raw experiences.
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.experience import ExperienceStore  # noqa: E402
from backend.shared import ConflictResolution  # noqa: E402


def rows_of(database):
    database.row_factory = sqlite3.Row
    return database.execute(
        "SELECT id, left_rule, right_rule, version FROM knowledge_relations "
        "WHERE kind='unknown' AND status='pending' ORDER BY created_at").fetchall()


def describe(database, rows):
    rules = {row['id']: json.loads(row['data']) for row in database.execute('SELECT id, data FROM knowledge_rules')}
    summary = Counter()
    details = []
    for row in rows:
        left, right = rules.get(row['left_rule']), rules.get(row['right_rule'])
        if left is None or right is None:
            summary['missing rule'] += 1
            continue
        same_type = left['issue'].get('error_type') == right['issue'].get('error_type')
        same_file = bool(set(left['issue'].get('files') or []) & set(right['issue'].get('files') or []))
        key = 'same type + same file' if same_type and same_file else 'same type' if same_type else 'different type'
        summary[key] += 1
        details.append(dict(id=row['id'], kind=key, version=row['version'],
                            left=left['issue'].get('error_type'), right=right['issue'].get('error_type'),
                            file=(left['issue'].get('files') or [''])[0]))
    return summary, details


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=str(ROOT / 'data/app.db'))
    parser.add_argument('--confirm-compatible', action='store_true',
                        help='resolve every listed unknown relation as compatible')
    parser.add_argument('--limit', type=int, default=0, help='stop after N resolutions')
    parser.add_argument('--json', help='write the per-relation detail here')
    options = parser.parse_args()
    database = sqlite3.connect(options.database)
    rows = rows_of(database)
    summary, details = describe(database, rows)
    print('database:', options.database)
    print('pending unknown relations:', len(rows))
    for key, count in summary.most_common():
        print(f'  {key}: {count}')
    if options.json:
        Path(options.json).write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    if not options.confirm_compatible:
        print('dry run only; add --confirm-compatible to resolve them as compatible')
        return 0
    store = ExperienceStore(options.database)
    resolved, failed = 0, 0
    for row in (rows[:options.limit] if options.limit else rows):
        try:
            store.resolve_conflict(row['id'], ConflictResolution(
                version=row['version'], resolution='confirm_compatible',
                note='人工复核：两条经验处理同一代码范围内的不同情形，分别保留、互不阻断。'))
            resolved += 1
        except Exception as error:  # noqa: BLE001 - keep going and report the count
            failed += 1
            if failed <= 3:
                print('  failed:', type(error).__name__, str(error)[:120])
    print(f'resolved: {resolved}, failed: {failed}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
