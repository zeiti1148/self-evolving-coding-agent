import hashlib, json, subprocess, sys, zipfile
from pathlib import Path

RUN=Path(__file__).resolve().parent
ROOT=RUN.parents[2]
sys.path.insert(0,str(ROOT))
from backend.tools.workspace import ignored_path

source=RUN/'source'
runtime=ROOT/'.cache/engineering-repair-research/pytest10356-auto-env/Scripts/python.exe'
subprocess.run([str(runtime),'-X','utf8','-c',
    'import sys; from setuptools_scm import dump_version; dump_version(sys.argv[1],"7.2.0.dev0","src/_pytest/_version.py")',str(source)],check=True)
manifest=json.loads((source/'.agent-upload.json').read_text(encoding='utf-8'))
manifest['runner_config']['deselect']=['testing/test_mark.py::TestKeywordSelection::test_keyword_extra_dash']
(source/'.agent-upload.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
pins=subprocess.check_output([str(runtime),'-X','utf8','-c',
    'import importlib.metadata as m; print("\\n".join(sorted(d.metadata["Name"]+"=="+d.version for d in m.distributions())))'],text=True,encoding='utf-8')
(source/'agent-requirements.txt').write_text(pins,encoding='utf-8')
archive=RUN/'pytest-10356-original.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for file in sorted(source.rglob('*')):
        relative=file.relative_to(source).as_posix()
        if file.is_file() and not ignored_path(relative): z.write(file,'pytest/'+relative)
manifest['archive_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
(RUN/'upload-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(manifest['archive_sha256'])
