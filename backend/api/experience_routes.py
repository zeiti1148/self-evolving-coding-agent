"""Experience management routes; none of these management lists are model retrieval."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.shared import ApplicationError, ErrorCode, Experience, PromotionDraft, PromotionPreview, ProvenanceGraph


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


def register_experience_routes(app, store):
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
                language: Literal["java", "python"] | None = None,
                repository_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
                scope: Literal["global", "project"] | None = None,
                reuse_status: Literal["active", "candidate", "legacy_unreviewed"] | None = None,
                source_task_id: UUID | None = None, active_only: bool = False):
        return operation(lambda: store.list_experiences(limit, offset, language=language, repository_id=repository_id,
            scope=scope, reuse_status=reuse_status, source_task_id=str(source_task_id) if source_task_id else None,
            active_only=active_only))

    @router.post("/merge-preview")
    def merge_preview(body: MergeRequest):
        return operation(lambda: store.preview_merge(sources(body)))

    @router.post("/merge", response_model=Experience)
    def merge(body: MergeRequest):
        return operation(lambda: store.merge_experiences(sources(body)))

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

    @router.get("/{identifier}", response_model=Experience)
    def detail(identifier: UUID):
        record = operation(lambda: store.get_experience(str(identifier)))
        if record is None:
            raise HTTPException(404, "未找到该经验。")
        return record

    app.include_router(router)
