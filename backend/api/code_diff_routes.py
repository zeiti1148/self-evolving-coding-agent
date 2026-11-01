"""Read-only diffs from immutable snapshots or explicitly marked legacy evidence."""
import difflib
import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException, Query, Response

from backend.shared import ApplicationError
from backend.shared.code_diff import CodeChangeInfo, CodeChangesIndex, CodeDiffFile, CodeSnapshot, DiffHunk, DiffLine
from backend.tools.code_changes import MAX_FILES, MAX_TOTAL_BYTES, code_path, make_change, read_code, read_snapshot
from backend.tools.workspace import Workspace
from .task_files import task_workspace

NOTICES = {
    'snapshot': '对比任务创建时与结束时保存的代码；查看对比不会修改文件。修复是否通过请以任务最终测试为准。',
    'recorded_writes': '旧任务没有完整快照：修复前原文已核对创建时校验值，修复后内容来自成功写入记录并与保留副本核对。',
}


def legacy_changes(record, manager, task, steps):
    writes = {}
    for step in steps:
        if step.call.name == 'write_file' and step.result.success:
            path = step.result.metadata.get('path')
            content = step.call.arguments.get('content')
            if isinstance(path, str) and code_path(path) and isinstance(content, str):
                writes[path] = content
    names = sorted(path for path in (task.result.changed_files if task.result else writes) if code_path(path))
    original = Workspace(record.source, record.source, record.baseline)
    files, remaining = [], MAX_TOTAL_BYTES
    for path in names[:MAX_FILES]:
        before_hash = record.baseline.get(path)
        before, after, digest, reason = None, None, None, None
        try:
            content = writes.get(path)
            if content is None:
                raise ValueError('No successful write evidence')
            digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
            if digest == before_hash:
                continue
            before = read_code(manager.resolve(original, path), before_hash) if before_hash is not None else None
            after = read_code(manager.resolve(record, path), digest)
            size = len((before or '').encode('utf-8')) + len(after.encode('utf-8'))
            if size > remaining:
                raise ValueError('Total source limit exceeded')
            remaining -= size
        except (ApplicationError, OSError, ValueError):
            reason = '旧任务的原文或成功写入记录无法核对，不能可靠展示该文件的对比。'
        files.append(make_change(path, before_hash, digest, before, after, reason))
    return CodeSnapshot(task_id=task.task_id, files=files, omitted_files=max(0, len(names) - MAX_FILES))


def line_diff(before, after):
    old, new = (before or '').splitlines(), (after or '').splitlines()
    if len(old) > 4000 or len(new) > 4000:
        raise HTTPException(413, '单个版本超过 4000 行，暂时无法生成在线对比。')
    matcher = difflib.SequenceMatcher(None, old, new)
    additions = deletions = 0
    hunks = []
    for group in matcher.get_grouped_opcodes(3):
        first, last = group[0], group[-1]
        old_size, new_size = last[2] - first[1], last[4] - first[3]
        lines = []
        for tag, i, j, a, b in group:
            if tag == 'equal':
                lines.extend(DiffLine(kind='context', old_line=k + 1, new_line=a + k - i + 1, content=old[k]) for k in range(i, j))
            else:
                if tag in {'replace', 'delete'}:
                    deletions += j - i
                    lines.extend(DiffLine(kind='removed', old_line=k + 1, content=old[k]) for k in range(i, j))
                if tag in {'replace', 'insert'}:
                    additions += b - a
                    lines.extend(DiffLine(kind='added', new_line=k + 1, content=new[k]) for k in range(a, b))
        hunks.append(DiffHunk(old_start=first[1] + bool(old_size), old_lines=old_size,
                              new_start=first[3] + bool(new_size), new_lines=new_size, lines=lines))
    return additions, deletions, hunks, before is not None and after is not None and old == new and before != after


def register_code_diff_routes(app, tasks, manager, ensure_recorded):
    def comparison(task_id):
        record = task_workspace(task_id, tasks, manager, ensure_recorded)
        task = tasks.get(str(task_id))
        if task.status in {'pending', 'running'}:
            raise HTTPException(409, '任务仍在执行，结束后可查看代码对比。')
        try:
            with record.lock:
                snapshot = read_snapshot(manager, record)
                if snapshot is not None:
                    return task, snapshot, 'snapshot'
                if record.before_code is not None:
                    raise HTTPException(409, '该任务缺少结束时的代码快照，无法确认最终改动。')
                return task, legacy_changes(record, manager, task, tasks.get_steps(str(task_id)).steps), 'recorded_writes'
        except (ApplicationError, OSError, ValueError):
            raise HTTPException(409, '代码快照无法核对，暂时不能展示该任务的改动。') from None

    @app.get('/api/tasks/{task_id}/code-changes', response_model=CodeChangesIndex)
    def list_changes(task_id: UUID, response: Response):
        task, snapshot, mode = comparison(task_id)
        response.headers['Cache-Control'] = 'no-store'
        return CodeChangesIndex(task_id=str(task_id), status=task.status, mode=mode, notice=NOTICES[mode],
                                files=[CodeChangeInfo(**file.model_dump(exclude={'before_content', 'after_content'})) for file in snapshot.files],
                                omitted_files=snapshot.omitted_files)

    @app.get('/api/tasks/{task_id}/code-diff', response_model=CodeDiffFile)
    def get_diff(task_id: UUID, response: Response, path: Annotated[str, Query(min_length=1, max_length=1000)]):
        _, snapshot, mode = comparison(task_id)
        selected = next((file for file in snapshot.files if file.path == path), None)
        if selected is None:
            raise HTTPException(404, '该文件不在任务记录的业务代码改动中。')
        if not selected.available:
            raise HTTPException(409, selected.unavailable_reason)
        additions, deletions, hunks, format_only = line_diff(selected.before_content, selected.after_content)
        response.headers['Cache-Control'] = 'no-store'
        return CodeDiffFile(**selected.model_dump(), task_id=str(task_id), mode=mode, notice=NOTICES[mode],
                            additions=additions, deletions=deletions, hunks=hunks, format_only=format_only)
