import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import ExperienceMaintenance from '../src/components/ExperienceMaintenance.vue'
import ExperiencesView from '../src/views/ExperiencesView.vue'
import { apiContextKey } from '../src/api/context'
import { ApiError, createHttpApi } from '../src/api/http'
import { readConflict, readConsolidationReport } from '../src/api/experienceWorkflowValidation'
import type { ExperienceConflict, ExperienceFilters } from '../src/api/contracts'

const ids = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222',
  '33333333-3333-4333-8333-333333333333']
const conflict: ExperienceConflict = {
  id: ids[2]!, sources: ids.slice(0, 2).map((id, index) => ({ id, created_at: '2026-11-14T00:00:00Z',
    task_summary: `规则 ${index + 1}`, error_type: 'ValueError', root_cause: '缺少输入',
    solution: index ? '使用默认值' : '拒绝输入', verification: '3 tests passed', tags: [], source_task_id: null,
    schema_version: 3, language: 'python', scope: 'project', repository_id: 'repo', reuse_status: 'active',
    batch_id: null, derived_from: [], merged_from: [], required_capabilities: [], source_revision: null, supersedes_id: null,
    applicability: ['文本输入'], issues: [{ error_type: 'ValueError', root_cause: '缺少输入', solution: index ? '使用默认值' : '拒绝输入', files: ['app.py'] }],
    evidence: [{ source_task_id: ids[0]!, tool_call_id: ids[1]!, runner: 'pytest', tests: 3, errors: 0, failures: 0, skipped: 0 }] })),
  details: [{ code: 'rule_overlap', message: '修复规则重叠', source_ids: ids.slice(0, 2), field: 'issues', suggestion: '核对原始测试与业务条件。' }],
  status: 'pending', version: 1, created_at: '2026-11-14T00:00:00Z', resolved_at: null,
  resolution: null, preferred_source_id: null, note: null,
}
const resolved: ExperienceConflict = { ...conflict, status: 'resolved', version: 2,
  resolved_at: '2026-11-14T01:00:00Z', resolution: 'prefer_source', preferred_source_id: ids[0]!, note: '核对测试' }
const filters: ExperienceFilters = { language: 'python', repository_id: 'repo' }
function api() {
  return { listExperiences: vi.fn().mockResolvedValue([]), listConflicts: vi.fn().mockResolvedValue([conflict]),
    consolidateExperiences: vi.fn().mockResolvedValue({ examined: 2, merged_ids: [], conflict_ids: [conflict.id], deferred_groups: 0 }),
    resolveConflict: vi.fn().mockResolvedValue(resolved) }
}
async function setup(client = api(), scope: ExperienceFilters = filters, parent = false) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/experiences', component: ExperiencesView },
    { path: '/', component: { template: '<div />' } }] })
  await router.push('/experiences'); await router.isReady()
  const wrapper = mount(parent ? ExperiencesView : ExperienceMaintenance, { props: parent ? {} : { filters: scope },
    global: { plugins: [router], provide: { [apiContextKey as symbol]: { api: client, mock: false } } } })
  await flushPromises()
  return { wrapper, client }
}

describe('experience maintenance', () => {
  it('requests a user decision only after automatic verification reports insufficient evidence', async () => {
    const data=structuredClone(conflict),client=api()
    data.automatic_resolution={state:'verifying',actor:'system',policy:'verified-cross-tests-v1',stage:'snapshot',
      reason:'正在核对冻结源码',targets:[],runs:[],resolution:null,preferred_source_id:null,
      started_at:data.created_at,updated_at:data.created_at,evidence_hash:null}
    client.listConflicts.mockResolvedValue([data])
    const {wrapper}=await setup(client)
    try {
      expect(wrapper.text()).toContain('系统自动处理 · 验证中')
      expect(wrapper.find('[data-testid="resolve-conflict"]').exists()).toBe(false)
      data.automatic_resolution.state='needs_confirmation';data.automatic_resolution.reason='缺少完整源码快照'
      client.listConflicts.mockResolvedValue([structuredClone(data)])
      await wrapper.get('[data-testid="refresh-conflicts"]').trigger('click');await flushPromises()
      expect(wrapper.text()).toContain('系统自动处理 · 需要用户确认')
      expect(wrapper.find('[data-testid="resolve-conflict"]').exists()).toBe(true)
      expect(client.resolveConflict).not.toHaveBeenCalled()
    } finally {wrapper.unmount()}
  })
  it('accepts a source-specific verbatim conflict quotation through the HTTP contract', () => {
    const data = structuredClone(conflict)
    data.details.push({code:'rule_overlap', message:'相互冲突的来源原文', source_ids:[ids[0]!],
      field:'issues[0].solution', suggestion:data.sources[0]!.issues![0]!.solution})
    expect(readConflict(data).details).toHaveLength(2)
    data.details[1]!.suggestion='原文不存在的引文'
    expect(()=>readConflict(data)).toThrow()
    data.details[1]!.suggestion=data.sources[0]!.issues![0]!.solution
    data.details[1]!.field='issues[99].solution'
    expect(()=>readConflict(data)).toThrow()
    data.details[1]!.field='issues'
    expect(()=>readConflict(data)).toThrow()
  })
  it('filters the queue and retries a failed durable job', async () => {
    const job = { id: ids[0], rule_id: ids[0], state: 'failed', attempts: 5, examined: 8, error: 'RuntimeError',
      created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' }
    const client = { ...api(), listMaintenanceJobs: vi.fn().mockResolvedValue([job]), retryMaintenanceJob: vi.fn().mockResolvedValue(job) }
    const { wrapper } = await setup(client)
    try {
      expect(client.listMaintenanceJobs).toHaveBeenCalledWith(expect.any(AbortSignal), filters)
      await wrapper.findAll('button').find(button => button.text() === '重试整理')!.trigger('click'); await flushPromises()
      expect(client.retryMaintenanceJob).toHaveBeenCalledWith(ids[0], expect.any(AbortSignal))
      expect(wrapper.text()).toContain('已重新排队')
    } finally { wrapper.unmount() }
  })
  it('preserves a stale undo reason and exposes the append-only decision history', async () => {
    const client = { ...api(), listConflicts: vi.fn().mockResolvedValue([resolved]),
      reopenConflict: vi.fn().mockRejectedValue(new ApiError(409, '版本已变化')),
      getReviewHistory: vi.fn().mockResolvedValue([{ version: 2, action: 'resolve', data: { note: '原始依据' }, created_at: resolved.resolved_at }]) }
    const { wrapper } = await setup(client)
    try {
      await wrapper.findAll('button').find(button => button.text() === '查看决定历史')!.trigger('click'); await flushPromises()
      expect(wrapper.text()).toContain('原始依据')
      await wrapper.get(`#reopen-${resolved.id}`).setValue('新增反例，需要复核')
      const button = wrapper.findAll('button').find(button => button.text() === '撤销当前决定')!
      await button.trigger('click'); await flushPromises()
      expect(client.reopenConflict).toHaveBeenCalledWith(resolved.id, 2, '新增反例，需要复核', expect.any(AbortSignal))
      expect(button.attributes('disabled')).toBeDefined()
      expect((wrapper.get(`#reopen-${resolved.id}`).element as HTMLTextAreaElement).value).toBe('新增反例，需要复核')
    } finally { wrapper.unmount() }
  })
  it('shows both source rules and requires an explicit decision with a reason', async () => {
    const { wrapper, client } = await setup()
    try {
      expect(wrapper.text()).toContain('拒绝输入')
      expect(wrapper.text()).toContain('使用默认值')
      expect(wrapper.get('[data-testid="resolve-conflict"]').attributes('disabled')).toBeDefined()
      await wrapper.get(`#choice-${conflict.id}`).setValue(ids[0])
      await wrapper.get('textarea').setValue('核对测试')
      client.listConflicts.mockResolvedValue([])
      await wrapper.get('form').trigger('submit'); await flushPromises()
      expect(client.resolveConflict).toHaveBeenCalledWith(conflict.id, { version: 1, resolution: 'prefer_source',
        preferred_source_id: ids[0], note: '核对测试' }, expect.any(AbortSignal))
      expect(wrapper.text()).toContain('冲突处理已保存')
      expect(wrapper.emitted('changed')).toHaveLength(1)
    } finally { wrapper.unmount() }
  })
  it('keeps both records paused when no trustworthy rule can be chosen', async () => {
    const { wrapper, client } = await setup()
    try {
      await wrapper.get(`#choice-${conflict.id}`).setValue('keep_separate')
      await wrapper.get('textarea').setValue('需要进一步验证')
      await wrapper.get('form').trigger('submit'); await flushPromises()
      expect(client.resolveConflict).toHaveBeenCalledWith(conflict.id, expect.objectContaining({ resolution: 'keep_separate',
        preferred_source_id: null }), expect.any(AbortSignal))
    } finally { wrapper.unmount() }
  })
  it('retains the reason and blocks stale resubmission until an explicit refresh', async () => {
    const client = api(); client.resolveConflict.mockRejectedValue(new ApiError(409, '版本已变化'))
    const { wrapper } = await setup(client)
    try {
      await wrapper.get(`#choice-${conflict.id}`).setValue(ids[0])
      await wrapper.get('textarea').setValue('保留这段依据')
      await wrapper.get('form').trigger('submit'); await flushPromises()
      expect(wrapper.text()).toContain('当前处理依据已保留')
      expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('保留这段依据')
      expect(wrapper.get('[data-testid="resolve-conflict"]').attributes('disabled')).toBeDefined()
      expect(wrapper.emitted('changed')).toBeUndefined()
      await wrapper.get('[data-testid="refresh-conflicts"]').trigger('click'); await flushPromises()
      expect(wrapper.get('[data-testid="resolve-conflict"]').attributes('disabled')).toBeUndefined()
    } finally { wrapper.unmount() }
  })
  it('requires a repository scope and accurately reports deferred groups', async () => {
    const client = api(); client.consolidateExperiences.mockResolvedValue({ examined: 2, merged_ids: [ids[2]!], conflict_ids: [], deferred_groups: 1 })
    const { wrapper } = await setup(client, {})
    try {
      expect(wrapper.get('[data-testid="consolidate-experiences"]').attributes('disabled')).toBeDefined()
      await wrapper.setProps({ filters }); await flushPromises()
      await wrapper.get('[data-testid="consolidate-experiences"]').trigger('click'); await flushPromises()
      expect(client.consolidateExperiences).toHaveBeenCalledWith('python', 'repo', expect.any(AbortSignal), undefined)
      expect(wrapper.text()).toContain('1 项规则具有多个来源')
      expect(wrapper.text()).toContain('1 个分组超出合并限制')
    } finally { wrapper.unmount() }
  })
  it('keeps failed requests distinct from empty or successful results', async () => {
    const client = api(); client.listConflicts.mockRejectedValue(new Error('存储不可用'))
    const { wrapper } = await setup(client)
    try {
      expect(wrapper.get('[role="alert"]').text()).toContain('存储不可用')
      expect(wrapper.text()).not.toContain('没有待处理冲突')
      client.listConflicts.mockResolvedValue([])
      await wrapper.get('[data-testid="refresh-conflicts"]').trigger('click'); await flushPromises()
      expect(wrapper.text()).toContain('没有待处理冲突')
    } finally { wrapper.unmount() }
  })
  it('continues large repository scans using the returned cursor and resets it when filters change', async () => {
    const client = api(), cursor = 'a'.repeat(64)
    client.consolidateExperiences.mockResolvedValueOnce({ examined: 2, merged_ids: [], conflict_ids: [], deferred_groups: 0, next_cursor: cursor })
    const { wrapper } = await setup(client)
    try {
      await wrapper.get('[data-testid="consolidate-experiences"]').trigger('click'); await flushPromises()
      expect(wrapper.get('[data-testid="consolidate-experiences"]').text()).toBe('继续整理本仓库')
      await wrapper.get('[data-testid="consolidate-experiences"]').trigger('click'); await flushPromises()
      expect(client.consolidateExperiences).toHaveBeenLastCalledWith('python', 'repo', expect.any(AbortSignal), cursor)
      await wrapper.setProps({ filters: { language: 'python', repository_id: 'other' } }); await flushPromises()
      expect(wrapper.get('[data-testid="consolidate-experiences"]').text()).toBe('自动整理本仓库')
    } finally { wrapper.unmount() }
  })
  it('ignores a stale list after the repository filter changes', async () => {
    const client = api(); let finish!: (value: ExperienceConflict[]) => void
    client.listConflicts.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      .mockResolvedValue([])
    const { wrapper } = await setup(client)
    try {
      await wrapper.setProps({ filters: { language: 'python', repository_id: 'other' } }); await flushPromises()
      finish([conflict]); await flushPromises()
      expect(wrapper.findAll('[data-testid="conflict-card"]')).toHaveLength(0)
    } finally { wrapper.unmount() }
  })
  it('shows historical decisions and paginates conflict records', async () => {
    const client = api()
    client.listConflicts.mockResolvedValue(Array.from({ length: 6 }, (_, index) => ({ ...resolved, id: `${index}1111111-1111-4111-8111-111111111111` })))
    const { wrapper } = await setup(client)
    try {
      await wrapper.get('#conflict-status').setValue('resolved'); await flushPromises()
      expect(wrapper.text()).toContain('处理依据：核对测试')
      expect(wrapper.find('textarea').exists()).toBe(false)
      await wrapper.findAll('button').find(button => button.text() === '下一页冲突')!.trigger('click'); await flushPromises()
      expect(client.listConflicts).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'resolved', offset: 5 }), expect.any(AbortSignal))
    } finally { wrapper.unmount() }
  })
  it('connects the parent filter controls to scoped consolidation', async () => {
    const { wrapper, client } = await setup(api(), {}, true)
    try {
      await wrapper.get('#experience-language').setValue('python')
      await wrapper.get('#experience-repository').setValue('repo')
      await wrapper.get('.experience-filters').trigger('submit'); await flushPromises()
      await wrapper.get('[data-testid="consolidate-experiences"]').trigger('click'); await flushPromises()
      expect(client.consolidateExperiences).toHaveBeenCalledWith('python', 'repo', expect.any(AbortSignal), undefined)
    } finally { wrapper.unmount() }
  })
})

describe('maintenance HTTP contracts', () => {
  it('round-trips routes, filters, decision version and response identity', async () => {
    const calls: { url: string; body?: string }[] = []
    const api = createHttpApi('', async (url, init) => {
      calls.push({ url: String(url), body: init?.body as string })
      return new Response(JSON.stringify(String(url).endsWith('/resolve') ? resolved :
        String(url).includes('/conflicts?') ? [conflict] : { examined: 2, merged_ids: [], conflict_ids: [conflict.id], deferred_groups: 0 }))
    })
    expect(await api.listConflicts!({ repository_id: 'repo / A', status: 'pending', limit: 6, offset: 5 })).toEqual([conflict])
    expect(calls[0]!.url).toContain('repository_id=repo+%2F+A')
    await api.resolveConflict!(conflict.id, { version: 1, resolution: 'keep_separate', preferred_source_id: null, note: 'Review' })
    expect(JSON.parse(calls[1]!.body!)).toMatchObject({ version: 1, resolution: 'keep_separate' })
    expect((await api.consolidateExperiences!('python', 'repo')).examined).toBe(2)
  })
  it('rejects wrong IDs, foreign preferred sources and inconsistent status', () => {
    expect(readConflict(conflict, conflict.id)).toEqual(conflict)
    for (const value of [{ ...conflict, id: ids[0] }, { ...resolved, preferred_source_id: ids[2] },
      { ...conflict, resolved_at: resolved.resolved_at }, { ...resolved, note: null },
      { ...conflict, sources: [conflict.sources[0], conflict.sources[0]] }, { ...conflict, details: [] }])
      expect(() => readConflict(value, conflict.id)).toThrow()
    expect(() => readConsolidationReport({ examined: true, merged_ids: [], conflict_ids: [], deferred_groups: 0 })).toThrow()
  })
})
