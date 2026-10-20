"""Synchronous experience persistence using the existing shared contracts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from backend.shared import ApplicationError, ErrorCode, Experience, ExperienceCreate

from .database import connect, database_path, storage_error


COLUMNS = (
    "id, task_summary, error_type, root_cause, solution, verification, "
    "tags, source_task_id, created_at"
)
ORDER = " ORDER BY created_at DESC, id DESC"


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
        except ValidationError:
            raise validation_error("Experience data does not match the shared model.") from None
        saved = Experience(
            **validated.model_dump(),
            id=str(uuid4()),
            created_at=datetime.now(timezone.utc),
        )
        with connect(self.database_path) as connection:
            connection.execute(
                f"INSERT INTO experiences ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    saved.id, saved.task_summary, saved.error_type, saved.root_cause,
                    saved.solution, saved.verification,
                    json.dumps(saved.tags, ensure_ascii=False), saved.source_task_id,
                    saved.created_at.isoformat(timespec="microseconds"),
                ),
            )
        return saved

    def list_experiences(self, limit: int = 20, offset: int = 0) -> list[Experience]:
        validate_integer(limit, "limit", 1)
        validate_integer(offset, "offset", 0)
        with connect(self.database_path) as connection:
            rows = connection.execute(
                f"SELECT {COLUMNS} FROM experiences{ORDER} LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [decode_row(row) for row in rows]

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

        conditions: list[str] = []
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
