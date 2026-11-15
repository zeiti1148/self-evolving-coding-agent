"""Run immutable, real repair implementations against both original test contracts.

No model writes a candidate or a test here. Python AST ranges select existing
function bodies; isolated WorkspaceManager copies and the regular ToolService
execute the registered complete suites. Unsupported mappings require review.
"""
import ast
import builtins
import copy
import hashlib
import json
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET

from backend.agent.extraction import verification_passed
from backend.experience.automatic_resolution import VerificationUnavailable
from backend.experience.knowledge import digest
from backend.shared import ErrorCode, ToolCall
from backend.tools import ToolService, WorkspaceManager
from backend.tools.code_changes import code_path, read_snapshot
from backend.tools.workspace import inventory
from .task_evidence import _baseline


def function(tree, qualified):
    nodes = [tree]
    for name in qualified.split('.'):
        nodes = [child for parent in nodes for child in getattr(parent, 'body', [])
                 if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == name]
    if len(nodes) != 1 or not isinstance(nodes[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise VerificationUnavailable('无法唯一定位双方相同的函数。')
    return nodes[0]


def signature(node):
    args = copy.deepcopy(node.args)
    for value in ast.walk(args):
        if isinstance(value, ast.arg):
            value.annotation = None
            value.type_comment = None
    return type(node).__name__, ast.dump(args), [ast.dump(v) for v in node.decorator_list]


def body_identity(node):
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return ast.dump(ast.Module(body=body, type_ignores=[]))


def transplant(target_text, donor_text, qualified):
    target_tree, donor_tree = ast.parse(target_text), ast.parse(donor_text)
    target, donor = function(target_tree, qualified), function(donor_tree, qualified)
    if signature(target) != signature(donor):
        raise VerificationUnavailable('函数调用接口或装饰器不同，不能可靠移植。')
    if body_identity(target) == body_identity(donor):
        raise VerificationUnavailable('双方真实函数实现相同，无法用此测试区分措辞冲突。')
    # Direct helper calls must retain their implementation. Incompatibility is
    # not allowed to masquerade as a failure of the competing repair rule.
    local = {n.arg for n in ast.walk(donor.args) if isinstance(n, ast.arg)}
    local.update(n.id for n in ast.walk(donor) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
    for call in [n for n in ast.walk(donor) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]:
        name = call.func.id
        if name in {'eval', 'exec', 'globals', 'locals', '__import__'}:
            raise VerificationUnavailable('动态函数依赖不支持自动交叉验证。')
        if name in local or hasattr(builtins, name):
            continue
        try:
            left, right = function(target_tree, name), function(donor_tree, name)
        except VerificationUnavailable:
            raise VerificationUnavailable('存在无法核对的外部函数依赖。') from None
        if signature(left) != signature(right) or body_identity(left) != body_identity(right):
            raise VerificationUnavailable('双方依赖的辅助函数不同，无法隔离单条规则的作用。')
    target_lines, donor_lines = target_text.splitlines(keepends=True), donor_text.splitlines(keepends=True)
    a, b = target.body[0].lineno-1, donor.body[0].lineno-1
    if target.body[0].col_offset != donor.body[0].col_offset:
        raise VerificationUnavailable('函数所在作用域不同，无法可靠移植。')
    output = ''.join(target_lines[:a] + donor_lines[b:donor.end_lineno] + target_lines[target.end_lineno:])
    ast.parse(output)
    return output


class ConflictVerifier:
    def __init__(self, settings, tasks, manager):
        self.settings, self.tasks, self.manager = settings, tasks, manager
        self.root = settings.workspace_root.parent / 'conflict-verification'

    def ready(self, sources):
        try:
            return all(self.tasks.get(s['source_task_id']).status not in {'pending','running'} for s in sources)
        except KeyError:
            return True  # Missing evidence is explicitly recorded as requiring confirmation.

    def context(self, source, *, general=False):
        task_id = source['source_task_id']
        task, request = self.tasks.get(task_id), self.tasks.get_request(task_id)
        result = task.result
        if (task.status not in {'succeeded', 'stopped'} or not request or not result or not result.repair_succeeded
                or not result.tests_passed or not result.changed_files or not result.steps or not result.tool_results
                or source['id'] not in (result.experience_ids or [])):
            raise VerificationUnavailable('来源缺少成功修复与经验保存的完整关联。')
        project = self.settings.project(request.source_project)
        if project.language != source['language'] or not general and project.language != 'python':
            raise VerificationUnavailable('当前自动交叉验证支持可定位的 Python 函数；此语言需用户确认。')
        final = result.tool_results[-1]
        if (result.steps[-1].name != 'run_tests' or final.call_id != result.steps[-1].id
                or final.metadata.get('final_verification') is not True or not verification_passed(final, project.language)
                or not source['evidence'] or any(e['source_task_id'] != task_id or e['tool_call_id'] != final.call_id
                    or e['tests'] != final.metadata['tests'] for e in source['evidence'])):
            raise VerificationUnavailable('经验的验收调用与真实任务记录不一致。')
        record = self.manager.get(self.manager.root / task_id)
        with record.lock:
            if record.source != project.source.resolve() or source['repository_id'] != project.repository_id:
                raise VerificationUnavailable('来源项目归属变化。')
            snapshot = read_snapshot(self.manager, record)
            if snapshot is None or snapshot.omitted_files or any(not f.available for f in snapshot.files):
                raise VerificationUnavailable('缺少完整的结束时源码快照。')
            if set(result.changed_files) != {f.path for f in snapshot.files}:
                raise VerificationUnavailable('最终修改清单不完整。')
            expected = dict(record.baseline)
            for change in snapshot.files:
                if change.after_sha256 is None:
                    expected.pop(change.path, None)
                else:
                    expected[change.path] = change.after_sha256
            if inventory(record.path) != expected:
                raise VerificationUnavailable('源任务文件已在最终验收后变化。')
            baseline = _baseline(self.tasks.get_steps(task_id).steps)
            if baseline is None or not baseline.result.metadata.get('test_failures'):
                raise VerificationUnavailable('没有可核对的原始缺陷基线。')
            config = project.runner_config
            if not config:
                raise VerificationUnavailable('缺少冻结的完整测试配置。')
            binding = dict(source_id=source['id'], task_id=task_id, project_id=project.id,
                repository_id=project.repository_id, inventory_hash=digest(expected),
                runner_hash=digest(config.model_dump(mode='json')), final_call_id=final.call_id,
                expected_tests=final.metadata['tests'], source_hash=digest(source))
        return dict(source=source, record=record, project=project, snapshot=snapshot, binding=binding,
                    baseline_failures={f['test'] for f in baseline.result.metadata['test_failures']})

    def verify(self, review_id, sources, targets, progress):
        if not 1 <= len(targets) <= 4:
            raise VerificationUnavailable('共同代码目标过多或缺失，无法隔离验证。')
        contexts = [self.context(s) for s in sources]
        if contexts[0]['source']['source_task_id'] == contexts[1]['source']['source_task_id']:
            raise VerificationUnavailable('两条规则并非独立任务产生。')
        texts = []
        for context in contexts:
            changed = {f.path:f for f in context['snapshot'].files}
            values = {}
            for target in targets:
                relative, _, qualified = target.partition('#')
                if not qualified or not code_path(relative) or not relative.endswith('.py') or relative not in changed:
                    raise VerificationUnavailable('目标不是双方已验证修改的生产函数。')
                change = changed[relative]
                if change.before_content is None or change.after_content is None:
                    raise VerificationUnavailable('新增或删除函数暂不支持自动交叉验证。')
                before = function(ast.parse(change.before_content), qualified)
                after = function(ast.parse(change.after_content), qualified)
                if body_identity(before) == body_identity(after):
                    raise VerificationUnavailable('经验声称的冲突目标没有对应实际源码修改。')
                values[relative] = change.after_content
            texts.append(values)
        grafts = []
        for index in range(2):
            replacement = dict(texts[index])
            for target in targets:
                relative, qualified = target.split('#', 1)
                replacement[relative] = transplant(replacement[relative], texts[1-index][relative], qualified)
            grafts.append(replacement)
        runs = []
        bindings = [c['binding'] for c in contexts]
        progress('snapshot', '双方快照、完整测试配置和生产函数映射已核对；测试保持只读。', targets=targets, bindings=bindings)
        for index, context in enumerate(contexts):
            run = self.execute(review_id, context, context['source']['id'], {}, 1)
            runs.append(run)
            progress('self_test', '正在重新验证双方真实修复在各自原测试上下文中的结果。', runs=list(runs))
            if run['outcome'] != 'passed' or run['tests'] != context['binding']['expected_tests']:
                raise VerificationUnavailable('来源实现未能重新通过原完整验收，停止自动裁决。')
        for index, context in enumerate(contexts):
            for repeat in (1, 2):
                progress('cross_test', '在隔离副本中交叉使用另一来源的真实函数；重复验证以排除偶然失败。', runs=list(runs))
                run = self.execute(review_id, context, contexts[1-index]['source']['id'], grafts[index], repeat)
                runs.append(run)
                progress('cross_test', '已保存实际交叉测试结果与失败用例。', runs=list(runs))
        return dict(runs=runs, bindings=bindings, targets=targets)

    def execute(self, review_id, context, implementation_id, replacements, repeat):
        record, project = context['record'], context['project']
        manager = WorkspaceManager(self.root/review_id/'workspaces', {'verified':record.path})
        with record.lock:
            if digest(inventory(record.path)) != context['binding']['inventory_hash']:
                raise VerificationUnavailable('来源在复制前发生变化。')
            run_id = str(uuid4())
            workspace = manager.create_workspace(record.path, run_id)
        service = ToolService(manager, self.settings.maven_home,
            python_executable=self.settings.python_executable, runner_config=project.runner_config)
        for relative, content in replacements.items():
            result = service.execute_tool(workspace, ToolCall(id=str(uuid4()), name='write_file', arguments={'path':relative,'content':content}))
            if not result.success:
                raise VerificationUnavailable('隔离副本的生产函数替换未通过边界检查。')
        expected = inventory(workspace)
        result = service.execute_tool(workspace, ToolCall(id=str(uuid4()), name='run_tests', arguments={}))
        if inventory(workspace) != expected:
            raise VerificationUnavailable('测试执行期间项目文件发生变化，不能自动使用该结果。')
        artifact = self.root/review_id/(run_id+'.json')
        artifact.write_text(result.model_dump_json(), encoding='utf-8')
        reports = list((manager.root/'.test-reports'/run_id).glob('*.xml'))
        if project.language=='java':
            reports=list(workspace.rglob('target/surefire-reports/TEST-*.xml'))
        elif project.language=='cpp':
            reports=list((manager.root/'.native-builds'/run_id).glob('*.xml'))
        data = result.metadata
        case_ids, failed = [], []
        if reports and sum(report.stat().st_size for report in reports) <= 2_000_000:
            try:
                for report in reports:
                    for case in ET.fromstring(report.read_bytes()).iter('testcase'):
                        key = case.get('classname', '')+'.'+case.get('name', '')
                        case_ids.append(key)
                        if case.find('failure') is not None or case.find('error') is not None:
                            failed.append(key)
            except ET.ParseError:
                case_ids=[]
        if project.language=='go':
            cases={}
            try:
                for line in result.output.splitlines():
                    if line.startswith('{'):
                        event=json.loads(line)
                        if event.get('Test') and event.get('Action') in {'pass','fail','skip'}:
                            cases[event['Package']+'.'+event['Test']]=event['Action']
                case_ids=sorted(cases);failed=sorted(k for k,v in cases.items() if v=='fail')
            except (ValueError,KeyError):case_ids=[]
        valid = (bool(case_ids) and data.get('tests') == len(case_ids) and not data.get('errors')
                 and not data.get('skipped') and not data.get('truncated') and data.get('exit_code') in {0,1})
        outcome = ('passed' if result.success else 'failed' if result.error_code == ErrorCode.TEST_FAILED and failed
                   else 'invalid') if valid else 'invalid'
        return dict(id=run_id, target_source_id=context['source']['id'], implementation_source_id=implementation_id,
            repeat=repeat, outcome=outcome, tests=data.get('tests',0), failures=data.get('failures',0),
            errors=data.get('errors',0), skipped=data.get('skipped',0), test_identity=digest(sorted(case_ids)),
            failed_tests=sorted(failed), reproduces_baseline=bool(failed) and set(failed)<=context['baseline_failures'],
            artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            artifact_path=artifact.relative_to(self.settings.workspace_root.parent).as_posix())

    @contextmanager
    def publication_guard(self, evidence):
        # Prevent a concurrent workspace write from invalidating a just-published verdict.
        with ExitStack() as stack:
            for binding in sorted(evidence['bindings'], key=lambda b:b['task_id']):
                record = self.manager.get(self.manager.root/binding['task_id'])
                stack.enter_context(record.lock)
                project = self.settings.project(binding['project_id'])
                if (digest(inventory(record.path)) != binding['inventory_hash']
                        or digest(project.runner_config.model_dump(mode='json')) != binding['runner_hash']):
                    raise VerificationUnavailable('发布决定前源码或测试配置发生变化。')
            yield
