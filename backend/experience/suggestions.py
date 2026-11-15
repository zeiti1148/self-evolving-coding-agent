"""Bounded, read-only discovery. Suggestions never replace explicit merge confirmation."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from itertools import combinations
from pathlib import Path
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .database import storage_error
from .store import (COLUMNS, ORDER, _file_keys, _issue_key, _validate_filters, build_merge_preview,
                    decode_row, validate_integer, validation_error)

SCAN_LIMIT = 1000
CANDIDATE_LIMIT = 200


class SuggestionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MergeSuggestion(SuggestionModel):
    source_ids: list[str] = Field(min_length=2, max_length=2)
    source_titles: list[str] = Field(min_length=2, max_length=2)
    language: Literal["java", "python"]
    scope: Literal["project", "global"]
    repository_id: str | None
    reasons: list[Literal["shared_rules", "shared_files"]]
    shared_issue_count: int = Field(ge=0, le=50)
    issue_count: int = Field(ge=1, le=50)
    verification_count: int = Field(ge=1, le=100)
    revision_warning: bool


class RejectedPairs(SuggestionModel):
    reason: Literal["conditions", "capabilities", "unrelated", "conflict"]
    count: int = Field(ge=1, le=19900)


class MergeSuggestions(SuggestionModel):
    suggestions: list[MergeSuggestion] = Field(default_factory=list, max_length=20)
    scanned_count: int = Field(default=0, ge=0, le=1000)
    eligible_count: int = Field(default=0, ge=0, le=200)
    checked_pairs: int = Field(default=0, ge=0, le=19900)
    rejected: list[RejectedPairs] = Field(default_factory=list, max_length=4)
    truncation_reasons: list[Literal["scan_limit", "candidate_limit", "result_limit"]] = Field(default_factory=list)


def _read_current(path: Path):
    # Do not use database.connect: it performs schema initialization/migration.
    # mode=ro preserves DB content/schema, but SQLite may maintain WAL/SHM
    # bookkeeping. Do not use immutable=1: committed WAL rows must remain visible.
    if not path.exists():
        return [], False
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "experiences" not in tables:
                return [], False
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(experiences)")}
            if "details" not in columns:
                return [], False
            exclusions = []
            if "experience_merge_sources" in tables:
                exclusions.append("SELECT source_id FROM experience_merge_sources")
            if "experience_publication_revisions" in tables:
                exclusions.append("SELECT previous_id FROM experience_publication_revisions")
            where = f" WHERE id NOT IN ({' UNION '.join(exclusions)})" if exclusions else ""
            rows = connection.execute(f"SELECT {COLUMNS} FROM experiences{where}{ORDER} LIMIT ?", (SCAN_LIMIT + 1,)).fetchall()
            return [decode_row(row) for row in rows[:SCAN_LIMIT]], len(rows) > SCAN_LIMIT
    except (sqlite3.Error, OSError):
        raise storage_error() from None


def suggest_merges(path: Path, *, limit=10, language=None, repository_id=None, scope=None, reuse_status=None) -> MergeSuggestions:
    validate_integer(limit, "limit", 1)
    if limit > 20:
        raise validation_error("合并建议每次最多返回 20 组。")
    _validate_filters(language, repository_id, scope)
    if reuse_status is not None and reuse_status not in {"active", "candidate", "legacy_unreviewed"}:
        raise validation_error("无法识别该复用状态。")
    records, truncated = _read_current(path)
    response = MergeSuggestions(scanned_count=len(records), truncation_reasons=["scan_limit"] if truncated else [])
    candidates = [item for item in records if item.schema_version == 3 and item.reuse_status == "active"
        and item.applicability and item.issues and item.evidence
        and (language is None or item.language == language)
        and (repository_id is None or item.repository_id == repository_id)
        and (scope is None or item.scope == scope)
        and (reuse_status is None or item.reuse_status == reuse_status)]
    if len(candidates) > CANDIDATE_LIMIT:
        response.truncation_reasons.append("candidate_limit")
    candidates = candidates[:CANDIDATE_LIMIT]
    response.eligible_count = len(candidates)
    groups = defaultdict(list)
    for item in candidates:
        groups[item.language, item.scope, item.repository_id].append(item)
    rejected = Counter()
    accepted = []
    for group in groups.values():
        for left, right in combinations(group, 2):
            response.checked_pairs += 1
            if set(left.applicability) != set(right.applicability):
                rejected["conditions"] += 1
                continue
            if set(left.required_capabilities) != set(right.required_capabilities):
                rejected["capabilities"] += 1
                continue
            shared = {_issue_key(issue) for issue in left.issues} & {_issue_key(issue) for issue in right.issues}
            files = set().union(*(_file_keys(issue) for issue in left.issues))
            other_files = set().union(*(_file_keys(issue) for issue in right.issues))
            if not shared and not files & other_files:
                rejected["unrelated"] += 1
                continue
            plan = build_merge_preview([left, right])
            if not plan["can_merge"]:
                rejected["conflict"] += 1
                continue
            sources = sorted([left, right], key=lambda item: item.id)
            preview = plan["preview"]
            accepted.append(MergeSuggestion(source_ids=[item.id for item in sources],
                source_titles=[item.task_summary for item in sources], language=left.language, scope=left.scope,
                repository_id=left.repository_id, reasons=["shared_rules" if shared else "shared_files"],
                shared_issue_count=len(shared), issue_count=len(preview.issues), verification_count=len(preview.evidence),
                revision_warning=not left.source_revision or not right.source_revision or left.source_revision != right.source_revision))
    accepted.sort(key=lambda item: (-item.shared_issue_count, -item.verification_count, item.source_ids))
    response.suggestions = accepted[:limit]
    response.rejected = [RejectedPairs(reason=reason, count=rejected[reason])
                         for reason in ("conditions", "capabilities", "unrelated", "conflict") if rejected[reason]]
    if len(accepted) > limit:
        response.truncation_reasons.append("result_limit")
    return response
