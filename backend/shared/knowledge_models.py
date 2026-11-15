"""Public read contracts for durable knowledge maintenance; worker leases stay private."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field
from .models import RepairIssue


class MaintenanceJob(BaseModel):
    id: UUID
    rule_id: UUID
    state: Literal['pending','running','succeeded','failed']
    cursor: str = ''
    attempts: int = Field(ge=0)
    examined: int = Field(ge=0)
    error: str | None
    created_at: datetime
    updated_at: datetime


class RuleData(BaseModel):
    language: Literal['java','python','go','cpp']
    scope: Literal['project','global']
    repository_id: str
    compatibility_key: str
    applicability: list[str]
    constraints: list[str]
    required_capabilities: list[str]
    issue: RepairIssue
    verification_class: Literal['verified','unreviewed'] = 'unreviewed'


class RuleStatus(BaseModel):
    id: UUID
    state: Literal['pending','ready','needs_review','failed']
    effective_status: Literal['active','paused']
    data: RuleData


class ExperienceMaintenanceStatus(BaseModel):
    experience_id: UUID
    effective_status: Literal['active','paused']
    current_rule_ids: list[UUID]
    rules: list[RuleStatus]


class RuleSources(BaseModel):
    rule_id: UUID
    source_ids: list[UUID]
    evidence_count: int = Field(ge=0)
    next_offset: int | None = Field(default=None,ge=0)


class ReviewEvent(BaseModel):
    version: int = Field(ge=1)
    action: Literal['resolve','reopen']
    data: dict[str,Any]
    created_at: datetime
