"""Evidence-gated decisions, sharing the normal review history and queue lease."""
import json
import time
from uuid import uuid4

from backend.shared import ConflictResolution
from .database import connect
from .knowledge import canonical, digest, now
from .reviews import read, resolve_in_transaction

POLICY = 'verified-cross-tests-v1'
SCHEMA = '''CREATE TABLE IF NOT EXISTS knowledge_auto_reviews (
 review_id TEXT PRIMARY KEY REFERENCES knowledge_relations(id), version INTEGER NOT NULL,
 token TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
)'''


def decide(runs, identifiers):
    """No model votes, dates or pass-count majority may choose a winner."""
    matrix = {}
    for target in identifiers:
        own = [r for r in runs if r['target_source_id'] == target and r['implementation_source_id'] == target]
        cross = [r for r in runs if r['target_source_id'] == target and r['implementation_source_id'] != target]
        if len(own) != 1 or len(cross) != 2 or own[0]['outcome'] != 'passed':
            return None, None, '缺少双方完整的本地通过与重复交叉测试证据。'
        if any(r['outcome'] not in {'passed', 'failed'} or r['test_identity'] != own[0]['test_identity']
               or r['tests'] != own[0]['tests'] for r in cross):
            return None, None, '交叉测试的测试集合不一致或执行环境异常。'
        if (cross[0]['outcome'], cross[0]['failed_tests']) != (cross[1]['outcome'], cross[1]['failed_tests']):
            return None, None, '重复交叉测试结果不稳定，不能自动决定。'
        if cross[0]['outcome'] == 'failed' and not cross[0]['reproduces_baseline']:
            return None, None, '另一实现产生了非原始缺陷失败，无法排除移植不兼容。'
        matrix[cross[0]['implementation_source_id']] = cross[0]['outcome'] == 'passed'
    winners = [identity for identity in identifiers if matrix.get(identity)]
    if len(winners) == 1:
        return 'prefer_source', winners[0], '一方真实实现通过双方冻结测试；另一方重复复现原始缺陷，自动采纳覆盖双方测试的来源。'
    if not winners:
        return 'keep_separate', None, '双方各自在原测试上下文通过，交叉使用却重复复现原始缺陷。自动分别保留并隔离互斥规则，暂停其自动复用；不按时间新旧选择。'
    return None, None, '双方实现均通过交叉测试，现有测试不能区分冲突，需用户确认适用条件。'


def run_pending(path, job, verifier):
    if verifier is None:
        return
    with connect(path) as connection:
        rows = connection.execute("SELECT DISTINCT r.* FROM knowledge_relations r JOIN knowledge_sources s "
            "ON s.rule_id IN (r.left_rule,r.right_rule) WHERE s.experience_id=? "
            "AND r.status='pending' AND r.kind IN ('conflict','unknown') ORDER BY r.created_at,r.id",
            (job['id'],)).fetchall()
    for candidate in rows:
        with connect(path) as connection:
            preliminary = read(connection, candidate)
        if hasattr(verifier, 'ready') and not verifier.ready([s.model_dump(mode='json') for s in preliminary.sources]):
            continue  # Extraction can enqueue just before the task freezes its final snapshot.
        with connect(path) as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM knowledge_relations WHERE id=?', (candidate['id'],)).fetchone()
            review = read(connection, row)
            if review.status != 'pending':
                continue
            previous = connection.execute('SELECT * FROM knowledge_auto_reviews WHERE review_id=?', (review.id,)).fetchone()
            if previous:
                if previous['state'] == 'verifying':
                    data = json.loads(previous['data'])
                    owner = connection.execute("SELECT 1 FROM experience_auto_jobs WHERE id=? AND lease_token=? AND state='running' AND lease_until>?",
                        (data.get('worker_job_id'), data.get('worker_token'), time.time())).fetchone()
                    if owner:
                        continue
                    data.update(state='needs_confirmation', reason='自动验证中断；保留已有证据，请用户确认。', updated_at=now())
                    connection.execute("UPDATE knowledge_auto_reviews SET state='needs_confirmation',data=?,updated_at=? WHERE review_id=?",
                                       (canonical(data), now(), review.id))
                continue
            token = str(uuid4())
            data = dict(state='verifying', actor='system', policy=POLICY, stage='snapshot',
                reason='正在核对双方任务快照并准备隔离交叉验证。', runs=[], targets=[],
                resolution=None, preferred_source_id=None, started_at=now(), updated_at=now(), evidence_hash=None)
            data.update(worker_job_id=job['id'], worker_token=job['lease_token'])
            connection.execute('INSERT INTO knowledge_auto_reviews VALUES(?,?,?,?,?,?,?)',
                (review.id, review.version, token, 'verifying', canonical(data), now(), now()))
            sources = [s.model_dump(mode='json') for s in review.sources]
            source_hash = digest(sources)
            assessment = json.loads(row['details'])

        def progress(stage, reason, **updates):
            data.update(stage=stage, reason=reason, updated_at=now(), **updates)
            with connect(path) as connection:
                current = connection.execute('SELECT version,status FROM knowledge_relations WHERE id=?', (review.id,)).fetchone()
                lease = connection.execute('SELECT 1 FROM experience_auto_jobs WHERE id=? AND lease_token=?',
                                           (job['id'], job['lease_token'])).fetchone()
                if not lease or tuple(current) != (review.version, 'pending'):
                    raise ValueError('Review or worker lease changed during evidence verification')
                changed = connection.execute('UPDATE knowledge_auto_reviews SET data=?,updated_at=? '
                    "WHERE review_id=? AND token=? AND state='verifying'", (canonical(data), now(), review.id, token)).rowcount
                if not changed:
                    raise ValueError('Automatic review is no longer current')

        try:
            if review.relation != 'conflict' or review.version != 1:
                raise ValueError('Only a newly confirmed conflict can be resolved automatically')
            targets = assessment.get('targets', [])
            if assessment.get('policy') != 'cross-upload-conflicts-v1' or not targets:
                # Same-repository classifiers do not yet carry independently grounded quotes.
                raise ValueError('Missing independently reviewed, grounded conflict targets')
            evidence = verifier.verify(review.id, sources, targets, progress)
            data.update(evidence)
            resolution, preferred, reason = decide(data['runs'], [s['id'] for s in sources])
            data.update(reason=reason, stage='decision', resolution=resolution, preferred_source_id=preferred)
            if not resolution:
                data['state'] = 'needs_confirmation'
            else:
                with verifier.publication_guard(evidence):
                    with connect(path) as connection:
                        connection.execute('BEGIN IMMEDIATE')
                        current = connection.execute('SELECT * FROM knowledge_relations WHERE id=?', (review.id,)).fetchone()
                        live = read(connection, current)
                        lease = connection.execute('SELECT 1 FROM experience_auto_jobs WHERE id=? AND lease_token=?',
                                                   (job['id'], job['lease_token'])).fetchone()
                        active = connection.execute('SELECT state,token FROM knowledge_auto_reviews WHERE review_id=?', (review.id,)).fetchone()
                        if (not lease or tuple(active) != ('verifying', token) or live.status != 'pending'
                                or live.version != review.version or digest([s.model_dump(mode='json') for s in live.sources]) != source_hash):
                            raise ValueError('Sources or decision version changed before publication')
                        data.update(state='resolved', updated_at=now())
                        data['evidence_hash'] = digest(evidence)
                        decision = ConflictResolution(version=review.version, resolution=resolution,
                            preferred_source_id=preferred, note='系统自动处理：'+reason)
                        resolved = resolve_in_transaction(connection, review.id, decision,
                            automatic=dict(actor='system', policy=POLICY, evidence_hash=data['evidence_hash']))
                        data['result_version'] = resolved.version
                        connection.execute("UPDATE knowledge_auto_reviews SET state='resolved',data=?,updated_at=? WHERE review_id=? AND token=?",
                                           (canonical(data), now(), review.id, token))
                continue
        except Exception as exc:
            # Machine uncertainty is never turned into a guessed decision or a fake test pass.
            data.update(state='needs_confirmation', reason='证据不足，保留双方并交由用户确认。'+
                        (str(exc) if isinstance(exc, VerificationUnavailable) else '快照、执行条件或审阅状态未通过校验。'),
                        diagnostic=type(exc).__name__)
        data['updated_at'] = now()
        with connect(path) as connection:
            connection.execute('UPDATE knowledge_auto_reviews SET state=?,data=?,updated_at=? WHERE review_id=? AND token=?',
                               (data['state'], canonical(data), now(), review.id, token))


class VerificationUnavailable(ValueError):
    """Only program-authored, safe explanations may be shown to the user."""
