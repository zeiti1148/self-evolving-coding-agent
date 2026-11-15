import hashlib, json, shutil, subprocess, sys, urllib.request, zipfile
from pathlib import Path

RUN=Path(__file__).resolve().parent
ROOT=RUN.parents[2]
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def fetch(url):
    with opener.open(urllib.request.Request(url,headers={'User-Agent':'verified-local-repair-research'}),timeout=180) as r:
        return r.read()

old_base='3c1534944cbd34e8a41bc9e76818018fadefc9a1'
meta=json.loads(fetch('https://api.github.com/repos/pytest-dev/pytest/commits/'+old_base))
base=meta['parents'][0]['sha']
archive=RUN/'original.zip'
if not archive.exists(): archive.write_bytes(fetch('https://codeload.github.com/pytest-dev/pytest/zip/'+base))
source=RUN/'source'
assert not source.exists(), 'Preparation is one-shot; preserve existing source and evidence'
source.mkdir()
with zipfile.ZipFile(archive) as z:
    for entry in z.infolist():
        parts=Path(entry.filename).parts[1:]
        if not parts or entry.is_dir():continue
        target=source.joinpath(*parts).resolve()
        assert target.is_relative_to(source.resolve())
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(z.read(entry))
row=next(v for v in json.loads((ROOT/'.cache/engineering-repair-research/verified.json').read_text(encoding='utf-8'))
         if v['instance_id']=='pytest-dev__pytest-10356')
patch=RUN/'upstream-tests.patch'
patch.write_text(row['test_patch'],encoding='utf-8',newline='\n')
assert all(p.startswith(('testing/','tests/')) for p in [line[6:] for line in row['test_patch'].splitlines() if line.startswith('+++ b/')])
subprocess.run(['git','init','--quiet'],cwd=source,check=True)
subprocess.run(['git','apply','--check',str(patch)],cwd=source,check=True)
subprocess.run(['git','apply',str(patch)],cwd=source,check=True)
assert 'def test_mark_mro()' in (source/'testing/test_mark.py').read_text(encoding='utf-8')
requirements=ROOT/'benchmarks/engineering-merge/pytest-requirements.txt'
shutil.copyfile(requirements,source/'agent-requirements.txt')
manifest=dict(task_description=row['problem_statement']+'\n请检查并修复这个真实缺陷，通过全部登记测试，仅修改生产源码，保留其他行为。',
    runner_config=dict(python_version='3.10',requirements_file='agent-requirements.txt',python_paths=['src'],
        pytest_plugins=['pytester'],timeout_seconds=300,
        test_targets=['testing/test_mark.py','testing/test_skipping.py','testing/python/collect.py','testing/python/fixtures.py::TestFixtureUsages'],
        deselect=['testing/test_mark.py::TestKeywordSelection::test_keyword_extra_dash'] ))
provenance=dict(repo='pytest-dev/pytest',base_commit=base,parent_of_previous_base=old_base,
    upstream_pr='https://github.com/pytest-dev/pytest/pull/10356',
    upstream_issue='https://github.com/pytest-dev/pytest/issues/7792',
    archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),test_patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),
    gold_patch_provided=False,reason='Fresh official source snapshot; prior human decisions and raw experiences remain unchanged.')
(RUN/'provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2),encoding='utf-8')
(source/'.agent-upload.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(provenance,ensure_ascii=False))
