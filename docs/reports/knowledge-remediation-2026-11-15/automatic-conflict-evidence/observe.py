"""Read-only capture of actual automatic review transitions; never creates decisions."""
import json, sqlite3, time
from datetime import datetime, timezone
from live import RUN, ROOT

last = None
for _ in range(120):
    with sqlite3.connect((ROOT/'data/app.db').as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('SELECT review_id,state,data FROM knowledge_auto_reviews')]
        jobs = [dict(r) for r in db.execute('SELECT state,notice,updated_at FROM experience_auto_jobs WHERE id=?',
                                          ('14e95e0b-29b3-4ab1-8a91-085e7333ad19',))]
    current = dict(reviews=[dict(id=r['review_id'],state=r['state'],data=json.loads(r['data'])) for r in rows], jobs=jobs)
    signature=json.dumps(current,sort_keys=True,ensure_ascii=False)
    if signature != last:
        last=signature
        stamp=datetime.now(timezone.utc).isoformat()
        with (RUN/'observed-transitions.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(dict(observed_at=stamp,**current),ensure_ascii=False)+'\n')
        print(json.dumps(dict(observed_at=stamp,jobs=jobs,reviews=[dict(id=r['id'],state=r['state'],
            stage=r['data']['stage'],runs=len(r['data']['runs']),reason=r['data']['reason']) for r in current['reviews']]),
            ensure_ascii=False),flush=True)
    if rows and all(r['state']!='verifying' for r in rows) and jobs[0]['state'] not in ('running','pending'):
        break
    time.sleep(5)
