"""Synchronous experience persistence using the existing shared contracts."""

from __future__ import annotations

import json
import hashlib
import heapq
import sqlite3
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from pydantic import ValidationError

from backend.shared import ApplicationError, ErrorCode, Experience, ExperienceCreate, RepairIssue

from .database import connect, database_path, storage_error


COLUMNS = (
    "id, task_summary, error_type, root_cause, solution, verification, "
    "tags, source_task_id, created_at, details"
)
ORDER = " ORDER BY created_at DESC, id DESC"
DETAIL_FIELDS = frozenset({"schema_version", "language", "scope", "repository_id", "source_revision",
                           "applicability", "issues", "evidence", "merged_from"})


def validation_error(message: str) -> ApplicationError:
    return ApplicationError(ErrorCode.EXPERIENCE_VALIDATION_ERROR, message)


def validate_integer(value: int, name: str, minimum: int) -> None:
    # sqlite3 parameters must fit in a signed 64-bit integer; bool is not a limit.
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise validation_error(f"{name} must be an integer from {minimum} to 2^63-1.")


def decode_row(row: sqlite3.Row) -> Experience:
    try:
        values = dict(row)
        values["tags"] = json.loads(values["tags"])
        details = json.loads(values.pop("details", "{}"))
        if not isinstance(details, dict) or set(details) - DETAIL_FIELDS:
            raise ValueError("Invalid experience details")
        # V1 records have no details; preserve that fact during a schema migration.
        values.update(details or {"schema_version": 1})
        return Experience.model_validate(values)
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise storage_error() from None


def _matches_keywords(query: str, *fields: str | None) -> bool:
    text_fields = [field.casefold() for field in fields if field is not None]
    return all(any(word in text for text in text_fields) for word in query.split())


def _matches_tags(stored_json: str, requested_json: str) -> bool:
    stored = json.loads(stored_json)
    if not isinstance(stored, list) or any(
        not isinstance(tag, str) or not tag.strip() for tag in stored
    ):
        # SQLite converts callback errors to OperationalError, handled by connect.
        raise ValueError("Invalid stored tags")
    actual = {tag.strip().casefold() for tag in stored}
    return set(json.loads(requested_json)).issubset(actual)


def _same_text(actual: str | None, expected: str) -> bool:
    return actual is not None and actual.casefold() == expected


def _uuid(value: str) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError("not text")
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise validation_error("Experience IDs must be valid UUID strings.") from None


def _merge_ids(values: list[str]) -> list[str]:
    if not isinstance(values, list) or not 2 <= len(values) <= 10:
        raise validation_error("Select between 2 and 10 distinct experiences.")
    ids = [_uuid(value) for value in values]
    if len(ids) != len(set(ids)):
        raise validation_error("Select distinct experiences.")
    return sorted(ids)


def _merge_key(ids: list[str]) -> str:
    return hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode("ascii")).hexdigest()


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _validate_filters(language=None, repository_id=None, scope=None):
    if language is not None and (not isinstance(language, str) or language not in {"java", "python"}):
        raise validation_error("language must be java or python.")
    if scope is not None and (not isinstance(scope, str) or scope not in {"global", "project"}):
        raise validation_error("scope must be global or project.")
    if repository_id is not None and (not isinstance(repository_id, str) or not repository_id.strip() or len(repository_id) > 200):
        raise validation_error("repository_id must be a nonblank string up to 200 characters.")
    try:
        (repository_id or "").encode("utf-8")
    except UnicodeEncodeError:
        raise validation_error("repository_id must be valid Unicode.") from None


def _insert(connection: sqlite3.Connection, saved: Experience) -> None:
    values = saved.model_dump(mode="json")
    details = {key: values[key] for key in DETAIL_FIELDS}
    connection.execute(
        f"INSERT INTO experiences ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (saved.id, saved.task_summary, saved.error_type, saved.root_cause, saved.solution,
         saved.verification, json.dumps(saved.tags, ensure_ascii=False), saved.source_task_id,
         saved.created_at.isoformat(timespec="microseconds"), json.dumps(details, ensure_ascii=False)),
    )


def _new_experience(data: ExperienceCreate) -> Experience:
    return Experience(**data.model_dump(), id=str(uuid4()), created_at=datetime.now(timezone.utc))


def _source_issues(entry: Experience) -> list[RepairIssue]:
    return entry.issues or [RepairIssue(error_type=entry.error_type or "UnclassifiedError",
                                        root_cause=entry.root_cause, solution=entry.solution)]


def _file_keys(issue: RepairIssue) -> frozenset[str]:
    # Models normalize newly saved and decoded records. Canonicalize again here
    # so conflict identity cannot depend on a path spelling in an older record.
    return frozenset(PurePosixPath(path).as_posix().casefold() for path in issue.files)


def _issue_key(issue: RepairIssue) -> tuple:
    return (_normalized(issue.error_type), _normalized(issue.root_cause), _normalized(issue.solution),
            tuple(sorted(_file_keys(issue))))


def _unique_text(values):
    seen = set()
    result = []
    for value in values:
        key = _normalized(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _preview(connection: sqlite3.Connection, ids: list[str]) -> dict:
    existing = connection.execute("SELECT merged_id FROM experience_merges WHERE merge_key=?", (_merge_key(ids),)).fetchone()
    if existing:
        row = connection.execute(f"SELECT {COLUMNS} FROM experiences WHERE id=?", (existing["merged_id"],)).fetchone()
        if row is None:
            raise storage_error()
        saved = decode_row(row)
        return {"can_merge": True, "conflicts": [], "source_ids": ids,
                "preview": ExperienceCreate.model_validate(saved.model_dump(exclude={"id", "created_at"}))}
    sources = []
    conflicts = []
    for source_id in ids:
        row = connection.execute(f"SELECT {COLUMNS} FROM experiences WHERE id=?", (source_id,)).fetchone()
        if row is None:
            conflicts.append(f"Experience was not found: {source_id}")
            continue
        sources.append(decode_row(row))
        if connection.execute("SELECT 1 FROM experience_merge_sources WHERE source_id=?", (source_id,)).fetchone():
            conflicts.append(f"Experience has already been merged: {source_id}")
    if conflicts:
        return {"can_merge": False, "conflicts": conflicts, "source_ids": ids, "preview": None}
    # Stable source chronology preserves each record's declared repair order.
    sources.sort(key=lambda source: (source.created_at, source.id))
    first = sources[0]
    for source in sources[1:]:
        if (source.language, source.scope, source.repository_id) != (first.language, first.scope, first.repository_id):
            conflicts.append("Experiences must have the same language, scope and repository.")
        if {_normalized(v) for v in source.applicability} != {_normalized(v) for v in first.applicability}:
            conflicts.append("Applicability conditions differ; keep these experiences separate.")
    issues = []
    evidence = []
    evidence_seen = set()
    issue_orders = []
    for source in sources:
        issue_orders.append(list(dict.fromkeys(_issue_key(issue) for issue in _source_issues(source))))
        for issue in _source_issues(source):
            duplicate = False
            for previous in issues:
                same_error = _normalized(issue.error_type) == _normalized(previous.error_type)
                overlap = not issue.files or not previous.files or bool(
                    _file_keys(issue) & _file_keys(previous))
                same_rule = (_normalized(issue.root_cause), _normalized(issue.solution)) == (
                    _normalized(previous.root_cause), _normalized(previous.solution))
                if same_error and overlap and not same_rule:
                    conflicts.append(f"Different repair rules overlap for {issue.error_type}; review the source records.")
                if _issue_key(issue) == _issue_key(previous):
                    duplicate = True
            if not duplicate:
                issues.append(issue)
        for item in source.evidence:
            key = (item.source_task_id, item.tool_call_id)
            if key in evidence_seen:
                previous = next(e for e in evidence if (e.source_task_id, e.tool_call_id) == key)
                if previous != item:
                    conflicts.append("The same verification call has inconsistent evidence.")
            else:
                evidence_seen.add(key)
                evidence.append(item)
    # Preserve each source's repair ordering. Opposite dependency orders cannot
    # be represented by one plan and must be reviewed rather than overwritten.
    pending = {_issue_key(issue): issue for issue in issues}
    predecessors = {key: set() for key in pending}
    for order in issue_orders:
        for before, after in zip(order, order[1:]):
            predecessors[after].add(before)
    ordered = []
    while pending:
        ready = next((key for key in pending if not predecessors[key] & pending.keys()), None)
        if ready is None:
            conflicts.append("Source experiences require conflicting repair orders.")
            break
        ordered.append(pending.pop(ready))
    if not pending:
        issues = ordered
    if conflicts:
        return {"can_merge": False, "conflicts": list(dict.fromkeys(conflicts)), "source_ids": ids, "preview": None}
    error_types = _unique_text(issue.error_type for issue in issues)
    revisions = {source.source_revision for source in sources}
    try:
        preview = ExperienceCreate(
            task_summary="Merged repair knowledge: " + " / ".join(_unique_text(source.task_summary for source in sources)),
            error_type=error_types[0] if len(error_types) == 1 else None,
            root_cause="\n".join(f"{i + 1}. {issue.root_cause}" for i, issue in enumerate(issues)),
            solution="\n".join(f"{i + 1}. {issue.solution}" for i, issue in enumerate(issues)),
            verification="\n".join(_unique_text(source.verification for source in sources)),
            tags=_unique_text(tag for source in sources for tag in source.tags),
            language=first.language, scope=first.scope, repository_id=first.repository_id,
            source_revision=next(iter(revisions)) if len(revisions) == 1 else None,
            applicability=first.applicability, issues=issues, evidence=evidence, merged_from=ids,
        )
    except ValidationError:
        return {"can_merge": False, "conflicts": ["Combined experience exceeds the structured record limits; merge fewer sources."],
                "source_ids": ids, "preview": None}
    return {"can_merge": True, "conflicts": [], "source_ids": ids, "preview": preview}


class ExperienceStore:
    """Inject a database file per store; connections are never shared or retained."""

    def __init__(self, path: str | Path) -> None:
        self.database_path = database_path(path)

    def save_experience(self, data: ExperienceCreate) -> Experience:
        if not isinstance(data, ExperienceCreate):
            raise validation_error("data must be an ExperienceCreate instance.")
        try:
            # Revalidate mutable nested fields that can bypass validate_assignment.
            validated = ExperienceCreate.model_validate(data.model_dump())
            json.dumps(validated.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        except (ValidationError, UnicodeError):
            raise validation_error("Experience data does not match the shared model.") from None
        if validated.merged_from:
            raise validation_error("Use merge_experiences to create records with merge provenance.")
        saved = _new_experience(validated)
        with connect(self.database_path) as connection:
            _insert(connection, saved)
        return saved

    def list_experiences(self, limit: int = 20, offset: int = 0, *, language: str | None = None,
                         repository_id: str | None = None, scope: str | None = None,
                         active_only: bool = False) -> list[Experience]:
        validate_integer(limit, "limit", 1)
        validate_integer(offset, "offset", 0)
        _validate_filters(language, repository_id, scope)
        if type(active_only) is not bool:
            raise validation_error("active_only must be a boolean.")
        with connect(self.database_path) as connection:
            if language is not None or repository_id is not None or scope is not None or active_only:
                where = " WHERE id NOT IN (SELECT source_id FROM experience_merge_sources)" if active_only else ""
                items = []
                matched = 0
                for row in connection.execute(f"SELECT {COLUMNS} FROM experiences{where}{ORDER}"):
                    item = decode_row(row)
                    if language is not None and item.language != language:
                        continue
                    if repository_id is not None and item.repository_id != repository_id:
                        continue
                    if scope is not None and item.scope != scope:
                        continue
                    if matched >= offset:
                        items.append(item)
                        if len(items) == limit:
                            break
                    matched += 1
                return items
            rows = connection.execute(
                f"SELECT {COLUMNS} FROM experiences{ORDER} LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [decode_row(row) for row in rows]

    def get_experience(self, experience_id: str) -> Experience | None:
        experience_id = _uuid(experience_id)
        with connect(self.database_path) as connection:
            row = connection.execute(f"SELECT {COLUMNS} FROM experiences WHERE id=?", (experience_id,)).fetchone()
            return decode_row(row) if row else None

    def retrieve_experiences(self, query: str = "", *, language: str, repository_id: str | None,
                             error_types: list[str], limit: int = 5) -> list[Experience]:
        validate_integer(limit, "limit", 1)
        _validate_filters(language, repository_id)
        if language is None:
            raise validation_error("language must be java or python.")
        if not isinstance(query, str) or not isinstance(error_types, list) or any(
            not isinstance(value, str) or not value.strip() for value in error_types):
            raise validation_error("query and error_types must contain text.")
        try:
            " ".join([query, *error_types]).encode("utf-8")
        except UnicodeEncodeError:
            raise validation_error("Retrieval text must be valid Unicode.") from None
        wanted = {_normalized(error) for error in error_types}
        keywords = set(query.casefold().split())
        ranked = []
        with connect(self.database_path) as connection:
            for row in connection.execute(f"SELECT {COLUMNS} FROM experiences WHERE id NOT IN "
                                          f"(SELECT source_id FROM experience_merge_sources){ORDER}"):
                item = decode_row(row)
                if item.language != language or item.scope == "project" and item.repository_id != repository_id:
                    continue
                actual = {_normalized(issue.error_type) for issue in item.issues}
                if item.error_type:
                    actual.add(_normalized(item.error_type))
                overlap = len(actual & wanted)
                if wanted and not overlap:
                    continue
                text = " ".join([item.task_summary, item.root_cause, item.solution,
                                 *item.applicability, *item.tags]).casefold()
                keyword_score = sum(word in text for word in keywords)
                candidate = ((int(item.scope == "project"), overlap, keyword_score,
                              item.created_at, item.id), item)
                if len(ranked) < limit:
                    heapq.heappush(ranked, candidate)
                elif candidate[0] > ranked[0][0]:
                    heapq.heapreplace(ranked, candidate)
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in ranked[:limit]]

    def preview_merge(self, experience_ids: list[str]) -> dict:
        ids = _merge_ids(experience_ids)
        with connect(self.database_path) as connection:
            connection.execute("BEGIN")
            return _preview(connection, ids)

    def merge_experiences(self, experience_ids: list[str]) -> Experience:
        ids = _merge_ids(experience_ids)
        key = _merge_key(ids)
        with connect(self.database_path) as connection:
            # One write transaction protects all source checks, insertion and links.
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT merged_id FROM experience_merges WHERE merge_key=?", (key,)).fetchone()
            if existing:
                row = connection.execute(f"SELECT {COLUMNS} FROM experiences WHERE id=?", (existing["merged_id"],)).fetchone()
                if row is None:
                    raise storage_error()
                return decode_row(row)
            preview = _preview(connection, ids)
            if not preview["can_merge"]:
                raise validation_error(" ".join(preview["conflicts"]))
            saved = _new_experience(preview["preview"])
            _insert(connection, saved)
            connection.execute("INSERT INTO experience_merges (merge_key, merged_id) VALUES (?, ?)", (key, saved.id))
            connection.executemany("INSERT INTO experience_merge_sources (source_id, merged_id) VALUES (?, ?)",
                                   [(source_id, saved.id) for source_id in ids])
            return saved

    def search_experiences(
        self,
        query: str,
        tags: list[str] | None = None,
        error_type: str | None = None,
        limit: int = 5,
    ) -> list[Experience]:
        validate_integer(limit, "limit", 1)
        if not isinstance(query, str):
            raise validation_error("query must be a string.")
        if tags is not None and (
            not isinstance(tags, list)
            or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
        ):
            raise validation_error("tags must be a list of nonblank strings.")
        if error_type is not None and (
            not isinstance(error_type, str) or not error_type.strip()
        ):
            raise validation_error("error_type must be a nonblank string or None.")
        try:
            for text in [query, error_type or "", *(tags or [])]:
                text.encode("utf-8")
        except UnicodeEncodeError:
            raise validation_error("Search text must be valid Unicode.") from None

        conditions: list[str] = ["id NOT IN (SELECT source_id FROM experience_merge_sources)"]
        parameters: list[str | int] = []
        if query.strip():
            conditions.append(
                "matches_keywords(?, task_summary, error_type, root_cause, solution, verification)"
            )
            parameters.append(query.casefold())
        if tags:
            conditions.append("matches_tags(tags, ?)")
            parameters.append(json.dumps(sorted({tag.strip().casefold() for tag in tags})))
        if error_type is not None:
            conditions.append("same_text(error_type, ?)")
            parameters.append(error_type.strip().casefold())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        parameters.append(limit)
        with connect(self.database_path) as connection:
            # Python callbacks avoid depending on optional SQLite JSON extensions
            # and provide Unicode casefold instead of SQLite's ASCII-only NOCASE.
            connection.create_function("matches_keywords", 6, _matches_keywords, deterministic=True)
            connection.create_function("matches_tags", 2, _matches_tags, deterministic=True)
            connection.create_function("same_text", 2, _same_text, deterministic=True)
            rows = connection.execute(
                f"SELECT {COLUMNS} FROM experiences{where}{ORDER} LIMIT ?", parameters
            ).fetchall()
            return [decode_row(row) for row in rows]
