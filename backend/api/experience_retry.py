"""Recover extraction from verified terminal repairs without rerunning or rewriting the repair."""
import time
from uuid import UUID

from fastapi import HTTPException

from backend.agent.extraction import extract_and_save_experiences, verification_passed
from backend.projects import source_revision
from backend.shared import AgentRequest, ApplicationError
from backend.tools.code_changes import read_snapshot
from backend.tools.workspace import inventory


def register_experience_retry_routes(app, tasks, manager, experiences, settings, executor, factory, ensure_recorded):
    def context(identifier):
        ensure_recorded(identifier)
        try:
            task = tasks.get(identifier)
            request = tasks.get_request(identifier)
        except KeyError:
            raise HTTPException(404, '未找到该任务。') from None
        result = task.result
        if (not request or not result or task.status not in {'succeeded', 'stopped'}
                or not result.repair_succeeded or result.tests_passed is not True
                or not result.changed_files or not result.steps or not result.tool_results):
            raise HTTPException(409, '需要已完成的修复、源码修改和通过的最终验证证据。')
        try:
            project = settings.project(request.source_project)
            final = result.tool_results[-1]
            if (result.steps[-1].name != 'run_tests' or final.call_id != result.steps[-1].id
                    or not final.metadata.get('final_verification') or not verification_passed(final, project.language)):
                raise ValueError('Missing final verification')
            record = manager.get(manager.root / identifier)
            if record.source != project.source.resolve():
                raise ValueError('Project source changed')
        except (KeyError, ValueError, ApplicationError):
            raise HTTPException(409, '无法核对该任务的项目归属或最终测试证据。') from None
        return task, request, project, record

    def verify_snapshot(record, result):
        snapshot = read_snapshot(manager, record)
        if snapshot is None or snapshot.omitted_files or set(result.changed_files) != {f.path for f in snapshot.files}:
            raise ValueError('Incomplete historical snapshot')
        expected = dict(record.baseline)
        for change in snapshot.files:
            if change.after_sha256 is None:
                expected.pop(change.path, None)
            else:
                expected[change.path] = change.after_sha256
        if inventory(record.path) != expected:
            raise ValueError('Workspace changed after final verification')

    def state(identifier):
        try:
            task = tasks.get(identifier)
        except KeyError:
            raise HTTPException(404, '未找到该任务。') from None
        retry = tasks.get_audit(identifier).get('experience_retry', {})
        saved = bool(task.result and task.result.experience_saved)
        return {'task_id': identifier, 'state': 'succeeded' if saved else retry.get('state', 'idle'),
                'notice': '经验已保存，后台将自动比较已有经验。' if saved else retry.get('notice', '')}

    def run(identifier, token):
        client, ids, diagnostic, error = None, [], {}, None
        try:
            task, request, project, record = context(identifier)
            result = task.result
            with record.lock:
                verify_snapshot(record, result)
                existing = experiences.list_experiences(limit=100, source_task_id=identifier)
                if existing:
                    if any(not entry.batch_id or not entry.evidence or any(
                            e.tool_call_id != result.steps[-1].id for e in entry.evidence) for entry in existing):
                        raise ValueError('Existing batch evidence mismatch')
                    ids = [entry.id for entry in existing]
                    diagnostic = {'status': 'succeeded', 'stage': 'existing_batch', 'saved_count': len(ids)}
                else:
                    client = factory()
                    deadline = time.monotonic() + min(settings.task_timeout, settings.llm_timeout + 10)
                    if hasattr(client, 'set_deadline'):
                        client.set_deadline(deadline)
                    agent_request = AgentRequest(task_id=identifier, workspace=record.path,
                        task_description=request.task_description, max_steps=request.max_steps,
                        language=project.language, repository_id=project.repository_id,
                        source_revision=source_revision(project.source, record.baseline),
                        experience_contract=project.experience_contract, capabilities=project.capabilities,
                        execution_mode=request.execution_mode)
                    class VerifiedStore:
                        def save_experiences(self, items, *, source_task_id):
                            verify_snapshot(record, result)
                            return experiences.save_experiences(items, source_task_id=source_task_id)
                    saved = extract_and_save_experiences(client, VerifiedStore(), agent_request,
                        summary=result.summary, changed_files=result.changed_files, calls=result.steps,
                        results=result.tool_results, deadline=deadline, diagnostic=diagnostic)
                    ids = [entry.id for entry in saved]
        except Exception:
            error = '经验提取未完成；请检查保留的源码、最终测试证据或模型服务后重试。'
        finally:
            events = list(getattr(client, 'audit', [])) if client else []
            if client and hasattr(client, 'close'):
                try:
                    client.close()
                except Exception:
                    pass
            tasks.finish_experience_retry(identifier, token, experience_ids=ids,
                diagnostic=diagnostic, model_requests=events, error=error)

    @app.get('/api/tasks/{task_id}/experience-retry')
    def retry_state(task_id: UUID):
        ensure_recorded(str(task_id))
        return state(str(task_id))

    @app.post('/api/tasks/{task_id}/experience-retry', status_code=202)
    def retry(task_id: UUID):
        identifier = str(task_id)
        task, _, _, record = context(identifier)
        current = state(identifier)
        if current['state'] in {'running', 'succeeded'}:
            return current
        if not task.result.experience_saved:
            try:
                with record.lock:
                    verify_snapshot(record, task.result)
            except (ValueError, OSError, ApplicationError):
                raise HTTPException(409, '源码已变化或缺少结束时快照，不能复用历史测试证据。') from None
        token = tasks.begin_experience_retry(identifier)
        if token:
            try:
                executor.submit(run, identifier, token)
            except RuntimeError:
                tasks.finish_experience_retry(identifier, token, experience_ids=[], diagnostic={},
                    model_requests=[], error='经验提取调度器暂不可用。')
        return state(identifier)
