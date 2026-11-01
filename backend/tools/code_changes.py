"""Bounded snapshots kept outside the writable task tree; no model decisions."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile

from backend.shared.code_diff import CodeSnapshot, StoredCodeChange
from .workspace import ignored_path, inventory, protected, reject_links

MAX_BYTES = 200_000
MAX_TOTAL_BYTES = 2_000_000
MAX_FILES = 100
MAX_SNAPSHOT_BYTES = 32_000_000


def code_path(path: str) -> bool:
    parsed = PurePosixPath(path)
    return (bool(path) and parsed.as_posix() == path and not parsed.is_absolute()
            and not any(c in path for c in ('\\', ':')) and not any(ord(c) < 32 for c in path) and '..' not in parsed.parts
            and parsed.suffix.casefold() in {'.java', '.py'} and not ignored_path(path)
            and not protected({path: ''}))


def read_code(path: Path, expected: str | None = None) -> str:
    reject_links(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Not a regular code file')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or b'\x00' in raw:
        raise ValueError('Code is too large or binary')
    if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Code does not match recorded hash')
    return raw.decode('utf-8')


def capture_before(root: Path, baseline: dict[str, str]) -> dict[str, str | None]:
    contents: dict[str, str | None] = {}
    remaining = MAX_TOTAL_BYTES
    for path, digest in sorted(baseline.items()):
        if not code_path(path):
            continue
        contents[path] = None
        try:
            if remaining <= 0 or (root / path).stat().st_size > min(MAX_BYTES, remaining):
                continue
            content = read_code(root / path, digest)
            size = len(content.encode('utf-8'))
            if size <= remaining:
                contents[path] = content
                remaining -= size
        except (OSError, ValueError):
            continue
    return contents


def make_change(path: str, before_hash: str | None, after_hash: str | None,
                before: str | None, after: str | None, reason: str | None = None) -> StoredCodeChange:
    available = reason is None and (before_hash is None or before is not None) and (after_hash is None or after is not None)
    return StoredCodeChange(path=path, before_sha256=before_hash, after_sha256=after_hash,
                            change_type='added' if before_hash is None else 'deleted' if after_hash is None else 'modified',
                            before_content=before if available else None, after_content=after if available else None,
                            available=available, unavailable_reason=None if available else reason or '原始内容缺失、过大或编码不受支持，无法可靠对比。')


def snapshot_path(manager, record) -> Path:
    target = manager.root / '.records' / (record.path.name + '.changes.json')
    reject_links(target)
    return target


def freeze_changes(manager, record) -> None:
    with record.lock:
        target = snapshot_path(manager, record)
        if target.exists():
            return  # Never replace a completed task's historical evidence.
        current = inventory(record.path)
        names = sorted(path for path in current.keys() | record.baseline.keys()
                       if code_path(path) and current.get(path) != record.baseline.get(path))
        files = []
        remaining = MAX_TOTAL_BYTES
        for path in names[:MAX_FILES]:
            before = (record.before_code or {}).get(path)
            after = None
            if path in current:
                try:
                    content = read_code(manager.resolve(record, path), current[path])
                    size = len(content.encode('utf-8'))
                    if size <= remaining:
                        after = content
                        remaining -= size
                except (OSError, ValueError):
                    pass
            files.append(make_change(path, record.baseline.get(path), current.get(path), before, after))
        data = CodeSnapshot(task_id=record.path.name, files=files, omitted_files=max(0, len(names) - MAX_FILES))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data.model_dump_json())
                stream.flush()
                os.fsync(stream.fileno())
            reject_links(target)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def read_snapshot(manager, record) -> CodeSnapshot | None:
    target = snapshot_path(manager, record)
    if not target.exists():
        return None
    with target.open('rb') as stream:
        raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError('Snapshot exceeds limit')
    data = CodeSnapshot.model_validate_json(raw)
    if data.task_id != record.path.name or len({f.path for f in data.files}) != len(data.files):
        raise ValueError('Snapshot belongs to another task or contains duplicate files')
    for file in data.files:
        if not code_path(file.path) or file.before_sha256 != record.baseline.get(file.path):
            raise ValueError('Snapshot does not match task baseline')
        expected_type = 'added' if file.before_sha256 is None else 'deleted' if file.after_sha256 is None else 'modified'
        if file.change_type != expected_type or file.before_sha256 == file.after_sha256:
            raise ValueError('Invalid change metadata')
        if file.available:
            for content, digest in [(file.before_content, file.before_sha256), (file.after_content, file.after_sha256)]:
                if (content is None) != (digest is None) or content is not None and hashlib.sha256(content.encode('utf-8')).hexdigest() != digest:
                    raise ValueError('Snapshot content hash mismatch')
    return data
