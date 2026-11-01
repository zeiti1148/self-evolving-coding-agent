import sqlite3

import pytest

from scripts.backup_database import backup


def test_backup_includes_committed_wal_and_is_verified_without_overwrite(tmp_path):
    source, target = tmp_path / "live.db", tmp_path / "backup.db"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE facts (value TEXT)")
        db.execute("INSERT INTO facts VALUES ('committed')")
        db.commit()
        report = backup(source, target)
        assert report["integrity_check"] == "ok"
        with sqlite3.connect(target) as copied:
            assert copied.execute("SELECT value FROM facts").fetchone()[0] == "committed"
        with pytest.raises(FileExistsError):
            backup(source, target)
    assert target.with_suffix(".db.json").is_file()


def test_missing_database_is_not_created_as_a_backup(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup(tmp_path / "missing.db", tmp_path / "backup.db")
    assert list(tmp_path.iterdir()) == []
