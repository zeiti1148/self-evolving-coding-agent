"""Real upstream repairs, isolated model runs, and cross-project knowledge evidence.

The source archives and official regression tests are prepared independently.
This runner never reads SWE-bench's reference patch or hints. It uses the existing
Agent, experience extractor, credential profile and test-verdict implementation.
Only the trusted test selection and two bounded editing/search conveniences are
adapted for large upstream repositories. This is not the official Docker score.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import Field, ValidationError
from backend.agent.core import Agent
from backend.agent.llm import ChatClient
from backend.config import Settings
from backend.experience import ExperienceStore
from backend.llm_configuration import api_identity
from backend.shared import AgentRequest, ApplicationError, ErrorCode, ToolCall, ToolResult
from backend.tools import ToolService, WorkspaceManager
from backend.tools.process import run_process
from backend.tools.service import Arguments
from backend.tools.workspace import inventory, protected

RESEARCH = ROOT / '.cache/engineering-repair-research'
CASES = {
    'pytest': dict(instance='pytest-dev__pytest-10356', title='pytest：多继承测试标记丢失',
                   source_prefix='src/_pytest/', import_root='src', package='pytest',
                   targets=['testing/test_mark.py'], plugins=['pytester'],
                   deselect=['testing/test_mark.py::TestKeywordSelection::test_keyword_extra_dash']),
    'sphinx': dict(instance='sphinx-doc__sphinx-8548', title='Sphinx：继承属性的文档遗漏',
                   source_prefix='sphinx/', import_root='.', package='sphinx',
                   targets=['tests/test_ext_autodoc_autoclass.py'], plugins=[], deselect=[]),
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class ReplaceText(Arguments):
    path: str
    old: str = Field(min_length=1, max_length=100_000)
    new: str = Field(max_length=100_000)


class SearchText(Arguments):
    path: str = '.'
    text: str = Field(min_length=1, max_length=500)


EXTRA_TOOLS = [
    {'type': 'function', 'function': {'name': 'replace_text',
      'description': 'Replace exactly one literal occurrence in a production Python file. Fails if the old text is absent or ambiguous. Read the source first. Tests and configuration remain read-only.',
      'parameters': ReplaceText.model_json_schema()}},
    {'type': 'function', 'function': {'name': 'search_text',
      'description': 'Search a literal string in Python files under a relative project path; returns line numbers and at most 100 matching lines.',
      'parameters': SearchText.model_json_schema()}},
]


class EngineeringClient(ChatClient):
    def complete(self, messages, *, tools=None):
        return super().complete(messages, tools=[*tools, *EXTRA_TOOLS] if tools else None)


class EngineeringTools(ToolService):
    def __init__(self, manager, settings, case, report_dir):
        super().__init__(manager, settings.maven_home, python_executable=case['python'])
        self.case = case
        self.report_dir = report_dir
        self.tests_log = []

    def execute_tool(self, workspace, call):
        if call.name not in {'replace_text', 'search_text', 'write_file'}:
            return super().execute_tool(workspace, call)
        try:
            record = self.manager.get(workspace)
            with record.lock:
                inventory(record.path)
                if call.name == 'search_text':
                    args = SearchText.model_validate(call.arguments)
                    location = self.manager.resolve(record, args.path)
                    output, meta, code = run_process(
                        [shutil.which('rg'), '--fixed-strings', '--line-number', '--glob', '*.py',
                         '--max-count', '30', '--', args.text, str(location)], record.path, 30)
                    if meta.get('exit_code') == 1:
                        code = None
                    output = '\n'.join(output.splitlines()[:100]).replace(str(record.path) + '\\', '')
                    return ToolResult(call_id=call.id, tool_name=call.name, success=code is None,
                        output=output, metadata=meta, error_code=code,
                        error_message='Search failed' if code else None)
                args = ReplaceText.model_validate(call.arguments) if call.name == 'replace_text' else None
                path = args.path if args else call.arguments.get('path', '')
                target = self.manager.resolve(record, path)
                relative = target.relative_to(record.path).as_posix()
                if not relative.startswith(self.case['source_prefix']) or not relative.endswith('.py') or protected({relative: ''}):
                    raise ValueError('Only registered production Python files may be edited')
                if args:
                    with target.open('r', encoding='utf-8', newline='') as stream:
                        old = stream.read()
                    if old.count(args.old) != 1:
                        raise ValueError('old must match exactly one occurrence; read the file before editing')
                    write_call = ToolCall(id=call.id, name='write_file', arguments={
                        'path': relative, 'content': old.replace(args.old, args.new, 1)})
                else:
                    write_call = call
                result = super().execute_tool(workspace, write_call)
                return result.model_copy(update={'tool_name': call.name})
        except (ValueError, ValidationError, OSError, ApplicationError) as exc:
            return ToolResult(call_id=call.id, tool_name=call.name, success=False,
                error_code=ErrorCode.INVALID_TOOL_CALL, error_message=str(exc)[:1500] or 'Invalid edit')

    def _run_python_tests(self, record, cwd, timeout_seconds):
        if cwd != record.path:
            raise ApplicationError(ErrorCode.INVALID_TOOL_CALL, 'Use the registered repository root')
        report = self.report_dir / 'junit' / (str(uuid4()) + '.xml')
        report.parent.mkdir(parents=True, exist_ok=True)
        argv = [self.python_executable, '-I', '-X', 'utf8', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                '-o', 'addopts=', '--junitxml=' + str(report)]
        for plugin in self.case['plugins']:
            argv.extend(['-p', plugin])
        argv.extend(self.case['targets'])
        for test in self.case.get('deselect', []):
            argv.extend(['--deselect', test])
        output, meta, code = run_process(argv, cwd, timeout_seconds)
        self.manager.verify_tests(record)
        for path, digest in self.case.get('restored_fixture_hashes', {}).items():
            if hashlib.sha256((record.path / path).read_bytes()).hexdigest() != digest:
                raise ValueError('An upstream fixture was modified')
        meta.update(changed_files=self.manager.changed_files(record.path), runner='pytest',
                    selection=list(self.case['targets']), junit_file=str(report))
        if code != ErrorCode.COMMAND_TIMEOUT:
            output, meta, code = self._test_verdict(output, meta, code, [report] if report.exists() else [], 'pytest')
        entry = dict(argv=argv, cwd=str(cwd), output=output, metadata=meta, error_code=code)
        self.tests_log.append(entry)
        write(self.report_dir / 'test-invocations.json', self.tests_log)
        return output, meta, code


def prepare(args, settings, identity):
    run = args.run.resolve()
    run.mkdir(parents=True, exist_ok=True)
    report_path = run / 'report.json'
    previous = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else None
    if previous and (previous['api_configuration'] != identity or any(t.get('result') for t in previous['tasks'])):
        raise ValueError('Cannot reprepare a run with model results or a different API profile')
    report = dict(created_at=datetime.now(timezone.utc).isoformat(), directory=str(run),
        api_configuration=identity, completed=False, official_docker_score=False,
        dataset=dict(name='princeton-nlp/SWE-bench_Verified',
                     sha256=hashlib.sha256((RESEARCH / 'verified.parquet').read_bytes()).hexdigest()),
        harness_adaptations=['Windows CPython 3.10 in isolated environments',
            'fixed upstream component tests; original tests immutable',
            'source import path set by a trusted external .pth file',
            'exact single-occurrence replacement and literal search tools'], tasks=[])
    report = previous or report
    write(report_path, report)
    for name, config in CASES.items():
        if any(t['case']['id'] == name for t in report['tasks']):
            continue
        case = json.loads((RESEARCH / config['instance'] / 'case.json').read_text(encoding='utf-8')) | config
        case['id'] = name
        environment_name = case.get('environment_name', name + '-env')
        case['python'] = str(RESEARCH / environment_name / 'Scripts/python.exe')
        source = Path(case['source'])
        if name == 'pytest':
            # Git archives omit this generated setuptools-scm version module.
            # Use the matching installed release's generated metadata; no repair code.
            version_file = source / 'src/_pytest/_version.py'
            if not version_file.exists():
                shutil.copyfile(RESEARCH / 'pytest-env/Lib/site-packages/_pytest/_version.py', version_file)
            case['build_metadata'] = 'Generated _version.py from installed pytest 7.2.0; upstream Git archive omits it'
        manager = WorkspaceManager(run / 'workspaces', {name: source})
        task_id = str(uuid4())
        workspace = manager.create_workspace(source, task_id)
        # The Java-oriented default copier ignores directories named target.
        # In Sphinx these contain essential, versioned Python test fixtures.
        # Restore those unchanged and verify their hashes on every test run.
        restored = {}
        for target in source.rglob('target'):
            if target.is_dir() and 'tests' in target.relative_to(source).parts:
                destination = workspace / target.relative_to(source)
                shutil.copytree(target, destination)
                for f in target.rglob('*'):
                    if f.is_file():
                        restored[f.relative_to(source).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
        case['restored_fixture_hashes'] = restored
        root = (workspace / case['import_root']).resolve()
        pth = RESEARCH / environment_name / 'Lib/site-packages/engineering_source.pth'
        pth.write_text('import sys; sys.path.insert(0, ' + ascii(str(root)) + ')\n', encoding='ascii')
        case_dir = run / name
        tools = EngineeringTools(manager, settings, case, case_dir)
        output, metadata, code = run_process([case['python'], '-I', '-X', 'utf8', '-c',
            "import json,sys,importlib.metadata as m; import " + case['package'] + " as p; print(json.dumps({'python':sys.version,'module':p.__file__,'packages':{d.metadata['Name']:d.version for d in m.distributions()}},ensure_ascii=False))"], workspace, 30)
        if code:
            raise RuntimeError(output)
        environment = json.loads(output)
        if not Path(environment['module']).resolve().is_relative_to(workspace):
            raise ValueError('Tests would import a released package instead of the faulty source')
        baseline = tools.execute_tool(workspace, ToolCall(id=str(uuid4()), name='run_tests', arguments={'timeout_seconds':300}))
        row = dict(case=case, task_id=task_id, workspace=str(workspace), environment=environment,
                   database=str(run / (name + '.db')), baseline=baseline.model_dump(mode='json'),
                   source_file_count=len(inventory(source)), retrieval_enabled=False,
                   source_hashes=inventory(source), state='prepared')
        report['tasks'].append(row)
        write(report_path, report)
        print(name, json.dumps(baseline.metadata,ensure_ascii=False)[:2500],flush=True)
    print('PREPARED', report_path,flush=True)


def repair(args, settings, identity):
    run = args.run.resolve()
    path = run / 'report.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    if report['api_configuration'] != identity:
        raise ValueError('API configuration differs from preparation')
    for row in report['tasks']:
        case = row['case']
        if args.case and case['id'] != args.case:
            continue
        if row.get('result'):
            print(case['id'], 'Retaining existing result; no paid retry',flush=True)
            continue
        baseline = row['baseline']
        if (baseline['success'] or baseline['metadata'].get('failures',0) < 1
                or baseline['metadata'].get('errors',0) or baseline['metadata'].get('skipped',0)
                or baseline['metadata'].get('exit_code') != 1):
            raise ValueError('Reproduce the actual bug in a working environment before model repair')
        source = Path(case['source'])
        workspace = Path(row['workspace'])
        manager = WorkspaceManager(run / 'workspaces', {case['id']: source})
        service = EngineeringTools(manager, settings, case, run / case['id'])
        old_log = service.report_dir / 'test-invocations.json'
        service.tests_log = json.loads(old_log.read_text(encoding='utf-8')) if old_log.exists() else []
        store = ExperienceStore(Path(row['database'])); store.initialize()
        model = EngineeringClient(settings)
        agent = Agent(model, service.execute_tool, store, use_experience=False, timeout_seconds=1200)
        task = (case['problem_statement'] + '\n\nEngineering acceptance: repair this original repository source. '
            'The registered run_tests tool executes ' + ', '.join(case['targets']) +
            '. These include the upstream issue regression and existing component tests. '
            'Do not modify tests, configuration, dependencies, or test selection. '
            'Use search_text to locate code and replace_text for exact source edits. '
            'Preserve behavior outside the reported bug. No other task knowledge is available.')
        task += '\n\n' + case.get('continuation_diagnosis', '')
        request = AgentRequest(task_id=row['task_id'], workspace=workspace, language='python',
            max_steps=case.get('max_steps',45), repository_id=case['repo'], source_revision=case.get('repair_start_revision',case['base_commit']),
            experience_contract=case['instance'], task_description=task)
        row.update(state='running', started_at=datetime.now(timezone.utc).isoformat(), steps=[])
        write(path,report)
        def progress(step):
            row['steps'].append(step.model_dump(mode='json'))
            row['requests'] = model.audit
            write(path,report)
            print(case['id'], step.sequence, step.call.name, 'ok' if step.result.success else step.result.error_code, flush=True)
        try:
            result = agent.run(request,on_step=progress)
            row.update(result=result.model_dump(mode='json'), audit=agent.last_run_audit,
                       experiences=[e.model_dump(mode='json') for e in store.list_experiences(limit=100)],
                       state='finished', finished_at=datetime.now(timezone.utc).isoformat(),
                       source_unchanged=inventory(source)==row['source_hashes'])
            changed = manager.changed_files(workspace)
            patches=[]
            for relative in changed:
                before=(source/relative).read_text(encoding='utf-8') if (source/relative).exists() else ''
                after=(workspace/relative).read_text(encoding='utf-8') if (workspace/relative).exists() else ''
                patches.extend(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+relative,tofile='b/'+relative))
            patch=run/case['id']/'model.patch'
            patch.write_text(''.join(patches),encoding='utf-8')
            row.update(patch=str(patch), original_tests_unchanged=True)
            manager.verify_tests(manager.get(workspace))
        finally:
            row['requests']=model.audit
            model.close()
            write(path,report)
        print(case['id'], 'RESULT',row.get('result',{}).get('status'), 'experiences',len(row.get('experiences',[])),flush=True)
    report['completed']=all(t.get('result',{}).get('repair_succeeded') and t.get('experiences') for t in report['tasks'])
    write(path,report)


def recheck(args, settings, identity):
    run = args.run.resolve()
    path = run / 'report.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    if report['api_configuration'] != identity:
        raise ValueError('API profile changed')
    for row in report['tasks']:
        if args.case and row['case']['id'] != args.case:
            continue
        if row.get('result') or row.get('requests'):
            raise ValueError('Cannot replace baseline after a model request')
        case = row['case']
        if args.deselect:
            case['deselect'] = args.deselect
        manager = WorkspaceManager(run / 'workspaces', {case['id']: Path(case['source'])})
        service = EngineeringTools(manager, settings, case, run / case['id'])
        old_log = service.report_dir / 'test-invocations.json'
        service.tests_log = json.loads(old_log.read_text(encoding='utf-8'))
        row.setdefault('baseline_history',[]).append(row['baseline'])
        baseline = service.execute_tool(Path(row['workspace']), ToolCall(id=str(uuid4()),name='run_tests',arguments={'timeout_seconds':300}))
        row['baseline'] = baseline.model_dump(mode='json')
        write(path,report)
        print(case['id'],baseline.metadata,flush=True)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=['prepare','recheck','repair'])
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--llm-env-file',type=Path,required=True)
    p.add_argument('--expect-api-identity',required=True)
    p.add_argument('--case',choices=list(CASES))
    p.add_argument('--deselect',action='append',help='Explicit pre-repair exclusion of a known optional/xfail test')
    p.add_argument('--live',action='store_true')
    args=p.parse_args()
    settings=Settings.from_env(llm_env_file=args.llm_env_file)
    identity=api_identity(settings)
    if identity['identity'] != args.expect_api_identity:
        raise ValueError('API profile changed; no request sent')
    settings=replace(settings,enable_knowledge_models=False,llm_timeout=180,task_timeout=1200)
    if args.mode=='prepare':
        prepare(args,settings,identity)
    elif args.mode=='recheck':
        recheck(args,settings,identity)
    elif args.live:
        repair(args,settings,identity)
    else:
        print(json.dumps(identity,ensure_ascii=False))


if __name__=='__main__':
    main()
