"""Deterministic merge analysis shared by manual and automatic workflows."""
from graphlib import CycleError, TopologicalSorter

from pydantic import ValidationError

from backend.projects import same_source_revision
from backend.shared import ExperienceCreate
from .store import _file_keys, _issue_key, _normalized, _source_issues, _unique_text


def detail(code, message, source_ids, field, suggestion):
    return dict(code=code, message=message, source_ids=sorted(source_ids), field=field, suggestion=suggestion)


def build_preview(sources, ids, *, automatic=False):
    sources = sorted(sources, key=lambda source: (source.created_at, source.id))
    first = sources[0]
    details = []

    def report(code, message, affected, field, suggestion):
        value = detail(code, message, affected, field, suggestion)
        if value not in details:
            details.append(value)

    for source in sources[1:]:
        pair = [first.id, source.id]
        if (source.language, source.scope, source.repository_id) != (first.language, first.scope, first.repository_id):
            report("scope_mismatch", "待合并经验必须具有相同的语言、使用范围和所属仓库。", pair,
                   "scope", "按相同仓库、语言和使用范围分别选择来源。")
        first_key = first.compatibility_key or first.source_revision
        source_key = source.compatibility_key or source.source_revision
        same_domain = first_key == source_key or (
            same_source_revision(first_key, first.source_revision) and
            same_source_revision(source_key, source.source_revision) and
            same_source_revision(first_key, source_key))
        if not same_domain:
            report('version_mismatch', '来源兼容域或版本不同，不能自动放宽。', pair, 'compatibility_key', '登记兼容域或分别保留。')
        if set(source.applicability) != set(first.applicability):
            report("applicability_mismatch", "适用条件不同，请分别保留这些经验。", pair,
                   "applicability", "保留各自的适用条件，不要为合并而扩大条件。")
        if source.reuse_status != first.reuse_status:
            report("status_mismatch", "复用状态不同，不能通过合并代替发布或审阅。", pair,
                   "reuse_status", "完成验证或显式发布后，再选择相同状态的来源。")
        if set(source.required_capabilities) != set(first.required_capabilities):
            report("capability_mismatch", "目标所需能力不同，请分别保留这些经验。", pair,
                   "required_capabilities", "保持各自的能力要求，分别复用。")

    issues = {}
    owners = []
    orders = []
    evidence = {}
    evidence_owners = {}
    for source in sources:
        current = _source_issues(source)
        orders.append([_issue_key(issue) for issue in current])
        for issue in current:
            matching_owners = {owner for owner, previous in owners if _issue_key(previous) == _issue_key(issue)}
            for previous_id, previous in owners:
                if previous_id in matching_owners:
                    continue
                overlap = not issue.files or not previous.files or bool(_file_keys(issue) & _file_keys(previous))
                if issue.symbols and previous.symbols:
                    overlap=bool(set(issue.symbols) & set(previous.symbols))
                if (_normalized(issue.error_type) == _normalized(previous.error_type) and overlap
                        and (issue.root_cause, issue.solution) != (previous.root_cause, previous.solution)):
                    report("unknown_relation", f"针对 {issue.error_type} 的规则关系尚未确认，请审阅适用条件。",
                           [previous_id, source.id], "issues",
                           "对照根因、修复方案和来源测试，选择可信来源；无法确定时暂停两条经验的复用。")
            issues.setdefault(_issue_key(issue), issue)
        # Related rules within one independently validated source are allowed.
        owners.extend((source.id, issue) for issue in current)
        for item in source.evidence:
            key = (item.source_task_id, item.tool_call_id)
            if key in evidence and evidence[key] != item:
                report("evidence_mismatch", "同一次验证调用的测试证据不一致。",
                       [evidence_owners[key], source.id] if evidence_owners[key] != source.id else ids,
                       "evidence", "核查原始测试调用，禁止累加或覆盖冲突证据。")
            else:
                evidence[key] = item
                evidence_owners[key] = source.id

    predecessors = {key: [] for key in issues}
    for source, order in zip(sources, orders):
        for ordinal, issue in enumerate(_source_issues(source)):
            for previous in issue.depends_on:
                before, after = order[previous], order[ordinal]
                if before not in predecessors[after]:
                    predecessors[after].append(before)
    try:
        keys = list(TopologicalSorter(predecessors).static_order())
        positions = {key: ordinal for ordinal, key in enumerate(keys)}
        ordered = [issues[key].model_copy(update={'depends_on': sorted(positions[p] for p in predecessors[key])}) for key in keys]
    except CycleError:
        report("order_cycle", "来源经验要求的修复顺序存在冲突。", ids, "issues",
               "核查各来源的依赖顺序，选用一个经过验证的方案。")
        ordered = []

    def result(preview=None):
        return dict(can_merge=not details, conflicts=list(dict.fromkeys(d["message"] for d in details)),
                    source_ids=ids, preview=preview, conflict_details=details)

    if details:
        return result()
    errors = _unique_text(issue.error_type for issue in ordered)
    revisions = {source.source_revision for source in sources}
    revision = next(iter(revisions)) if len(revisions) == 1 else None
    if all(same_source_revision(first.source_revision, source.source_revision) for source in sources):
        # Equivalent legacy host-Git prefixes must not erase the content boundary
        # needed to recognize this merged record's derived compatibility key.
        revision = next((source.source_revision for source in sources
                         if source.source_revision.startswith('tree:')), first.source_revision)
    try:
        preview = ExperienceCreate(
            schema_version=3,
            reuse_status=first.reuse_status if all(source.evidence for source in sources) else "legacy_unreviewed",
            required_capabilities=first.required_capabilities,
            task_summary=(f"自动合并经验：{errors[0] if len(errors) == 1 else '多问题修复'}（{len(ordered)} 条规则）"
                          if automatic else "合并经验：" + " / ".join(_unique_text(s.task_summary for s in sources))),
            error_type=errors[0] if len(errors) == 1 else None,
            root_cause="\n".join(dict.fromkeys(source.root_cause for source in sources)),
            solution="\n".join(dict.fromkeys(source.solution for source in sources)),
            constraints=list(dict.fromkeys(c for source in sources for c in source.constraints)),
            compatibility_key=first.compatibility_key,
            verification="\n".join(_unique_text(source.verification for source in sources)),
            tags=_unique_text(tag for source in sources for tag in source.tags),
            language=first.language, scope=first.scope, repository_id=first.repository_id,
            source_revision=revision,
            applicability=first.applicability, issues=ordered, evidence=list(evidence.values()), merged_from=ids,
        )
    except ValidationError:
        report("content_limit", "合并后内容超出记录限制，请减少合并的经验数量。", ids, "preview",
               "保留分组记录，避免截断修复规则或测试证据。")
        return result()
    return result(preview)
