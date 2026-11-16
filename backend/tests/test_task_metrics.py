"""Metrics integrity tests. Fixtures stay in temporary databases, never study data."""
import json
from dataclasses import replace
from pathlib import Path
import sqlite3
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api import task_metrics as metrics
from backend.config import Settings


def create_database(path):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE api_tasks(id TEXT PRIMARY KEY,request_json TEXT,status TEXT,created_at TEXT,status_json TEXT,audit_json TEXT)')
        db.execute('CREATE TABLE experiences(id TEXT PRIMARY KEY)')


def record(path, *, status='succeeded', audit=None, result=None, identifier=None):
    identifier = identifier or str(uuid4())
    data = dict(task_id=identifier, status=status, started_at='2026-11-15T12:00:00+00:00',
                finished_at='2026-11-15T12:00:02+00:00', result=result)
    row = dict(id=identifier, request_json=json.dumps({'source_project': 'uploaded-real-source'}), status=status,
               created_at='2026-11-15T12:00:00+00:00', status_json=json.dumps(data), audit_json=json.dumps(audit or {}))
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO api_tasks VALUES(:id,:request_json,:status,:created_at,:status_json,:audit_json)', row)
    return row


def read_metric(path, row):
    with metrics.read_database(path) as db:
        return metrics.metric(db, row, {})


def serve(local, registry=None):
    app = FastAPI()
    metrics.register_task_metrics_routes(app, replace(Settings(), database_path=local, task_metrics_registry=registry))
    return TestClient(app)


def test_usage_keeps_missing_invalid_and_failed_request_costs_visible():
    events = [{'success': True, 'usage': {'total_tokens': 13}}, {'success': False, 'usage': {'total_tokens': 7}}]
    assert metrics.token_usage(events) == dict(total_tokens=20, reported_total_tokens=20, reported_requests=2, total_requests=2)
    for unknown in (None, {}, 'missing', {'total_tokens': True}, {'total_tokens': -1}):
        summary = metrics.token_usage([*events, {'success': False, 'usage': unknown}])
        assert summary == dict(total_tokens=None, reported_total_tokens=20, reported_requests=2, total_requests=3)
    assert metrics.token_usage([], known=False)['total_tokens'] is None
    assert metrics.token_usage([], known=False)['total_requests'] is None
    assert metrics.token_usage([])['total_tokens'] == 0


def test_absent_audit_is_unknown_and_failed_requests_remain_in_counts(tmp_path):
    path = tmp_path / 'metrics.db'
    create_database(path)
    absent = read_metric(path, record(path, status='failed'))
    assert absent['model_requests'] is None
    assert absent['token_usage']['total_tokens'] is None
    assert absent['retrieval']['reference_count'] is None
    assert absent['experience']['model_requests'] is None
    events = [dict(role='diagnosis', requests=[{'success': True, 'usage': {'total_tokens': 10}, 'request_experience_ids': ['source']}]),
              dict(role='repair', requests=[{'success': False, 'usage': None, 'request_experience_ids': ['unreceived']}])]
    value = read_metric(path, record(path, status='failed', audit={'model_requests': events}))
    assert value['model_requests'] == value['token_usage']['total_requests'] == 2
    assert value['token_usage']['total_tokens'] is None and value['token_usage']['reported_total_tokens'] == 10
    assert value['retrieval']['injected_requests'] == value['retrieval']['reference_count'] == 1
    assert value['repair_verified'] is False and value['status'] == 'failed'
    known_empty = read_metric(path, record(path, audit={'model_requests': [], 'knowledge_automation_enabled': False}))
    assert known_empty['model_requests'] == known_empty['token_usage']['total_tokens'] == 0
    assert known_empty['experience']['state'] == 'not_supported'
    assert known_empty['experience']['model_requests'] == known_empty['experience']['token_usage']['total_tokens'] == 0


@pytest.mark.parametrize('saved', [None, False])
def test_complete_terminal_audit_and_database_prove_no_background_work(tmp_path, saved):
    path = tmp_path / 'metrics.db'; create_database(path)
    identifier = str(uuid4())
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE experiences ADD COLUMN source_task_id TEXT')
        db.execute('INSERT INTO experiences VALUES(?,?)', ('unrelated-source', str(uuid4())))
    audit = {'task_id': identifier, 'knowledge_automation_enabled': True, 'model_requests': [
        {'success': True, 'usage': {'total_tokens': 17}}]}
    result = {'task_id': identifier, 'status': 'stopped', 'experience_saved': saved, 'experience_ids': [], 'experience_id': None}
    value = read_metric(path, record(path, status='stopped', identifier=identifier, audit=audit, result=result))
    background = value['experience']
    assert background['state'] == 'not_applicable'
    assert background['elapsed_ms'] == background['model_requests'] == background['token_usage']['total_tokens'] == 0
    assert background['merged_count'] == background['conflict_count'] == background['pending_confirmation_count'] == 0
    assert value['token_usage']['total_tokens'] == 17  # Real repair/extraction requests are still counted.


@pytest.mark.parametrize('change', ['historical', 'missing_request_log', 'wrong_audit_task', 'running', 'pending',
    'no_result', 'wrong_result_task', 'missing_ids', 'missing_saved', 'saved_true', 'source_row', 'missing_source_column', 'retry_running'])
def test_absent_source_ids_do_not_turn_missing_or_ongoing_evidence_into_zero(tmp_path, change):
    path = tmp_path / 'metrics.db'; create_database(path)
    identifier = str(uuid4())
    if change != 'missing_source_column':
        with sqlite3.connect(path) as db:
            db.execute('ALTER TABLE experiences ADD COLUMN source_task_id TEXT')
            if change == 'source_row':db.execute('INSERT INTO experiences VALUES(?,?)', ('actual-source', identifier))
    audit = {'task_id': identifier, 'knowledge_automation_enabled': True, 'model_requests': []}
    result = {'task_id': identifier, 'status': 'stopped', 'experience_saved': None, 'experience_ids': []}
    state = 'stopped'
    if change == 'historical':audit = {}
    elif change == 'missing_request_log':audit.pop('model_requests')
    elif change == 'wrong_audit_task':audit['task_id'] = str(uuid4())
    elif change in {'pending', 'running'}:state = change
    elif change == 'no_result':result = None
    elif change == 'wrong_result_task':result['task_id'] = str(uuid4())
    elif change == 'missing_ids':result.pop('experience_ids')
    elif change == 'missing_saved':result.pop('experience_saved')
    elif change == 'saved_true':result['experience_saved'] = True
    elif change == 'retry_running':audit['experience_retry'] = {'state': 'running'}
    value = read_metric(path, record(path, status=state, identifier=identifier, audit=audit, result=result))['experience']
    assert value['state'] == 'not_recorded'
    assert value['model_requests'] is None and value['token_usage']['total_tokens'] is None


@pytest.mark.parametrize('log', [None, 'missing', [None], [{'role': 'repair', 'requests': None}]])
def test_incomplete_request_log_never_becomes_zero_cost(tmp_path, log):
    path = tmp_path / 'metrics.db'; create_database(path)
    value = read_metric(path, record(path, audit={'model_requests': log}))
    assert value['model_requests'] is None and value['token_usage']['total_requests'] is None
    assert value['token_usage']['total_tokens'] is None


def test_repair_verified_requires_real_independent_test_result(tmp_path):
    from backend.shared import ToolCall
    from backend.tools import ToolService, WorkspaceManager
    source = tmp_path / 'source'; source.mkdir()
    (source / 'pyproject.toml').write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (source / 'tests').mkdir()
    (source / 'tests/test_real.py').write_text('def test_actual():\n    assert 2 + 3 == 5\n')
    manager = WorkspaceManager(tmp_path / 'work', {'source': source})
    workspace = manager.create_workspace(source, str(uuid4()))
    tool = ToolService(manager, tmp_path / 'unused-maven').execute_tool(workspace, ToolCall(id=str(uuid4()), name='run_tests'))
    assert tool.success and tool.metadata['tests'] == 1
    tool.metadata['final_verification'] = True
    final = tool.model_dump(mode='json')
    result = dict(repair_succeeded=True, tests_passed=True, tool_results=[final])
    path = tmp_path / 'metrics.db'; create_database(path)
    accepted = read_metric(path, record(path, result=result))
    assert accepted['repair_verified'] is True and accepted['final_tests_passed'] is True
    variants = [dict(final, tool_name='read_file'), dict(final, success=False), dict(final, error_code='TEST_FAILED'),
                dict(final, metadata=final['metadata'] | {'tests': True}),
                dict(final, metadata=final['metadata'] | {'tests': 0}),
                dict(final, metadata=final['metadata'] | {'failures': False}),
                dict(final, metadata=final['metadata'] | {'skipped': 1}),
                dict(final, metadata=final['metadata'] | {'runner': 'unknown'}),
                dict(final, metadata=final['metadata'] | {'final_verification': 'true'})]
    for value in variants:
        assert read_metric(path, record(path, result=result | {'tool_results': [value]}))['repair_verified'] is False
    for status in ('stopped', 'failed'):
        stopped = read_metric(path, record(path, status=status, result=result))
        assert stopped['repair_verified'] is False and stopped['final_tests_passed'] is True
    for result_without_evidence in (None, result | {'tool_results': []},
                                    result | {'tool_results': [dict(final, metadata={})]}):
        unknown = read_metric(path, record(path, result=result_without_evidence))
        assert unknown['repair_verified'] is False and unknown['final_tests_passed'] is None
    for value in variants:
        if value['tool_name'] != 'run_tests' or value['metadata'].get('runner') == 'unknown' or any(
                type(value['metadata'].get(k)) is not int for k in ('tests', 'failures', 'errors', 'skipped')) or (
                value['metadata'].get('final_verification') is not True):
            assert metrics.final_test_outcome({'tool_results': [value]}) is None
        else:
            assert metrics.final_test_outcome({'tool_results': [value]}) is False
    # An actual failed final pytest execution must remain false even with optimistic result flags.
    (source / 'tests/test_real.py').write_text('def test_actual():\n    assert 2 + 3 == 6\n')
    failed_workspace = manager.create_workspace(source, str(uuid4()))
    failed = ToolService(manager, tmp_path / 'unused-maven').execute_tool(
        failed_workspace, ToolCall(id=str(uuid4()), name='run_tests'))
    assert not failed.success and failed.metadata['tests'] == failed.metadata['failures'] == 1
    failed.metadata['final_verification'] = True
    rejected = read_metric(path, record(path, result=result | {'tool_results': [failed.model_dump(mode='json')]}))
    assert rejected['repair_verified'] is False and rejected['final_tests_passed'] is False


def knowledge_schema(path):
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE experience_auto_jobs(id TEXT PRIMARY KEY,state TEXT,created_at TEXT,updated_at TEXT,notice TEXT);
            CREATE TABLE experience_auto_pairs(id TEXT PRIMARY KEY,state TEXT,attempt INTEGER,result TEXT);
            CREATE TABLE experience_auto_links(job_id TEXT,pair_id TEXT);
            CREATE TABLE experience_auto_attempts(pair_id TEXT,attempt INTEGER,result TEXT,PRIMARY KEY(pair_id,attempt));
            CREATE TABLE knowledge_relations(id TEXT PRIMARY KEY,status TEXT);
            CREATE TABLE knowledge_auto_reviews(review_id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE knowledge_sources(experience_id TEXT,rule_id TEXT);
            CREATE TABLE knowledge_jobs(id TEXT PRIMARY KEY,rule_id TEXT,state TEXT,updated_at TEXT);
        ''')


def knowledge_value(path, ids):
    with metrics.read_database(path) as db:
        return metrics.knowledge_metrics(db, {'result': {'experience_ids': ids}}, {}, metrics.tables(db))


def test_background_shared_pairs_and_retry_history_count_actual_attempts_once(tmp_path):
    path = tmp_path / 'knowledge.db'; create_database(path); knowledge_schema(path)
    def payload(tokens):
        return json.dumps({'state': 'succeeded', 'model_requests': [{'success': True, 'usage': {'total_tokens': tokens}}],
                           'conflict_check': {'conflict_ids': ['conflict', 'conflict']}})
    with sqlite3.connect(path) as db:
        for identifier in ('source-a', 'source-b'):
            db.execute("INSERT INTO experience_auto_jobs VALUES(?,'succeeded','2026-11-15T12:00:00+00:00','2026-11-15T12:01:00+00:00','complete')", (identifier,))
            db.execute('INSERT INTO experience_auto_links VALUES(?,?)', (identifier, 'pair'))
        db.execute("INSERT INTO experience_auto_pairs VALUES('pair','succeeded',3,?)", (payload(30),))
        for number, value in ((1, payload(10)), (2, payload(20)), (3, payload(30))):
            db.execute("INSERT INTO experience_auto_attempts VALUES('pair',?,?)", (number, value))
        db.execute("INSERT INTO knowledge_relations VALUES('conflict','resolved')")
        db.execute("INSERT INTO knowledge_auto_reviews VALUES('conflict',?)", (json.dumps({'state': 'needs_confirmation'}),))
    value = knowledge_value(path, ['source-a', 'source-b'])
    assert value['model_requests'] == 3 and value['token_usage']['total_tokens'] == 60
    assert value['merged_count'] == value['conflict_count'] == value['resolved_count'] == 1
    assert value['pending_confirmation_count'] == 0
    assert value['elapsed_ms'] == 60_000
    with sqlite3.connect(path) as db:
        db.execute("UPDATE experience_auto_attempts SET result=? WHERE attempt=1", (json.dumps({'model_requests': [{'success': False}]}),))
    value = knowledge_value(path, ['source-a'])
    assert value['model_requests'] == 3 and value['token_usage']['total_tokens'] is None
    assert value['token_usage']['reported_total_tokens'] == 50


def test_background_ongoing_and_missing_archived_audit_never_report_zero(tmp_path):
    path = tmp_path / 'knowledge.db'; create_database(path); knowledge_schema(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO experience_auto_jobs VALUES('source','running','2026-11-15T12:00:00+00:00','2026-11-15T12:01:00+00:00','working')")
        db.execute("INSERT INTO experience_auto_pairs VALUES('pair','running',2,NULL)")
        db.execute("INSERT INTO experience_auto_links VALUES('source','pair')")
        db.execute("INSERT INTO experience_auto_attempts VALUES('pair',1,?)", (json.dumps({'model_requests': [{'usage': {'total_tokens': 20}}]}),))
    value = knowledge_value(path, ['source'])
    assert value['state'] == 'running' and value['elapsed_ms'] is None
    assert value['model_requests'] is None and value['token_usage']['total_tokens'] is None
    assert value['token_usage']['reported_total_tokens'] == 20
    with sqlite3.connect(path) as db:
        db.execute("UPDATE experience_auto_jobs SET state='failed'")
        db.execute("UPDATE experience_auto_pairs SET state='failed',result=?", (json.dumps({'model_requests': []}),))
        db.execute("UPDATE experience_auto_attempts SET result=NULL")
    value = knowledge_value(path, ['source'])
    assert value['state'] == 'failed' and value['model_requests'] is None
    assert value['token_usage']['total_requests'] is None


def test_local_rule_maintenance_is_part_of_background_completion(tmp_path):
    path = tmp_path / 'knowledge.db'; create_database(path); knowledge_schema(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO experience_auto_jobs VALUES('source','succeeded','2026-11-15T12:00:00+00:00','2026-11-15T12:00:10+00:00','no candidates')")
        db.execute("INSERT INTO knowledge_sources VALUES('source','rule')")
        db.execute("INSERT INTO knowledge_jobs VALUES('maintenance','rule','pending','2026-11-15T12:00:15+00:00')")
    value = knowledge_value(path, ['source'])
    assert value['state'] == 'pending' and value['elapsed_ms'] is None
    assert value['token_usage']['total_tokens'] is None and value['model_requests'] is None
    with sqlite3.connect(path) as db:
        db.execute("UPDATE knowledge_jobs SET state='succeeded'")
    value = knowledge_value(path, ['source'])
    assert value['state'] == 'succeeded' and value['elapsed_ms'] == 15_000
    assert value['model_requests'] == value['token_usage']['total_tokens'] == 0


def test_peer_metrics_reads_do_not_create_or_modify_databases(tmp_path):
    local, peer, missing = (tmp_path / name for name in ('local.db', 'peer.db', 'missing.db'))
    create_database(peer)
    row = record(peer, audit={'model_requests': []})
    with sqlite3.connect(peer) as db:
        db.execute('CREATE TABLE sentinel(value TEXT)')
        db.execute("INSERT INTO sentinel VALUES('untouched')")
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'sources': [dict(database_path=str(peer), runtime_label='before', task_ids=[row['id']]),
                                               dict(database_path=str(missing), runtime_label='not started')]}))
    original = peer.read_bytes()
    with serve(local, registry) as client:
        response = client.get('/api/task-metrics')
        assert response.status_code == 200 and len(response.json()) == 1
        assert response.json()[0]['runtime_label'] == 'before'
        assert client.get('/api/tasks/' + row['id'] + '/metrics').status_code == 200
        assert client.get('/api/tasks/' + str(uuid4()) + '/metrics').status_code == 404
    assert not local.exists() and not missing.exists() and peer.read_bytes() == original
    with sqlite3.connect(peer) as db:
        assert db.execute('SELECT value FROM sentinel').fetchone()[0] == 'untouched'
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='knowledge_rules'").fetchall()
    with metrics.read_database(peer) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("INSERT INTO sentinel VALUES('forbidden')")


def test_peer_task_allowlist_and_corrupt_registry_fail_closed(tmp_path):
    peer = tmp_path / 'peer.db'; create_database(peer)
    row = record(peer)
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'sources': [dict(database_path=str(peer), task_ids=[])]}))
    with serve(tmp_path / 'missing-local.db', registry) as client:
        assert client.get('/api/task-metrics').json() == []
        assert client.get('/api/tasks/' + row['id'] + '/metrics').status_code == 404
        registry.write_text('{broken')
        assert client.get('/api/task-metrics').status_code == 503


def test_invalid_elapsed_time_remains_unknown():
    assert metrics.elapsed(None, None) is None
    assert metrics.elapsed('invalid', 'invalid') is None
    assert metrics.elapsed('2026-11-15T12:01:00+00:00', '2026-11-15T12:00:00+00:00') is None


def local_review_fixture(path):
    create_database(path)
    knowledge_schema(path)
    with sqlite3.connect(path) as db:
        db.executescript('''
            ALTER TABLE knowledge_relations ADD COLUMN left_rule TEXT;
            ALTER TABLE knowledge_relations ADD COLUMN right_rule TEXT;
            ALTER TABLE knowledge_relations ADD COLUMN kind TEXT;
            ALTER TABLE knowledge_relations ADD COLUMN version INTEGER DEFAULT 1;
            CREATE TABLE experience_merge_sources(source_id TEXT);
            CREATE TABLE experience_publication_revisions(previous_id TEXT);
            CREATE TABLE experience_retirements(experience_id TEXT);
            CREATE TABLE knowledge_pauses(rule_id TEXT,review_id TEXT);
        ''')
        db.execute("INSERT INTO experience_auto_jobs VALUES('source-a','waiting','2026-11-15T12:00:00+00:00','2026-11-15T12:01:00+00:00','awaiting review')")
        for source, rule in (('source-a', 'rule-a'), ('source-b', 'rule-b'), ('source-c', 'rule-c'), ('source-d', 'rule-d')):
            db.execute('INSERT INTO knowledge_sources VALUES(?,?)', (source, rule))
        for identity, left, right in (('local-review', 'rule-a', 'rule-b'), ('unrelated-review', 'rule-c', 'rule-d')):
            db.execute("INSERT INTO knowledge_relations VALUES(?,'pending',?,?,'unknown',1)", (identity, left, right))
            db.execute('INSERT INTO knowledge_auto_reviews VALUES(?,?)', (identity, json.dumps({
                'state': 'needs_confirmation', 'actor': 'system', 'reason': 'Both real implementations pass both test sets',
                'model_requests': [{'usage': {'total_tokens': 999}}]})))


def test_local_unknown_review_is_visible_without_cross_project_pair_and_not_new_cost(tmp_path):
    path = tmp_path / 'local.db'
    local_review_fixture(path)
    original = path.read_bytes()
    value = knowledge_value(path, ['source-a'])
    assert value['conflict_count'] == value['pending_confirmation_count'] == 1
    assert value['resolved_count'] == 0 and value['state'] == 'waiting'
    assert '本次来源涉及' in value['notice'] and '不表示全部由本次新建' in value['notice']
    assert value['token_usage']['reported_total_tokens'] == 0  # Related review data is not a charge to this job.
    assert path.read_bytes() == original
    with sqlite3.connect(path) as db:
        # A relation may also be referenced by a cross-project pair; count it once.
        payload = {'model_requests': [{'success': True, 'usage': {'total_tokens': 12}}],
                   'conflict_check': {'conflict_ids': ['local-review']}}
        db.execute("INSERT INTO experience_auto_pairs VALUES('pair','succeeded',1,?)", (json.dumps(payload),))
        db.execute("INSERT INTO experience_auto_links VALUES('source-a','pair')")
        db.execute("UPDATE knowledge_relations SET status='resolved',kind='compatible',version=2 WHERE id='local-review'")
        db.execute("UPDATE knowledge_auto_reviews SET data=? WHERE review_id='local-review'", (json.dumps({
            'state': 'resolved', 'actor': 'system', 'result_version': 2, 'resolution': 'confirm_compatible'}),))
        db.execute("UPDATE experience_auto_jobs SET state='succeeded'")
    value = knowledge_value(path, ['source-a'])
    assert value['conflict_count'] == value['resolved_count'] == 1
    assert value['pending_confirmation_count'] == 0
    assert value['model_requests'] == 1 and value['token_usage']['total_tokens'] == 12


@pytest.mark.parametrize('reason', ['resolved', 'retired', 'merged', 'published', 'paused'])
def test_obsolete_or_resolved_local_review_does_not_keep_waiting(tmp_path, reason):
    path = tmp_path / 'local.db'
    local_review_fixture(path)
    with sqlite3.connect(path) as db:
        if reason == 'resolved':
            db.execute("UPDATE knowledge_relations SET status='resolved' WHERE id='local-review'")
        elif reason == 'retired':
            db.execute("INSERT INTO experience_retirements VALUES('source-b')")
        elif reason == 'merged':
            db.execute("INSERT INTO experience_merge_sources VALUES('source-b')")
        elif reason == 'published':
            db.execute("INSERT INTO experience_publication_revisions VALUES('source-b')")
        else:
            db.execute("INSERT INTO knowledge_pauses VALUES('rule-b','another-review')")
    value = knowledge_value(path, ['source-a'])
    assert value['conflict_count'] == 1
    assert value['pending_confirmation_count'] == 0
    assert value['resolved_count'] == int(reason == 'resolved')


def test_reopened_local_review_requires_confirmation_even_with_old_system_result(tmp_path):
    path = tmp_path / 'local.db'
    local_review_fixture(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE knowledge_relations SET version=3 WHERE id='local-review'")
        db.execute("UPDATE knowledge_auto_reviews SET data=? WHERE review_id='local-review'", (json.dumps({
            'state': 'resolved', 'actor': 'system', 'result_version': 2, 'resolution': 'keep_separate'}),))
    value = knowledge_value(path, ['source-a'])
    assert value['pending_confirmation_count'] == 1 and value['resolved_count'] == 0
