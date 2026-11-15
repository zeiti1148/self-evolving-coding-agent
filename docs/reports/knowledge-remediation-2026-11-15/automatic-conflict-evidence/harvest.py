"""Read-only evidence export. Never writes the application database."""
import collections
import difflib
import hashlib
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

from live import RUN, ROOT, request, save

state = json.loads((RUN/'run.json').read_text(encoding='utf-8'))
identifier = state['task']['task_id']
task = request('/api/tasks/'+identifier)
assert task['status'] not in ('pending','running'), 'Wait for actual repair and extraction completion'
result = task['result']
experience_ids = result.get('experience_ids') or [result['experience_id']]
all_ids = [*experience_ids, '9977f923-1736-4de7-ac0b-dea4a8e70001', 'b6716e28-3f6f-409a-a462-17f008382321', '414bf457-a740-4d05-9760-772bd04e7616']
stage = sys.argv[1] if len(sys.argv)>1 else 'current'
snapshot = dict(experiences=[], maintenance=[], automation=[], reviews=[])
for identity in all_ids:
    source = request('/api/experiences/'+identity)
    frozen = RUN/('source-'+identity+'.json')
    prior = ROOT/'.cache/live-project-flow/20261115-normal-upload'/f'source-{identity}.json'
    if prior.exists():
        assert json.loads(prior.read_text(encoding='utf-8')) == source, 'Prior original changed'
    if frozen.exists():
        assert json.loads(frozen.read_text(encoding='utf-8')) == source, 'Original changed during review'
    else:
        save(frozen.name, source)
    snapshot['experiences'].append(source)
    snapshot['maintenance'].append(request('/api/experiences/'+identity+'/maintenance'))
    snapshot['automation'].append(request('/api/experiences/automation?experience_id='+identity))
for status in ('pending','resolved','obsolete'):
    snapshot['reviews'].append(dict(status=status, items=request('/api/experiences/conflicts?limit=100&status='+status)))
save('snapshot-'+stage+'.json', snapshot)

with sqlite3.connect((ROOT/'data/app.db').as_uri()+'?mode=ro', uri=True) as db:
    db.row_factory = sqlite3.Row
    audit = json.loads(db.execute('SELECT audit_json FROM api_tasks WHERE id=?',(identifier,)).fetchone()[0])
    model_requests = audit.get('model_requests', [])
    safe_keys = ('phase','attempt','transport_policy','requested_model','success','http_status','finish_reason',
                 'usage','returned_model','response_id','duration_ms','error_type','error_code','request_experience_ids')
    identities = [{k:v for k,v in e.get('api_configuration',{}).items()
                   if k in ('source','source_file','model','origin','transport')} for e in model_requests]
    safe = {k:audit.get(k) for k in ('decision_rounds','tool_calls','elapsed_ms','retrieved_experience_ids',
            'injection_events','experience_extraction','final_verification_attempts')}
    safe['model_requests'] = [{k:v for k,v in e.items() if k in safe_keys} for e in model_requests]
    safe['api_identities'] = list({json.dumps(i,sort_keys=True):i for i in identities}.values())
    save('repair-audit-safe.json',safe)
    placeholders=','.join('?' for _ in experience_ids)
    pairs = [dict(row) for row in db.execute('SELECT * FROM experience_auto_pairs WHERE left_id IN ('+placeholders+
              ') OR right_id IN ('+placeholders+')',(*experience_ids,*experience_ids))]
    for pair in pairs:
        pair['inputs'] = json.loads(pair['inputs'])
        pair['result'] = json.loads(pair['result']) if pair['result'] else None
    save('comparison-pairs-'+stage+'.json',pairs)
    history = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='experience_auto_attempts'").fetchone():
        for pair in pairs:
            for row in db.execute('SELECT * FROM experience_auto_attempts WHERE pair_id=?', (pair['id'],)):
                item = dict(row)
                item['result'] = json.loads(item['result']) if item['result'] else None
                history.append(item)
    save('comparison-attempts-'+stage+'.json', history)
    comparisons = [e for p in [*pairs,*history] for e in (p['result'] or {}).get('model_requests',[])]
    events = model_requests+comparisons
    request_summary = dict(total=len(events), repair=len(model_requests), comparison=len(comparisons),
        phases=dict(collections.Counter(e['phase'] for e in events)), successes=sum(e.get('success',False) for e in events),
        usage={k:sum(e.get('usage',{}).get(k,0) for e in events) for k in ('prompt_tokens','completion_tokens','total_tokens')})
patch=[]; changed=[]; missing=[]
with zipfile.ZipFile(RUN/'pytest-10356-original.zip') as archive:
    for info in archive.infolist():
        if info.is_dir():continue
        relative=Path(info.filename).relative_to('pytest')
        target=ROOT/'runtime/workspaces'/identifier/relative
        if not target.exists(): missing.append(relative.as_posix()); continue
        original, final = archive.read(info),target.read_bytes()
        if original!=final:
            changed.append(relative.as_posix())
            patch.extend(difflib.unified_diff(original.decode('utf-8').splitlines(keepends=True),
                final.decode('utf-8').splitlines(keepends=True),fromfile='original/'+relative.as_posix(),
                tofile='repaired/'+relative.as_posix()))
(RUN/'repair.diff').write_text(''.join(patch),encoding='utf-8')
assert not missing and all(p.startswith('src/') for p in changed), (missing,changed)
evidence=dict(task_id=identifier, experience_ids=experience_ids, status=task['status'],
    repair_succeeded=result.get('repair_succeeded'),experience_saved=result.get('experience_saved'),
    started_at=task.get('started_at'),finished_at=task.get('finished_at'),
    requests=request_summary, changed_files=changed, original_sources_unchanged=True,
    archive_sha256=hashlib.sha256((RUN/'pytest-10356-original.zip').read_bytes()).hexdigest())
save('evidence-'+stage+'.json',evidence)
print(json.dumps(evidence,ensure_ascii=False,indent=2))
