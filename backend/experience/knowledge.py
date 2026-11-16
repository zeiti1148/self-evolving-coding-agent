"""Immutable rule projection, flat provenance, and transactional outbox ingestion."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from .database import connect


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def rule_text(data):
    issue = data['issue']
    return '\n'.join([*data['applicability'], *data['constraints'], issue['error_type'],
                       issue['root_cause'], issue['solution'], *issue['files'], *issue.get('symbols', [])])


def ingest(connection, entry):
    """Called in the raw save transaction. No network, model inference or pair scans."""
    from .store import _source_issues
    eligible = bool(entry.schema_version >= 3 and entry.reuse_status == 'active' and entry.evidence
                    and entry.applicability and entry.issues)
    ids = []
    for ordinal, issue in enumerate(_source_issues(entry)):
        data = dict(verification_class='verified' if eligible else 'unreviewed', language=entry.language, scope=entry.scope, repository_id=entry.repository_id or '',
                    compatibility_key=entry.compatibility_key or entry.source_revision or '',
                    applicability=sorted(set(entry.applicability)),
                    constraints=sorted(set([*entry.constraints,*([entry.root_cause, entry.solution] if entry.schema_version < 4 else [])])),
                    required_capabilities=sorted(set(entry.required_capabilities)),
                    issue=issue.model_dump(exclude={'depends_on'}))
        data['issue']['files'] = sorted(set(data['issue']['files']))
        data['issue']['symbols'] = sorted(set(data['issue']['symbols']))
        fingerprint = digest(data)
        identifier = str(uuid5(NAMESPACE_URL, 'experience-rule:' + fingerprint))
        ids.append(identifier)
        was_current=is_current(connection,identifier)
        inserted = connection.execute(
            "INSERT INTO knowledge_rules(id,fingerprint,language,repository_id,scope,compatibility_key,data,created_at,state) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(fingerprint) DO NOTHING",
            (identifier, fingerprint, entry.language, entry.repository_id or '', entry.scope,
             data['compatibility_key'], canonical(data), entry.created_at.isoformat(), 'pending' if eligible else 'needs_review')).rowcount
        connection.execute('INSERT OR IGNORE INTO knowledge_sources VALUES(?,?,?)', (identifier, entry.id, ordinal))
        for file in issue.files or ['']:
            targets=[symbol for symbol in issue.symbols if symbol.startswith(file+'#')] or ['']
            connection.executemany('INSERT OR IGNORE INTO knowledge_targets VALUES(?,?,?)',
                                   [(identifier,file,symbol) for symbol in targets])
        valid_evidence = True
        for evidence in entry.evidence:
            key = evidence.source_task_id + ':' + evidence.tool_call_id
            payload = canonical(evidence.model_dump())
            previous = connection.execute('SELECT data FROM knowledge_evidence WHERE id=?', (key,)).fetchone()
            if previous and previous[0] != payload:
                valid_evidence = False
                connection.execute("UPDATE knowledge_rules SET state='needs_review',version=version+1 WHERE id IN "
                                   '(SELECT rule_id FROM knowledge_rule_evidence WHERE evidence_id=?)', (key,))
            else:
                connection.execute('INSERT OR IGNORE INTO knowledge_evidence VALUES(?,?)', (key, payload))
                connection.execute('INSERT OR IGNORE INTO knowledge_rule_evidence VALUES(?,?)', (identifier, key))
        if not valid_evidence:
            connection.execute("UPDATE knowledge_rules SET state='needs_review' WHERE id=?", (identifier,))
        elif inserted and eligible:
            stamp = now()
            connection.execute("INSERT INTO knowledge_jobs(id,rule_id,state,created_at,updated_at) VALUES(?,?,'pending',?,?)",
                               (identifier, identifier, stamp, stamp))
        elif eligible and not was_current:
            # A fresh source can reactivate a rule whose old sources were superseded.
            # Recheck it against knowledge added while it was inactive.
            changed=connection.execute("UPDATE knowledge_rules SET state='pending',version=version+1 WHERE id=? AND state='ready'",(identifier,)).rowcount
            if changed:
                connection.execute("UPDATE knowledge_jobs SET state='pending',cursor='',available_at=0,lease_until=NULL,lease_token=NULL,updated_at=? WHERE rule_id=?",
                                   (now(),identifier))
        if inserted and connection.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_fts'").fetchone():
            connection.execute('INSERT INTO knowledge_fts(rule_id,text) VALUES(?,?)', (identifier, rule_text(data)))
    for ordinal, issue in enumerate(entry.issues):
        for dependency in issue.depends_on:
            connection.execute('INSERT OR IGNORE INTO knowledge_dependencies VALUES(?,?)', (ids[dependency], ids[ordinal]))
            connection.execute('INSERT OR IGNORE INTO knowledge_prerequisites VALUES(?,?)', (ids[dependency], ids[ordinal]))
            if entry.schema_version >= 4:
                cycle = connection.execute('WITH RECURSIVE reach(id) AS (SELECT ? UNION SELECT d.child_id FROM '
                    'knowledge_dependencies d JOIN reach r ON d.parent_id=r.id) SELECT 1 FROM reach WHERE id=?',
                    (ids[ordinal], ids[dependency])).fetchone()
                if cycle:
                    connection.execute("UPDATE knowledge_rules SET state='needs_review',version=version+1 WHERE id IN (?,?)",
                                       (ids[ordinal], ids[dependency]))
    # V3 prose can describe the whole bundle; don't expose a partial legacy bundle after a conflict.
    if entry.schema_version < 4 and len(ids) > 1:
        for left in ids:
            for right in ids:
                if left != right:
                    connection.execute('INSERT OR IGNORE INTO knowledge_dependencies VALUES(?,?)', (left, right))
                    connection.execute('INSERT OR IGNORE INTO knowledge_prerequisites VALUES(?,?)', (left, right))
    # A publication remains dependent on the sources that justified it.
    for source in [*entry.derived_from,*entry.merged_from]:
        for parent in connection.execute('SELECT rule_id FROM knowledge_sources WHERE experience_id=?', (source,)):
            for child in ids:
                if parent[0] != child:
                    connection.execute('INSERT OR IGNORE INTO knowledge_dependencies VALUES(?,?)', (parent[0], child))
    return ids


def backfill(path, limit=200):
    from .store import COLUMNS, decode_row
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        rows = connection.execute(f'SELECT {COLUMNS} FROM experiences WHERE id NOT IN '
            '(SELECT experience_id FROM knowledge_sources) ORDER BY created_at,id LIMIT ?', (limit,)).fetchall()
        for row in rows:
            ingest(connection, decode_row(row))
    return len(rows)


CURRENT_RULES = """SELECT DISTINCT s.rule_id FROM knowledge_sources s WHERE s.experience_id NOT IN (
 SELECT source_id FROM experience_merge_sources UNION SELECT previous_id FROM experience_publication_revisions
 UNION SELECT experience_id FROM experience_retirements)"""

CURRENT_SOURCES = """SELECT id FROM experiences WHERE id NOT IN (
 SELECT source_id FROM experience_merge_sources UNION SELECT previous_id FROM experience_publication_revisions
 UNION SELECT experience_id FROM experience_retirements)"""

# Every projection retains the source's whole constraint set. Until constraints
# are independently scoped to issues, a sibling cannot be treated as independent
# merely because its own issue describes another function or error type.
SHARED_CONTEXT = """SELECT s.experience_id,s.rule_id FROM knowledge_sources s
 WHERE s.experience_id IN (""" + CURRENT_SOURCES + """) AND EXISTS (
 SELECT 1 FROM knowledge_sources sibling JOIN knowledge_rules r ON r.id=sibling.rule_id
 WHERE sibling.experience_id=s.experience_id AND json_array_length(r.data,'$.constraints')>0)"""


def shared_context_rules(connection, identifiers):
    """Current source bundles only; retired sources must not connect live rules."""
    groups = {}
    for source_id, rule_id in connection.execute(SHARED_CONTEXT):
        groups.setdefault(source_id, set()).add(rule_id)
    members = set(identifiers)
    while True:
        previous = len(members)
        for rules in groups.values():
            if members & rules:
                members.update(rules)
        if len(members) == previous:
            return members


def is_current(connection, identifier):
    return bool(connection.execute('SELECT 1 FROM knowledge_sources s WHERE s.rule_id=? AND s.experience_id NOT IN ('
        'SELECT source_id FROM experience_merge_sources UNION SELECT previous_id FROM experience_publication_revisions '
        'UNION SELECT experience_id FROM experience_retirements) LIMIT 1',(identifier,)).fetchone())

AMBIGUITY_SEEDS = """
 SELECT r.left_rule FROM knowledge_relations r WHERE r.kind IN ('conflict','unknown') AND r.status='pending'
   AND r.left_rule IN current_rules AND r.right_rule IN current_rules
   AND NOT EXISTS(SELECT 1 FROM knowledge_pauses p WHERE p.rule_id IN (r.left_rule,r.right_rule))
 UNION SELECT r.right_rule FROM knowledge_relations r WHERE r.kind IN ('conflict','unknown') AND r.status='pending'
   AND r.left_rule IN current_rules AND r.right_rule IN current_rules
   AND NOT EXISTS(SELECT 1 FROM knowledge_pauses p WHERE p.rule_id IN (r.left_rule,r.right_rule))
"""
# Filter ambiguity before candidate selection, preserving its equivalent bridges.
# An unrelated member's evidence expiry alone does not invalidate verified peers.
AMBIGUITY_PROPAGATION = """
 UNION SELECT r.right_rule FROM knowledge_relations r JOIN ambiguous a ON a.id=r.left_rule
   WHERE r.kind='equivalent' AND r.left_rule IN current_rules AND r.right_rule IN current_rules
 UNION SELECT r.left_rule FROM knowledge_relations r JOIN ambiguous a ON a.id=r.right_rule
   WHERE r.kind='equivalent' AND r.left_rule IN current_rules AND r.right_rule IN current_rules
"""

BLOCKED_SEEDS = """
 SELECT id FROM knowledge_rules WHERE state!='ready'
 UNION SELECT r.id FROM knowledge_rules r WHERE EXISTS(SELECT 1 FROM knowledge_sources s WHERE s.rule_id=r.id)
   AND NOT EXISTS(SELECT 1 FROM knowledge_sources s WHERE s.rule_id=r.id
     AND s.experience_id NOT IN (SELECT experience_id FROM experience_retirements
       UNION SELECT left_id FROM experience_conflicts WHERE status='pending'
       UNION SELECT right_id FROM experience_conflicts WHERE status='pending'))
 UNION SELECT rule_id FROM knowledge_pauses
 UNION SELECT rule_id FROM knowledge_reuse_grants
 UNION SELECT id FROM ambiguous
"""
BLOCKED_PREFIX = """WITH RECURSIVE current_rules(id) AS (""" + CURRENT_RULES + """),
 shared_context(source_id,rule_id) AS (""" + SHARED_CONTEXT + """),
 ambiguous(id) AS (""" + AMBIGUITY_SEEDS + AMBIGUITY_PROPAGATION + """),
 seeds(id) AS (""" + BLOCKED_SEEDS + """), blocked(id) AS (
 SELECT id FROM seeds"""
DEPENDENCY_PROPAGATION = ' UNION SELECT d.child_id FROM knowledge_dependencies d JOIN blocked b ON d.parent_id=b.id'
SHARED_PROPAGATION = """
 UNION SELECT sibling.rule_id FROM shared_context origin JOIN blocked b ON b.id=origin.rule_id
   JOIN shared_context sibling ON sibling.source_id=origin.source_id
"""
BLOCKED = BLOCKED_PREFIX + DEPENDENCY_PROPAGATION + SHARED_PROPAGATION + ') SELECT id FROM blocked'


def effective_rules(connection):
    return {r[0] for r in connection.execute(BLOCKED)}


def blocked_without_seeds(connection, identifiers):
    """Trace causes not covered by independently checked grants in one scope."""
    identifiers = sorted(set(identifiers))
    if not identifiers:
        return effective_rules(connection)
    query = (BLOCKED_PREFIX + ' WHERE id NOT IN (' + ','.join('?' for _ in identifiers) + ')' + DEPENDENCY_PROPAGATION +
             SHARED_PROPAGATION + ') SELECT id FROM blocked')
    return {r[0] for r in connection.execute(query, identifiers)}


def source_requires_review(connection, identifier):
    """Whole-record transformations cannot smuggle a paused or ambiguous sub-rule."""
    rules={r[0] for r in connection.execute('SELECT s.rule_id FROM knowledge_sources s JOIN knowledge_rules r ON r.id=s.rule_id '
        "WHERE s.experience_id=? AND json_extract(r.data,'$.verification_class')='verified'",(identifier,))}
    if rules & effective_rules(connection):
        return True
    for row in connection.execute("SELECT left_rule,right_rule FROM knowledge_relations WHERE kind IN ('unknown','conflict') AND status='pending' "
        'AND NOT EXISTS(SELECT 1 FROM knowledge_pauses p WHERE p.rule_id IN (left_rule,right_rule)) '
        f'AND left_rule IN ({CURRENT_RULES}) AND right_rule IN ({CURRENT_RULES})'):
        if rules.intersection(row):
            return True
    return False


def source_status(path, identifier, *, offset=0, limit=50, connection=None, blocked=None):
    if connection is None:
        with connect(path) as connection:
            connection.execute('BEGIN')
            return source_status(path, identifier, offset=offset, limit=limit, connection=connection)
    if blocked is None:
        from .recovery import active_grants
        blocked = effective_rules(connection) - active_grants(connection).keys()
    rows = connection.execute('SELECT r.* FROM knowledge_rules r JOIN knowledge_sources s ON r.id=s.rule_id '
                              'WHERE s.experience_id=? ORDER BY s.ordinal', (identifier,)).fetchall()
    rules = [dict(id=r['id'], state=r['state'], effective_status='paused' if r['id'] in blocked else 'active',
                  data=json.loads(r['data'])) for r in rows]
    current_ids = [r['id'] for r in rules if r['effective_status'] == 'active']
    return dict(experience_id=identifier, effective_status='active' if current_ids else 'paused',
                current_rule_ids=current_ids, rules=rules)


def provenance_page(path, identifier, *, offset=0, limit=50, connection=None, blocked=None):
    if connection is None:
        with connect(path) as connection:
            connection.execute('BEGIN')
            return provenance_page(path, identifier, offset=offset, limit=limit, connection=connection)
    if not connection.execute('SELECT 1 FROM knowledge_rules WHERE id=?',(identifier,)).fetchone():
        from backend.shared import ApplicationError,ErrorCode
        raise ApplicationError(ErrorCode.EXPERIENCE_NOT_FOUND,'未找到规范规则。')
    from .equivalence import equivalent_groups
    from .store import SUPERSEDED, RETIRED, QUARANTINED
    inactive={r[0] for r in connection.execute(f'{SUPERSEDED} UNION {RETIRED} UNION {QUARANTINED}')}
    active={r[0] for r in connection.execute('SELECT rule_id,experience_id FROM knowledge_sources') if r[1] not in inactive}
    if blocked is None:
        from .recovery import active_grants
        blocked=effective_rules(connection) - active_grants(connection).keys()
    active-=blocked
    relations={(r[0],r[1]):r[2] for r in connection.execute('SELECT left_rule,right_rule,kind FROM knowledge_relations')}
    dependencies={}
    for row in connection.execute('SELECT parent_id,child_id FROM knowledge_prerequisites'):
        dependencies.setdefault(row[1],set()).add(row[0])
    members=sorted(equivalent_groups(active,relations,dependencies).get(identifier,{identifier}))
    placeholders=','.join('?' for _ in members)
    rows = connection.execute(f'SELECT DISTINCT experience_id FROM knowledge_sources WHERE rule_id IN ({placeholders}) '
                              'ORDER BY experience_id LIMIT ? OFFSET ?', (*members, limit+1, offset)).fetchall()
    evidence = connection.execute(f'SELECT count(DISTINCT evidence_id) FROM knowledge_rule_evidence WHERE rule_id IN ({placeholders})', members).fetchone()[0]
    return dict(rule_id=identifier, source_ids=[r[0] for r in rows[:limit]], evidence_count=evidence,
                next_offset=offset+limit if len(rows)>limit else None)
