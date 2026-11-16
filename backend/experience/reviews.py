"""Rule-granular review, immutable decision events, and optimistic undo."""
import json
from uuid import uuid4

from backend.shared import ApplicationError,ErrorCode,ExperienceConflict,ConflictResolution
from .database import connect
from .knowledge import canonical,now,CURRENT_RULES,is_current
from .store import COLUMNS,decode_row,validation_error


def sources(connection,row):
    result=[]
    exact=json.loads(row['details']).get('source_ids', [])
    for rule in (row['left_rule'],row['right_rule']):
        source=None
        for identifier in exact:
            source=connection.execute('SELECT experience_id FROM knowledge_sources WHERE rule_id=? AND experience_id=?', (rule,identifier)).fetchone()
            if source:
                break
        source=source or connection.execute('SELECT experience_id FROM knowledge_sources WHERE rule_id=? ORDER BY experience_id LIMIT 1',(rule,)).fetchone()
        result.append(decode_row(connection.execute(f'SELECT {COLUMNS} FROM experiences WHERE id=?',(source[0],)).fetchone()))
    return result


def read(connection,row):
    items=sources(connection,row)
    status=row['status']
    if status=='pending' and any(not is_current(connection,identity)
                                 for identity in (row['left_rule'],row['right_rule'])):
        status='obsolete'
    if status=='pending' and connection.execute('SELECT 1 FROM knowledge_pauses WHERE rule_id IN (?,?)',
                                              (row['left_rule'],row['right_rule'])).fetchone():
        status='obsolete'
    assessment=json.loads(row['details'])
    description=('相同代码目标存在互斥规则。' if row['kind']=='conflict' else
                 '规则可能相关，尚不能确认等价、互补或矛盾。')
    details=[dict(code='rule_overlap' if row['kind']=='conflict' else 'unknown_relation',message=description,
        source_ids=sorted({v.id for v in items}),field='issues',
        suggestion=assessment.get('reason','核对来源、目标条件与行为后作出决定。'))]
    if assessment.get('policy') == 'cross-upload-conflicts-v1':
        details.append(dict(code='applicability_mismatch', message='发生冲突的共同适用条件',
            source_ids=assessment['source_ids'], field='applicability', suggestion=assessment['condition']))
        for quote in assessment['quotes']:
            details.append(dict(code='rule_overlap', message='相互冲突的来源原文',
                source_ids=[quote['source_id']], field=f'issues[{quote["issue_index"]}].solution', suggestion=quote['quote']))
        details.append(dict(code='evidence_mismatch', message='独立模型复核',
            source_ids=assessment['source_ids'], field='issues', suggestion=assessment['independent_review']['reason']))
    automatic=None
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_auto_reviews'").fetchone():
        saved=connection.execute('SELECT data FROM knowledge_auto_reviews WHERE review_id=?',(row['id'],)).fetchone()
        if saved:
            data=json.loads(saved[0])
            automatic={k:data[k] for k in ('state','actor','policy','stage','reason','targets','runs','resolution',
                'preferred_source_id','started_at','updated_at','evidence_hash')}
            if row['version'] != data.get('result_version',1) and status=='pending':
                automatic.update(state='needs_confirmation',reason='该决定已重新打开审阅，系统不会覆盖新的人工审阅。')
    return ExperienceConflict(id=row['id'],sources=items,rule_ids=[row['left_rule'],row['right_rule']],
        relation=row['kind'] if row['kind'] in {'conflict','unknown','equivalent','compatible'} else 'unknown',
        details=details, automatic_resolution=automatic,
        status=status,version=row['version'],created_at=row['created_at'],
        resolved_at=row['resolved_at'] or (row['created_at'] if status=='obsolete' else None),
        resolution=row['resolution'],preferred_source_id=row['preferred_source_id'],note=row['note'])


def list_reviews(path,language=None,repository_id=None,status='pending',limit=20,offset=0):
    with connect(path) as connection:
        connection.execute('BEGIN')
        clauses=["(c.kind IN ('conflict','unknown') OR c.status='resolved')"]
        params=[]
        for field,value in [('language',language),('repository_id',repository_id)]:
            if value is not None:
                clauses.append(f'(r.{field}=? OR other.{field}=?)');params.extend([value,value])
        rows=connection.execute('SELECT c.* FROM knowledge_relations c JOIN knowledge_rules r ON r.id=c.left_rule '
            'JOIN knowledge_rules other ON other.id=c.right_rule WHERE '
            +' AND '.join(clauses)+' ORDER BY c.created_at DESC,c.id DESC',params)
        result=[]
        matched=0
        for row in rows:
            value=read(connection,row)
            if value.status==status:
                if matched>=offset:
                    result.append(value)
                matched+=1
                if len(result)==limit:
                    break
        return result


def resolve(path,identifier,resolution):
    if not isinstance(resolution,ConflictResolution):
        raise validation_error('冲突处理内容无效。')
    resolution=ConflictResolution.model_validate(resolution.model_dump())
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        return resolve_in_transaction(connection,identifier,resolution)


def resolve_in_transaction(connection,identifier,resolution,*,automatic=None):
    """Caller holds an IMMEDIATE transaction; automatic evidence is server-only."""
    row=connection.execute('SELECT * FROM knowledge_relations WHERE id=?',(identifier,)).fetchone()
    if row is None:
        raise ApplicationError(ErrorCode.EXPERIENCE_NOT_FOUND,'未找到审阅项。')
    value=read(connection,row)
    if (row['status']=='resolved' and row['version']==resolution.version+1
            and all(row[k]==getattr(resolution,k) for k in ('resolution','preferred_source_id','note'))):
        return value
    if row['version']!=resolution.version or value.status!='pending':
        raise ApplicationError(ErrorCode.EXPERIENCE_CONFLICT,'审阅版本已变化，请刷新后重试。')
    ids=[v.id for v in value.sources]
    if resolution.preferred_source_id and resolution.preferred_source_id not in ids:
        raise validation_error('只能选择当前审阅项的来源。')
    kind={'confirm_equivalent':'equivalent','confirm_compatible':'compatible'}.get(resolution.resolution,row['kind'])
    if resolution.resolution in {'prefer_source','keep_separate'}:
        for identity,source in zip(value.rule_ids,ids):
            if source!=resolution.preferred_source_id:
                connection.execute('INSERT OR IGNORE INTO knowledge_pauses VALUES(?,?)',(identity,identifier))
    connection.execute("UPDATE knowledge_relations SET status='resolved',kind=?,version=version+1,resolved_at=?,resolution=?,"
        'preferred_source_id=?,note=? WHERE id=?',
        (kind,now(),resolution.resolution,resolution.preferred_source_id,resolution.note,identifier))
    connection.execute('INSERT INTO knowledge_decisions(review_id,version,action,data,created_at) VALUES(?,?,?,?,?)',
        (identifier,resolution.version+1,'resolve',canonical(resolution.model_dump() | {'previous_kind':row['kind']} | (automatic or {})),now()))
    from .automation import review_changed
    review_changed(connection, value.rule_ids)
    return read(connection,connection.execute('SELECT * FROM knowledge_relations WHERE id=?',(identifier,)).fetchone())


def reopen(path,identifier,version,note):
    if type(version) is not int or version<1 or not isinstance(note,str) or not note.strip() or len(note)>2000:
        raise validation_error('重新审阅需要当前版本与明确依据。')
    note=note.strip()
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT * FROM knowledge_relations WHERE id=?',(identifier,)).fetchone()
        if row is None:
            raise ApplicationError(ErrorCode.EXPERIENCE_NOT_FOUND,'未找到审阅项。')
        last=connection.execute('SELECT * FROM knowledge_decisions WHERE review_id=? ORDER BY version DESC LIMIT 1',(identifier,)).fetchone()
        if last and last['version']==version+1 and last['action']=='reopen' and json.loads(last['data'])['note']==note:
            return read(connection,row)
        if row['version']!=version or row['status']!='resolved':
            raise ApplicationError(ErrorCode.EXPERIENCE_CONFLICT,'审阅版本已变化，请刷新后重试。')
        previous=json.loads(last['data']).get('previous_kind','unknown') if last else 'unknown'
        connection.execute('DELETE FROM knowledge_pauses WHERE review_id=?',(identifier,))
        connection.execute("UPDATE knowledge_relations SET kind=?,status='pending',version=version+1,resolved_at=NULL,resolution=NULL,"
                           'preferred_source_id=NULL,note=NULL WHERE id=?',(previous,identifier))
        connection.execute('INSERT INTO knowledge_decisions(review_id,version,action,data,created_at) VALUES(?,?,?,?,?)',
                           (identifier,version+1,'reopen',canonical({'note':note}),now()))
        from .automation import review_changed
        review_changed(connection, [row['left_rule'], row['right_rule']])
        return read(connection,connection.execute('SELECT * FROM knowledge_relations WHERE id=?',(identifier,)).fetchone())


def history(path,identifier,*,connection=None):
    if connection is None:
        with connect(path) as connection:
            return history(path,identifier,connection=connection)
    return [dict(r) | {'data':json.loads(r['data'])} for r in connection.execute(
        'SELECT version,action,data,created_at FROM knowledge_decisions WHERE review_id=? ORDER BY version',(identifier,))]
