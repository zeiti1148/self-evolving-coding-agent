"""Paired contextual/adaptive run on one registered project with real model calls.

This is a NEW, self-contained protocol, not a reproduction of the 2026-11-15
sequence: each arm gets an identical seeded rule library, then one real repair
task runs through the ordinary HTTP API. Seeded rules are synthetic fixtures and
are labelled as such in the report.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import Settings  # noqa: E402
from backend.experience import ExperienceStore  # noqa: E402
from backend.experience.database import connect  # noqa: E402
from backend.experience.jobs import drain  # noqa: E402
from backend.projects import source_revision  # noqa: E402
from backend.shared import ExperienceCreate  # noqa: E402

OUT = ROOT / '.cache/minimal-retrieval-pair'
PROJECT = 'java-null-greeting'
PORTS = {'baseline': 8019, 'contextual': 8017, 'adaptive': 8018}
MAX_STEPS = 12
TASK_TIMEOUT = 900


def seeded(language, repository_id, index, task_id, revision):
    return ExperienceCreate(**dict(
        schema_version=4, language=language, scope='project', repository_id=repository_id,
        compatibility_key=revision,
        task_summary=f'Seed rule {index}: null input handled before formatting',
        root_cause='A null value reaches string formatting and raises a null pointer error.',
        solution=f'Check the value for null before formatting and return the caller default {index}.',
        verification='Original tests passed', source_task_id=task_id, applicability=['Text input may be null'],
        constraints=['Do not change the default text for non-null input.'],
        issues=[dict(error_type='NullPointerException', root_cause='Null value reaches formatting.',
                     solution='Return the default greeting when the name is null.',
                     files=['src/main/java/dev/selfevolving/benchmark/GreetingService.java'],
                     symbols=['src/main/java/dev/selfevolving/benchmark/GreetingService.java#GreetingService.greet'])],
        evidence=[dict(source_task_id=task_id, tool_call_id=str(uuid4()), runner='maven', tests=2)]))


def seed(database, revision):
    store = ExperienceStore(database)
    store.initialize()
    saved = []
    for index in (1, 2):
        task = str(uuid4())
        saved.append(store.save_experiences([seeded('java', 'java-benchmarks', index, task, revision)],
                                            source_task_id=task)[0])
    drain(database, seconds=60)
    with connect(database) as connection:
        rules = connection.execute("SELECT count(*) FROM knowledge_rules WHERE state='ready'").fetchone()[0]
    return [item.id for item in saved], rules


def request(port, path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=data, method=method or ('POST' if data else 'GET'),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def wait_ready(port, deadline):
    while time.monotonic() < deadline:
        try:
            request(port, '/api/projects')
            return True
        except OSError:
            time.sleep(0.5)
    return False


def run_arm(arm, dry, revision, compact=False):
    folder = OUT / arm
    database = folder / 'app.db'
    if dry:
        return dict(arm=arm, database=str(database), port=PORTS[arm], planned=True, revision=revision)
    folder.mkdir(parents=True, exist_ok=True)
    if database.exists():
        database.unlink()
    ids, rules = seed(database, revision)
    env = dict(os.environ, DATABASE_URL='sqlite:///' + str(database).replace('\\', '/'),
               EXPERIENCE_RETRIEVAL_STRATEGY=arm, LLM_CONFIG_SOURCE='project',
               EXPERIENCE_COMPACT_AFTER_FIRST='true' if compact else 'false',
               LLM_CONFIG_FILE=str(ROOT / '.env'), PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    log = open(folder / 'server.log', 'ab')
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.api.main:app', '--host', '127.0.0.1',
                               '--port', str(PORTS[arm]), '--workers', '1'],
                              cwd=ROOT, env=env, stdout=log, stderr=log)
    try:
        if not wait_ready(PORTS[arm], time.monotonic() + 90):
            raise RuntimeError(f'{arm}: service did not become ready')
        projects = request(PORTS[arm], '/api/projects')
        project = next(item for item in projects if item['id'] == PROJECT)
        task = request(PORTS[arm], '/api/tasks', dict(source_project=PROJECT,
                                                      task_description=project.get('task_example') or 'Fix the null greeting',
                                                      max_steps=MAX_STEPS, execution_mode='single'))
        task_id = task['task_id']
        deadline = time.monotonic() + TASK_TIMEOUT
        status = None
        while time.monotonic() < deadline:
            status = request(PORTS[arm], f'/api/tasks/{task_id}')
            if status['status'] not in {'pending', 'running'}:
                break
            time.sleep(3)
        metrics = request(PORTS[arm], f'/api/tasks/{task_id}/metrics')
        with connect(database) as connection:
            row = connection.execute('SELECT audit_json FROM api_tasks WHERE id=?', (task_id,)).fetchone()
        audit = json.loads(row[0]) if row and row[0] else {}
        injected = audit.get('injection_events') or []
        result = dict(arm=arm, task_id=task_id, status=status['status'],
                      simulated_final_tests=(status.get('result') or {}).get('tests_passed'),
                      seeded_experience_ids=ids, ready_rules=rules,
                      retrieval=metrics.get('retrieval'), token_usage=metrics.get('token_usage'),
                      elapsed_ms=metrics.get('elapsed_ms'), model_requests=metrics.get('model_requests'),
                      injection_events=[dict(decision=item.get('decision'),
                                             rules=len(item.get('experience_ids') or []),
                                             characters=item.get('injected_characters'),
                                             reference_limit=item.get('reference_limit'),
                                             character_budget=item.get('character_budget'))
                                        for item in injected])
        return result
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()


def main():
    global PROJECT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--project', default=PROJECT, help='registered project id to repair')
    parser.add_argument('--arms', default='contextual,adaptive', help='comma separated arms to run')
    parser.add_argument('--compact-after-first', action='store_true',
                        help='enable EXPERIENCE_COMPACT_AFTER_FIRST for the arm services')
    options = parser.parse_args()
    PROJECT = options.project
    arms = [value.strip() for value in options.arms.split(',') if value.strip()]
    unknown = [arm for arm in arms if arm not in ('baseline', 'contextual', 'adaptive')]
    if unknown:
        parser.error('unknown arms: ' + ', '.join(unknown))
    OUT.mkdir(parents=True, exist_ok=True)
    project = Settings.from_env().project(PROJECT)
    revision = source_revision(project.source)
    print('project revision:', revision, flush=True)
    if not options.live:
        for arm in arms:
            print(json.dumps(run_arm(arm, dry=True, revision=revision)))
        print('dry run: no service started, no model call made')
        return 0
    results = []
    for arm in arms:
        print(f'running {arm} ...', flush=True)
        value = run_arm(arm, dry=False, revision=revision, compact=options.compact_after_first)
        results.append(value)
        (OUT / f'{arm}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: value[k] for k in ('arm', 'status', 'elapsed_ms', 'token_usage')}, ensure_ascii=False))
    (OUT / 'pair.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print('written', OUT / 'pair.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
