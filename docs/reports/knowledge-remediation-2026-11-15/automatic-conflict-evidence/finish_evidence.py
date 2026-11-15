"""Verify and export completed ordinary-workflow evidence without database mutation."""
import difflib, hashlib, json, shutil, sqlite3, sys
from pathlib import Path
from live import RUN, ROOT, request, save
sys.path.insert(0,str(ROOT))
from backend.tools.workspace import inventory
from backend.experience.knowledge import digest

review_id='47b041f9-73d5-5adb-9d61-8ff95bf7d990'
with sqlite3.connect((ROOT/'data/app.db').as_uri()+'?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    record=dict(db.execute('SELECT * FROM knowledge_auto_reviews WHERE review_id=?',(review_id,)).fetchone())
    data=json.loads(record['data'])
    assert record['state']==data['state']=='resolved' and len(data['runs'])==6, data['state']
    assert digest({k:data[k] for k in ('runs','bindings','targets')})==data['evidence_hash']
    for previous in json.loads((RUN/'relations-before.json').read_text(encoding='utf-8')):
        current=dict(db.execute('SELECT * FROM knowledge_relations WHERE id=?',(previous['id'],)).fetchone())
        assert previous==current, 'Prior manual decision changed'
history=request('/api/experiences/conflicts/'+review_id+'/history')
assert len(history)==1 and history[0]['data']['actor']=='system'
assert history[0]['data']['evidence_hash']==data['evidence_hash']
public_data={k:v for k,v in data.items() if k not in ('worker_job_id','worker_token','diagnostic')}
save('automatic-review.json',public_data);save('automatic-history.json',history)
folder=ROOT/'docs/reports/knowledge-remediation-2026-11-15/automatic-conflict-evidence'
folder.mkdir(parents=True,exist_ok=True)
bindings={b['source_id']:b for b in data['bindings']}
matrix=[]
for source_id,binding in bindings.items():
    original=ROOT/'runtime/workspaces'/binding['task_id']
    assert digest(inventory(original))==binding['inventory_hash'], 'Original workspace changed'
for run in data['runs']:
    artifact=ROOT/'runtime'/run['artifact_path']
    assert hashlib.sha256(artifact.read_bytes()).hexdigest()==run['artifact_sha256']
    payload=json.loads(artifact.read_text(encoding='utf-8'))
    assert all(payload['metadata'][k]==run[k] for k in ('tests','failures','errors','skipped'))
    shutil.copyfile(artifact,folder/artifact.name)
    proof=ROOT/'runtime/conflict-verification'/review_id/'workspaces'
    reports=list((proof/'.test-reports'/run['id']).glob('*.xml'))
    assert len(reports)==1
    shutil.copyfile(reports[0],folder/(run['id']+'.xml'))
    original=ROOT/'runtime/workspaces'/bindings[run['target_source_id']]['task_id']
    trial=proof/run['id']
    a,b=inventory(original),inventory(trial)
    changed=sorted(k for k in a.keys()|b.keys() if a.get(k)!=b.get(k))
    assert changed==([] if run['target_source_id']==run['implementation_source_id'] else ['src/_pytest/mark/structures.py'])
    patch=[]
    for relative in changed:
        patch.extend(difflib.unified_diff((original/relative).read_text(encoding='utf-8').splitlines(keepends=True),
            (trial/relative).read_text(encoding='utf-8').splitlines(keepends=True),
            fromfile='target-verified/'+relative,tofile='cross-tested/'+relative))
    if patch:(folder/(run['id']+'.diff')).write_text(''.join(patch),encoding='utf-8')
    matrix.append({k:run[k] for k in ('id','target_source_id','implementation_source_id','repeat','outcome','tests','failures','failed_tests','reproduces_baseline')})
save('independent-proof-check.json',dict(review_id=review_id,resolution=data['resolution'],actor='system',
    evidence_hash=data['evidence_hash'],six_artifacts_verified=True,original_workspaces_unchanged=True,
    prior_manual_decisions_unchanged=True,only_conflicting_function_file_replaced=True,matrix=matrix))
names=['provenance.json','upload-manifest.json','submitted-request.json','preflight.log','repair.diff',
    'task.json','steps.json','repair-audit-safe.json','evidence-after-auto.json','snapshot-before-auto.json',
    'snapshot-verifying.json','snapshot-after-auto.json','comparison-pairs-after-auto.json','comparison-attempts-after-auto.json',
    'automatic-review.json','automatic-history.json','independent-proof-check.json',
    'backend-tests.log','frontend-tests.log','prepare.py','build_package.py','live.py','observe.py','harvest.py','finish_evidence.py']
for name in names:shutil.copyfile(RUN/name,folder/name)
observations=[]
for line in (RUN/'observed-transitions.jsonl').read_text(encoding='utf-8').splitlines():
    item=json.loads(line)
    for review in item['reviews']:
        review['data']={k:v for k,v in review['data'].items() if k not in ('worker_job_id','worker_token','diagnostic')}
    observations.append(json.dumps(item,ensure_ascii=False))
(folder/'observed-transitions.jsonl').write_text('\n'.join(observations)+'\n',encoding='utf-8')
print(json.dumps(dict(review_id=review_id,resolution=data['resolution'],actor='system',matrix=matrix),ensure_ascii=False,indent=2))
