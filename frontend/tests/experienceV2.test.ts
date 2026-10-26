import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import NewTaskView from '../src/views/NewTaskView.vue'
import ExperiencesView from '../src/views/ExperiencesView.vue'
import { apiContextKey } from '../src/api/context'
import { createHttpApi } from '../src/api/http'
import { readExperiences, readMergePreview } from '../src/api/validation'
import type { AgentApi, Experience, ExperienceKnowledge, MergePreview, RegisteredProject } from '../src/api/contracts'

const idA = '11111111-1111-4111-8111-111111111111'
const idB = '22222222-2222-4222-8222-222222222222'
const idC = '33333333-3333-4333-8333-333333333333'
const projects: RegisteredProject[] = [
  { id: 'java-sample', label: 'Java 组合异常', language: 'java', repository_id: 'java-orders', source_revision: 'abc123', task_example: '修复空指针和金额计算' },
  { id: 'python-sample', label: 'Python 订单导入', language: 'python', repository_id: 'python-orders', source_revision: 'def456', task_example: '修复缺失字段和非法数量' },
]
const knowledge: ExperienceKnowledge = {
  task_summary: '订单导入组合修复', error_type: 'ValueError', root_cause: '输入字段未校验', solution: '先验证字段，再计算订单',
  verification: '最终测试通过', tags: ['python'], source_task_id: idA, schema_version: 2,
  language: 'python', scope: 'project', repository_id: 'python-orders', source_revision: 'def456',
  applicability: ['导入外部订单'],
  issues: [
    { error_type: 'KeyError', root_cause: '缺失字段', solution: '校验必需字段', files: ['orders/parser.py'] },
    { error_type: 'ValueError', root_cause: '非法数量', solution: '转换前校验数量', files: ['orders/pricing.py'] },
  ],
  evidence: [{ source_task_id: idA, tool_call_id: idB, runner: 'pytest', tests: 9, failures: 0, errors: 0, skipped: 0 }],
  merged_from: [],
}
const entries: Experience[] = [idA, idB].map((id, index) => ({ ...knowledge, id, task_summary: `经验 ${index + 1}`, created_at: '2026-10-26T00:00:00Z' }))
const mergePreview: MergePreview = { can_merge: true, conflicts: [], source_ids: [idA, idB], preview: { ...knowledge, merged_from: [idA, idB] } }
function api(): AgentApi {
  return { createTask: vi.fn(), getTask: vi.fn(), getSteps: vi.fn(), listExperiences: vi.fn().mockResolvedValue(entries),
    previewMerge: vi.fn().mockResolvedValue(mergePreview),
    mergeExperiences: vi.fn().mockResolvedValue({ ...knowledge, id: idC, created_at: '2026-10-26T01:00:00Z', merged_from: [idA, idB] }) }
}
async function setup(component: typeof NewTaskView | typeof ExperiencesView, client: AgentApi) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component }, { path: '/tasks/:id', component: { template: '<p>任务</p>' } },
  ] })
  await router.push('/'); await router.isReady()
  const wrapper = mount(component, { global: { plugins: [router], provide: {
    [apiContextKey as symbol]: { api: client, mock: false, projectId: 'java-sample', projectLabel: 'Java 组合异常' },
  } } })
  await flushPromises()
  return { wrapper, router }
}

describe('registered repositories', () => {
  it('selects a Python project and submits its registered id and example', async () => {
    const client = api(); client.listProjects = vi.fn().mockResolvedValue(projects)
    vi.mocked(client.createTask).mockResolvedValue({ task_id: idA, status: 'pending', created_at: '2026-10-26T00:00:00Z' })
    const { wrapper, router } = await setup(NewTaskView, client)
    try {
      await wrapper.get('#project').setValue('python-sample')
      await wrapper.get('.label-row button').trigger('click')
      expect(wrapper.text()).toContain('Python / pytest')
      expect(wrapper.text()).toContain('python-orders')
      expect(wrapper.text()).toContain('def456')
      await wrapper.get('form').trigger('submit'); await flushPromises()
      expect(client.createTask).toHaveBeenCalledWith({ task_description: projects[1]!.task_example, source_project: 'python-sample', max_steps: 20 }, expect.any(AbortSignal))
      expect(router.currentRoute.value.path).toBe(`/tasks/${idA}`)
    } finally { wrapper.unmount() }
  })
  it('blocks submission while the real project catalog is unavailable and supports retry', async () => {
    const client = api(); client.listProjects = vi.fn().mockRejectedValueOnce(new Error('项目登记接口不可用')).mockResolvedValueOnce(projects)
    const { wrapper } = await setup(NewTaskView, client)
    try {
      expect(wrapper.get('[role="alert"]').text()).toContain('项目登记接口不可用')
      expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
      await wrapper.get('textarea').setValue('保留手工描述')
      await wrapper.get('form').trigger('submit')
      expect(client.createTask).not.toHaveBeenCalled()
      await wrapper.get('[data-testid="retry-projects"]').trigger('click'); await flushPromises()
      expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
      expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('保留手工描述')
    } finally { wrapper.unmount() }
  })
})

describe('complex experience display and merge', () => {
  it('renders ordered issues, repository, applicability and structured verification', async () => {
    const { wrapper } = await setup(ExperiencesView, api())
    try {
      const first = wrapper.findAll('[data-testid="experience-item"]')[0]!
      expect(first.text()).toContain('仓库专属：python-orders')
      expect(first.findAll('.issue-list > li').map(row => row.get('h4').text())).toEqual(['KeyError', 'ValueError'])
      expect(first.text()).toContain('导入外部订单')
      expect(first.text()).toContain('pytest · 9 项测试通过')
      expect(first.text()).toContain('orders/pricing.py')
    } finally { wrapper.unmount() }
  })
  it('passes all filters to the server and starts pagination from zero', async () => {
    const client = api(); const { wrapper } = await setup(ExperiencesView, client)
    try {
      await wrapper.get('#experience-language').setValue('python')
      await wrapper.get('#experience-scope').setValue('project')
      await wrapper.get('#experience-repository').setValue(' team/repo ')
      await wrapper.get('.experience-filters').trigger('submit'); await flushPromises()
      expect(client.listExperiences).toHaveBeenLastCalledWith(6, 0, expect.any(AbortSignal), { language: 'python', scope: 'project', repository_id: 'team/repo' })
    } finally { wrapper.unmount() }
  })
  it('requires preview then explicit confirmation and saves only once during an in-flight merge', async () => {
    const client = api(); let resolveMerge!: (value: Experience) => void
    vi.mocked(client.mergeExperiences!).mockImplementation(() => new Promise(resolve => { resolveMerge = resolve }))
    const { wrapper } = await setup(ExperiencesView, client)
    try {
      expect(wrapper.get('[data-testid="preview-merge"]').attributes('disabled')).toBeDefined()
      for (const input of wrapper.findAll('.merge-selection input')) await input.setValue(true)
      await wrapper.get('[data-testid="preview-merge"]').trigger('click'); await flushPromises()
      expect(client.previewMerge).toHaveBeenCalledWith([idA, idB], expect.any(AbortSignal))
      expect(client.mergeExperiences).not.toHaveBeenCalled()
      expect(wrapper.get('[data-testid="merge-preview"]').text()).toContain('原记录保留')
      await wrapper.get('[data-testid="confirm-merge"]').trigger('click')
      await wrapper.get('[data-testid="confirm-merge"]').trigger('click')
      expect(client.mergeExperiences).toHaveBeenCalledTimes(1)
      expect(wrapper.get('#experience-language').attributes('disabled')).toBeDefined()
      resolveMerge({ ...knowledge, id: idC, created_at: '2026-10-26T01:00:00Z' }); await flushPromises()
      expect(wrapper.text()).toContain('合并经验已生成')
      expect(wrapper.text()).toContain('已选择 0 / 10 条')
    } finally { wrapper.unmount() }
  })
  it('shows conflicts and never enables confirmation for incompatible records', async () => {
    const client = api(); vi.mocked(client.previewMerge!).mockResolvedValue({ can_merge: false, conflicts: ['仓库不一致'], source_ids: [idA, idB], preview: null })
    const { wrapper } = await setup(ExperiencesView, client)
    try {
      for (const input of wrapper.findAll('.merge-selection input')) await input.setValue(true)
      await wrapper.get('[data-testid="preview-merge"]').trigger('click'); await flushPromises()
      expect(wrapper.get('[role="alert"]').text()).toContain('仓库不一致')
      expect(wrapper.get('[data-testid="confirm-merge"]').attributes('disabled')).toBeDefined()
      await wrapper.get('[data-testid="confirm-merge"]').trigger('click')
      expect(client.mergeExperiences).not.toHaveBeenCalled()
    } finally { wrapper.unmount() }
  })
  it('invalidates a preview when the selection changes', async () => {
    const client = api(); const { wrapper } = await setup(ExperiencesView, client)
    try {
      for (const input of wrapper.findAll('.merge-selection input')) await input.setValue(true)
      await wrapper.get('[data-testid="preview-merge"]').trigger('click'); await flushPromises()
      await wrapper.findAll('.merge-selection input')[0]!.setValue(false)
      expect(wrapper.find('[data-testid="merge-preview"]').exists()).toBe(false)
      expect(client.mergeExperiences).not.toHaveBeenCalled()
    } finally { wrapper.unmount() }
  })
})

describe('V2 wire contracts', () => {
  it('supports legacy records while validating repository scope and test evidence', () => {
    const legacy = { id: idA, task_summary: '旧经验', root_cause: '空值', solution: '检查空值', verification: '测试通过', created_at: '2026-10-26T00:00:00Z' }
    expect(readExperiences([legacy])[0]).toMatchObject({ language: 'java', scope: 'global', issues: [], evidence: [] })
    expect(() => readExperiences([{ ...entries[0], repository_id: null }])).toThrow()
    expect(() => readExperiences([{ ...entries[0], evidence: [{ ...knowledge.evidence![0], skipped: 1 }] }])).toThrow()
    expect(() => readMergePreview({ ...mergePreview, preview: null })).toThrow()
  })
  it('encodes repository filters and posts source identities to both merge endpoints', async () => {
    const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(new Response(JSON.stringify(
      url === '/api/projects' ? projects : url.endsWith('merge-preview') ? mergePreview : url.endsWith('/merge') ? entries[0] : entries,
    ))))
    const client = createHttpApi('', fetcher)
    await client.listProjects!()
    await client.listExperiences(6, 5, undefined, { language: 'python', repository_id: 'team/repo & tools', scope: 'project' })
    await client.previewMerge!([idA, idB]); await client.mergeExperiences!([idA, idB])
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(['/api/projects', '/api/experiences?limit=6&offset=5&language=python&repository_id=team%2Frepo+%26+tools&scope=project', '/api/experiences/merge-preview', '/api/experiences/merge'])
    expect(JSON.parse(fetcher.mock.calls[2]![1].body)).toEqual({ experience_ids: [idA, idB] })
    expect(JSON.parse(fetcher.mock.calls[3]![1].body)).toEqual({ experience_ids: [idA, idB] })
  })
})
