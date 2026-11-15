import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RUN = Path(__file__).resolve().parent
ROOT = RUN.parents[2]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def request(path, data=None, content_type='application/json'):
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=False).encode()
    req = urllib.request.Request('http://127.0.0.1:8000'+path, data=data,
                                 headers={'Content-Type': content_type})
    try:
        with opener.open(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        print(error.read().decode('utf-8'))
        raise

def save(name, value):
    (RUN/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def idle():
    with sqlite3.connect((ROOT/'data/app.db').as_uri()+'?mode=ro', uri=True) as db:
        active = db.execute("SELECT id,status FROM api_tasks WHERE status IN ('pending','running')").fetchall()
        jobs = db.execute("SELECT id,state FROM experience_auto_jobs WHERE state='running'").fetchall()
        retries = db.execute("SELECT id FROM api_tasks WHERE json_extract(audit_json,'$.experience_retry.state')='running'").fetchall()
    assert not (active or jobs or retries), (active, jobs, retries)
    print('No active repairs, model comparisons, or extraction retries.')

if __name__ == '__main__':
    command = sys.argv[1]
    state_path = RUN/'run.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    if command == 'idle':
        idle()
    elif command == 'inspect':
        runtime = request('/api/runtime-options')
        save('runtime.json', runtime)
        print(json.dumps(runtime, ensure_ascii=False, indent=2))
    elif command == 'upload':
        assert not state.get('project'), 'Already uploaded; reuse the registered project'
        project = request('/api/projects/upload?label=pytest-10356-auto-evidence',
                          (RUN/'pytest-10356-original.zip').read_bytes(), 'application/zip')
        state['uploaded_project'] = project
        config = json.loads((RUN/'upload-manifest.json').read_text(encoding='utf-8'))['runner_config']
        config['python_executable'] = str(ROOT/'.cache/engineering-repair-research/pytest10356-auto-env/Scripts/python.exe')
        project = request('/api/projects/'+project['id']+'/configure', {'runner_config':config})
        state['project'] = project
        state['uploaded_at'] = datetime.now(timezone.utc).isoformat()
        save('run.json', state)
        print(json.dumps(project, ensure_ascii=False, indent=2))
    elif command == 'submit':
        assert not state.get('task'), 'A live task was already submitted'
        manifest = json.loads((RUN/'upload-manifest.json').read_text(encoding='utf-8'))
        body = dict(source_project=state['project']['id'], task_description=manifest['task_description'],
                    max_steps=70, execution_mode='single')
        save('submitted-request.json', body)
        state['task'] = request('/api/tasks', body)
        save('run.json', state)
        print(json.dumps(state['task'], ensure_ascii=False))
    elif command == 'poll':
        identifier = state['task']['task_id']
        task = request('/api/tasks/'+identifier)
        steps = request('/api/tasks/'+identifier+'/steps')
        save('task.json', task); save('steps.json', steps)
        result = task.get('result') or {}
        ids = result.get('experience_ids') or ([result['experience_id']] if result.get('experience_id') else [])
        tests = [dict(sequence=s['sequence'], success=s['result']['success'],
                    **{k:s['result']['metadata'].get(k) for k in ('tests','failures','errors','skipped','final_verification')})
                 for s in steps['steps'] if s['call']['name']=='run_tests']
        summary = dict(id=identifier, state=task['status'], steps=task['steps_completed'], tests=tests,
                       result={k:result.get(k) for k in ('repair_succeeded','experience_saved','experience_ids','error_message')})
        for identity in ids:
            source = request('/api/experiences/'+identity)
            save('experience-'+identity+'.json', source)
            page = request('/api/experiences/automation?experience_id='+identity)
            save('automation-'+identity+'.json', page)
            summary['automation'] = [dict(id=j['id'],state=j['state'],notice=j['notice'],
                changes=[dict(id=c['id'],kind=c['kind'],state=c['state'],reason=c['reason']) for c in j['changes']])
                for j in page['items']]
        save('status.json', summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
