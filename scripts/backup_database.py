"""Create a consistent SQLite backup (including committed WAL) without overwriting files."""
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def backup(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if not source.is_file():
        raise FileNotFoundError("Configured database does not exist; no backup was created.")
    manifest = destination.with_suffix(destination.suffix + ".json")
    if destination == source or destination.exists() or manifest.exists():
        raise FileExistsError("Backup destination already exists; choose a new filename.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
        if original.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Source database integrity check failed.")
        with destination.open("xb"):
            pass
        with sqlite3.connect(destination) as copied:
            original.backup(copied)
            if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup verification failed; do not use the destination.")
    report = {"source": str(source), "backup": str(destination), "integrity_check": "ok",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
    with manifest.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.source is None:
        from backend.config import Settings
        args.source = Settings.from_env().database_path
    try:
        result = backup(args.source, args.output)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Backup not completed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
