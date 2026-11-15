"""Experience management routes; none of these management lists are model retrieval."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.shared import (ApplicationError, ErrorCode, Experience, PromotionDraft, PromotionPreview, ProvenanceGraph,
                            MergePreview, ConsolidationReport, ConflictResolution, ExperienceConflict)
from backend.shared.knowledge_models import MaintenanceJob, ExperienceMaintenanceStatus, RuleSources, ReviewEvent


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experience_ids: list[UUID] = Field(min_length=2, max_length=10)

    @field_validator("experience_ids")
    @classmethod
    def unique_ids(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("请选择不同的经验")
        return values


class PromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: UUID


class PublishRequest(PromotionRequest):
    draft: PromotionDraft
    supersedes_id: UUID | None = None


class ConsolidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    language: Literal["java", "python", "go", "cpp"]
    repository_id: str = Field(min_length=1, max_length=200)
    cursor: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: int = Field(strict=True, ge=1)
    note: str = Field(min_length=1, max_length=2000)


class HistoryScanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    retry: bool = False


def register_experience_routes(app, store, projects=None):
    router = APIRouter(prefix="/api/experiences")
    def operation(callback):
        try:
            return callback()
        except ApplicationError as exc:
            status = (404 if exc.code == ErrorCode.EXPERIENCE_NOT_FOUND else
                      409 if exc.code in {ErrorCode.EXPERIENCE_VALIDATION_ERROR, ErrorCode.EXPERIENCE_CONFLICT} else 503)
            raise HTTPException(status, exc.message) from None
    def sources(body):
        ids = [str(value) for value in body.experience_ids]
        if any(store.get_experience(identifier) is None for identifier in ids):
            raise HTTPException(404, "未找到选中用于合并的经验。")
        return ids

    @router.get("", response_model=list[Experience])
    def listing(limit: Annotated[int, Query(ge=1, le=2**63-1)] = 20,
                offset: Annotated[int, Query(ge=0, le=2**63-1)] = 0,
                language: Literal["java", "python", "go", "cpp"] | None = None,
                repository_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
                scope: Literal["global", "project"] | None = None,
                reuse_status: Literal["active", "candidate", "legacy_unreviewed"] | None = None,
                source_task_id: UUID | None = None, active_only: bool = False):
        return operation(lambda: store.list_experiences(limit, offset, language=language, repository_id=repository_id,
            scope=scope, reuse_status=reuse_status, source_task_id=str(source_task_id) if source_task_id else None,
            active_only=active_only))

    @router.post("/merge-preview", response_model=MergePreview)
    def merge_preview(body: MergeRequest):
        return operation(lambda: store.preview_merge(sources(body)))

    @router.get('/library')
    def library(language: Literal['java','python','go','cpp'] | None=None,
                repository_id: Annotated[str | None, Query(min_length=1,max_length=200)]=None,
                scope: Literal['global','project'] | None=None,
                q: Annotated[str,Query(max_length=300)]='',
                error_type: Annotated[str | None,Query(max_length=200)]=None,
                state: Literal['active','paused','unverified'] | None=None,
                tag: Annotated[str | None,Query(max_length=100)]=None,merged: bool | None=None,
                limit: Annotated[int,Query(ge=1,le=100)]=20,offset: Annotated[int,Query(ge=0)]=0,
                sort: Literal['recent','relevance','sources']='recent',card_id: UUID | None=None):
        from backend.experience.library import library
        return operation(lambda: library(store.database_path,language=language,repository_id=repository_id,scope=scope,
            q=q,error_type=error_type,state=state,tag=tag,merged=merged,limit=limit,offset=offset,sort=sort,projects=projects() if projects else [],card_id=str(card_id) if card_id else None))

    @router.post('/history-scan',status_code=202)
    def history_scan(body: HistoryScanRequest):
        from backend.experience.history import enqueue_scan
        return operation(lambda: enqueue_scan(store.database_path,retry=body.retry))

    @router.post("/merge", response_model=Experience)
    def merge(body: MergeRequest):
        return operation(lambda: store.merge_experiences(sources(body)))

    @router.post("/consolidate", response_model=ConsolidationReport)
    def consolidate(body: ConsolidationRequest):
        return operation(lambda: store.consolidate_experiences(**body.model_dump()))

    @router.post('/jobs', status_code=202, response_model=ConsolidationReport)
    def enqueue(body: ConsolidationRequest):
        from backend.experience.jobs import report
        from backend.experience.knowledge import backfill
        def create():
            backfill(store.database_path)
            return report(store.database_path, language=body.language, repository_id=body.repository_id)
        return operation(create)

    @router.get('/jobs', response_model=list[MaintenanceJob])
    def jobs(limit: Annotated[int, Query(ge=1, le=100)]=50, offset: Annotated[int, Query(ge=0)]=0,
             language: Literal['java','python','go','cpp'] | None=None,
             repository_id: Annotated[str | None, Query(min_length=1,max_length=200)]=None):
        from backend.experience.jobs import list_jobs
        return operation(lambda: list_jobs(store.database_path, limit=limit, offset=offset,language=language,repository_id=repository_id))

    @router.get('/automation')
    def automation(limit: Annotated[int, Query(ge=1, le=10)]=5,
                   offset: Annotated[int, Query(ge=0)]=0,
                   language: Literal['java','python','go','cpp'] | None=None,
                   repository_id: Annotated[str | None, Query(min_length=1,max_length=200)]=None,
                   scope: Literal['project','global'] | None=None,
                   experience_id: UUID | None=None):
        from backend.experience.automation import feed
        return operation(lambda: feed(store.database_path, limit=limit, offset=offset, language=language,
                                      repository_id=repository_id, scope=scope,
                                      experience_id=str(experience_id) if experience_id else None))

    @router.post('/automation/{identifier}/retry', status_code=202)
    def retry_automation(identifier: UUID):
        from backend.experience.automation import retry
        if not operation(lambda: retry(store.database_path, str(identifier))):
            raise HTTPException(404, '未找到自动比较记录。')
        return {'id': str(identifier)}

    @router.post('/jobs/{identifier}/retry', response_model=MaintenanceJob)
    def retry_job(identifier: UUID):
        from backend.experience.jobs import retry
        result=operation(lambda: retry(store.database_path,str(identifier)))
        if result is None:
            raise HTTPException(404,'未找到整理任务。')
        return result

    @router.get('/rules/{identifier}/sources', response_model=RuleSources)
    def rule_sources(identifier: UUID, offset: Annotated[int, Query(ge=0)]=0, limit: Annotated[int, Query(ge=1,le=100)]=50):
        from backend.experience.knowledge import provenance_page
        return operation(lambda: provenance_page(store.database_path,str(identifier),offset=offset,limit=limit))

    @router.get("/conflicts", response_model=list[ExperienceConflict])
    def conflicts(language: Literal["java", "python", "go", "cpp"] | None = None,
                  repository_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
                  status: Literal["pending", "resolved", "obsolete"] = "pending",
                  limit: Annotated[int, Query(ge=1, le=100)] = 20,
                  offset: Annotated[int, Query(ge=0, le=2**63-1)] = 0):
        return operation(lambda: store.list_conflicts(language=language, repository_id=repository_id,
                                                     status=status, limit=limit, offset=offset))

    @router.post("/conflicts/{identifier}/resolve", response_model=ExperienceConflict)
    def resolve_conflict(identifier: UUID, body: ConflictResolution):
        return operation(lambda: store.resolve_conflict(str(identifier), body))

    @router.post('/conflicts/{identifier}/reopen', response_model=ExperienceConflict)
    def reopen_conflict(identifier: UUID, body: ReopenRequest):
        return operation(lambda: store.reopen_conflict(str(identifier),body.version,body.note))

    @router.get('/conflicts/{identifier}/history', response_model=list[ReviewEvent])
    def review_history(identifier: UUID):
        return operation(lambda: store.review_history(str(identifier)))

    @router.post("/promotion-preview", response_model=PromotionPreview)
    def promotion_preview(body: PromotionRequest):
        return operation(lambda: store.preview_promotion(str(body.source_id)))

    @router.post("/publish-global", response_model=Experience)
    def publish(body: PublishRequest):
        return operation(lambda: store.publish_global(str(body.source_id), body.draft,
                         supersedes_id=str(body.supersedes_id) if body.supersedes_id else None))

    @router.get("/{identifier}/provenance", response_model=ProvenanceGraph)
    def provenance(identifier: UUID):
        return operation(lambda: store.get_provenance(str(identifier)))

    @router.get('/{identifier}/maintenance', response_model=ExperienceMaintenanceStatus)
    def maintenance(identifier: UUID):
        if operation(lambda: store.get_experience(str(identifier))) is None:
            raise HTTPException(404,'未找到经验。')
        return operation(lambda: store.experience_maintenance(str(identifier)))

    @router.get("/{identifier}", response_model=Experience)
    def detail(identifier: UUID):
        record = operation(lambda: store.get_experience(str(identifier)))
        if record is None:
            raise HTTPException(404, "未找到该经验。")
        return record

    app.include_router(router)
