"""Recovery actions belong to the normal experience and repair workflow."""
from uuid import UUID
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from backend.shared import ApplicationError
from backend.experience.recovery import enqueue, history, public
from backend.experience.database import connect


class RecoveryRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task_id: UUID | None = None
    retry: bool = False


class RecoveryConfirmation(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    version: int = Field(strict=True,ge=1)
    note: str = Field(min_length=1,max_length=2000)
    accept: bool


def register_recovery_routes(app,service):
    def operation(callback):
        try:return callback()
        except ApplicationError as exc:raise HTTPException(409,exc.message) from None

    @app.get('/api/experiences/library/{card_id}/recovery')
    def listing(card_id: UUID):
        return operation(lambda:history(service.path,str(card_id)))

    @app.post('/api/experiences/library/{card_id}/recovery',status_code=202)
    def start(card_id: UUID,body: RecoveryRequest):
        if body.task_id:
            try:task=service.verifier.tasks.get(str(body.task_id))
            except KeyError:raise HTTPException(404,'未找到补充验证任务。') from None
            if task.status not in {'succeeded','stopped'} or not task.result or not task.result.experience_saved:
                raise HTTPException(409,'请选择已完成验收并保存经验的修复任务。')
        return operation(lambda:enqueue(service.path,str(card_id),task_id=str(body.task_id) if body.task_id else None,retry=body.retry))

    @app.get('/api/experiences/library/{card_id}/evidence-tasks')
    def evidence_tasks(card_id: UUID):
        from backend.experience.library import library
        card=next((c for c in library(service.path)['items'] if c['id']==str(card_id)),None)
        if not card:raise HTTPException(404,'未找到当前经验。')
        result=[]
        for task in service.verifier.tasks.list_tasks(100,0):
            if not task.result or not task.result.repair_succeeded or not task.result.experience_saved:continue
            request=service.verifier.tasks.get_request(task.task_id)
            if not request:continue
            project=service.verifier.settings.project(request.source_project)
            if project.language!=card['language']:continue
            result.append(dict(id=task.task_id,label=request.task_description[:150],project_label=project.label,
                created_at=task.created_at.isoformat(),tests=task.result.tool_results[-1].metadata.get('tests',0) if task.result.tool_results else 0))
        return result

    @app.get('/api/experiences/recovery/{identifier}')
    def get(identifier: UUID):
        with connect(service.path) as connection:
            row=connection.execute('SELECT * FROM knowledge_recoveries WHERE id=?',(str(identifier),)).fetchone()
            if not row:raise HTTPException(404,'未找到补充验证请求。')
            return public(row,connection)

    @app.post('/api/experiences/recovery/{identifier}/confirm')
    def confirm(identifier: UUID,body: RecoveryConfirmation):
        return operation(lambda:service.confirm(str(identifier),body.version,body.note,body.accept))
