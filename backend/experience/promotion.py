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
        raise ApplicationError(ErrorCode.EXPERIENCE_NOT_FOUND, "Experience was not found.")
    return decode_row(row)


def is_superseded(connection, identifier):
    return bool(connection.execute(f"SELECT 1 WHERE ? IN ({SUPERSEDED})", (identifier,)).fetchone())


def source_conflicts(connection, source):
    reasons = []
    if not source.evidence:
        reasons.append("Source requires structured evidence from successful independent final tests.")
    if source.reuse_status == "candidate" or source.scope == "project" and source.reuse_status != "active":
        reasons.append("Source has not been verified for publication.")
    if source.scope == "global" and source.reuse_status == "active" and not source.merged_from:
        reasons.append("Revise a published rule through its original source and current version.")
    own_publication = connection.execute(
        "SELECT 1 FROM experience_publication_revisions r JOIN experience_promotions p ON p.global_id=r.new_id "
        "WHERE r.previous_id=? AND p.source_id=?", (source.id, source.id)).fetchone()
    merged_elsewhere = connection.execute("SELECT 1 FROM experience_merge_sources WHERE source_id=?", (source.id,)).fetchone()
    if is_superseded(connection, source.id) and (not own_publication or merged_elsewhere):
        reasons.append("Source has been superseded; select its current merged or published record.")
    head = connection.execute("SELECT global_id FROM experience_publication_heads WHERE source_id=?", (source.id,)).fetchone()
    if head and is_superseded(connection, head[0]):
        reasons.append("Published head has been merged; generalize the current merge result instead.")
    return reasons


def _draft(source):
    # A conservative scaffold intentionally asks the reviewer to supply the target-independent rule.
    # Deterministic text redaction cannot establish semantic generality of arbitrary source prose.
    errors = list(dict.fromkeys(issue.error_type for issue in source.issues)) or [source.error_type or "InputError"]
    return PromotionDraft(task_summary="Reusable input validation", language=source.language,
        error_type=errors[0] if len(errors) == 1 else None,
        root_cause="Unchecked input preconditions can violate a caller's contract.",
        solution="Check input preconditions before the operation; use the target's explicit default or rejection policy.",
        applicability=["Apply only after confirming equivalent input preconditions in the target.",
                       "Confirm target-specific defaults, rejection policies and boundary rules against its original tests."],
        issues=[RepairIssue(error_type=error, root_cause=f"An input precondition for {error} needs validation.",
                            solution="Validate the input, preserve valid behavior, and honor the target contract.") for error in errors],
        required_capabilities=source.required_capabilities)


def _publication(source, draft, previous=None):
    return _new_experience(ExperienceCreate(**draft.model_dump(), schema_version=3, scope="global", reuse_status="active",
        verification="Source verified by independent tests; every target must independently validate its own contract.",
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
        raise validation_error("Publication requires a PromotionDraft.")
    try:
        draft = PromotionDraft.model_validate(draft.model_dump())
        text = json.dumps(draft.model_dump(mode="json"), ensure_ascii=False)
        text.encode("utf-8")
    except (ValidationError, ValueError, UnicodeError):
        raise validation_error("Invalid publication draft.") from None
    if draft.language != source.language:
        raise conflict("Publication must preserve the source language.")
    if any(issue.files for issue in draft.issues):
        raise conflict("Public rules must omit source file paths.")
    private = [source.repository_id, source.source_revision, source.source_task_id,
               *(file for issue in source.issues for file in issue.files)]
    for value in private:
        if value and re.search(r"(?<![\w])" + re.escape(value) + r"(?![\w])", text):
            raise conflict("Draft still contains a known private source path or identifier; edit the injection preview.")
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
            raise conflict("Publication version changed; refresh the preview and supply current_global_id as supersedes_id.")
        if current_id and is_superseded(connection, current_id):
            raise conflict("Published head has been merged; generalize the current merge result instead.")
        previous = current_id or (source.id if source.scope == "global" else None)
        saved = _publication(source, draft, previous)
        _insert(connection, saved)
        connection.execute("INSERT INTO experience_promotions VALUES (?, ?, ?)", (key, source.id, saved.id))
        connection.execute("INSERT INTO experience_publication_heads VALUES (?, ?) "
                           "ON CONFLICT(source_id) DO UPDATE SET global_id=excluded.global_id", (source.id, saved.id))
        if previous:
            connection.execute("INSERT INTO experience_publication_revisions VALUES (?, ?)", (previous, saved.id))
        return saved
