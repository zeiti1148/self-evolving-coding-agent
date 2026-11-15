"""Model references assembled from checked rules; full history stays in storage."""
import json
import re

from .database import read_only as connect
from .knowledge import effective_rules, rule_text
from .equivalence import equivalent_groups


def lexical_candidates(connection, query, limit=50):
    terms=list(dict.fromkeys(re.findall(r'[A-Za-z_0-9]{3,}|[\u4e00-\u9fff]{3}',query)))[:32]
    if not terms or not connection.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_fts'").fetchone():
        return []
    expression=' OR '.join('"'+term.replace('"','""')+'"' for term in terms)
    return [r[0] for r in connection.execute('SELECT rule_id FROM knowledge_fts WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?',
                                            (expression,limit))]


def retrieve(path, query='', *, language, repository_id, error_types, capabilities=None,
             compatibility_key=None, source_revision=None, runner_hash=None, limit=5, character_budget=16000, semantic=None):
    from .semantic import runtime
    semantic = semantic or runtime()
    with connect(path) as connection:
        connection.execute('BEGIN')
        from .store import SUPERSEDED, RETIRED, QUARANTINED
        inactive_sources={r[0] for r in connection.execute(f'{SUPERSEDED} UNION {RETIRED} UNION {QUARANTINED}')}
        active_rules={r['rule_id'] for r in connection.execute('SELECT rule_id,experience_id FROM knowledge_sources')
                      if r['experience_id'] not in inactive_sources}
        blocked=effective_rules(connection)
        from .recovery import active_grants
        grant_context=dict(repository_id=repository_id,compatibility_key=compatibility_key or source_revision,
                           source_revision=source_revision,runner_hash=runner_hash)
        grants=active_grants(connection,context=grant_context)
        scoped_ids={r[0] for r in connection.execute('SELECT rule_id FROM knowledge_reuse_grants')} if connection.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_reuse_grants'").fetchone() else set()
        blocked-=grants.keys()
        rows=connection.execute("SELECT * FROM knowledge_rules WHERE language=? AND (scope='global' OR repository_id=?) AND state='ready'",
                                (language,repository_id or '')).fetchall()
        lexical=lexical_candidates(connection,query)
        relation_rows = connection.execute('SELECT left_rule,right_rule,kind,version FROM knowledge_relations').fetchall()
        relation_version = [tuple(r) for r in relation_rows]
        relations={(r['left_rule'],r['right_rule']):r['kind'] for r in relation_rows}
        dependencies={}
        # Provenance invalidates descendants, but private source rules are not
        # execution prerequisites of a published, self-contained global rule.
        prerequisite_rows = connection.execute('SELECT parent_id,child_id FROM knowledge_prerequisites ORDER BY parent_id,child_id').fetchall()
        prerequisite_version = [tuple(r) for r in prerequisite_rows]
        for row in prerequisite_rows:
            dependencies.setdefault(row['child_id'],set()).add(row['parent_id'])
        embedding_rows=connection.execute('SELECT rule_id,vector FROM knowledge_embeddings WHERE model_version=?',
                                          (semantic.version,)).fetchall() if semantic.available else []
        entries=[]
        for row in rows:
            if row['id'] in blocked or row['id'] not in active_rules or row['id'] in scoped_ids and row['id'] not in grants:
                continue
            data=json.loads(row['data'])
            if not set(data['required_capabilities']).issubset(capabilities or []):
                continue
            target=compatibility_key or source_revision
            if target and data['scope']=='project' and data['compatibility_key']!=target:
                continue
            entries.append((dict(row),data))
    vector_scores={}
    if query and embedding_rows:
        import numpy as np
        vector=semantic.embed([query],query=True)
        if vector is not None:
            vector_scores={r[0]:float(np.frombuffer(r[1],dtype='float32') @ vector[0]) for r in embedding_rows}
    vector_rank={key:i for i,(key,_) in enumerate(sorted(vector_scores.items(),key=lambda v:v[1],reverse=True)[:50])}
    lexical_rank={key:i for i,key in enumerate(lexical)}
    wanted={v.casefold() for v in error_types}
    def score(item):
        row,data=item
        rank=(1/(60+lexical_rank[row['id']]) if row['id'] in lexical_rank else 0)+(1/(60+vector_rank[row['id']]) if row['id'] in vector_rank else 0)
        overlap=data['issue']['error_type'].casefold() in wanted
        return (data['scope']=='project',overlap,rank,row['created_at'],row['id'])
    entries.sort(key=score,reverse=True)
    by_id={row['id']:(row,data) for row,data in entries}
    # Only combinations actually eligible for this query need to coexist. Keep
    # the full prerequisite closure, including parents with another error type.
    relevant={identity for identity,(_,data) in by_id.items()
              if not wanted or data['issue']['error_type'].casefold() in wanted}
    pending=list(relevant)
    while pending:
        for parent in dependencies.get(pending.pop(),set()):
            if parent not in relevant:
                relevant.add(parent)
                pending.append(parent)
    by_id={identity:item for identity,item in by_id.items() if identity in relevant}
    entries=[item for item in entries if item[0]['id'] in by_id]
    # Unknown overlapping combinations must not be decided by recency or rank.
    ambiguous=set()
    for (a,b),kind in relations.items():
        if kind in {'unknown','conflict'} and a in by_id and b in by_id:
            ambiguous.update((a,b))
    changed=True
    while changed:
        previous=len(ambiguous)
        for (a,b),kind in relations.items():
            if kind=='equivalent' and a in by_id and b in by_id and ({a,b} & ambiguous):
                ambiguous.update((a,b))
        changed=len(ambiguous)!=previous
    groups = equivalent_groups(by_id,relations,dependencies)
    representatives={identity:min(group) for identity,group in groups.items()}
    for identity,group in groups.items():
        if group & ambiguous:
            ambiguous.update(group)
    picked=[]
    def closure(identifier,seen):
        if identifier in seen:
            return []
        if identifier not in by_id or identifier in ambiguous:
            raise ValueError('dependency unavailable')
        seen.add(identifier)
        result=[]
        for parent in sorted(dependencies.get(identifier,set())):
            result.extend(closure(parent,seen))
        return [*result,identifier]
    for row,data in entries:
        identity=row['id']
        if wanted and data['issue']['error_type'].casefold() not in wanted:
            continue
        if identity in ambiguous or identity in picked:
            continue
        if identity in representatives and any(representatives.get(previous)==representatives[identity] for previous in picked):
            continue
        try:
            additions=[v for v in closure(identity,set()) if v not in picked]
        except ValueError:
            continue
        if len(picked)+len(additions)>limit:
            continue
        if sum(len(rule_text(by_id[v][1])) for v in [*picked,*additions])>character_budget:
            continue
        picked.extend(additions)
    references=[]
    with connect(path) as connection:
        connection.execute('BEGIN')
        # A decision may have changed while a local embedding was calculated.
        blocked=effective_rules(connection)
        current_grants=active_grants(connection,context=grant_context)
        if any(identity not in current_grants or current_grants[identity]!=value for identity,value in grants.items()):
            return []
        blocked-=current_grants.keys()
        current_relations=[tuple(r) for r in connection.execute('SELECT left_rule,right_rule,kind,version FROM knowledge_relations')]
        current_prerequisites=[tuple(r) for r in connection.execute('SELECT parent_id,child_id FROM knowledge_prerequisites ORDER BY parent_id,child_id')]
        current_inactive={r[0] for r in connection.execute(f'{SUPERSEDED} UNION {RETIRED} UNION {QUARANTINED}')}
        used_members=set().union(*(groups[v] for v in picked)) if picked else set()
        if (current_relations!=relation_version or current_prerequisites!=prerequisite_version
                or current_inactive!=inactive_sources or used_members & blocked):
            return []
        for identity in picked:
            row,data=by_id[identity]
            members=sorted(groups[identity])
            placeholders=','.join('?' for _ in members)
            sources=[r[0] for r in connection.execute('SELECT DISTINCT s.experience_id,e.created_at FROM knowledge_sources s JOIN experiences e '
                f'ON e.id=s.experience_id WHERE s.rule_id IN ({placeholders}) ORDER BY e.created_at,s.experience_id',members) if r[0] not in inactive_sources]
            if not sources:
                continue
            # Evidence is a set of task/tool-call observations, not a sum of
            # links. A single run may support multiple equivalent projections.
            source_placeholders=','.join('?' for _ in sources)
            evidence=[json.loads(r[0]) for r in connection.execute('SELECT DISTINCT e.data FROM knowledge_evidence e '
                'JOIN knowledge_rule_evidence l ON l.evidence_id=e.id '
                f'WHERE l.rule_id IN ({placeholders}) AND json_extract(e.data,\'$.source_task_id\') IN '
                f'(SELECT source_task_id FROM experiences WHERE id IN ({source_placeholders}))',[*members,*sources])]
            references.append(dict(id=sources[0],rule_id=identity,schema_version=4,language=data['language'],scope=data['scope'],
                repository_id=data['repository_id'] or None,reuse_status='active',
                task_summary=data['issue']['root_cause'],error_type=data['issue']['error_type'],
                root_cause=data['issue']['root_cause'],solution=data['issue']['solution'],
                applicability=data['applicability'],constraints=data['constraints'],
                compatibility_key=data['compatibility_key'] or None,required_capabilities=data['required_capabilities'],
                issues=[{k:v for k,v in data['issue'].items() if data['scope']=='project' or k not in {'files','symbols'}}],
                evidence_summary=dict(verified_runs=len(evidence),level='source_project_tests',
                    minimum_tests=min((v['tests'] for v in evidence),default=0)),
                **({'source_ids':sources[:5]} if data['scope']=='project' else {})))
            if identity in current_grants:
                grant=current_grants[identity]
                references[-1]['reuse_boundary']=dict(repository_id=grant['repository_id'],source_revision=grant['source_revision'],
                    runner_hash=grant['runner_hash'],recovery_id=grant['recovery_id'],mode='verified_context_only')
                references[-1]['applicability']=[*references[-1]['applicability'],'仅限已核对的来源项目、代码版本与测试配置；其他上下文必须重新验证。']
    for reference in references:
        if reference['scope']=='global':
            reference.pop('repository_id',None)
            reference.pop('compatibility_key',None)
    return references
