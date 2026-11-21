"""Real task/upload/test pipeline with explicitly controlled model responses."""
import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.experience import ExperienceStore
from backend.experience.automation import AutomaticMergeWorker, claim, feed, process, retry
from backend.experience.database import connect
from backend.shared import ConflictResolution
from backend.tests.test_experience_consolidation import record, save, scan, EvidenceClassifier, alternative
from backend.tests.test_uploaded_projects import configured, archive, python_files


class WorkflowModel:
    comparisons = 0
    reject = False
    fail = False

    def __init__(self):
        self.decisions = 0

    def complete(self, messages, *, tools=None):
        system, facts = messages[0]['content'], json.loads(messages[1]['content'])
        if 'consolidate repair knowledge' in system:
            WorkflowModel.comparisons += 1
            if self.fail:
                raise RuntimeError('controlled transport failure')
            return {'content': json.dumps(dict(
                decision='different_principles' if self.reject else 'shared_principle',
                title='先处理缺失值，再执行转换', principle='转换可选输入前先识别缺失情况，按各项目合同采用本地默认值。',
                applicability=['输入允许缺失且需要进行数值转换'], boundaries=['默认值由具体业务决定，不允许互相覆盖。'],
                bindings=[dict(source_id=s['id'], supports=[dict(issue_index=0, field='solution',
                    quote=s['issues'][0]['solution'])], business_parameters={}) for s in facts['sources']],
                reason='两条经验支持相同的操作顺序，各自默认值保留。'))}
        if 'Independently audit' in system:
            return {'content': json.dumps(dict(approved=True,
                checks=[dict(source_id=s['id'], supported=True, business_conditions_preserved=True,
                             reason='来源原文与业务约束均保留。') for s in facts['sources']],
                unsafe_generalizations=[], reason='两份来源独立支持共同规则。'))}
        second = 'seats' in facts.get('task', '')
        filename, default = ('seats.py', 1) if second else ('app.py', 0)
        if tools:
            self.decisions += 1
            if self.decisions > 1:
                return {'role': 'assistant', 'content': '已修改生产代码，请验收。'}
            return {'role':'assistant', 'content':None, 'tool_calls':[{'id':'write', 'type':'function',
                'function':{'name':'write_file', 'arguments':json.dumps({'path':filename,
                    'content':f'def count(value):\n    return {default} if value is None else int(value)\n'})}}]}
        solution = ('Interpret an absent seat selection as the booking default before parsing quantities.' if second else
                    'Guard missing stock input before converting the supplied text to an integer.')
        return {'content':json.dumps({'experiences':[dict(task_summary='Seat reservations' if second else 'Stock counts',
            root_cause='Missing optional values cannot be converted directly.', solution=solution,
            applicability=['Optional numeric input'], constraints=[f'The local missing-input default is {default}.'],
            tags=['python'], issues=[dict(error_type='TypeError', root_cause='Missing optional values cannot be converted directly.',
                                        solution=solution, files=[filename])])]})}

    def close(self):
        pass


def wait_for(read, done, timeout=35):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        value = read()
        if done(value):
            return value
        time.sleep(.1)
    raise AssertionError(value)


def test_normal_uploaded_repairs_automatically_compare_merge_and_survive_restart(tmp_path):
    WorkflowModel.comparisons = 0
    settings = configured(tmp_path)
    identifiers = []
    with TestClient(create_app(settings, llm_factory=WorkflowModel)) as client:
        for files, description in [(python_files(), 'Fix optional stock count'), (python_files('seats', 1), 'Fix seats selection')]:
            project = client.post('/api/projects/upload', content=archive(files)).json()
            created = client.post('/api/tasks', json={'source_project':project['id'], 'task_description':description, 'max_steps':3})
            assert created.status_code == 202, created.text
            task = wait_for(lambda: client.get('/api/tasks/'+created.json()['task_id']).json(),
                            lambda value: value['status'] not in {'pending','running'})
            assert task['status'] == 'succeeded', task
            identifiers.append(task['result']['experience_id'])
        page = wait_for(lambda: client.get('/api/experiences/automation').json(),
                        lambda value: any(c['state']=='merged' and c['kind']=='shared_principle'
                                          for j in value['items'] if j['state']=='succeeded' for c in j['changes']))
        job = next(j for j in page['items'] if j['id'] == identifiers[-1])
        merged = next(c for c in job['changes'] if c['kind']=='shared_principle')
        assert job['state'] == 'succeeded'
        cards=client.get('/api/experiences/library').json()['items']
        [principle]=[card for card in cards if card['kind']=='principle']
        assert principle['merged'] and set(principle['source_ids'])==set(identifiers)
        assert principle['source_count']==2 and principle['evidence_count']==2
        assert {s['id'] for s in merged['before']} == set(identifiers)
        assert len({s['source_task_id'] for s in merged['before']}) == 2
        assert len({s['issues'][0]['solution'] for s in merged['before']}) == 2
        assert {i['constraints'][0] for i in merged['after']['instances']} == {
            'The local missing-input default is 0.', 'The local missing-input default is 1.'}
        assert WorkflowModel.comparisons == 1
        assert client.get('/api/experiments').status_code == 404
        # Display and refresh are read-only, and preserve the actual originals.
        for original in merged['before']:
            assert client.get('/api/experiences/'+original['id']).json() == original
        assert len(client.get('/api/experiences/automation?experience_id='+identifiers[-1]).json()['items']) == 1
    with TestClient(create_app(configured(tmp_path), llm_factory=WorkflowModel)) as client:
        assert client.get('/api/experiences/automation').json()['items']
        assert WorkflowModel.comparisons == 1
        assert 'lease_token' not in json.dumps(client.get('/api/experiences/automation').json())


def local_store(tmp_path):
    return ExperienceStore(tmp_path/'auto.db')


def no_model():
    raise AssertionError('Local rule merging must not require a remote model.')


def test_nonidentical_local_rules_show_before_after_without_rewriting_sources(tmp_path):
    store = local_store(tmp_path)
    a = save(store, record())
    b = save(store, record(issues=[dict(error_type='ValueError', root_cause='An optional input is absent',
        solution='Raise ValueError for missing input', files=['app.py'])]))
    scan(store, EvidenceClassifier())
    worker = AutomaticMergeWorker(store.database_path, no_model)
    while worker.run_once():
        pass
    page = feed(store.database_path)
    change = next(c for j in page['items'] for c in j['changes'] if c['kind']=='equivalent_rules')
    assert change['state']=='merged' and len(change['before'])==2 and change['evidence_count']==2
    assert {s['issues'][0]['solution'] for s in change['before']} == {'Reject missing input', 'Raise ValueError for missing input'}
    assert store.get_experience(a.id)==a and store.get_experience(b.id)==b


def test_conflict_decision_and_reopen_are_live_in_before_after_feed(tmp_path):
    store = local_store(tmp_path)
    a = save(store, record()); save(store, alternative())
    scan(store, EvidenceClassifier())
    review = store.list_conflicts()[0]
    before = feed(store.database_path)['items'][0]['changes']
    assert any(c['kind']=='review' and c['state']=='pending' for c in before)
    store.resolve_conflict(review.id, ConflictResolution(version=review.version, resolution='prefer_source',
        preferred_source_id=a.id, note='Verified the required exception contract.'))
    resolved = next(c for c in feed(store.database_path)['items'][0]['changes'] if c['kind']=='review')
    assert resolved['state']=='resolved'
    assert resolved['review']['preferred_source_id']==a.id
    assert resolved['history'][-1]['data']['note']=='Verified the required exception contract.'
    store.reopen_conflict(review.id, resolved['review']['version'], 'New regression needs another review.')
    reopened = next(c for c in feed(store.database_path)['items'][0]['changes'] if c['kind']=='review')
    assert reopened['state']=='pending' and [h['action'] for h in reopened['history']]==['resolve','reopen']
    assert not any(c['state']=='merged' for c in feed(store.database_path)['items'][0]['changes'])


def test_failure_retries_are_explicit_idempotent_and_do_not_rerun_repairs(tmp_path):
    store = local_store(tmp_path)
    save(store, record(repository_id='one')); b = save(store, record(repository_id='two'))
    scan(store)
    class Failing(WorkflowModel):
        fail = True
    worker = AutomaticMergeWorker(store.database_path, Failing)
    WorkflowModel.comparisons = 0
    while worker.run_once():
        pass
    job = next(j for j in feed(store.database_path)['items'] if j['id']==b.id)
    assert job['state']=='failed' and WorkflowModel.comparisons==1
    assert not worker.run_once()
    retry(store.database_path, b.id); retry(store.database_path, b.id)
    with connect(store.database_path) as connection:
        assert connection.execute('SELECT attempt FROM experience_auto_pairs').fetchone()[0]==2
    worker.llm_factory = WorkflowModel
    assert worker.run_once()
    assert WorkflowModel.comparisons==2
    assert any(c['state']=='merged' for c in feed(store.database_path)['items'][0]['changes'])


def test_expired_job_is_fenced_and_running_model_pair_requires_explicit_retry(tmp_path):
    store = local_store(tmp_path)
    a = save(store, record(repository_id='one')); b = save(store, record(repository_id='two')); scan(store)
    worker = AutomaticMergeWorker(store.database_path, no_model)
    assert worker.run_once()  # First source has no older cross-project candidates.
    old = claim(store.database_path)
    from backend.experience.automation import _plan, _finish
    _plan(store.database_path, old)
    with connect(store.database_path) as connection:
        connection.execute("UPDATE experience_auto_pairs SET state='running'")
        connection.execute('UPDATE experience_auto_jobs SET lease_until=0 WHERE id=?', (b.id,))
    newer = claim(store.database_path)
    _finish(store.database_path, old, 'succeeded', 'stale worker')
    process(store.database_path, newer, no_model)
    with connect(store.database_path) as connection:
        assert connection.execute('SELECT state FROM experience_auto_jobs WHERE id=?', (b.id,)).fetchone()[0]=='failed'
    assert store.get_experience(a.id) is not None


def test_new_save_and_queue_rollback_together(tmp_path, monkeypatch):
    store = local_store(tmp_path)
    store.initialize()
    import backend.experience.automation as automation
    original = automation.enqueue
    def broken(connection, entry):
        original(connection, entry)
        raise RuntimeError('rollback test')
    monkeypatch.setattr(automation, 'enqueue', broken)
    import pytest
    with pytest.raises(RuntimeError):
        save(store, record())
    with connect(store.database_path) as connection:
        assert connection.execute('SELECT count(*) FROM experiences').fetchone()[0]==0
        assert connection.execute('SELECT count(*) FROM experience_auto_jobs').fetchone()[0]==0


def test_rejected_pairs_are_not_retried_by_refresh(tmp_path):
    store = local_store(tmp_path)
    save(store, record(repository_id='one')); b = save(store, record(repository_id='two')); scan(store)
    class Rejecting(WorkflowModel):
        reject = True
    WorkflowModel.comparisons = 0
    worker = AutomaticMergeWorker(store.database_path, Rejecting)
    while worker.run_once():
        pass
    page = feed(store.database_path)
    assert page['items'][0]['changes'][0]['state']=='rejected'
    assert page['items'][0]['changes'][0]['after'] is None
    retry(store.database_path, b.id)
    assert not worker.run_once() and WorkflowModel.comparisons==1


def test_paused_source_hides_a_previously_accepted_merge(tmp_path):
    store = local_store(tmp_path)
    a = save(store, record(repository_id='one')); save(store, record(repository_id='two')); scan(store)
    worker = AutomaticMergeWorker(store.database_path, WorkflowModel)
    while worker.run_once():
        pass
    assert feed(store.database_path)['items'][0]['changes'][0]['state']=='merged'
    with connect(store.database_path) as connection:
        connection.execute("UPDATE knowledge_rules SET state='needs_review' WHERE id IN "
                           '(SELECT rule_id FROM knowledge_sources WHERE experience_id=?)', (a.id,))
    change = feed(store.database_path)['items'][0]['changes'][0]
    assert change['state']=='invalidated' and change['after'] is None


def test_candidates_beyond_the_first_batch_are_compared_and_failed_pairs_are_not_merges(tmp_path):
    store = local_store(tmp_path)
    for index in range(7):
        save(store, record(repository_id=f'old-{index}'))
    with connect(store.database_path) as connection:
        connection.execute("UPDATE experience_auto_jobs SET state='succeeded'")
    current = save(store, record(repository_id='new')); scan(store)
    class Rejecting(WorkflowModel):
        reject = True
    WorkflowModel.comparisons = 0
    worker = AutomaticMergeWorker(store.database_path, Rejecting)
    while worker.run_once():
        pass
    job = feed(store.database_path, experience_id=current.id)['items'][0]
    # Seven candidates exceed one batch of five; the remainder must not be dropped.
    assert job['candidate_total']==7 and job['selected_candidates']==7
    assert job['compared_candidates']==7 and job['deferred_candidates']==0
    assert WorkflowModel.comparisons==7
    assert len(job['changes'])==7 and all(c['state']=='rejected' for c in job['changes'])


def test_conflict_resolution_automatically_resumes_eligible_comparisons(tmp_path):
    store = local_store(tmp_path)
    a = save(store, record())
    b = save(store, alternative())
    current = save(store, record(repository_id='another-team'))
    scan(store, EvidenceClassifier())
    worker = AutomaticMergeWorker(store.database_path, WorkflowModel)
    while worker.run_once():
        pass
    assert feed(store.database_path, experience_id=current.id)['items'][0]['state']=='waiting'
    review = store.list_conflicts()[0]
    store.resolve_conflict(review.id, ConflictResolution(version=review.version, resolution='prefer_source',
        preferred_source_id=a.id, note='Original contract requires rejecting missing input.'))
    def poll():
        worker.run_once()
        return feed(store.database_path, experience_id=current.id)['items'][0]
    job = wait_for(poll, lambda value: value['state']=='succeeded', timeout=10)
    assert job['state']=='succeeded'
    merged = [change for change in job['changes'] if change['state']=='merged']
    assert len(merged)==1 and {source['id'] for source in merged[0]['before']}=={a.id, current.id}
    rejected = [change for change in job['changes'] if change['state']=='rejected']
    assert len(rejected)==1 and b.id in {source['id'] for source in rejected[0]['before']}


def test_failed_uploaded_repair_does_not_create_merge_jobs(tmp_path):
    class BrokenRepair(WorkflowModel):
        def complete(self, messages, *, tools=None):
            response = super().complete(messages, tools=tools)
            if response.get('tool_calls'):
                response['tool_calls'][0]['function']['arguments'] = json.dumps({
                    'path':'app.py', 'content':'def count(value):\n    return int(value) + 1\n'})
            return response
    with TestClient(create_app(configured(tmp_path), llm_factory=BrokenRepair)) as client:
        project = client.post('/api/projects/upload', content=archive(python_files())).json()
        created = client.post('/api/tasks', json={'source_project':project['id'], 'task_description':'Fix missing count', 'max_steps':3})
        result = wait_for(lambda: client.get('/api/tasks/'+created.json()['task_id']).json(),
                          lambda value: value['status'] not in {'pending','running'})
        assert result['status'] in {'failed', 'stopped'} and result['result']['tests_passed'] is False
        assert result['result']['experience_saved'] is not True
        assert client.get('/api/experiences/automation').json()['items']==[]
