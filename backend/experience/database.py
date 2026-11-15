"""SQLite connection ownership and file configuration; no import-time I/O."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator
from threading import RLock

from backend.shared import ApplicationError, ErrorCode


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = "sqlite:///./data/app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiences (
    id TEXT PRIMARY KEY NOT NULL,
    task_summary TEXT NOT NULL,
    error_type TEXT,
    root_cause TEXT NOT NULL,
    solution TEXT NOT NULL,
    verification TEXT NOT NULL,
    tags TEXT NOT NULL,
    source_task_id TEXT,
    created_at TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}'
)
"""


def storage_error() -> ApplicationError:
    return ApplicationError(
        ErrorCode.EXPERIENCE_STORAGE_ERROR,
        "经验存储不可用或包含无效数据。",
    )


def database_path(value: str | Path) -> Path:
    """Resolve a file path independently of the caller's working directory."""
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise storage_error()
    if str(value) == ":memory:" or "\x00" in str(value):
        raise storage_error()
    try:
        path = Path(value)
        if not path.is_absolute():
            path = REPOSITORY_ROOT / path
        return path.resolve()
    except (OSError, ValueError, RuntimeError):
        raise storage_error() from None


def configured_database_path() -> Path:
    """Support the existing plain sqlite:///file configuration (no URI options)."""
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise storage_error()
    return database_path(url[len(prefix):])


_initialization_lock = RLock()
_initialized = {}


def initialize(path: Path) -> None:
    try:
        identity = (path.stat().st_dev, path.stat().st_ino) if path.exists() else None
        if identity is not None and _initialized.get(path) == identity:
            return
        with _initialization_lock:
            identity = (path.stat().st_dev, path.stat().st_ino) if path.exists() else None
            if identity is not None and _initialized.get(path) == identity:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path, timeout=5.0)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute('PRAGMA foreign_keys=ON')
                with connection:
                    # Serialize schema inspection and migration across first-use workers.
                    # Old nine-column databases are migrated without rewriting records.
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(SCHEMA)
                    columns = {row["name"] for row in connection.execute("PRAGMA table_info(experiences)")}
                    if "details" not in columns:
                        connection.execute("ALTER TABLE experiences ADD COLUMN details TEXT NOT NULL DEFAULT '{}'")
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS experiences_created_at_idx "
                        "ON experiences(created_at DESC, id DESC)"
                    )
                    connection.execute("""CREATE TABLE IF NOT EXISTS experience_merges (
                        merge_key TEXT PRIMARY KEY NOT NULL,
                        merged_id TEXT NOT NULL UNIQUE REFERENCES experiences(id)
                    )""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS experience_merge_sources (
                        source_id TEXT PRIMARY KEY NOT NULL REFERENCES experiences(id),
                        merged_id TEXT NOT NULL REFERENCES experiences(id),
                        CHECK (source_id != merged_id)
                    )""")
                    connection.execute("CREATE INDEX IF NOT EXISTS experience_merge_target_idx ON experience_merge_sources(merged_id)")
                    connection.execute("""CREATE TABLE IF NOT EXISTS experience_batches (
                        batch_id TEXT PRIMARY KEY NOT NULL,
                        source_task_id TEXT NOT NULL UNIQUE,
                        payload_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS experience_batch_items (
                        batch_id TEXT NOT NULL REFERENCES experience_batches(batch_id),
                        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                        experience_id TEXT NOT NULL UNIQUE REFERENCES experiences(id),
                        PRIMARY KEY(batch_id, ordinal)
                    )""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS experience_promotions (
                        promotion_key TEXT PRIMARY KEY NOT NULL,
                        source_id TEXT NOT NULL REFERENCES experiences(id),
                        global_id TEXT NOT NULL UNIQUE REFERENCES experiences(id)
                    )""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS experience_publication_heads (
                        source_id TEXT PRIMARY KEY NOT NULL REFERENCES experiences(id),
                        global_id TEXT NOT NULL UNIQUE REFERENCES experiences(id)
                    )""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS experience_publication_revisions (
                        previous_id TEXT PRIMARY KEY NOT NULL REFERENCES experiences(id),
                        new_id TEXT NOT NULL UNIQUE REFERENCES experiences(id),
                        CHECK(previous_id != new_id)
                    )""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS experience_consolidation_index (
                        experience_id TEXT PRIMARY KEY NOT NULL REFERENCES experiences(id),
                        partition_key TEXT NOT NULL,
                        language TEXT NOT NULL,
                        repository_id TEXT NOT NULL
                    )""")
                    connection.execute("CREATE INDEX IF NOT EXISTS experience_partition_idx "
                                       "ON experience_consolidation_index(partition_key)")
                    connection.execute("CREATE INDEX IF NOT EXISTS experience_consolidation_scope_idx "
                                       "ON experience_consolidation_index(language, repository_id, partition_key)")
                    connection.execute("""CREATE TABLE IF NOT EXISTS experience_conflicts (
                        id TEXT PRIMARY KEY NOT NULL,
                        left_id TEXT NOT NULL REFERENCES experiences(id),
                        right_id TEXT NOT NULL REFERENCES experiences(id),
                        details TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'resolved', 'obsolete')),
                        version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
                        created_at TEXT NOT NULL,
                        resolved_at TEXT,
                        resolution TEXT CHECK(resolution IN ('prefer_source', 'keep_separate')),
                        preferred_source_id TEXT REFERENCES experiences(id),
                        note TEXT,
                        UNIQUE(left_id, right_id), CHECK(left_id < right_id)
                    )""")
                    connection.execute("CREATE INDEX IF NOT EXISTS experience_conflict_left_idx ON experience_conflicts(left_id, status)")
                    connection.execute("CREATE INDEX IF NOT EXISTS experience_conflict_right_idx ON experience_conflicts(right_id, status)")
                    connection.execute("CREATE INDEX IF NOT EXISTS experience_conflict_status_idx ON experience_conflicts(status, created_at DESC, id DESC)")
                    connection.execute("""CREATE TABLE IF NOT EXISTS experience_retirements (
                        experience_id TEXT PRIMARY KEY NOT NULL REFERENCES experiences(id),
                        conflict_id TEXT NOT NULL REFERENCES experience_conflicts(id)
                    )""")
                    from .maintenance_schema import migrate, wal_supported
                    migrate(connection)
                if os.environ.get('EXPERIENCE_SQLITE_WAL', 'false').lower() == 'true' and wal_supported():
                    connection.execute('PRAGMA journal_mode=WAL')
            _initialized[path] = (path.stat().st_dev, path.stat().st_ino)
    except (sqlite3.Error, OSError):
        raise storage_error() from None


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    """First use initializes once; subsequent reads acquire no reserved write lock."""
    initialize(path)
    try:
        with closing(sqlite3.connect(path, timeout=5.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            with connection:
                yield connection
    except (sqlite3.Error, OSError):
        raise storage_error() from None


@contextmanager
def read_only(path: Path) -> Iterator[sqlite3.Connection]:
    """Never migrate or modify a frozen database, even on first access in a process."""
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5.0)) as connection:
            connection.row_factory=sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            yield connection
    except (sqlite3.Error,OSError):
        raise storage_error() from None
