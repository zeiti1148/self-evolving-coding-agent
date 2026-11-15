import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile
import httpx

ROOT = Path(__file__).resolve().parents[1] / '.cache/engineering-repair-research'
data = json.loads((ROOT / 'verified.json').read_text(encoding='utf-8'))
ids = ['pytest-dev__pytest-10356', 'sphinx-doc__sphinx-8548']

def prepare(instance):
    row = next(x for x in data if x['instance_id'] == instance)
    folder = ROOT / instance
    folder.mkdir(exist_ok=True)
    archive = folder / 'source.zip'
    url = 'https://codeload.github.com/' + row['repo'] + '/zip/' + row['base_commit']
    if not archive.exists():
        response = httpx.get(url, timeout=180, follow_redirects=True, trust_env=False)
        response.raise_for_status()
        archive.write_bytes(response.content)
    with zipfile.ZipFile(archive) as z:
        prefix = z.namelist()[0].split('/')[0]
        source = folder / prefix
        if not source.exists():
            for member in z.infolist():
                target = (folder / member.filename).resolve()
                if not target.is_relative_to(folder.resolve()):
                    raise ValueError('Unsafe archive path')
            z.extractall(folder)
    test_patch = folder / 'upstream-tests.patch'
    test_patch.write_text(row['test_patch'], encoding='utf-8', newline='\n')
    marker = folder / 'tests-applied.json'
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(folder))
    applied = subprocess.run(['git', 'apply', '--reverse', '--check', str(test_patch)], cwd=source, env=env, capture_output=True).returncode == 0
    if not applied:
        p = subprocess.run(['git', 'apply', '--check', str(test_patch)], cwd=source, env=env, capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError(p.stderr)
        subprocess.run(['git', 'apply', str(test_patch)], cwd=source, env=env, check=True)
        subprocess.run(['git', 'apply', '--reverse', '--check', str(test_patch)], cwd=source, env=env, check=True)
        marker.write_text('{}')
    metadata = {k: row[k] for k in ['instance_id','repo','base_commit','version','problem_statement','difficulty','created_at']}
    metadata.update(source=str(source), archive_url=url, archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        test_patch_sha256=hashlib.sha256(test_patch.read_bytes()).hexdigest(),
        upstream_pr='https://github.com/'+row['repo']+'/pull/'+instance.rsplit('-',1)[1],
        fail_to_pass=json.loads(row['FAIL_TO_PASS']),
        pass_to_pass_count=len(json.loads(row['PASS_TO_PASS'])),
        gold_patch_provided_to_agent=False, hints_provided_to_agent=False)
    (folder / 'case.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    files=[p for p in source.rglob('*') if p.is_file()]
    print(json.dumps({'id':instance,'source':str(source),'files':len(files),'python_files':sum(p.suffix=='.py' for p in files),'sha256':metadata['archive_sha256']},ensure_ascii=False), flush=True)

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
    list(executor.map(prepare,ids))
