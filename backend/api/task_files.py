"""One ownership rule for all task-bound file viewers."""
from fastapi import HTTPException

from backend.shared import ApplicationError, ErrorCode


def task_workspace(task_id, tasks, manager, ensure_recorded):
    key = str(task_id)
    ensure_recorded(key)
    try:
        request = tasks.get_request(key)
    except KeyError:
        raise HTTPException(404, "未找到该任务。") from None
    try:
        record = manager.get(manager.root / key)
        expected = manager.projects.get(request.source_project) if request else None
        if expected is None or record.source != expected.resolve():
            raise HTTPException(404, "无法确认该任务工作副本的归属。")
        return record
    except ApplicationError as exc:
        if exc.code == ErrorCode.PATH_OUTSIDE_WORKSPACE:
            raise HTTPException(403, "不允许通过链接读取任务文件。") from None
        raise HTTPException(404, "任务工作副本尚未创建、已被清理或无法读取。") from None
    except (OSError, ValueError):
        raise HTTPException(404, "无法读取任务工作副本。") from None
