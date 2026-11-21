"""Build upload archives and the cases manifest for the primary comparison sequence.

Sources come from the upstream default branch at each case's base commit; only
public tests from the dataset test patch are applied, never the gold fix patch.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.api.project_uploads import extract_project  # noqa: E402
from backend.tools.workspace import ignored_path  # noqa: E402

OUT = ROOT / '.cache/real-update-comparison'
ROWS = OUT / 'cases/rows.json'
# Upstream trees contain paths that exceed the Windows limit under the project
# directory, so unpacked sources and digest extraction use a short root.
SHORT = Path('C:/rs') if os.name == 'nt' else Path(tempfile.gettempdir()) / 'rs'
SOURCES = SHORT / 'sources'
LABELS = {'pytest-10356': 'pytest #10356 标记收集回归', 'sphinx-8548': 'Sphinx #8548 文档生成回归',
          'pytest-10482': 'pytest #10482（替代用例）'}
VERSIONS = {'pytest-10356': '3.10', 'sphinx-8548': '3.9', 'pytest-10482': '3.10'}
INSTRUCTION = '\n\n请让已登记的上游回归测试全部通过，保留原有业务语义；测试与构建配置不可修改。'


def download(row, target):
    url = 'https://codeload.github.com/' + row['repo'] + '/zip/' + row['base_commit']
    if not target.exists():
        with urllib.request.urlopen(url, timeout=600) as response:
            target.write_bytes(response.read())
    return url


def unpack(archive, folder):
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    with zipfile.ZipFile(archive) as handle:
        handle.extractall(folder)
    children = [child for child in folder.iterdir() if child.is_dir()]
    if len(children) != 1:
        raise SystemExit(f'unexpected archive layout in {archive}')
    return children[0]


def apply_tests(source, patch_text):
    patch = source.parent / 'upstream-tests.patch'
    patch.write_text(patch_text, encoding='utf-8', newline='\n')
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(source.parent))
    reverse = subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)], cwd=source, env=env,
                             capture_output=True)
    if reverse.returncode == 0:
        return
    check = subprocess.run(['git', 'apply', '--check', str(patch)], cwd=source, env=env,
                           capture_output=True, text=True)
    if check.returncode:
        raise SystemExit(f'cannot apply public tests for {source.name}: {check.stderr[:400]}')
    subprocess.run(['git', 'apply', str(patch)], cwd=source, env=env, check=True)


def import_root(source):
    return 'src' if (source / 'src').is_dir() else '.'


def package(source, key, row, archive_path):
    runner_config = dict(python_paths=[import_root(source)], test_targets=row['test_files'],
                         deselect=[], pytest_plugins=[], timeout_seconds=900,
                         python_version=VERSIONS[key], requirements_file='')
    hints = dict(task_description=row['problem_statement'] + INSTRUCTION, runner_config=runner_config,
                 provenance=dict(repo=row['repo'], base_commit=row['base_commit'], repaired=False,
                                 test_source='public upstream tests from the dataset test patch'))
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(source.rglob('*')):
            relative = path.relative_to(source).as_posix()
            if path.is_file() and not ignored_path(relative) and path.suffix != '.pyc':
                handle.write(path, key + '/' + relative)
        handle.writestr(key + '/.agent-upload.json', json.dumps(hints, ensure_ascii=False, indent=2))
    return runner_config, hints['task_description']


def digest_of(archive_path, workdir):
    destination = workdir / ('extract-' + archive_path.stem)
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    with open(archive_path, 'rb') as stream:
        root, language, digest, count, excluded = extract_project(stream, destination)
    return digest, count, language, excluded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='*', help='build only these case keys')
    options = parser.parse_args()
    rows = json.loads(ROWS.read_text(encoding='utf-8'))
    SOURCES.mkdir(parents=True, exist_ok=True)
    workdir = SHORT / 'extract'
    workdir.mkdir(parents=True, exist_ok=True)
    manifest, details = [], []
    for key, row in rows.items():
        if options.only and key not in options.only:
            continue
        archive = OUT / 'cases' / f'{key}-source.zip'
        archive.parent.mkdir(parents=True, exist_ok=True)
        url = download(row, archive)
        source = unpack(archive, SOURCES / key)
        apply_tests(source, row['test_patch'])
        upload = OUT / 'cases' / f'{key}.zip'
        runner_config, description = package(source, key, row, upload)
        digest, count, language, excluded = digest_of(upload, workdir)
        sha = hashlib.sha256(upload.read_bytes()).hexdigest()
        manifest.append(dict(id=key, label=LABELS[key], archive=str(upload), archive_sha256=sha,
                             source_digest=digest, runner_config=runner_config,
                             task_description=description, baseline=dict(tests=0)))
        details.append(dict(id=key, files=count, language=language, excluded=excluded,
                            sha256=sha, digest=digest, archive_bytes=upload.stat().st_size,
                            archive_url=url, test_files=row['test_files']))
        print(json.dumps(details[-1], ensure_ascii=False))
    shutil.rmtree(workdir, ignore_errors=True)
    (OUT / 'cases/manifest.json').write_text(json.dumps(dict(cases=manifest), ensure_ascii=False, indent=2),
                                             encoding='utf-8')
    (OUT / 'cases/build-details.json').write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    print('manifest cases:', [case['id'] for case in manifest])


if __name__ == '__main__':
    main()
