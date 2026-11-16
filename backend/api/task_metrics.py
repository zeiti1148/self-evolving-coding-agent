"""Read-only task cost and knowledge-processing metrics, including registered archives."""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from uuid import UUID

from fastapi import HTTPException, Query


@contextmanager
def read_database(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    try:
        yield db
    finally:
        db.close()


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def experience_count(path):
    try:
        with read_database(path) as db:
            return db.execute('SELECT count(*) FROM experiences').fetchone()[0] if 'experiences' in tables(db) else 0
    except sqlite3.Error:
        return None


def requests(values):
    if not isinstance(values, list):
        return
    for item in values:
        if isinstance(item, dict):
            if isinstance(item.get('requests'), list):
                yield from requests(item['requests'])
            else:
                yield item


def complete_request_log(values):
    if not isinstance(values, list):
        return False
    return all(isinstance(item, dict) and
               ('requests' not in item or complete_request_log(item['requests'])) for item in values)


def token_usage(events, *, known=True):
    values = [(e['usage'].get('total_tokens') if isinstance(e.get('usage'), dict) else None) for e in events]
    reported = [v for v in values if type(v) is int and v >= 0]
    return dict(total_tokens=sum(reported) if known and len(reported) == len(values) else None,
                reported_total_tokens=sum(reported), reported_requests=len(reported),
                total_requests=len(values) if known else None)


def elapsed(start, end):
    if not start or not end:
        return None
    try:
        milliseconds = round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000)
        return milliseconds if milliseconds >= 0 else None
    except (ValueError, TypeError):
        return None


def sources(settings):
    local = dict(database_path=str(settings.database_path), runtime_label=None, code_revision=None,
                 comparison_group=None, task_url_base=None)
    entries = []
    if settings.task_metrics_registry:
        data = json.loads(settings.task_metrics_registry.read_text(encoding='utf-8'))
        entries = data['sources']
        if not isinstance(entries, list) or len(entries) > 24:
            raise ValueError('Invalid metrics registry')
    paths = {str(Path(e['database_path']).resolve()) for e in entries}
    return entries if str(settings.database_path.resolve()) in paths else [local, *entries]


def permitted(entry, identifier):
    return 'task_ids' not in entry or identifier in entry['task_ids']


def effective_relation_status(db, row, available):
    """Use the review UI's live-source boundary without changing review history."""
    if row['status'] != 'pending':
        return row['status']
    required = {'knowledge_sources', 'experience_merge_sources', 'experience_publication_revisions',
                'experience_retirements', 'knowledge_pauses'}
    if not required <= available or not {'left_rule', 'right_rule'} <= set(row.keys()):
        return 'unknown'
    from backend.experience.knowledge import is_current
    if any(not is_current(db, rule) for rule in (row['left_rule'], row['right_rule'])):
        return 'obsolete'
    if db.execute('SELECT 1 FROM knowledge_pauses WHERE rule_id IN (?,?) LIMIT 1',
                  (row['left_rule'], row['right_rule'])).fetchone():
        return 'obsolete'
    return 'pending'


def confirmed_no_knowledge_source(db, status, audit, available):
    """Zero background cost requires affirmative current evidence, not missing IDs."""
    result = status.get('result')
    task_id = status.get('task_id')
    retry = audit.get('experience_retry')
    if (audit.get('knowledge_automation_enabled') is not True or not task_id or
            audit.get('task_id') != task_id or not complete_request_log(audit.get('model_requests')) or
            status.get('status') not in {'succeeded', 'failed', 'stopped'} or not isinstance(result, dict) or
            result.get('task_id') != task_id or result.get('status') != status['status'] or
            result.get('experience_ids') != [] or result.get('experience_id') is not None or
            'experience_saved' not in result or (result['experience_saved'] is not None and result['experience_saved'] is not False) or
            retry is not None and (not isinstance(retry, dict) or retry.get('state') == 'running') or
            'experiences' not in available):
        return False
    columns = {row['name'] for row in db.execute('PRAGMA table_info(experiences)')}
    if 'source_task_id' not in columns:
        return False
    return db.execute('SELECT 1 FROM experiences WHERE source_task_id=? LIMIT 1', (task_id,)).fetchone() is None


def knowledge_metrics(db, status, audit, available):
    result = status.get('result') or {}
    ids = result.get('experience_ids') or ([result['experience_id']] if result.get('experience_id') else [])
    counts = audit.get('experience_counts', {})
    view = dict(state='not_recorded', elapsed_ms=None, model_requests=None, token_usage=token_usage([], known=False),
                before_count=counts.get('before'), after_count=counts.get('after'), merged_count=None,
                conflict_count=None, resolved_count=None, pending_confirmation_count=None,
                experience_ids=ids, notice='未记录后台经验处理指标。')
    if audit.get('knowledge_automation_enabled') is False:
        view.update(state='not_supported', model_requests=0, token_usage=token_usage([]), merged_count=0, conflict_count=0,
                    resolved_count=0, pending_confirmation_count=0,
                    notice='此运行版本没有自动经验处理；前后数量为保留的原始记录，不代表去重后的知识数。')
        return view
    if not ids and confirmed_no_knowledge_source(db, status, audit, available):
        view.update(state='not_applicable', elapsed_ms=0, model_requests=0, token_usage=token_usage([]),
                    merged_count=0, conflict_count=0, resolved_count=0, pending_confirmation_count=0,
                    notice='本次任务已结束，结果与数据库均确认未生成经验来源，因此未启动后台自动经验处理。')
        return view
    if not ids or 'experience_auto_jobs' not in available:
        return view
    marks = ','.join('?' for _ in ids)
    jobs = list(db.execute(f'SELECT * FROM experience_auto_jobs WHERE id IN ({marks})', ids))
    if not jobs:
        return view
    pairs = list(db.execute('SELECT DISTINCT p.* FROM experience_auto_pairs p JOIN experience_auto_links l '
                            f'ON p.id=l.pair_id WHERE l.job_id IN ({marks})', ids))
    events, conflicts, log_complete = [], set(), True
    merged = 0
    for pair in pairs:
        payload = json.loads(pair['result'] or '{}')
        log_complete &= complete_request_log(payload.get('model_requests')) or (
            pair['state'] == 'rejected' and not pair['result'])
        events.extend(requests(payload.get('model_requests', [])))
        merged += int(pair['state'] == 'succeeded' and payload.get('state') == 'succeeded')
        conflicts.update((payload.get('conflict_check') or {}).get('conflict_ids', []))
        if 'experience_auto_attempts' in available:
            for attempt in db.execute('SELECT result FROM experience_auto_attempts WHERE pair_id=? AND attempt!=?',
                                      (pair['id'], pair['attempt'])):
                archived = json.loads(attempt[0] or '{}')
                log_complete &= complete_request_log(archived.get('model_requests'))
                events.extend(requests(archived.get('model_requests', [])))
    # Same-project maintenance can discover unknown/conflicting rules without a
    # cross-project comparison pair. Count every review involving this task's
    # source rules, including older relations reused by a shared projection.
    if {'knowledge_relations', 'knowledge_sources'} <= available:
        columns = {r['name'] for r in db.execute('PRAGMA table_info(knowledge_relations)')}
        if {'left_rule', 'right_rule', 'kind'} <= columns:
            conflicts.update(r[0] for r in db.execute(
                'SELECT DISTINCT r.id FROM knowledge_relations r JOIN knowledge_sources s '
                'ON s.rule_id IN (r.left_rule,r.right_rule) '
                f"WHERE s.experience_id IN ({marks}) AND (r.kind IN ('unknown','conflict') OR r.status='resolved')", ids))
    resolved = pending = 0
    for identifier in conflicts:
        row = db.execute('SELECT * FROM knowledge_relations WHERE id=?', (identifier,)).fetchone()
        if row and row['status'] == 'resolved':
            resolved += 1
        if 'knowledge_auto_reviews' in available:
            review = db.execute('SELECT data FROM knowledge_auto_reviews WHERE review_id=?', (identifier,)).fetchone()
            if row and review and effective_relation_status(db, row, available) == 'pending':
                data = json.loads(review[0])
                reopened = 'version' in row.keys() and row['version'] != data.get('result_version', 1)
                pending += int(data.get('state') == 'needs_confirmation' or reopened)
    states = {j['state'] for j in jobs}
    local_jobs = []
    if {'knowledge_jobs', 'knowledge_sources'} <= available:
        local_jobs = list(db.execute('SELECT DISTINCT j.id,j.state,j.updated_at FROM knowledge_jobs j '
            'JOIN knowledge_sources s ON s.rule_id=j.rule_id '
            f'WHERE s.experience_id IN ({marks})', ids))
        states.update(j['state'] for j in local_jobs)
    state = next((s for s in ('running', 'pending', 'waiting', 'failed') if s in states), 'succeeded')
    log_complete &= state in {'succeeded', 'failed'} and all(p['state'] not in {'running', 'pending', 'waiting'} for p in pairs)
    notices = ['前后数量是原始记录；共同原则不删除来源。后台耗时包含排队、审阅与测试。',
               '冲突与待确认数量表示本次来源涉及的审阅关系，包含同仓库未知关系，不表示全部由本次新建。']
    if any(j['state'] in {'pending', 'running'} for j in local_jobs):
        notices.append('本次来源规则仍在后台验证，处理和费用统计尚未最终完成。')
    if not log_complete:
        notices.append('部分后台调用尚未上报或历史审计缺失，总请求数与总 token 暂为未知。')
    view.update(state=state, elapsed_ms=elapsed(min(j['created_at'] for j in jobs),
                    max(j['updated_at'] for j in [*jobs, *local_jobs]))
                if state in {'succeeded', 'failed'} else None,
                model_requests=len(events) if log_complete else None,
                token_usage=token_usage(events, known=log_complete), merged_count=merged,
                conflict_count=len(conflicts), resolved_count=resolved, pending_confirmation_count=pending,
                notice='；'.join(dict.fromkeys([*(j['notice'] for j in jobs), *notices])))
    return view


def final_test_outcome(result):
    """Independent test acceptance is distinct from the agent's lifecycle status."""
    final = next((r for r in reversed(result.get('tool_results', []))
                  if r.get('metadata', {}).get('final_verification') is True), None)
    if not final or final.get('tool_name') != 'run_tests' or type(final.get('success')) is not bool:
        return None
    verification = final.get('metadata', {})
    if (verification.get('runner') not in {'maven', 'pytest', 'go', 'ctest'} or
            any(type(verification.get(key)) is not int or verification[key] < 0
                for key in ('tests', 'failures', 'errors', 'skipped'))):
        return None
    return bool(final['success'] and final.get('error_code') is None and verification['tests'] > 0
                and all(verification[key] == 0 for key in ('failures', 'errors', 'skipped')))


def metric(db, row, entry):
    status, audit = json.loads(row['status_json']), json.loads(row['audit_json'])
    request = json.loads(row['request_json'] or '{}')
    available = tables(db)
    project_label = request.get('source_project')
    if 'uploaded_projects' in available:
        project = db.execute('SELECT data FROM uploaded_projects WHERE id=?', (project_label,)).fetchone()
        if project:
            project_label = json.loads(project[0])['label']
    events = list(requests(audit.get('model_requests', [])))
    log_complete = complete_request_log(audit.get('model_requests'))
    injected = [e for e in events if e.get('success') and e.get('request_experience_ids')]
    result = status.get('result') or {}
    final_tests_passed = final_test_outcome(result)
    verified = bool(status['status'] == 'succeeded' and result.get('repair_succeeded') is True
                    and result.get('tests_passed') is True and final_tests_passed is True)
    base = entry.get('task_url_base')
    return dict(task_id=row['id'], task_url=base.rstrip('/') + '/#/tasks/' + row['id'] if base else None,
                status=status['status'], project_label=project_label,
                runtime_label=entry.get('runtime_label'), code_revision=entry.get('code_revision'),
                comparison_group=entry.get('comparison_group'), repair_verified=verified,
                final_tests_passed=final_tests_passed,
                elapsed_ms=elapsed(status.get('started_at'), status.get('finished_at')),
                model_requests=len(events) if log_complete else None, token_usage=token_usage(events, known=log_complete),
                retrieval=dict(strategy=audit.get('retrieval_strategy'),
                    injected_requests=len(injected) if log_complete else None,
                    reference_count=len({i for e in injected for i in e['request_experience_ids']}) if log_complete else None,
                    events=[dict(reason=e.get('reason', 'not_recorded'), error_types=e.get('error_types', []),
                                 experience_ids=e.get('experience_ids', [])) for e in audit.get('retrieval_events', [])]),
                experience=knowledge_metrics(db, status, audit, available))


def register_task_metrics_routes(app, settings):
    @app.get('/api/task-metrics')
    def listing(limit: int = Query(100, ge=1, le=100)):
        values = {}
        try:
            for entry in sources(settings):
                if not Path(entry['database_path']).is_file():
                    continue
                with read_database(entry['database_path']) as db:
                    if 'api_tasks' not in tables(db):
                        continue
                    for row in db.execute('SELECT * FROM api_tasks ORDER BY created_at DESC,id DESC LIMIT ?', (limit,)):
                        if permitted(entry, row['id']) and row['id'] not in values:
                            values[row['id']] = (row['created_at'], metric(db, row, entry))
            return [v[1] for v in sorted(values.values(), key=lambda x: x[0], reverse=True)[:limit]]
        except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
            raise HTTPException(503, '任务指标暂时不可用，请检查登记的历史记录。') from None

    @app.get('/api/tasks/{task_id}/metrics')
    def detail(task_id: UUID):
        try:
            for entry in sources(settings):
                if not permitted(entry, str(task_id)) or not Path(entry['database_path']).is_file():
                    continue
                with read_database(entry['database_path']) as db:
                    if 'api_tasks' not in tables(db):
                        continue
                    row = db.execute('SELECT * FROM api_tasks WHERE id=?', (str(task_id),)).fetchone()
                    if row:
                        return metric(db, row, entry)
        except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
            raise HTTPException(503, '任务指标暂时不可用，请检查登记的历史记录。') from None
        raise HTTPException(404, '未找到该任务的指标。')
