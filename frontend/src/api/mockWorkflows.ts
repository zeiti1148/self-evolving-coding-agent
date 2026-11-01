/** Browser-only examples. These records are explicitly labelled synthetic, never backend evidence. */
import type { AgentApi, Experience, ExperienceKnowledge, PromotionDraft, ProvenanceGraph } from './contracts'
import type { MockState } from './mockStorage'
import { ApiError } from './http'

export function mockWorkflows(state: MockState, persist: () => void, now: () => number): Partial<AgentApi> {
  const clone = <T>(value: T): T => structuredClone(value)
  const abort = (signal?: AbortSignal) => { if (signal?.aborted) throw new DOMException('Aborted', 'AbortError') }
  const find = (id: string) => {
    const record = state.experiences.find(item => item.id === id)
    if (!record) throw new ApiError(404, '找不到经验，请刷新列表。')
    return record
  }
  const replaced = (id: string) => state.experiences.some(item => item.merged_from?.includes(id) || item.supersedes_id === id)
  const head = (id: string) => state.experiences.find(item => item.derived_from?.includes(id))
  const save = (knowledge: ExperienceKnowledge) => {
    const record: Experience = { ...clone(knowledge), id: crypto.randomUUID(), created_at: new Date(now()).toISOString() }
    state.experiences.unshift(record); persist(); return clone(record)
  }
  function mergePreview(ids: string[]) {
    if (ids.length < 2 || ids.length > 10 || new Set(ids).size !== ids.length) throw new ApiError(422, '请选择 2 至 10 条不同经验。')
    const items = ids.map(find), first = items[0]!
    const conflicts: string[] = []
    const fieldLabels = { language: '语言', scope: '使用范围', repository_id: '所属仓库', reuse_status: '复用状态', applicability: '适用条件', required_capabilities: '目标所需能力' }
    if (ids.some(replaced)) conflicts.push('来源已被替代，请刷新列表。')
    for (const field of ['language', 'scope', 'repository_id', 'reuse_status', 'applicability', 'required_capabilities'] as const) {
      if (items.some(item => JSON.stringify(item[field]) !== JSON.stringify(first[field]))) conflicts.push(`来源的${fieldLabels[field]}不一致。`)
    }
    const preview: ExperienceKnowledge = { ...first, task_summary: '演示合并经验',
      root_cause: items.map(item => item.root_cause).join('\n'), solution: items.map(item => item.solution).join('\n'),
      issues: items.flatMap(item => item.issues ?? []), merged_from: [...ids].sort(), batch_id: null,
      evidence: [...new Map(items.flatMap(item => item.evidence ?? []).map(e => [e.tool_call_id, e])).values()],
      derived_from: [], supersedes_id: null }
    return { can_merge: !conflicts.length, conflicts, source_ids: ids, preview: conflicts.length ? null : preview }
  }
  return {
    async getExperience(id, signal) { abort(signal); return clone(find(id)) },
    async getProvenance(id, signal) {
      abort(signal)
      const graph: ProvenanceGraph = { nodes: [], edges: [], truncated: false }, seen = new Set<string>()
      const queue = [{ id, depth: 0 }]
      while (queue.length) {
        const next = queue.shift()!
        if (seen.has(next.id)) continue
        if (graph.nodes.length >= 100 || next.depth > 10) { graph.truncated = true; continue }
        const record = find(next.id); seen.add(next.id); graph.nodes.push(clone(record))
        for (const relation of ['derived_from', 'merged_from', 'supersedes_id'] as const) {
          const targets = relation === 'supersedes_id' ? (record.supersedes_id ? [record.supersedes_id] : []) : record[relation] ?? []
          for (const target of targets) { graph.edges.push({ source: next.id, target, relation }); queue.push({ id: target, depth: next.depth + 1 }) }
        }
      }
      return graph
    },
    async previewMerge(ids, signal) { abort(signal); return clone(mergePreview(ids)) },
    async mergeExperiences(ids, signal) {
      abort(signal)
      const existing = state.experiences.find(item => JSON.stringify(item.merged_from) === JSON.stringify([...ids].sort()))
      if (existing) return clone(existing)
      const plan = mergePreview(ids)
      if (!plan.preview) throw new ApiError(409, plan.conflicts.join(' '))
      return save(plan.preview)
    },
    async previewPromotion(id, signal) {
      abort(signal); const source = find(id)
      const conflicts = !source.evidence?.length || source.reuse_status !== 'active' || replaced(id)
        ? ['需要有效的来源及测试证据；旧演示种子不可直接发布。'] : []
      const draft: PromotionDraft = { language: source.language ?? 'java', task_summary: '演示：输入前置条件校验',
        error_type: source.error_type, root_cause: '输入前置条件未检查。', solution: '检查输入并按目标业务的默认值或拒绝规则处理。',
        tags: [], applicability: ['确认目标业务边界并独立运行测试。'], required_capabilities: [],
        issues: [{ error_type: source.error_type ?? 'InputError', root_cause: '前置条件未校验', solution: '按目标合同处理输入', files: [] }] }
      return { can_publish: !conflicts.length, source_id: id, current_global_id: head(id)?.id ?? null, conflicts,
        draft: conflicts.length ? null : draft, injection_preview: conflicts.length ? null : { ...draft,
          id: 'assigned-on-publication', scope: 'global', reuse_status: 'active', evidence_summary: [{ runner: 'maven', tests: 2, failures: 0, errors: 0, skipped: 0 }] } }
    },
    async publishGlobal(id, draft, previous = null, signal) {
      abort(signal); const source = find(id), current = head(id)
      const prior = state.experiences.find(item => item.derived_from?.includes(id) && (item.supersedes_id ?? null) === previous
        && Object.entries(draft).every(([key, value]) => JSON.stringify(item[key as keyof Experience]) === JSON.stringify(value)))
      if (prior) return clone(prior)
      if ((current?.id ?? null) !== previous || replaced(id) || !source.evidence?.length || source.reuse_status !== 'active') throw new ApiError(409, '来源或版本改变，请重新预览。')
      if (draft.language !== source.language || draft.issues.some(issue => issue.files.length)) throw new ApiError(409, '通用草稿不能改变语言或包含来源路径。')
      return save({ ...draft, schema_version: 3, scope: 'global', reuse_status: 'active', repository_id: null,
        source_task_id: null, source_revision: null, verification: '仅为浏览器演示数据，未运行真实模型或测试。',
        evidence: source.evidence, derived_from: [id], merged_from: [], supersedes_id: previous })
    },
  }
}
