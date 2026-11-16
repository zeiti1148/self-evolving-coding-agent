"""Same-project semantic merging uses real task evidence and independent review."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.experience import ExperienceStore
from backend.experience.automation import reschedule_local_comparisons
from backend.experience.database import connect
from backend.experience.local_equivalence import same_domain
from backend.tests.test_automatic_repair_merge import WorkflowModel, wait_for
from backend.tests.test_experience_consolidation import record, save, scan
from backend.tests.test_uploaded_projects import archive, configured, python_files


class EquivalenceModel(WorkflowModel):
    calls = []
    reject_review = False

    def complete(self, messages, *, tools=None):
        facts = json.loads(messages[1]['content'])
        role = facts.get('agent_role')
        if role in {'local_equivalence', 'local_equivalence_review'}:
            self.calls.append(role)
            checks = []
            for candidate in facts['candidates']:
                left, right = candidate['left_issue'], candidate['right_issue']
                checks.append(dict(left_issue=left, right_issue=right,
                    equivalent=not (self.reject_review and role.endswith('_review')), conditions_preserved=True,
                    left_source_id=facts['sources'][0]['id'],
                    right_source_id=facts['sources'][1]['id'], reason='两种写法保持相同的缺失值与非空输入行为。'))
            assert facts['actual_changes'] and facts['runner_config'] is not None
            return {'content': json.dumps({'checks': checks})}
        if not tools and 'task' in facts:
            result = super().complete(messages, tools=tools)
            payload = json.loads(result['content'])
            if 'second' in facts['task']:
                payload['experiences'][0]['issues'][0]['solution'] = 'Use the local zero fallback for missing input; otherwise parse the supplied integer.'
            return {'content': json.dumps(payload)}
        return super().complete(messages, tools=tools)


@pytest.mark.parametrize('reject', [False, True])
def test_normal_upload_repairs_merge_only_after_both_reviews(tmp_path, reject):
    class Model(EquivalenceModel):
        reject_review = reject
        calls = []
    settings = configured(tmp_path)
    with TestClient(create_app(settings, llm_factory=Model)) as client:
        project = client.post('/api/projects/upload', content=archive(python_files())).json()
        ids = []
        for description in ('Fix optional stock count', 'Fix optional stock count second wording'):
            created = client.post('/api/tasks', json={'source_project': project['id'], 'task_description': description, 'max_steps': 3})
            task = wait_for(lambda: client.get('/api/tasks/' + created.json()['task_id']).json(), lambda t: t['status'] not in {'pending','running'})
            assert task['status'] == 'succeeded', task
            ids.append(task['result']['experience_id'])
        def done(value):
            return len(Model.calls) == 2 and any(c['state'] == ('rejected' if reject else 'merged')
                for j in value['items'] if j['id'] == ids[-1] for c in j['changes'])
        page = wait_for(lambda: client.get('/api/experiences/automation').json(), done)
        cards = client.get('/api/experiences/library').json()['items']
        assert len(cards) == (2 if reject else 1)
        if not reject:
            assert cards[0]['source_count'] == 2 and cards[0]['evidence_count'] == 2
            merged = next(c for j in page['items'] if j['id']==ids[-1] for c in j['changes'] if c['state']=='merged')
            assert merged['history'][-1]['data']['actor'] == 'system'
            assert {s['id'] for s in merged['before']} == set(ids)
            with connect(settings.database_path) as c:
                assessment=json.loads(c.execute("SELECT details FROM knowledge_relations WHERE kind='equivalent'").fetchone()[0])
            assert {quote['source_id'] for quote in assessment['quotes']} == set(ids)
            assert all(quote['quote'] == next(s['issues'][quote['issue_index']]['solution']
                       for s in merged['before'] if s['id']==quote['source_id']) for quote in assessment['quotes'])
        assert Model.calls == ['local_equivalence','local_equivalence_review']
        for _ in range(2):
            client.get('/api/experiences/automation')
        assert len(Model.calls) == 2


def pair(tmp_path, **changes):
    store = ExperienceStore(tmp_path/'app.db')
    revision = 'tree:'+'a'*64
    a = save(store, record(source_revision='git:'+'1'*40+';'+revision))
    b = save(store, record(source_revision='git:'+'2'*40+';'+revision,
        issues=[dict(error_type='ValueError', root_cause='Missing input', solution='Raise the required error before handling absent input',files=['app.py'])], **changes))
    scan(store)
    return store, a, b


def test_same_content_host_git_metadata_replans_completed_empty_jobs_once(tmp_path):
    store, a, b = pair(tmp_path)
    assert same_domain(a,b)
    with connect(store.database_path) as c:
        c.execute("UPDATE experience_auto_jobs SET state='succeeded',candidates='[]'")
    reschedule_local_comparisons(store.database_path)
    with connect(store.database_path) as c:
        assert c.execute("SELECT count(*) FROM experience_auto_jobs WHERE state='pending' AND candidates IS NULL").fetchone()[0] == 2
        c.execute("UPDATE experience_auto_jobs SET state='succeeded'")
    reschedule_local_comparisons(store.database_path)
    with connect(store.database_path) as c:
        assert c.execute("SELECT count(*) FROM experience_auto_jobs WHERE state='pending'").fetchone()[0] == 0
    assert store.get_experience(a.id).model_dump() == a.model_dump()


def test_named_contracts_are_not_normalized_as_host_metadata(tmp_path):
    _, a, b = pair(tmp_path, compatibility_key='different-business-contract')
    assert not same_domain(a,b)


def test_existing_conflict_or_reopened_review_cannot_be_overwritten(tmp_path):
    from backend.experience.local_equivalence import snapshot
    store, a, b = pair(tmp_path)
    with connect(store.database_path) as c:
        assert snapshot(c,[a,b])
        c.execute("UPDATE knowledge_relations SET kind='conflict'")
        assert snapshot(c,[a,b]) == []
        c.execute("UPDATE knowledge_relations SET kind='unknown',version=3")
        assert snapshot(c,[a,b]) == []


def test_source_binding_and_issue_coverage_cannot_be_invented(tmp_path):
    from backend.experience.local_equivalence import Assessment, validate
    _,a,b=pair(tmp_path)
    check=dict(left_issue=0,right_issue=0,equivalent=True,conditions_preserved=True,
               left_source_id=a.id,right_source_id=b.id,reason='Checked both complete source rules.')
    candidates=[dict(left_issue=0,right_issue=0)]
    validate(Assessment(checks=[check]),candidates,[a,b])
    with pytest.raises(ValueError,match='source binding'):
        validate(Assessment(checks=[check|{'left_source_id':b.id}]),candidates,[a,b])
    with pytest.raises(ValueError,match='Incomplete'):
        validate(Assessment(checks=[check,check]),candidates,[a,b])
