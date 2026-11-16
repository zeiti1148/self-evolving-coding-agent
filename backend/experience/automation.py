"""Normal repair workflow: durable comparison jobs and a read-only change feed."""
from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import suppress
from uuid import NAMESPACE_URL, uuid4, uuid5

from .database import connect
from .knowledge import canonical, now

logger = logging.getLogger(__name__)
MAX_CANDIDATES = 5
LEASE_SECONDS = 120
SCHEMA = """
CREATE TABLE IF NOT EXISTS experience_auto_jobs (
 id TEXT PRIMARY KEY REFERENCES experiences(id), state TEXT NOT NULL DEFAULT 'pending',
 candidates TEXT, candidate_total INTEGER NOT NULL DEFAULT 0, notice TEXT NOT NULL DEFAULT '',
 lease_token TEXT, lease_until REAL, available_at REAL NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS experience_auto_queue ON experience_auto_jobs(state,available_at);
CREATE TABLE IF NOT EXISTS experience_auto_pairs (
 id TEXT PRIMARY KEY, left_id TEXT NOT NULL REFERENCES experiences(id),
 right_id TEXT NOT NULL REFERENCES experiences(id), inputs TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 1,
 result TEXT, notice TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
 UNIQUE(left_id,right_id)
);
CREATE TABLE IF NOT EXISTS experience_auto_links (
 job_id TEXT NOT NULL REFERENCES experience_auto_jobs(id),
 pair_id TEXT NOT NULL REFERENCES experience_auto_pairs(id),
 PRIMARY KEY(job_id,pair_id)
);
CREATE TABLE IF NOT EXISTS experience_auto_attempts (
 pair_id TEXT NOT NULL REFERENCES experience_auto_pairs(id), attempt INTEGER NOT NULL,
 state TEXT NOT NULL, result TEXT, notice TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(pair_id,attempt)
);
"""


def enqueue(connection, entry):
    """Shares the original experience transaction; never calls a model."""
    if (entry.schema_version >= 4 and entry.scope == 'project' and entry.reuse_status == 'active'
            and entry.source_task_id and entry.issues and entry.evidence
            and not entry.merged_from and not entry.derived_from
            and all(e.tests > 0 and not (e.failures or e.errors or e.skipped) for e in entry.evidence)):
        connection.execute('INSERT OR IGNORE INTO experience_auto_jobs(id,created_at,updated_at) VALUES(?,?,?)',
                           (entry.id, now(), now()))


def _source(connection, identifier):
    from .store import COLUMNS, decode_row
    row = connection.execute(f'SELECT {COLUMNS} FROM experiences WHERE id=?', (identifier,)).fetchone()
    return decode_row(row) if row else None


def _retired(connection, identifier):
    from .store import SUPERSEDED, RETIRED
    return bool(connection.execute(f'SELECT 1 WHERE ? IN ({SUPERSEDED} UNION {RETIRED})', (identifier,)).fetchone())


def _plan(path, job):
    from backend.projects import same_source_revision
    from .local_equivalence import same_domain
    from .retrieval import lexical_candidates
    from .store import COLUMNS, decode_row, SUPERSEDED, RETIRED
    with connect(path) as connection:
        source = _source(connection, job['id'])
        connection.create_function('same_source_revision', 2, same_source_revision, deterministic=True)
        # A strict ordering gives every pair one owner, including concurrent repairs.
        where = ("json_extract(details,'$.language')=? AND (json_extract(details,'$.repository_id')!=? "
                 "OR same_source_revision(json_extract(details,'$.source_revision'),?)) "
                 "AND json_extract(details,'$.schema_version')>=4 AND json_extract(details,'$.reuse_status')='active' "
                 "AND json_extract(details,'$.scope')='project' AND source_task_id!=? "
                 "AND (created_at<? OR (created_at=? AND id<?)) "
                 f"AND id NOT IN ({SUPERSEDED} UNION {RETIRED})")
        stamp = source.created_at.isoformat(timespec='microseconds')
        params = (source.language, source.repository_id, source.source_revision, source.source_task_id, stamp, stamp, source.id)
        total = connection.execute('SELECT count(*) FROM experiences WHERE '+where, params).fetchone()[0]
        rows = connection.execute(f'SELECT {COLUMNS} FROM experiences WHERE '+where+
                                  ' ORDER BY created_at DESC,id DESC LIMIT 200', params).fetchall()
        query = '\n'.join([source.task_summary, *(v.root_cause+'\n'+v.solution for v in source.issues)])
        ranks = {}
        for rank, rule_id in enumerate(lexical_candidates(connection, query)):
            if len(ranks) >= 200:
                break
            for row in connection.execute('SELECT experience_id FROM knowledge_sources WHERE rule_id=? ORDER BY experience_id LIMIT ?',
                                          (rule_id, 200-len(ranks))):
                ranks[row[0]] = min(rank, ranks.get(row[0], rank))
        if ranks:
            marks = ','.join('?' for _ in ranks)
            rows += connection.execute(f'SELECT {COLUMNS} FROM experiences WHERE '+where+
                                       f' AND id IN ({marks})', (*params, *ranks)).fetchall()
        candidates = {row['id']: decode_row(row) for row in rows}
        errors = {i.error_type for i in source.issues}
        ordered = sorted(candidates.values(), key=lambda item: (
            1/(60+ranks[item.id]) if item.id in ranks else 0,
            bool(errors.intersection(i.error_type for i in item.issues)), item.created_at, item.id), reverse=True)
        chosen = [item for item in ordered if item.source_task_id and not item.derived_from and not item.merged_from
                  and (item.repository_id != source.repository_id or same_domain(source, item))
                  and item.evidence and all(e.tests > 0 and not (e.failures or e.errors or e.skipped)
                                           for e in item.evidence)][:MAX_CANDIDATES]
        connection.execute('BEGIN IMMEDIATE')
        active = connection.execute('SELECT lease_token FROM experience_auto_jobs WHERE id=?', (job['id'],)).fetchone()
        if active[0] != job['lease_token']:
            return
        for candidate in chosen:
            entries = sorted([source, candidate], key=lambda item: item.id)
            pair_id = str(uuid5(NAMESPACE_URL, 'automatic-repair-pair:'+':'.join(e.id for e in entries)))
            connection.execute('INSERT OR IGNORE INTO experience_auto_pairs'
                               '(id,left_id,right_id,inputs,updated_at) VALUES(?,?,?,?,?)',
                               (pair_id, entries[0].id, entries[1].id,
                                canonical([e.model_dump(mode='json') for e in entries]), now()))
            connection.execute('INSERT OR IGNORE INTO experience_auto_links VALUES(?,?)', (job['id'], pair_id))
        connection.execute('UPDATE experience_auto_jobs SET candidates=?,candidate_total=? WHERE id=?',
                           (canonical([e.id for e in chosen]), total, job['id']))


def reschedule_local_comparisons(path):
    """One policy migration; keep completed pair results and all raw sources intact."""
    from .local_equivalence import POLICY
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        previous = connection.execute("SELECT value FROM knowledge_meta WHERE key='automatic_candidate_policy'").fetchone()
        if previous and previous[0] == POLICY:
            return
        connection.execute("UPDATE experience_auto_jobs SET state='pending',candidates=NULL,available_at=0,updated_at=? "
                           "WHERE state='succeeded'", (now(),))
        connection.execute("INSERT OR REPLACE INTO knowledge_meta VALUES('automatic_candidate_policy',?)", (POLICY,))


def claim(path):
    stamp = time.time()
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute("SELECT * FROM experience_auto_jobs WHERE "
            "((state IN ('pending','waiting') AND available_at<=?) OR (state='running' AND lease_until<?)) "
            "ORDER BY created_at,id LIMIT 1", (stamp, stamp)).fetchone()
        if not row:
            return None
        token = str(uuid4())
        connection.execute("UPDATE experience_auto_jobs SET state='running',lease_token=?,lease_until=?,updated_at=? WHERE id=?",
                           (token, stamp+LEASE_SECONDS, now(), row['id']))
        return dict(row) | {'lease_token': token}


def _finish(path, job, state, notice):
    with connect(path) as connection:
        connection.execute('UPDATE experience_auto_jobs SET state=?,notice=?,available_at=?,lease_token=NULL,'
            'lease_until=NULL,updated_at=? WHERE id=? AND lease_token=?',
            (state, notice, time.time()+3 if state == 'waiting' else 0, now(), job['id'], job['lease_token']))


def _pair_result(path, job, pair, state, notice, result=None):
    with connect(path) as connection:
        return bool(connection.execute('UPDATE experience_auto_pairs SET state=?,notice=?,result=?,updated_at=? WHERE id=? '
            'AND EXISTS(SELECT 1 FROM experience_auto_jobs WHERE id=? AND lease_token=?)',
            (state, notice, canonical(result) if result is not None else None, now(), pair['id'],
             job['id'], job['lease_token'])).rowcount)


def process(path, job, llm_factory, conflict_verifier=None):
    from .principles import _sources, consolidate_principle
    with connect(path) as connection:
        if _retired(connection, job['id']):
            _finish(path, job, 'succeeded', '来源已被替代或停用，不再生成共同规则。')
            return
    if job['candidates'] is None:
        _plan(path, job)
    with connect(path) as connection:
        pairs = connection.execute('SELECT p.* FROM experience_auto_pairs p JOIN experience_auto_links l ON p.id=l.pair_id '
                                   'WHERE l.job_id=? ORDER BY p.id', (job['id'],)).fetchall()
    for pair in pairs:
        if pair['state'] not in {'pending', 'waiting', 'running'}:
            continue
        if pair['state'] == 'running':
            _pair_result(path, job, pair, 'failed', '上次比较中断；请重试，已完成的比较不会重复调用模型。')
            continue
        identifiers = [pair['left_id'], pair['right_id']]
        local = False
        with connect(path) as connection:
            if any(_retired(connection, identifier) for identifier in identifiers):
                _pair_result(path, job, pair, 'rejected', '来源已被替代或停用，保留原始比较记录。')
                continue
            originals = [_source(connection, identifier) for identifier in identifiers]
            local = originals[0].repository_id == originals[1].repository_id
            pending = local and connection.execute("SELECT 1 FROM knowledge_rules r JOIN knowledge_sources s ON r.id=s.rule_id "
                "WHERE s.experience_id IN (?,?) AND r.state='pending' LIMIT 1", identifiers).fetchone()
            if pending or local and conflict_verifier and not conflict_verifier.ready([s.model_dump(mode='json') for s in originals]):
                _pair_result(path, job, pair, 'waiting', '等待来源任务快照冻结与同项目规则整理。')
                continue
            try:
                if not local:
                    _sources(connection, identifiers)
            except ValueError:
                paused = connection.execute('SELECT 1 FROM knowledge_pauses p JOIN knowledge_sources s ON p.rule_id=s.rule_id '
                                            'WHERE s.experience_id IN (?,?) LIMIT 1', identifiers).fetchone()
                _pair_result(path, job, pair, 'rejected' if paused else 'waiting',
                             '来源规则已在冲突处理中暂停，保留原经验。' if paused else '等待规则整理或冲突审阅；当前不合并。')
                continue
        if not _pair_result(path, job, pair, 'running', '正在归纳共同规则并独立复核。'):
            return
        model = None
        try:
            model = llm_factory()
            if local:
                from .local_equivalence import compare
                result = compare(path, job, pair, model, conflict_verifier)
                _pair_result(path, job, pair, result['state'], result['reason'], result)
                break
            from .cross_conflicts import detect
            conflict_check = detect(path, job, pair, model)
            if conflict_check['conflict_ids']:
                break  # Already atomically published through normal rule reviews.
            result = consolidate_principle(path, identifiers, model,
                                           namespace=f'project-repairs-v1/attempt-{pair["attempt"]}')
            result.update(conflict_check=conflict_check, model_requests=getattr(model, 'audit', []))
            state = result['state'] if result['state'] in {'succeeded', 'rejected'} else 'failed'
            reason = ((result.get('review') or {}).get('reason') or
                      (result.get('proposal') or {}).get('reason') or '比较未完成，可重试。')
            _pair_result(path, job, pair, state, reason, result)
        except Exception:
            logger.exception('Automatic experience comparison failed for pair=%s', pair['id'])
            with connect(path) as connection:
                previous = connection.execute('SELECT result FROM experience_auto_pairs WHERE id=?', (pair['id'],)).fetchone()
            result = json.loads(previous[0]) if previous and previous[0] else {}
            result['model_requests'] = getattr(model, 'audit', [])
            _pair_result(path, job, pair, 'failed', '经验比较或复核失败；原始经验和已完成的修复已保存。', result)
        finally:
            if model and hasattr(model, 'close'):
                with suppress(Exception):
                    model.close()
        # One model pair per claim: publish progress and service shutdown promptly.
        break
    from .automatic_resolution import run_pending
    run_pending(path, job, conflict_verifier)
    with connect(path) as connection:
        states = [r[0] for r in connection.execute('SELECT p.state FROM experience_auto_pairs p '
                  'JOIN experience_auto_links l ON l.pair_id=p.id WHERE l.job_id=?', (job['id'],))]
        from .reviews import read
        pending_review = any(read(connection, row).status == 'pending' for row in connection.execute(
            "SELECT DISTINCT r.* FROM knowledge_relations r JOIN knowledge_sources s "
            "ON s.rule_id IN (r.left_rule,r.right_rule) WHERE s.experience_id=? "
            "AND r.status='pending' AND r.kind IN ('conflict','unknown')", (job['id'],)))
    state = ('pending' if any(s in {'pending', 'running'} for s in states) else
             'waiting' if 'waiting' in states or pending_review else 'failed' if 'failed' in states else 'succeeded')
    notice = {'pending': '继续比较已有经验。', 'waiting': '等待后台整理或冲突审阅，处理后自动继续。',
              'failed': '部分比较未完成，可单独重试经验比较。',
              'succeeded': '比较完成。' if states else '当前没有更早且符合比较条件的经验；后续修复会自动与本条比较。'}[state]
    _finish(path, job, state, notice)


def retry(path, identifier):
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT * FROM experience_auto_jobs WHERE id=?', (identifier,)).fetchone()
        if row is None:
            return False
        # Double-clicks cannot increment attempts twice or steal a live lease.
        if row['state'] not in {'failed', 'waiting'}:
            return True
        connection.execute('INSERT OR IGNORE INTO experience_auto_attempts '
            'SELECT id,attempt,state,result,notice,updated_at FROM experience_auto_pairs '
            "WHERE state='failed' AND id IN (SELECT pair_id FROM experience_auto_links WHERE job_id=?)", (identifier,))
        connection.execute("UPDATE experience_auto_pairs SET state='pending',attempt=attempt+1,result=NULL "
                           "WHERE state='failed' AND id IN (SELECT pair_id FROM experience_auto_links WHERE job_id=?)",
                           (identifier,))
        connection.execute("UPDATE experience_auto_jobs SET state='pending',available_at=0,notice='',updated_at=? WHERE id=?",
                           (now(), identifier))
        return True


def review_changed(connection, rule_ids):
    """Resume only eligibility checks, never repeat a finished model decision."""
    marks = ','.join('?' for _ in rule_ids)
    if not marks:
        return
    affected = [r[0] for r in connection.execute(
        f'SELECT DISTINCT experience_id FROM knowledge_sources WHERE rule_id IN ({marks})', rule_ids)]
    for identifier in affected:
        connection.execute("UPDATE experience_auto_pairs SET state='waiting' WHERE result IS NULL "
            "AND state='rejected' AND (left_id=? OR right_id=?)", (identifier, identifier))
        connection.execute("UPDATE experience_auto_jobs SET state='pending',available_at=0,updated_at=? "
            "WHERE state!='running' AND (id=? OR id IN (SELECT l.job_id FROM experience_auto_links l "
            "JOIN experience_auto_pairs p ON p.id=l.pair_id WHERE p.state='waiting' AND (p.left_id=? OR p.right_id=?)))",
            (now(), identifier, identifier, identifier))


def _local_changes(path, identifier, *, connection, blocked):
    from .knowledge import source_status, provenance_page
    from .reviews import read, history
    changes, seen = [], set()
    status = source_status(path, identifier, connection=connection, blocked=blocked)
    for rule in status['rules']:
        if rule['state'] != 'ready' or rule['effective_status'] != 'active':
            continue
        provenance = provenance_page(path, rule['id'], limit=20, connection=connection, blocked=blocked)
        ids = provenance['source_ids']
        if len(ids) < 2 or tuple(ids) in seen:
            continue
        seen.add(tuple(ids))
        sources = [_source(connection, i).model_dump(mode='json') for i in ids]
        changes.append(dict(id=rule['id'], kind='equivalent_rules', state='merged', before=sources,
            after=dict(title='共同修复规则', **rule['data']), reason='相同或已验证等价的规则已合并引用，原始记录和独立验证证据保留。',
            evidence_count=provenance['evidence_count'], more_sources=provenance['next_offset'] is not None))
    rows = connection.execute("SELECT DISTINCT r.* FROM knowledge_relations r JOIN knowledge_sources s "
        "ON s.rule_id IN (r.left_rule,r.right_rule) WHERE s.experience_id=? "
        "AND (r.kind IN ('unknown','conflict') OR r.status='resolved') ORDER BY r.created_at DESC LIMIT 20",
        (identifier,)).fetchall()
    for row in rows:
        if json.loads(row['details']).get('policy') == 'same-project-evidence-equivalence-v1' and row['kind']=='equivalent':
            merged = next((c for c in changes if c['kind']=='equivalent_rules'
                           and {s.id for s in read(connection, row).sources} <= {s['id'] for s in c['before']}), None)
            if merged:
                merged['history'] = history(path, row['id'], connection=connection)
                merged['reason'] = row['note'] or merged['reason']
                continue
        review = read(connection, row)
        changes.append(dict(id=row['id'], kind='review', state=review.status,
            before=[s.model_dump(mode='json') for s in review.sources], after=None,
            reason=json.loads(row['details']).get('reason', ''), review=review.model_dump(mode='json'),
            history=history(path, row['id'], connection=connection)))
    return changes, any(r['state'] == 'pending' for r in status['rules'])


def feed(path, *, limit=5, offset=0, language=None, repository_id=None, scope=None, experience_id=None):
    from .principles import current_principle
    from .database import read_only
    from .knowledge import effective_rules
    from .recovery import active_grants
    if scope == 'global':
        return {'items': [], 'next_offset': None}
    clauses, params = [], []
    for field, value in [('language', language), ('repository_id', repository_id)]:
        if value is not None:
            clauses.append(f"json_extract(e.details,'$.{field}')=?"); params.append(value)
    if experience_id:
        clauses.append('j.id=?'); params.append(experience_id)
    where = ' WHERE '+' AND '.join(clauses) if clauses else ''
    with read_only(path) as connection:
        connection.execute('BEGIN')
        jobs = connection.execute('SELECT j.* FROM experience_auto_jobs j JOIN experiences e ON e.id=j.id'+where+
                                  ' ORDER BY j.created_at DESC,j.id DESC LIMIT ? OFFSET ?', (*params, limit+1, offset)).fetchall()
        # Check evidence once for this response, on the same database snapshot.
        # Do not reuse this result across requests or on the repair retrieval path.
        blocked = effective_rules(connection) - active_grants(connection).keys() if jobs[:limit] else set()
        result = []
        for job in jobs[:limit]:
            changes, local_pending = _local_changes(path, job['id'], connection=connection, blocked=blocked)
            source = _source(connection, job['id'])
            pairs = connection.execute('SELECT p.* FROM experience_auto_pairs p JOIN experience_auto_links l ON p.id=l.pair_id '
                                       'WHERE l.job_id=? ORDER BY p.id', (job['id'],)).fetchall()
            for pair in pairs:
                value = json.loads(pair['result']) if pair['result'] else {}
                inputs = json.loads(pair['inputs'])
                if inputs[0]['repository_id'] == inputs[1]['repository_id']:
                    already_merged = any(c['state']=='merged' and {s['id'] for s in inputs} <= {s['id'] for s in c['before']}
                                         for c in changes)
                    if not value.get('relation_ids') and not already_merged:
                        changes.append(dict(id=pair['id'], kind='equivalent_rules', state=pair['state'],
                            before=inputs, after=None, reason=pair['notice']))
                    continue  # Canonical provenance and normal decision history above are authoritative.
                if value.get('conflict_check', {}).get('conflict_ids'):
                    continue  # The normal review above is the single source of displayed resolution state.
                principle = current_principle(path, value['id'], connection=connection) if pair['state'] == 'succeeded' else None
                state = ('merged' if principle else 'invalidated') if pair['state'] == 'succeeded' else pair['state']
                changes.append(dict(id=pair['id'], kind='shared_principle', state=state, before=json.loads(pair['inputs']),
                    after=principle, reason=pair['notice'] if state != 'invalidated' else '来源已变更、停用或有待审阅规则；历史合并当前不可复用。',
                    proposal=value.get('proposal'), review_result=value.get('review')))
            selected = len(json.loads(job['candidates'])) if job['candidates'] else 0
            result.append(dict(id=job['id'], source=source.model_dump(mode='json'), state=job['state'],
                notice=job['notice'], created_at=job['created_at'], updated_at=job['updated_at'],
                local_pending=local_pending, candidate_total=job['candidate_total'], selected_candidates=selected,
                deferred_candidates=max(0, job['candidate_total']-selected), changes=changes))
        return {'items': result, 'next_offset': offset+limit if len(jobs)>limit else None}


class AutomaticMergeWorker:
    def __init__(self, path, llm_factory, conflict_verifier=None, recovery=None):
        self.path, self.llm_factory = path, llm_factory
        self.conflict_verifier = conflict_verifier
        self.recovery = recovery
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='experience-auto-merge', daemon=True)

    def start(self):
        reschedule_local_comparisons(self.path)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=3)

    def run_once(self):
        job = claim(self.path)
        if not job:
            from .history import run_once
            return run_once(self.path, self.llm_factory) or bool(self.recovery and self.recovery.run_once())
        done = threading.Event()

        def heartbeat():
            while not done.wait(20):
                with suppress(Exception), connect(self.path) as connection:
                    connection.execute('UPDATE experience_auto_jobs SET lease_until=? WHERE id=? AND lease_token=?',
                                       (time.time()+LEASE_SECONDS, job['id'], job['lease_token']))

        pulse = threading.Thread(target=heartbeat, daemon=True)
        pulse.start()
        try:
            process(self.path, job, self.llm_factory, self.conflict_verifier)
        except Exception:
            logger.exception('Automatic experience job failed for source=%s', job['id'])
            _finish(self.path, job, 'failed', '自动比较暂未完成；可重试，已保存的经验不受影响。')
        finally:
            done.set()
            pulse.join(timeout=1)
        return True

    def run(self):
        while not self.stop_event.is_set():
            try:
                if not self.run_once():
                    self.stop_event.wait(1)
            except Exception:
                logger.exception('Automatic experience queue unavailable')
                self.stop_event.wait(2)
