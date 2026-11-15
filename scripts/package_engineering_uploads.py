"""Package unrepaired upstream projects for the ordinary browser ZIP importer.

No live repair code imports this module. Only original sources, issue text and
explicit test selections are distributed, never gold/model repair patches.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.tools.workspace import ignored_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', type=Path, required=True, help='Directory of unrepaired pytest and sphinx sources')
    parser.add_argument('--report', type=Path, required=True, help='Prior provenance report; only original issue metadata is used')
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/experiment-materials')
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding='utf-8'))
    args.output.mkdir(parents=True, exist_ok=True)
    sections = []
    manifest = []
    for task in report['tasks']:
        case = task['case']; name = case['id']; source = args.inputs / name
        if name not in {'pytest', 'sphinx'} or not source.is_dir():
            raise ValueError('Expected original input source trees')
        targets = list(case['targets'])
        extra_test = ('testing' if name == 'pytest' else 'tests') + '/test_upload_regression.py'
        targets.append(extra_test)
        config = dict(python_paths=[case['import_root']], test_targets=targets, deselect=case['deselect'],
                      pytest_plugins=case['plugins'], timeout_seconds=300,
                      python_version='3.10' if name == 'pytest' else '3.9', requirements_file='agent-requirements.txt')
        prompt = case['problem_statement'] + '\n\n请通过已配置的全部原始和扩展回归测试，保留各自业务语义；测试与构建配置不可修改。'
        hints = dict(task_description=prompt, runner_config=config,
                     provenance=dict(repo=case['repo'], base_commit=case['base_commit'], upstream_pr=case['upstream_pr'],
                                     repaired=False, regression_tests='public upstream tests and explicitly added acceptance tests'))
        target = args.output / (name + '.zip')
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob('*')):
                relative = path.relative_to(source).as_posix()
                if path.is_file() and not ignored_path(relative) and path.suffix not in {'.pyc'}:
                    archive.write(path, name + '/' + relative)
            archive.writestr(name + '/.agent-upload.json', json.dumps(hints, ensure_ascii=False, indent=2))
            archive.write(ROOT / 'benchmarks/engineering-merge' / (name + '_fresh_holdout.py'), name + '/' + extra_test)
            archive.write(ROOT / 'benchmarks/engineering-merge' / (name + '-requirements.txt'), name + '/agent-requirements.txt')
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest.append(dict(name=name, sha256=digest, source=case['base_commit'], targets=targets))
        runtime = 'pytest-env（Python 3.10）' if name == 'pytest' else 'sphinx39-env（Python 3.9）'
        sections.append(f'<section><h2>{html.escape(case["title"])}</h2><p><a href="{html.escape(case["upstream_pr"], quote=True)}">上游缺陷与修复讨论</a> · 基础版本 <code>{html.escape(case["base_commit"])}</code></p>'
            f'<p><a href="{name}.zip" download>下载缺陷源码 ZIP</a> → 在“新建任务”上传 → 展开“测试与运行环境设置” → Python 解释器选择 <b>{runtime}</b> → 保存测试配置。</p>'
            '<p>测试选择会随上传包读入；原始任务描述可在项目预览中查看，填写后提交修复。源码未带入修复补丁。新扩展测试现在属于可见验收集，不再称为隐藏测试。</p>'
            f'<details><summary>全部测试配置</summary><pre>{html.escape(json.dumps(config, ensure_ascii=False, indent=2))}</pre></details>'
            f'<details><summary>原始问题描述</summary><pre>{html.escape(prompt)}</pre></details><p>ZIP SHA-256：<code>{digest}</code></p></section>')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>工程研究材料上传说明</title>
<style>body{font:16px/1.8 system-ui;max-width:900px;margin:40px auto;padding:0 20px;color:#203646;background:#f3f7f9}section{background:white;border:1px solid #dbe5eb;padding:24px;border-radius:16px;margin:24px 0}pre,code{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#087e87}</style>
<h1>上传原始工程，重新运行修复</h1><p>这些归档仅用于离线研究复现。将任一 ZIP 作为普通项目上传，填写任务并提交修复。任务页面会记录实际执行结果，经验在经验记录中查看。</p>
<p>在当前机器上已准备相应依赖环境。其他机器可使用包内 agent-requirements.txt 安装依赖；不要使用现代 Python 强行运行不兼容的旧版本工程。后端仍仅从本项目 .env 读取模型配置。</p>''' + ''.join(sections) + '<p><a href="http://127.0.0.1:5173/#/">打开新建任务</a></p></html>'
    (args.output / 'guide.html').write_text(page, encoding='utf-8')
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == '__main__':
    main()
