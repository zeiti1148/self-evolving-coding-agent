import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import NewTaskView from '../src/views/NewTaskView.vue'
import ResultSummary from '../src/components/ResultSummary.vue'
import ExperiencesView from '../src/views/ExperiencesView.vue'
import TaskView from '../src/views/TaskView.vue'
import AgentTimeline from '../src/components/AgentTimeline.vue'
import { apiContextKey } from '../src/api/context'
import { createHttpApi } from '../src/api/http'
import { ApiError } from '../src/api/http'
import ExperiencePromotion from '../src/components/ExperiencePromotion.vue'
import { createMockApi } from '../src/api/mock'
import { readExperiences, readTask } from '../src/api/validation'
import type { AgentApi, Experience } from '../src/api/contracts'

const id = '11111111-1111-4111-8111-111111111111'
const second = '22222222-2222-4222-8222-222222222222'
const third = '33333333-3333-4333-8333-333333333333'
const record: Experience = { id, created_at: '2026-10-31T00:00:00Z', task_summary: '处理文本边界',
  error_type: 'ValueError', root_cause: '输入缺失或空白', solution: '按目标业务合同校验', verification: '18 tests',
  tags: ['python'], source_task_id: second, schema_version: 3, language: 'python', scope: 'project',
  repository_id: 'A', applicability: ['目标有明确输入合同'], issues: [], merged_from: [],
  evidence: [{ source_task_id: second, tool_call_id: third, runner: 'pytest', tests: 18, failures: 0, errors: 0, skipped: 0 }] }

async function setup(component: any, client: AgentApi, path = '/') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: NewTaskView }, { path: '/experiences', component: ExperiencesView },
    { path: '/tasks/:taskId', component: { template: '<div>task</div>' } },
  ] })
  await router.push(path); await router.isReady()
  const wrapper = mount(component, { attachTo: document.body, global: { plugins: [router], provide: {
    [apiContextKey as symbol]: { api: client, mock: false, projectId: 'source', projectLabel: '来源项目' },
  } } })
  await flushPromises()
  return { wrapper, router }
}

describe('V3 contracts and mode', () => {
  it('reads schema 3 and normalizes old single links while rejecting inconsistent arrays', async () => {
    expect(readExperiences([record])[0]!.schema_version).toBe(3)
    let now = 0
    const api = createMockApi({ now: () => now, stepMs: 1, storage: null })
    const task = await api.createTask({ source_project: 'java-null-greeting', task_description: 'Fix', max_steps: 20 })
    now = 100
    const status = await api.getTask(task.task_id)
    const parsed = readTask(status, task.task_id)
    expect((parsed.result as any).experience_ids).toEqual([parsed.result!.experience_id])
    expect(() => readTask({ ...status, result: { ...status.result, experience_ids: [second] } }, task.task_id)).toThrow()
  })
  it('defaults to single and submits the selected multi mode', async () => {
    const client: AgentApi = { createTask: vi.fn().mockResolvedValue({ task_id: id, status: 'pending', created_at: record.created_at }),
      getTask: vi.fn(), getSteps: vi.fn(), listExperiences: vi.fn() }
    const { wrapper } = await setup(NewTaskView, client)
    try {
      expect((wrapper.get('#execution-mode').element as HTMLSelectElement).value).toBe('single')
      await wrapper.get('#execution-mode').setValue('multi')
      await wrapper.get('#description').setValue('Fix all input groups')
      await wrapper.get('form').trigger('submit'); await flushPromises()
      expect(client.createTask).toHaveBeenCalledWith(expect.objectContaining({ execution_mode: 'multi' }), expect.any(AbortSignal))
    } finally { wrapper.unmount() }
  })
  it('renders every saved experience as a direct detail link', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/experiences', component: ExperiencesView }] })
    const wrapper = mount(ResultSummary, { props: { result: { task_id: id, status: 'succeeded', summary: 'Verified',
      steps: [], tool_results: [], repair_succeeded: true, tests_passed: true, changed_files: [],
      experience_saved: true, experience_id: id, experience_ids: [id, second, third],
      experience_save_error: null, error_message: null } as any }, global: { plugins: [router] } })
    try { expect(wrapper.findAll('a[href*="?id="]').map(a => a.attributes('href'))).toHaveLength(3) }
    finally { wrapper.unmount() }
  })
  it('uses HTTP detail and cursor routes and preserves V3 filter precision', async () => {
    const urls: string[] = []
    const api = createHttpApi('', async input => { urls.push(String(input)); return new Response(JSON.stringify(record), { status: 200 }) }) as any
    expect(typeof api.getExperience).toBe('function')
    expect((await api.getExperience(id)).id).toBe(id)
    expect(urls[0]).toBe(`/api/experiences/${id}`)
  })
})

describe('V3 role and publication flows', () => {
  it('drains final events when terminal status arrives during an older poll', async () => {
    let resolveOld!: (events: unknown[]) => void
    const finalEvent = { event_id: second, task_id: id, agent_id: third, role: 'extractor', phase: 'extract', status: 'completed',
      sequence: 1, snapshot_revision: null, summary: 'Atomic batch saved', created_at: record.created_at, usage: { reported_tokens: null, elapsed_ms: 1 } }
    const client = { getAgentEvents: vi.fn().mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve })).mockResolvedValue([finalEvent]) }
    const wrapper = mount(AgentTimeline, { props: { taskId: id, status: 'running' }, global: {
      provide: { [apiContextKey as symbol]: { api: client, mock: false } },
    } })
    try {
      await wrapper.setProps({ status: 'succeeded' })
      resolveOld([]); await flushPromises()
      expect(client.getAgentEvents).toHaveBeenCalledTimes(2)
      expect(wrapper.text()).toContain('Atomic batch saved')
    } finally { wrapper.unmount() }
  })
  it('retains a rejected publication draft until the user requests a new preview', async () => {
    const draft = { task_summary: 'Rule', root_cause: 'Cause', solution: 'Initial', error_type: null,
      language: 'python', tags: [], applicability: ['Check target'], required_capabilities: [], issues: [] }
    const client: any = { previewPromotion: vi.fn().mockResolvedValue({ can_publish: true, conflicts: [],
      source_id: id, current_global_id: second, draft, injection_preview: {} }),
      publishGlobal: vi.fn().mockRejectedValue(new ApiError(409, '版本已改变')) }
    const { wrapper } = await setup({ components: { ExperiencePromotion }, template: `<ExperiencePromotion source-id="${id}" />` }, client)
    try {
      await wrapper.get('#promotion-solution').setValue('Preserve my edited rule')
      await wrapper.get('form').trigger('submit'); await flushPromises()
      expect(wrapper.text()).toContain('当前草稿保留')
      expect((wrapper.get('#promotion-solution').element as HTMLTextAreaElement).value).toBe('Preserve my edited rule')
      expect(client.previewPromotion).toHaveBeenCalledTimes(1)
      expect(wrapper.get('[data-testid="publish-global"]').attributes('disabled')).toBeDefined()
    } finally { wrapper.unmount() }
  })
  it('persists mock role history, single-purpose experience and explicit publication across reload', async () => {
    let now = 0
    const memory = new Map<string, string>()
    const storage: Storage = { getItem: key => memory.get(key) ?? null, setItem: (key, value) => { memory.set(key, value) },
      clear: () => memory.clear(), removeItem: key => { memory.delete(key) }, key: index => [...memory.keys()][index] ?? null,
      get length() { return memory.size } }
    const api = createMockApi({ now: () => now, stepMs: 1, storage })
    const task = await api.createTask({ source_project: 'java-null-greeting', task_description: 'Fix greeting', max_steps: 20, execution_mode: 'multi' })
    now = 100
    const status = await api.getTask(task.task_id)
    const events = await api.getAgentEvents!(task.task_id)
    expect(new Set(events.map(event => event.role))).toEqual(new Set(['diagnosis', 'repair', 'review', 'coordinator']))
    const experience = await api.getExperience!(status.result!.experience_id!)
    const preview = await api.previewPromotion!(experience.id)
    expect(preview.can_publish).toBe(true)
    const published = await api.publishGlobal!(experience.id, preview.draft!, preview.current_global_id)
    const reload = createMockApi({ now: () => now, storage })
    expect(await reload.getAgentEvents!(task.task_id)).toEqual(events)
    expect(await reload.getExperience!(published.id)).toMatchObject({ scope: 'global', derived_from: [experience.id] })
    expect((await reload.getProvenance!(published.id)).nodes).toHaveLength(2)
    expect((await reload.getTask(task.task_id)).result!.experience_ids).toEqual([experience.id])
  })
  it('shows durable role facts and stops cursor polling at terminal state', async () => {
    const events = [{ event_id: second, task_id: id, agent_id: third, role: 'review', phase: 'review', status: 'completed',
      sequence: 1, snapshot_revision: 'revision', summary: 'Review accepted; program tests still required', created_at: record.created_at,
      usage: { reported_tokens: null, elapsed_ms: 25 } }]
    const client: any = { createTask: vi.fn(), listExperiences: vi.fn(), getTask: vi.fn().mockResolvedValue({
      task_id: id, status: 'stopped', steps_completed: 0, created_at: record.created_at, started_at: record.created_at,
      finished_at: record.created_at, result: null, error_message: 'Budget exhausted' }),
      getSteps: vi.fn().mockResolvedValue({ task_id: id, status: 'stopped', steps: [] }), getAgentEvents: vi.fn().mockResolvedValue(events) }
    const { wrapper } = await setup(TaskView, client, `/tasks/${id}`)
    try {
      expect(wrapper.text()).toContain('独立复核')
      expect(wrapper.text()).toContain(events[0]!.summary)
      expect(wrapper.text()).toContain('用量未报告')
      expect(client.getAgentEvents).toHaveBeenCalledWith(id, 0, expect.any(AbortSignal))
      expect(wrapper.findAll('[data-testid="agent-event"]')).toHaveLength(1)
    } finally { wrapper.unmount() }
  })
  it('edits a draft, displays injection content and publishes explicitly with current version', async () => {
    const draft = { task_summary: 'Public rule', root_cause: 'Missing input', solution: 'Validate input', error_type: 'ValueError',
      language: 'python', tags: [], applicability: ['Confirm target policy'], required_capabilities: ['text-input'],
      issues: [{ error_type: 'ValueError', root_cause: 'Missing input', solution: 'Validate input', files: [] }] }
    const client: any = { createTask: vi.fn(), getTask: vi.fn(), getSteps: vi.fn(), listExperiences: vi.fn().mockResolvedValue([record]),
      previewPromotion: vi.fn().mockResolvedValue({ can_publish: true, source_id: id, current_global_id: second,
        conflicts: [], draft, injection_preview: { id: 'assigned-on-publication', evidence_summary: [] } }),
      publishGlobal: vi.fn().mockResolvedValue({ ...record, id: third, scope: 'global', repository_id: null }), }
    const { wrapper } = await setup(ExperiencesView, client, '/experiences')
    try {
      await wrapper.get('[data-testid="promote-experience"]').trigger('click'); await flushPromises()
      expect(client.publishGlobal).not.toHaveBeenCalled()
      await wrapper.get('#promotion-solution').setValue('Reject invalid values under the target contract')
      expect(wrapper.get('[data-testid="injection-preview"]').text()).toContain('Reject invalid values under the target contract')
      await wrapper.get('[data-testid="publish-global"]').trigger('click'); await flushPromises()
      expect(client.publishGlobal).toHaveBeenCalledWith(id, expect.objectContaining({ solution: 'Reject invalid values under the target contract' }), second, expect.any(AbortSignal))
      expect(wrapper.text()).toContain('通用经验已发布')
    } finally { wrapper.unmount() }
  })
})
