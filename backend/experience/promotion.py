"""Explicit, version-checked publication; original records are never rewritten."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pydantic import ValidationError

from backend.shared import (ApplicationError, ErrorCode, ExperienceCreate, PromotionDraft, PromotionPreview, RepairIssue)
from .database import connect
from .policy import to_model_reference
from .store import COLUMNS, SUPERSEDED, _uuid, _insert, _new_experience, decode_row, validation_error


def conflict(message):
    return ApplicationError(ErrorCode.EXPERIENCE_CONFLICT, message)


def get_source(connection, identifier):
    row = connection.execute(f"SELECT {COLUMNS} FROM experiences WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise ApplicationError(ErrorCode.EXPERIENCE_NOT_FOUND, "未找到该经验。")
    return decode_row(row)


def is_superseded(connection, identifier):
    return bool(connection.execute(f"SELECT 1 WHERE ? IN ({SUPERSEDED})", (identifier,)).fetchone())


def source_conflicts(connection, source):
    reasons = []
    if not source.evidence:
        reasons.append("来源经验必须具有程序独立运行且通过的最终测试证据。")
    if source.reuse_status == "candidate" or source.scope == "project" and source.reuse_status != "active":
        reasons.append("来源经验尚未通过发布所需的验证。")
    if source.scope == "global" and source.reuse_status == "active" and not source.merged_from:
        reasons.append("请基于原始来源和当前版本修订已发布规则。")
    own_publication = connection.execute(
        "SELECT 1 FROM experience_publication_revisions r JOIN experience_promotions p ON p.global_id=r.new_id "
        "WHERE r.previous_id=? AND p.source_id=?", (source.id, source.id)).fetchone()
    merged_elsewhere = connection.execute("SELECT 1 FROM experience_merge_sources WHERE source_id=?", (source.id,)).fetchone()
    if is_superseded(connection, source.id) and (not own_publication or merged_elsewhere):
        reasons.append("来源已被替代，请选择当前的合并或发布记录。")
    head = connection.execute("SELECT global_id FROM experience_publication_heads WHERE source_id=?", (source.id,)).fetchone()
    if head and is_superseded(connection, head[0]):
        reasons.append("当前发布版本已被合并，请对新的合并记录进行通用化。")
    return reasons


def _draft(source):
    # A conservative scaffold intentionally asks the reviewer to supply the target-independent rule.
    # Deterministic text redaction cannot establish semantic generality of arbitrary source prose.
    errors = list(dict.fromkeys(issue.error_type for issue in source.issues)) or [source.error_type or "InputError"]
    return PromotionDraft(task_summary="可复用的输入校验规则", language=source.language,
        error_type=errors[0] if len(errors) == 1 else None,
        root_cause="未检查输入条件，可能导致程序行为不符合调用方的要求。",
        solution="操作前先检查输入条件，并遵循目标项目明确规定的默认值或异常处理要求。",
        applicability=["确认目标项目具有相同的输入条件后，才能使用本规则。",
                       "根据目标项目的原始测试，核对默认值、异常处理和边界规则。"],
        issues=[RepairIssue(error_type=error, root_cause=f"触发 {error} 的输入条件需要检查。",
                            solution="检查输入，保持正常输入的原有行为，并符合目标项目的业务要求。") for error in errors],
        required_capabilities=source.required_capabilities)


def _publication(source, draft, previous=None):
    return _new_experience(ExperienceCreate(**draft.model_dump(), schema_version=3, scope="global", reuse_status="active",
        verification="来源已通过独立测试；每个目标项目仍须独立验证自身的业务要求。",
        evidence=source.evidence, derived_from=[source.id], supersedes_id=previous))


def preview_promotion(path: Path, source_id: str) -> PromotionPreview:
    source_id = _uuid(source_id)
    with connect(path) as connection:
        connection.execute("BEGIN")
        source = get_source(connection, source_id)
        reasons = source_conflicts(connection, source)
        head = connection.execute("SELECT global_id FROM experience_publication_heads WHERE source_id=?", (source_id,)).fetchone()
        draft = _draft(source) if not reasons else None
        injection = to_model_reference(_publication(source, draft)) if draft else None
        if injection:
            injection["id"] = "assigned-on-publication"
        return PromotionPreview(can_publish=not reasons, conflicts=reasons, source_id=source_id,
                                current_global_id=head[0] if head else None, draft=draft, injection_preview=injection)


def validate_draft(source, draft):
    if not isinstance(draft, PromotionDraft):
        raise validation_error("发布内容必须符合 PromotionDraft 草稿模型。")
    try:
        draft = PromotionDraft.model_validate(draft.model_dump())
        text = json.dumps(draft.model_dump(mode="json"), ensure_ascii=False)
        text.encode("utf-8")
    except (ValidationError, ValueError, UnicodeError):
        raise validation_error("发布草稿无效。") from None
    if draft.language != source.language:
        raise conflict("发布时必须保留来源的编程语言。")
    if any(issue.files for issue in draft.issues):
        raise conflict("通用规则不得包含来源文件路径。")
    private = [source.repository_id, source.source_revision, source.source_task_id,
               *(file for issue in source.issues for file in issue.files)]
    for value in private:
        if value and re.search(r"(?<![\w])" + re.escape(value) + r"(?![\w])", text):
            raise conflict("草稿仍包含来源仓库的私有路径或标识，请编辑将发送给其他仓库的内容。")
    return draft


def publish_global(path: Path, source_id: str, draft: PromotionDraft, *, supersedes_id: str | None = None):
    source_id = _uuid(source_id)
    supersedes_id = _uuid(supersedes_id) if supersedes_id is not None else None
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        source = get_source(connection, source_id)
        draft = validate_draft(source, draft)
        key = hashlib.sha256(json.dumps([source_id, draft.model_dump(mode="json"), supersedes_id],
                                        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        prior = connection.execute("SELECT global_id FROM experience_promotions WHERE promotion_key=?", (key,)).fetchone()
        if prior:
            return get_source(connection, prior[0])
        reasons = source_conflicts(connection, source)
        if reasons:
            raise conflict(" ".join(reasons))
        head = connection.execute("SELECT global_id FROM experience_publication_heads WHERE source_id=?", (source_id,)).fetchone()
        current_id = head[0] if head else None
        if current_id != supersedes_id:
            raise conflict("发布版本已变化，请刷新预览，再基于当前版本发布修订。")
        if current_id and is_superseded(connection, current_id):
            raise conflict("当前发布版本已被合并，请对新的合并记录进行通用化。")
        previous = current_id or (source.id if source.scope == "global" else None)
        saved = _publication(source, draft, previous)
        _insert(connection, saved)
        connection.execute("INSERT INTO experience_promotions VALUES (?, ?, ?)", (key, source.id, saved.id))
        connection.execute("INSERT INTO experience_publication_heads VALUES (?, ?) "
                           "ON CONFLICT(source_id) DO UPDATE SET global_id=excluded.global_id", (source.id, saved.id))
        if previous:
            connection.execute("INSERT INTO experience_publication_revisions VALUES (?, ?)", (previous, saved.id))
        return saved
