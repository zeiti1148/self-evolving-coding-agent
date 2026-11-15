"""Synchronous experience persistence using the existing shared contracts."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from pydantic import ValidationError

from backend.shared import ApplicationError, ErrorCode, Experience, ExperienceCreate, RepairIssue

from .database import connect, database_path, storage_error
from .policy import RetrievalContext, rank_references


COLUMNS = (
    "id, task_summary, error_type, root_cause, solution, verification, "
    "tags, source_task_id, created_at, details"
)
ORDER = " ORDER BY created_at DESC, id DESC"
SUPERSEDED = "SELECT source_id FROM experience_merge_sources UNION SELECT previous_id FROM experience_publication_revisions"
RETIRED = "SELECT experience_id FROM experience_retirements"
QUARANTINED = ("SELECT left_id FROM experience_conflicts WHERE status='pending' UNION "
               "SELECT right_id FROM experience_conflicts WHERE status='pending'")
from .knowledge import BLOCKED
UNCHECKED = "SELECT s.experience_id FROM knowledge_sources s JOIN experiences e ON e.id=s.experience_id WHERE json_extract(e.details, '$.schema_version')>=3 AND json_extract(e.details, '$.reuse_status')='active' GROUP BY s.experience_id HAVING sum(s.rule_id NOT IN (" + BLOCKED + "))=0"
UNAVAILABLE = f"{SUPERSEDED} UNION {RETIRED} UNION {QUARANTINED} UNION {UNCHECKED}"


def unavailable_source_query(connection) -> str:
    """Same exclusion policy for immutable snapshots made before schema upgrades."""
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    queries = [query for table, query in (
        ("experience_merge_sources", "SELECT source_id FROM experience_merge_sources"),
        ("experience_publication_revisions", "SELECT previous_id FROM experience_publication_revisions"),
        ("experience_retirements", RETIRED), ("experience_conflicts", QUARANTINED),
        ("knowledge_sources", UNCHECKED),
    ) if table in tables]
    return " UNION ".join(queries) or "SELECT id FROM experiences WHERE 0"


DETAIL_FIELDS = frozenset({"schema_version", "language", "scope", "repository_id", "source_revision",
                           "applicability", "issues", "evidence", "merged_from", "batch_id", "reuse_status",
                           "derived_from", "supersedes_id", "required_capabilities", "compatibility_key", "constraints"})


def validation_error(message: str) -> ApplicationError:
    return ApplicationError(ErrorCode.EXPERIENCE_VALIDATION_ERROR, message)


def validate_integer(value: int, name: str, minimum: int) -> None:
    # sqlite3 parameters must fit in a signed 64-bit integer; bool is not a limit.
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise validation_error(f"参数 {name} 必须是 {minimum} 到 2^63-1 之间的整数。")


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
        raise validation_error("经验编号必须是有效的 UUID。") from None


def _merge_ids(values: list[str]) -> list[str]:
    if not isinstance(values, list) or not 2 <= len(values) <= 10:
        raise validation_error("请选择 2 至 10 条不同的经验。")
    ids = [_uuid(value) for value in values]
    if len(ids) != len(set(ids)):
        raise validation_error("请选择不同的经验。")
    return sorted(ids)


def _merge_key(ids: list[str]) -> str:
    return hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode("ascii")).hexdigest()


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _validate_filters(language=None, repository_id=None, scope=None):
    if language is not None and (not isinstance(language, str) or language not in {"java", "python", "go", "cpp"}):
        raise validation_error("语言必须为 java 或 python。")
    if scope is not None and (not isinstance(scope, str) or scope not in {"global", "project"}):
        raise validation_error("经验范围必须为通用（global）或仓库专属（project）。")
    if repository_id is not None and (not isinstance(repository_id, str) or not repository_id.strip() or len(repository_id) > 200):
        raise validation_error("仓库标识不能为空，且不能超过 200 个字符。")
    try:
        (repository_id or "").encode("utf-8")
    except UnicodeEncodeError:
        raise validation_error("仓库标识包含无效字符。") from None


def _insert(connection: sqlite3.Connection, saved: Experience) -> None:
    values = saved.model_dump(mode="json")
    details = {key: values[key] for key in DETAIL_FIELDS}
    connection.execute(
        f"INSERT INTO experiences ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (saved.id, saved.task_summary, saved.error_type, saved.root_cause, saved.solution,
         saved.verification, json.dumps(saved.tags, ensure_ascii=False), saved.source_task_id,
         saved.created_at.isoformat(timespec="microseconds"), json.dumps(details, ensure_ascii=False)),
    )
    from .consolidation import index_experience
    index_experience(connection, saved)
    from .knowledge import ingest
    ingest(connection, saved)
    from .automation import enqueue
    enqueue(connection, saved)


def _new_experience(data: ExperienceCreate) -> Experience:
    return Experience(**data.model_dump(), id=str(uuid4()), created_at=datetime.now(timezone.utc))


def _source_issues(entry: Experience) -> list[RepairIssue]:
    return entry.issues or [RepairIssue(error_type=entry.error_type or "UnclassifiedError",
                                        root_cause=entry.root_cause, solution=entry.solution)]


def _file_keys(issue: RepairIssue) -> frozenset[str]:
    # Models normalize newly saved and decoded records. Canonicalize again here
    # so conflict identity cannot depend on a path spelling in an older record.
    return frozenset(PurePosixPath(path).as_posix() for path in issue.files)


def _issue_key(issue: RepairIssue) -> tuple:
    # Prose may contain case-sensitive identifiers and significant string whitespace.
    return (issue.error_type, issue.root_cause, issue.solution,
            tuple(sorted(_file_keys(issue))), tuple(sorted(issue.symbols)))


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
            conflicts.append(f"未找到经验：{source_id}")
            continue
        sources.append(decode_row(row))
        from .knowledge import source_requires_review
        if connection.execute(f"SELECT 1 WHERE ? IN ({UNAVAILABLE})", (source_id,)).fetchone() or source_requires_review(connection,source_id):
            conflicts.append(f"经验已被合并、替代或因冲突暂停复用：{source_id}")
    if conflicts:
        from .merging import detail
        return {"can_merge": False, "conflicts": conflicts, "source_ids": ids, "preview": None,
                "conflict_details": [detail("source_unavailable", message, ids, "source_ids",
                    "刷新列表，检查来源图或处理待审阅冲突。") for message in conflicts]}
    from .merging import build_preview
    return build_preview(sources, ids)


class ExperienceStore:
    """Inject a database file per store; connections are never shared or retained."""

    def __init__(self, path: str | Path) -> None:
        self.database_path = database_path(path)

    def save_experience(self, data: ExperienceCreate) -> Experience:
        if not isinstance(data, ExperienceCreate):
            raise validation_error("经验数据必须符合 ExperienceCreate 模型。")
        try:
            # Revalidate mutable nested fields that can bypass validate_assignment.
            validated = ExperienceCreate.model_validate(data.model_dump())
            json.dumps(validated.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        except (ValidationError, UnicodeError):
            raise validation_error("经验数据不符合公共数据模型。") from None
        if validated.merged_from or validated.derived_from or validated.supersedes_id or validated.batch_id:
            raise validation_error("请通过批量保存、合并或发布操作生成来源信息。")
        if validated.scope == "global" and validated.reuse_status == "active":
            raise validation_error("通用经验必须显式发布后才能启用。")
        saved = _new_experience(validated)
        with connect(self.database_path) as connection:
            _insert(connection, saved)
        return saved

    def save_experiences(self, items: list[ExperienceCreate], *, source_task_id: str) -> list[Experience]:
        from .batches import save_batch
        return save_batch(self.database_path, items, source_task_id=source_task_id)

    def initialize(self):
        from .database import initialize
        initialize(self.database_path)

    def consolidate_experiences(self, *, language: str, repository_id: str, cursor: str | None = None):
        from .jobs import drain
        _validate_filters(language, repository_id)
        if language is None or repository_id is None:
            raise validation_error("自动整理需要指定语言和仓库。")
        if cursor is not None and (not isinstance(cursor, str) or not re.fullmatch(r"[0-9a-f]{64}", cursor)):
            raise validation_error("自动整理游标无效。")
        return drain(self.database_path, language=language, repository_id=repository_id)

    def list_conflicts(self, *, language=None, repository_id=None, status="pending", limit=20, offset=0):
        from .conflicts import list_conflicts
        _validate_filters(language, repository_id)
        validate_integer(limit, "limit", 1)
        validate_integer(offset, "offset", 0)
        if limit > 100 or not isinstance(status, str) or status not in {"pending", "resolved", "obsolete"}:
            raise validation_error("冲突状态无效或分页数量超过 100。")
        from .reviews import list_reviews
        count = offset + limit
        current = list_reviews(self.database_path, language, repository_id, status, count, 0)
        legacy = list_conflicts(self.database_path, language, repository_id, status, count, 0)
        return sorted([*current, *legacy], key=lambda item: (item.created_at, item.id), reverse=True)[offset:count]

    def resolve_conflict(self, conflict_id, resolution):
        from .conflicts import resolve_conflict
        from .reviews import resolve
        with connect(self.database_path) as connection:
            current = connection.execute('SELECT 1 FROM knowledge_relations WHERE id=?', (_uuid(conflict_id),)).fetchone()
        return (resolve if current else resolve_conflict)(self.database_path, _uuid(conflict_id), resolution)

    def retrieve_model_references(self, query='', **context):
        from .retrieval import retrieve
        self.initialize()
        return retrieve(self.database_path, query, **context)

    def reopen_conflict(self, identifier, version, note):
        from .reviews import reopen
        from .conflicts import reopen_legacy
        with connect(self.database_path) as connection:
            current = connection.execute('SELECT 1 FROM knowledge_relations WHERE id=?', (_uuid(identifier),)).fetchone()
        return (reopen if current else reopen_legacy)(self.database_path, _uuid(identifier), version, note)

    def review_history(self, identifier):
        from .reviews import history
        from .conflicts import legacy_history
        return history(self.database_path, _uuid(identifier)) or legacy_history(self.database_path, _uuid(identifier))

    def experience_maintenance(self, identifier):
        from .knowledge import source_status
        return source_status(self.database_path, _uuid(identifier))


    def preview_promotion(self, source_id: str):
        from .promotion import preview_promotion
        return preview_promotion(self.database_path, source_id)

    def publish_global(self, source_id: str, draft, *, supersedes_id: str | None = None):
        from .promotion import publish_global
        return publish_global(self.database_path, source_id, draft, supersedes_id=supersedes_id)

    def get_provenance(self, experience_id: str, *, max_nodes: int = 100, max_depth: int = 10):
        from .provenance import get_provenance
        return get_provenance(self.database_path, experience_id, max_nodes=max_nodes, max_depth=max_depth)

    def list_experiences(self, limit: int = 20, offset: int = 0, *, language: str | None = None,
                         repository_id: str | None = None, scope: str | None = None,
                         active_only: bool = False, reuse_status: str | None = None,
                         source_task_id: str | None = None) -> list[Experience]:
        validate_integer(limit, "limit", 1)
        validate_integer(offset, "offset", 0)
        _validate_filters(language, repository_id, scope)
        if type(active_only) is not bool:
            raise validation_error("仅显示有效记录的参数必须为布尔值。")
        if reuse_status is not None and reuse_status not in {"active", "candidate", "legacy_unreviewed"}:
            raise validation_error("无法识别该复用状态。")
        if source_task_id is not None:
            source_task_id = _uuid(source_task_id)
        with connect(self.database_path) as connection:
            if any(value is not None for value in (language, repository_id, scope, reuse_status, source_task_id)) or active_only:
                where = f" WHERE id NOT IN ({UNAVAILABLE})" if active_only else ""
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
                    if reuse_status is not None and item.reuse_status != reuse_status:
                        continue
                    if source_task_id is not None and item.source_task_id != source_task_id:
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
                             error_types: list[str], limit: int = 5,
                             capabilities: list[str] | None = None) -> list[Experience]:
        validate_integer(limit, "limit", 1)
        _validate_filters(language, repository_id)
        if language is None:
            raise validation_error("语言必须为 java 或 python。")
        if not isinstance(query, str) or not isinstance(error_types, list) or any(
            not isinstance(value, str) or not value.strip() for value in error_types):
            raise validation_error("检索内容和异常类型必须为文本。")
        try:
            " ".join([query, *error_types]).encode("utf-8")
        except UnicodeEncodeError:
            raise validation_error("检索文本包含无效字符。") from None
        if capabilities is not None and (not isinstance(capabilities, list) or any(
                not isinstance(value, str) or not value.strip() for value in capabilities)):
            raise validation_error("能力列表必须包含非空标识。")
        context = RetrievalContext(language, repository_id, frozenset(capabilities or []))
        with connect(self.database_path) as connection:
            entries = (decode_row(row) for row in connection.execute(
                f"SELECT {COLUMNS} FROM experiences WHERE id NOT IN ({UNAVAILABLE}){ORDER}"))
            return rank_references(entries, context, query=query, error_types=error_types, limit=limit)

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
            raise validation_error("查询内容必须为文本。")
        if tags is not None and (
            not isinstance(tags, list)
            or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
        ):
            raise validation_error("标签必须是非空文本组成的列表。")
        if error_type is not None and (
            not isinstance(error_type, str) or not error_type.strip()
        ):
            raise validation_error("异常类型必须为非空文本或空值。")
        try:
            for text in [query, error_type or "", *(tags or [])]:
                text.encode("utf-8")
        except UnicodeEncodeError:
            raise validation_error("搜索文本包含无效字符。") from None

        conditions: list[str] = [f"id NOT IN ({UNAVAILABLE})"]
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
