"""Read-only knowledge cards: canonical rules, provenance and unverified archives."""
import json

from .database import read_only as connect
from .equivalence import equivalent_groups
from .history import archive_groups, public, records
from .knowledge import CURRENT_RULES, effective_rules


def library(path, *, language=None, repository_id=None, scope=None, q='',error_type=None,state=None,tag=None,
            merged=None,limit=None,offset=0,sort='recent',projects=None,card_id=None):
    with connect(path) as connection:
        connection.execute('BEGIN')
        sources={s.id:s for s in records(connection)}
        matches=lambda s: (not language or s.language==language) and (not repository_id or s.repository_id==repository_id) and (not scope or s.scope==scope)
        visible=dict(sources)
        blocked=effective_rules(connection)
        groups=archive_groups(connection)
        archived={i for group in groups for i in group['source_ids']}
        rows=connection.execute(f'SELECT * FROM knowledge_rules WHERE id IN ({CURRENT_RULES}) ORDER BY created_at DESC,id').fetchall()
        relations={(r[0],r[1]):r[2] for r in connection.execute('SELECT left_rule,right_rule,kind FROM knowledge_relations')}
        dependencies={}
        for parent,child in connection.execute('SELECT parent_id,child_id FROM knowledge_prerequisites'):
            dependencies.setdefault(child,set()).add(parent)
        eligible=[r['id'] for r in rows if r['state']=='ready' and r['id'] not in blocked]
        equivalents=equivalent_groups(eligible,relations,dependencies)
        source_links={}
        for rule,identity in connection.execute('SELECT DISTINCT rule_id,experience_id FROM knowledge_sources'):
            if identity in sources:source_links.setdefault(rule,set()).add(identity)
        cards=[];seen=set()
        for row in rows:
            identities=equivalents.get(row['id'],{row['id']})
            if identities & seen:continue
            seen.update(identities)
            ids=sorted(set().union(*(source_links.get(i,set()) for i in identities)))
            if not set(ids)&visible.keys() or set(ids)<=archived:continue
            data=json.loads(row['data']); issue=data['issue']
            unverified=data['verification_class']!='verified'
            rule_state='unverified' if unverified else 'paused' if row['id'] in blocked else 'active'
            evidence=set()
            for identity in identities:
                evidence.update(r[0] for r in connection.execute('SELECT evidence_id FROM knowledge_rule_evidence WHERE rule_id=?',(identity,)))
            target=(issue.get('symbols') or issue.get('files') or [issue['error_type']])[0]
            cards.append(dict(id=row['id'],kind='rule',state=rule_state,title=target.split('#')[-1].split('/')[-1],
                summary=issue['solution'],root_cause=issue['root_cause'],language=data['language'],error_type=issue['error_type'],
                applicability=data['applicability'],constraints=data['constraints'],source_ids=ids,
                sources=[sources[i].model_dump(mode='json') for i in ids[:20]],source_count=len(ids),evidence_count=len(evidence),
                merged=len(ids)>1,reason=('相同内容已归并引用；' if len(ids)>1 else '')+'缺少可核对的测试证据，暂不自动复用。' if unverified else
                '相关规则已隔离或尚未完成检查。' if rule_state=='paused' else
                '仅保留一条规范规则，来源与独立测试分别保存。' if len(ids)>1 else '已验证，可在适用范围内复用。',
                created_at=row['created_at']))
        for group in groups:
            if not set(group['source_ids']) & visible.keys():continue
            representative=sources[group['representative_id']]
            cards.append(dict(id=group['id'],kind='archive',state='unverified',title='历史经验归并',
                summary=representative.solution,root_cause=representative.root_cause,language=representative.language,
                error_type=representative.error_type or '',applicability=representative.applicability,constraints=representative.constraints,
                source_ids=group['source_ids'],sources=[sources[i].model_dump(mode='json') for i in group['source_ids']],
                source_count=len(group['source_ids']),evidence_count=0,merged=True,
                reason='语义与业务条件已独立复核。原任务和结构化测试证据缺失，归并后仍待验证，不自动注入修复。',
                grouping_reason=group['reason'],created_at=representative.created_at.isoformat()))
        # Cross-project principles preserve each concrete business rule, but must
        # still be visible as a single result in the knowledge view.
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_principles'").fetchone():
            from .principles import current_principle
            for row in connection.execute('SELECT id FROM knowledge_principles'):
                principle=current_principle(path,row[0],connection=connection)
                if not principle or not set(principle['source_ids'])&visible.keys():continue
                members=[sources[i] for i in principle['source_ids']]
                evidence={(e.source_task_id,e.tool_call_id) for source in members for e in source.evidence}
                cards.append(dict(id=principle['id'],kind='principle',state='active',title=principle['title'],
                    summary=principle['principle'],root_cause='多个独立修复任务支持相同的处理方式，各任务的具体业务条件继续保留。',
                    language=members[0].language,error_type='共同处理原则',applicability=principle['applicability'],
                    constraints=principle['boundaries'],source_ids=principle['source_ids'],
                    sources=[source.model_dump(mode='json') for source in members],source_count=len(members),
                    evidence_count=len(evidence),merged=True,created_at=max(s.created_at for s in members).isoformat(),
                    reason='共同原则已通过来源引证与独立复核；只在列明条件下适用，目标项目仍须测试。'))
        # A scoped grant changes only target-bound retrieval. Broad conflicting
        # rules and their original decision history remain isolated.
        from .recovery import active_grants, public as recovery_public
        grants=active_grants(connection)
        restricted={r[0] for r in connection.execute('SELECT rule_id FROM knowledge_reuse_grants')}
        for card in cards:
            if card['id'] in restricted and card['id'] not in grants:
                card.update(state='paused',reason='补充验证授权已失效或正在重新验证，当前暂停复用。')
        by_id={c['id']:c for c in cards};hidden=set()
        for rule_id,grant in grants.items():
            card=by_id.get(grant['card_id'])
            if not card:continue
            job=connection.execute('SELECT * FROM knowledge_recoveries WHERE id=?',(grant['recovery_id'],)).fetchone()
            recovered=json.loads(job['data'])
            if card['id']!=rule_id:
                replacement=by_id.get(rule_id)
                if not replacement:continue
                ids=sorted(set(card['source_ids']+replacement['source_ids']),key=lambda identifier:(sources[identifier].created_at,identifier))
                card.update({k:v for k,v in replacement.items() if k not in {'id','kind','source_ids','sources','source_count','merged'}})
                card.update(kind='validated',source_ids=ids,sources=[sources[i].model_dump(mode='json') for i in ids],source_count=len(ids),merged=True)
                hidden.add(rule_id)
            card.update(state='active',reuse_mode='scoped',reuse_scope=recovered['scope'],
                        reason=recovered['reason'],evidence_count=card['evidence_count']+len(recovered['runs']))
        cards=[c for c in cards if c['id'] not in hidden]
        project_labels={p.repository_id:p.label for p in projects or []}
        for card in cards:
            members=[sources[i] for i in card['source_ids']]
            card['repository_ids']=sorted({s.repository_id for s in members if s.repository_id})
            card['project_labels']=[project_labels.get(r,r) for r in card['repository_ids']]
            card['tags']=sorted({t for s in members for t in s.tags})
            card['scope']='project' if card['repository_ids'] else 'global'
            projection_id=next((r for r,g in grants.items() if g['card_id']==card['id']),card['id'])
            rule=connection.execute('SELECT data FROM knowledge_rules WHERE id=?',(projection_id,)).fetchone()
            issue=json.loads(rule[0])['issue'] if rule else {}
            card['files']=issue.get('files',[]);card['symbols']=issue.get('symbols',[])
            job=connection.execute('SELECT * FROM knowledge_recoveries WHERE card_id=? ORDER BY created_at DESC LIMIT 1',(card['id'],)).fetchone()
            if job:
                value=recovery_public(job,connection)
                card['recovery']={k:value[k] for k in ('id','state','reason','version','task_id','created_at','updated_at')}
        all_cards=cards
        from collections import Counter
        def facets(field):
            counts=Counter(v for c in all_cards for v in (c[field] if isinstance(c[field],list) else [c[field]]) if v)
            return [dict(value=k,label=project_labels.get(k,k),count=v) for k,v in sorted(counts.items())]
        facets_data=dict(languages=facets('language'),repositories=facets('repository_ids'),error_types=facets('error_type'),tags=facets('tags'))
        cards=[c for c in cards if (not language or c['language']==language) and (not repository_id or repository_id in c['repository_ids'])
               and (not scope or c['scope']==scope) and (not error_type or c['error_type']==error_type) and (not tag or tag in c['tags'])]
        summary_cards=list(cards)
        query_words=q.casefold().split()
        cards=[c for c in cards if (not card_id or c['id']==card_id) and (not state or c['state']==state) and (merged is None or c['merged']==merged)
               and all(word in ' '.join([c['title'],c['summary'],c['root_cause'],c['error_type'],*c['tags'],*c['files'],*c['symbols'],*c['project_labels']]).casefold() for word in query_words)]
        from .retrieval import lexical_candidates
        ranked={identifier:i for i,identifier in enumerate(lexical_candidates(connection,q,1000))} if q else {}
        cards.sort(key=lambda c:(ranked.get(c['id'],1001),-c['source_count'],c['id']) if q and sort=='relevance'
                   else (-c['source_count'],c['id']) if sort=='sources' else (c.get('recovery',{}).get('updated_at',c['created_at']),c['id']),reverse=sort=='recent')
        total=len(cards)
        visible_ids={identity for c in summary_cards for identity in c['source_ids']}
        visible={i:s for i,s in sources.items() if i in visible_ids and matches(s)}
        latest=connection.execute('SELECT * FROM experience_history_scans ORDER BY created_at DESC LIMIT 1').fetchone()
        pending_reviews=sum(1 for r in connection.execute("SELECT * FROM knowledge_relations WHERE status='pending' AND kind IN ('unknown','conflict')")
            if not {r['left_rule'],r['right_rule']} & {v[0] for v in connection.execute('SELECT rule_id FROM knowledge_pauses')}
            and any(source_links.get(i,set())&visible.keys() for i in (r['left_rule'],r['right_rule'])))
        return dict(items=cards[offset:offset+limit] if limit is not None else cards,summary=dict(original_records=len(visible),knowledge_cards=len(summary_cards),
            active=sum(c['state']=='active' for c in summary_cards),paused=sum(c['state']=='paused' for c in summary_cards),
            unverified=sum(c['state']=='unverified' for c in summary_cards),merged=sum(c['merged'] for c in summary_cards),pending_reviews=pending_reviews),
            total=total,offset=offset,limit=limit,next_offset=offset+limit if limit and offset+limit<total else None,
            facets=facets_data,history_scan=public(latest) if latest else None)
