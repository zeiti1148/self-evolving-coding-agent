"""Durable leased work, bounded pages, and short publication transactions."""
from __future__ import annotations

import json
import threading
import time
from uuid import uuid4

from backend.shared import ConsolidationReport
from backend.projects import same_source_revision
from .database import connect
from .knowledge import backfill, canonical, now, CURRENT_RULES, is_current
from .relations import classify, source_derived_compatibility, RELATION_POLICY_VERSION

PAGE_SIZE = 40
LEASE_SECONDS = 120
RETRY_DELAYS = (1, 5, 30, 120, 300)


def claim(path, *, language=None, repository_id=None):
    moment, token = time.time(), str(uuid4())
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        filters = ["((j.state='pending' AND j.available_at<=?) OR (j.state='running' AND j.lease_until<?))"]
        params = [moment, moment]
        for field, value in [('language',language),('repository_id',repository_id)]:
            if value is not None:
                filters.append(f'r.{field}=?'); params.append(value)
        row = connection.execute('SELECT j.* FROM knowledge_jobs j JOIN knowledge_rules r ON r.id=j.rule_id WHERE '
            + ' AND '.join(filters) + ' ORDER BY j.updated_at,j.id LIMIT 1', params).fetchone()
        if not row:
            return None
        connection.execute("UPDATE knowledge_jobs SET state='running',lease_token=?,lease_until=?,updated_at=? WHERE id=?",
                           (token, moment+LEASE_SECONDS, now(), row['id']))
        return dict(row) | {'lease_token':token}


def fail(path, job, exc):
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        count = job['attempts'] + 1
        state = 'failed' if count >= len(RETRY_DELAYS) else 'pending'
        changed = connection.execute('UPDATE knowledge_jobs SET state=?,attempts=?,error=?,available_at=?,lease_until=NULL,'
            'lease_token=NULL,updated_at=? WHERE id=? AND lease_token=?',
            (state,count,type(exc).__name__,time.time()+RETRY_DELAYS[min(count-1,4)],now(),job['id'],job['lease_token'])).rowcount
        if changed and state == 'failed':
            connection.execute("UPDATE knowledge_rules SET state='failed',version=version+1 WHERE id=?", (job['rule_id'],))


def process_page(path, job, semantic=None):
    """Inference reads immutable payloads outside a write transaction; lease tokens fence commits."""
    with connect(path) as connection:
        rule = connection.execute('SELECT * FROM knowledge_rules WHERE id=?', (job['rule_id'],)).fetchone()
        current=is_current(connection,rule['id'])
        if rule['state'] == 'needs_review' or not current:
            changed=connection.execute("UPDATE knowledge_jobs SET state='succeeded',lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=?",
                               (job['id'], job['lease_token'])).rowcount
            if changed and not current:
                connection.execute("UPDATE knowledge_rules SET state='ready' WHERE id=? AND state='pending'",(rule['id'],))
            return 0
        connection.create_function('same_source_revision', 2, same_source_revision, deterministic=True)
        candidates = connection.execute('SELECT * FROM knowledge_rules WHERE language=? AND repository_id=? AND scope=? '
            'AND (compatibility_key=? OR same_source_revision(compatibility_key,?)) AND id>? AND id!=? AND state!=\'needs_review\' AND id IN '
            '(SELECT a.rule_id FROM knowledge_targets a JOIN knowledge_targets b '
            "ON (a.file=b.file OR a.file='' OR b.file='') AND (a.symbol=b.symbol OR a.symbol='' OR b.symbol='') "
            'WHERE b.rule_id=?) AND EXISTS(SELECT 1 FROM knowledge_sources s WHERE s.rule_id=knowledge_rules.id AND s.experience_id NOT IN ('
            'SELECT source_id FROM experience_merge_sources UNION SELECT previous_id FROM experience_publication_revisions '
            'UNION SELECT experience_id FROM experience_retirements)) ORDER BY id LIMIT ?',
            (rule['language'],rule['repository_id'],rule['scope'],rule['compatibility_key'],rule['compatibility_key'],
             job['cursor'],rule['id'],rule['id'],PAGE_SIZE+1)).fetchall()
        # A revision-looking explicit contract is insufficient: each differing
        # key must be backed by its own immutable source revision.
        source_derived = source_derived_compatibility(connection, rule['id'], rule['compatibility_key'])
        compatible_candidates = {row['id'] for row in candidates[:PAGE_SIZE]
            if row['compatibility_key'] == rule['compatibility_key'] or source_derived
            and source_derived_compatibility(connection, row['id'], row['compatibility_key'])}
        embedded = connection.execute('SELECT 1 FROM knowledge_embeddings WHERE rule_id=? AND model_version=?',
                                      (rule['id'],semantic.version)).fetchone() if semantic else True
    if semantic and not embedded:
        vectors=semantic.embed([__import__('backend.experience.knowledge',fromlist=['rule_text']).rule_text(json.loads(rule['data']))])
        if vectors is not None:
            with connect(path) as connection:
                connection.execute('INSERT OR IGNORE INTO knowledge_embeddings VALUES(?,?,?)',(rule['id'],semantic.version,vectors[0].tobytes()))
    version = semantic.version if semantic else RELATION_POLICY_VERSION
    data, decisions = json.loads(rule['data']), []
    for row in candidates[:PAGE_SIZE]:
        if row['id'] not in compatible_candidates:
            continue
        left, right = sorted([rule['id'],row['id']])
        with connect(path) as connection:
            previous = connection.execute('SELECT model_version,status FROM knowledge_relations WHERE left_rule=? AND right_rule=?',
                                          (left,right)).fetchone()
            shared_source=connection.execute('SELECT 1 FROM knowledge_sources a JOIN knowledge_sources b ON a.experience_id=b.experience_id '
                                             'WHERE a.rule_id=? AND b.rule_id=? LIMIT 1',(left,right)).fetchone()
            derived=connection.execute('WITH RECURSIVE lineage(parent,child) AS (SELECT parent_id,child_id FROM knowledge_dependencies '
                'UNION SELECT l.parent,d.child_id FROM lineage l JOIN knowledge_dependencies d ON d.parent_id=l.child) '
                'SELECT 1 FROM lineage WHERE (parent=? AND child=?) OR (parent=? AND child=?) LIMIT 1',(left,right,right,left)).fetchone()
        if shared_source or derived:
            continue
        if previous and (previous['model_version'] == version or previous['status'] == 'resolved'):
            continue
        kind, details = classify(data, json.loads(row['data']), semantic,
                                 verified_source_compatibility=row['compatibility_key'] != rule['compatibility_key'])
        decisions.append((left,right,kind,canonical(details)))
        if semantic:
            with connect(path) as connection:
                changed = connection.execute('UPDATE knowledge_jobs SET lease_until=? WHERE id=? AND lease_token=?',
                    (time.time()+LEASE_SECONDS,job['id'],job['lease_token'])).rowcount
                if not changed:
                    return 0
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        active = connection.execute('SELECT lease_token FROM knowledge_jobs WHERE id=?', (job['id'],)).fetchone()
        if active[0] != job['lease_token']:
            return 0
        for left,right,kind,details in decisions:
            connection.execute('INSERT INTO knowledge_relations(id,left_rule,right_rule,kind,details,model_version,created_at) '
                'VALUES(?,?,?,?,?,?,?) ON CONFLICT(left_rule,right_rule) DO UPDATE SET kind=excluded.kind,details=excluded.details,'
                "model_version=excluded.model_version,version=knowledge_relations.version+1 WHERE knowledge_relations.status='pending'",
                (str(uuid4()),left,right,kind,details,version,now()))
        more = len(candidates) > PAGE_SIZE
        cursor = candidates[PAGE_SIZE-1]['id'] if more else ''
        connection.execute('UPDATE knowledge_jobs SET state=?,cursor=?,examined=examined+?,lease_token=NULL,lease_until=NULL,'
            'error=NULL,updated_at=? WHERE id=?', ('pending' if more else 'succeeded',cursor,len(candidates[:PAGE_SIZE]),now(),job['id']))
        if not more:
            connection.execute("UPDATE knowledge_rules SET state='ready',version=version+1 WHERE id=? AND state='pending'", (rule['id'],))
    return len(candidates[:PAGE_SIZE])


def report(path, *, language=None, repository_id=None):
    with connect(path) as connection:
        clauses, params = [], []
        for name, value in [('language',language),('repository_id',repository_id)]:
            if value is not None:
                clauses.append(f'r.{name}=?');params.append(value)
        where = ' WHERE '+' AND '.join(clauses) if clauses else ''
        rows = connection.execute('SELECT j.* FROM knowledge_jobs j JOIN knowledge_rules r ON r.id=j.rule_id'+where,params).fetchall()
        pending = [r['id'] for r in rows if r['state'] in {'pending','running'}]
        rule_ids = {r['rule_id'] for r in rows}
        relations = [r for r in connection.execute("SELECT * FROM knowledge_relations WHERE status='pending'")
                     if r['left_rule'] in rule_ids or r['right_rule'] in rule_ids]
        merged = [r[0] for r in connection.execute('SELECT rule_id FROM knowledge_sources GROUP BY rule_id HAVING count(DISTINCT experience_id)>1')
                  if r[0] in rule_ids]
        return ConsolidationReport(examined=sum(r['examined'] for r in rows),merged_ids=merged,
            conflict_ids=[r['id'] for r in relations if r['kind']=='conflict'],
            pending_jobs=len(pending),failed_jobs=sum(r['state']=='failed' for r in rows),
            unknown_pairs=sum(r['kind']=='unknown' for r in relations),job_id=pending[0] if pending else None)


def drain(path, *, language=None, repository_id=None, seconds=2.0, max_pages=100, semantic=None):
    backfill(path)
    deadline = time.monotonic()+seconds
    for _ in range(max_pages):
        if time.monotonic() >= deadline:
            break
        job = claim(path,language=language,repository_id=repository_id)
        if not job:
            break
        try:
            process_page(path,job,semantic)
        except Exception as exc:
            fail(path,job,exc)
    return report(path,language=language,repository_id=repository_id)


def list_jobs(path, *, limit=50, offset=0, language=None, repository_id=None):
    with connect(path) as connection:
        clauses=[];params=[]
        for name,value in [('language',language),('repository_id',repository_id)]:
            if value is not None: clauses.append(f'r.{name}=?');params.append(value)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        return [dict(r) for r in connection.execute('SELECT j.id,j.rule_id,j.state,j.cursor,j.attempts,j.examined,j.error,j.created_at,j.updated_at '
            'FROM knowledge_jobs j JOIN knowledge_rules r ON r.id=j.rule_id'+where+
            " ORDER BY CASE j.state WHEN 'failed' THEN 0 WHEN 'running' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,j.updated_at DESC,j.id LIMIT ? OFFSET ?",
            [*params,limit,offset])]


def retry(path, identifier):
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT * FROM knowledge_jobs WHERE id=?',(identifier,)).fetchone()
        if row is None:
            return None
        if connection.execute("SELECT 1 FROM knowledge_rules WHERE id=? AND state='needs_review'", (row['rule_id'],)).fetchone():
            return dict(row)
        if row['state'] == 'running' and row['lease_until'] > time.time():
            return dict(row)
        connection.execute("UPDATE knowledge_jobs SET state='pending',attempts=0,available_at=0,error=NULL,cursor='',updated_at=? WHERE id=?",(now(),identifier))
        connection.execute("UPDATE knowledge_rules SET state='pending',version=version+1 WHERE id=?",(row['rule_id'],))
        return dict(connection.execute('SELECT * FROM knowledge_jobs WHERE id=?',(identifier,)).fetchone())


class MaintenanceWorker:
    def __init__(self,path,*,semantic_enabled=True):
        self.path=path
        self.semantic_enabled=semantic_enabled
        self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self.run,name='experience-maintenance',daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=30)

    def run(self):
        from .semantic import runtime
        semantic=runtime()
        while not self.stop_event.is_set():
            try:
                backfill(self.path)
                semantic.refresh()
                current_version=semantic.version if self.semantic_enabled and semantic.available else RELATION_POLICY_VERSION
                with connect(self.path) as connection:
                    previous=connection.execute("SELECT value FROM knowledge_meta WHERE key='semantic_version'").fetchone()
                    if previous is None or previous[0]!=current_version:
                        connection.execute("UPDATE knowledge_jobs SET state='pending',cursor='',available_at=0 WHERE state='succeeded'")
                        connection.execute("UPDATE knowledge_rules SET state='pending' WHERE state='ready' AND id IN (SELECT rule_id FROM knowledge_jobs WHERE state='pending')")
                        connection.execute("INSERT OR REPLACE INTO knowledge_meta VALUES('semantic_version',?)",(current_version,))
                job=claim(self.path)
                if job:
                    try:
                        process_page(self.path,job,semantic if self.semantic_enabled and semantic.available else None)
                    except Exception as exc:
                        fail(self.path,job,exc)
                else:
                    self.stop_event.wait(1)
            except Exception:
                self.stop_event.wait(2)
