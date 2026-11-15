"""Durable conflict review with version checks and immutable source records."""
import json
from datetime import datetime, timezone

from pydantic import ValidationError

from backend.shared import ApplicationError, ConflictResolution, ErrorCode, ExperienceConflict
from .database import connect, storage_error
from .store import COLUMNS, RETIRED, SUPERSEDED, decode_row, validation_error


def _read(connection, row):
    try:
        values = dict(row)
        sources = []
        for field in ("left_id", "right_id"):
            source = connection.execute(f"SELECT {COLUMNS} FROM experiences WHERE id=?", (values.pop(field),)).fetchone()
            if source is None:
                raise storage_error()
            sources.append(decode_row(source))
        values["details"] = json.loads(values["details"])
        return ExperienceConflict(**values, sources=sources)
    except (ValueError, TypeError, RecursionError):
        raise storage_error() from None


def list_conflicts(path, language, repository_id, status, limit, offset):
    filters, params = ["c.status=?"], [status]
    if language is not None:
        filters.append("i.language=?")
        params.append(language)
    if repository_id is not None:
        filters.append("i.repository_id=?")
        params.append(repository_id)
    with connect(path) as connection:
        connection.execute("BEGIN")
        rows = connection.execute("SELECT c.* FROM experience_conflicts c JOIN experience_consolidation_index i "
            "ON i.experience_id=c.left_id WHERE " + " AND ".join(filters) +
            " ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        return [_read(connection, row) for row in rows]


def resolve_conflict(path, identifier, resolution):
    if not isinstance(resolution, ConflictResolution):
        raise validation_error("冲突处理需要符合 ConflictResolution 模型。")
    try:
        resolution = ConflictResolution.model_validate(resolution.model_dump())
        resolution.note.encode("utf-8")
    except (ValidationError, UnicodeError):
        raise validation_error("冲突处理内容无效。") from None
    if resolution.resolution not in {'prefer_source','keep_separate'}:
        raise validation_error("旧版整条经验审阅仅支持选择来源或暂停两方，请使用新规则审阅确认语义关系。")
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM experience_conflicts WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ApplicationError(ErrorCode.EXPERIENCE_NOT_FOUND, "未找到该冲突。")
        value = _read(connection, row)
        # A retry of the exact committed decision returns its existing audit record.
        if (value.status == "resolved" and value.version == resolution.version + 1
                and all(getattr(value, key) == getattr(resolution, key)
                        for key in ("resolution", "preferred_source_id", "note"))):
            return value
        if value.status != "pending" or value.version != resolution.version:
            raise ApplicationError(ErrorCode.EXPERIENCE_CONFLICT, "冲突已被处理或版本已变化，请刷新后重试。")
        ids = [source.id for source in value.sources]
        if resolution.preferred_source_id is not None and resolution.preferred_source_id not in ids:
            raise validation_error("只能选择本次冲突中的来源经验。")
        if any(connection.execute(f"SELECT 1 WHERE ? IN ({SUPERSEDED} UNION {RETIRED})", (identity,)).fetchone()
               for identity in ids):
            raise ApplicationError(ErrorCode.EXPERIENCE_CONFLICT, "来源已被替代或暂停复用，请刷新后重试。")
        now = datetime.now(timezone.utc).isoformat()
        connection.execute("UPDATE experience_conflicts SET status='resolved', version=version+1, resolved_at=?, "
            "resolution=?, preferred_source_id=?, note=? WHERE id=? AND version=?",
            (now, resolution.resolution, resolution.preferred_source_id, resolution.note, identifier, resolution.version))
        connection.execute('INSERT INTO legacy_review_decisions(review_id,version,action,data,created_at) VALUES(?,?,?,?,?)',
                           (identifier,resolution.version+1,'resolve',json.dumps(resolution.model_dump(),ensure_ascii=False),now))
        for source_id in ids:
            if source_id != resolution.preferred_source_id:
                connection.execute("INSERT INTO experience_retirements VALUES (?, ?)", (source_id, identifier))
        # Other reviews involving a retired source no longer block the surviving
        # alternatives. Preserve those reviews as obsolete audit records.
        connection.execute("UPDATE experience_conflicts SET status='obsolete', version=version+1, resolved_at=? "
            f"WHERE status='pending' AND (left_id IN ({RETIRED}) OR right_id IN ({RETIRED}))", (now,))
        row = connection.execute("SELECT * FROM experience_conflicts WHERE id=?", (identifier,)).fetchone()
        return _read(connection, row)


def reopen_legacy(path, identifier, version, note):
    if type(version) is not int or version < 1 or not isinstance(note,str) or not note.strip() or len(note)>2000:
        raise validation_error('重新审阅需要当前版本与明确依据。')
    note=note.strip()
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT * FROM experience_conflicts WHERE id=?',(identifier,)).fetchone()
        if row is None:
            raise ApplicationError(ErrorCode.EXPERIENCE_NOT_FOUND,'未找到该冲突。')
        last=connection.execute('SELECT * FROM legacy_review_decisions WHERE review_id=? ORDER BY version DESC LIMIT 1',(identifier,)).fetchone()
        if last and last['version']==version+1 and last['action']=='reopen' and json.loads(last['data']).get('note')==note:
            return _read(connection,row)
        if row['version']!=version or row['status']!='resolved':
            raise ApplicationError(ErrorCode.EXPERIENCE_CONFLICT,'审阅版本已变化，请刷新后重试。')
        stamp=datetime.now(timezone.utc).isoformat()
        # Preserve a decision made before event history was introduced.
        if not last:
            connection.execute('INSERT INTO legacy_review_decisions(review_id,version,action,data,created_at) VALUES(?,?,?,?,?)',
                (identifier,version,'resolve',json.dumps({k:row[k] for k in ('resolution','preferred_source_id','note')},ensure_ascii=False),row['resolved_at']))
        connection.execute('DELETE FROM experience_retirements WHERE conflict_id=?',(identifier,))
        connection.execute("UPDATE experience_conflicts SET status='pending',version=version+1,resolved_at=NULL,resolution=NULL,"
                           'preferred_source_id=NULL,note=NULL WHERE id=?',(identifier,))
        connection.execute('INSERT INTO legacy_review_decisions(review_id,version,action,data,created_at) VALUES(?,?,?,?,?)',
                           (identifier,version+1,'reopen',json.dumps({'note':note},ensure_ascii=False),stamp))
        obsolete=connection.execute("SELECT * FROM experience_conflicts WHERE status='obsolete' "
            f'AND left_id NOT IN ({RETIRED} UNION {SUPERSEDED}) AND right_id NOT IN ({RETIRED} UNION {SUPERSEDED})').fetchall()
        for item in obsolete:
            connection.execute("UPDATE experience_conflicts SET status='pending',version=version+1,resolved_at=NULL WHERE id=?",(item['id'],))
            connection.execute('INSERT INTO legacy_review_decisions(review_id,version,action,data,created_at) VALUES(?,?,?,?,?)',
                (item['id'],item['version']+1,'reopen',json.dumps({'note':'关联来源已恢复，需重新审阅。','trigger':identifier},ensure_ascii=False),stamp))
        return _read(connection,connection.execute('SELECT * FROM experience_conflicts WHERE id=?',(identifier,)).fetchone())


def legacy_history(path, identifier):
    with connect(path) as connection:
        return [dict(row)|{'data':json.loads(row['data'])} for row in connection.execute(
            'SELECT version,action,data,created_at FROM legacy_review_decisions WHERE review_id=? ORDER BY version',(identifier,))]
