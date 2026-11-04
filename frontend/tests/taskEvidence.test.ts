import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import TaskEvidencePanel from '../src/components/TaskEvidencePanel.vue'
import { apiContextKey } from '../src/api/context'
import type { AgentApi, TaskEvidence } from '../src/api/contracts'
import { createHttpApi } from '../src/api/http'

const id = '11111111-1111-4111-8111-111111111111', other = '22222222-2222-4222-8222-222222222222'
const rule = '33333333-3333-4333-8333-333333333333'
function fixture(taskId = id): TaskEvidence {
  return { task_id: taskId, status: 'succeeded', source_project: 'booking', project_label: '预约项目',
    tests: { before: { tool_call_id: id, sequence: 1, runner: 'maven', tests: 12, passed: 3, failures: 2, errors: 7, skipped: 0 },
      after: { tool_call_id: other, sequence: 8, runner: 'maven', tests: 12, passed: 12, failures: 0, errors: 0, skipped: 0 },
      final_verified: true, original_tests_unchanged: true, comparable: true, unavailable_reason: null },
    failure_cases: [{ test: 'ServiceTest.missingName', error_type: 'java.lang.NullPointerException', message: '访问了缺失的对象或输入。', source_file: 'src/test/ServiceTest.java', source_line: 9 },
      { test: 'ServiceTest.invalidNumber', error_type: 'NumberFormatException', message: '输入文本不能解析为数字。', source_file: 'src/test/ServiceTest.java', source_line: 16 }],
    references: [{ experience_id: rule, title: '按目标业务要求处理空白文本', source_task_id: other, scope: 'global', content_sha256: 'a'.repeat(64),
      roles: [{ role: 'diagnosis', request_count: 2 }, { role: 'repair', request_count: 1 }, { role: 'review', request_count: 1 }], request_count: 4, detail_available: true }],
    reference_recording: 'recorded', reference_notice: '记录表明经验已提供给模型，不代表效率或因果关系。' }
}
function api(): AgentApi { return { createTask: vi.fn(), getTask: vi.fn(), getSteps: vi.fn(), listExperiences: vi.fn(), getTaskEvidence: vi.fn(async id => fixture(id)) } }
async function setup(client = api()) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div />' } }, { path: '/tasks/:taskId/tests', component: { template: '<div />' } },
    { path: '/tasks/:taskId', component: { template: '<div />' } }, { path: '/experiences', component: { template: '<div />' } },
  ] })
  await router.push('/')
  const wrapper = mount(TaskEvidencePanel, { props: { taskId: id, status: 'succeeded', stepsCompleted: 8 }, global: { plugins: [router], provide: {
    [apiContextKey as symbol]: { api: client, mock: false, projectId: 'booking', projectLabel: '预约项目' },
  } } })
  await flushPromises()
  return { wrapper, router }
}
describe('task repair evidence', () => {
  it('lets the user inspect actual failed cases and navigate to their original test line', async () => {
    const { wrapper } = await setup()
    expect(wrapper.get('[data-testid="before-failed"]').text()).toBe('9')
    expect(wrapper.get('[data-testid="after-failed"]').text()).toBe('0')
    await wrapper.get('[data-testid="toggle-failures"]').trigger('click')
    expect(wrapper.get('[data-testid="failure-source"]').attributes('href')).toContain('line=9')
    await wrapper.get('[data-testid="failure-1"]').trigger('click')
    expect(wrapper.get('.failure-description').text()).toContain('输入文本不能解析为数字')
    expect(wrapper.get('[data-testid="failure-source"]').attributes('href')).toContain('line=16')
    wrapper.unmount()
  })
  it('shows actual model request roles and links to the exact rule and source task', async () => {
    const { wrapper } = await setup()
    await wrapper.get('[data-testid="comparison-tab"]').trigger('keydown', { key: 'ArrowRight' })
    expect(wrapper.get('[data-testid="references-tab"]').attributes('aria-selected')).toBe('true')
    expect(wrapper.get('.reference-roles').text()).toContain('诊断2 次')
    expect(wrapper.get('[data-testid="reference-detail"]').attributes('href')).toContain(`id=${rule}`)
    expect(wrapper.get('[data-testid="reference-source"]').attributes('href')).toBe(`/tasks/${other}`)
    expect(wrapper.text()).toContain('不代表效率或因果关系')
    wrapper.unmount()
  })
  it('does not present an improvement when original tests cannot be compared', async () => {
    const client = api(), data = fixture()
    data.tests.comparable = false; data.tests.original_tests_unchanged = false; data.tests.unavailable_reason = '原始测试已变化，不能进行前后对比。'
    vi.mocked(client.getTaskEvidence!).mockResolvedValue(data)
    const { wrapper } = await setup(client)
    expect(wrapper.find('[data-testid="before-failed"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('不能进行前后对比')
    wrapper.unmount()
  })
  it('never displays a late evidence response from another task', async () => {
    const client = api(); let finish!: (value: TaskEvidence) => void
    vi.mocked(client.getTaskEvidence!).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const { wrapper } = await setup(client)
    await wrapper.setProps({ taskId: other })
    await flushPromises()
    finish({ ...fixture(), project_label: '旧任务不应显示' }); await flushPromises()
    expect(wrapper.text()).not.toContain('旧任务不应显示')
    expect(wrapper.get('[data-testid="before-failed"]').text()).toBe('9')
    wrapper.unmount()
  })
  it('explains an unsupported backend and retries without inventing evidence', async () => {
    const client = api(); vi.mocked(client.getTaskEvidence!).mockRejectedValueOnce(new Error('此后端暂不支持修复证据摘要'))
    const { wrapper } = await setup(client)
    expect(wrapper.find('[data-testid="before-failed"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('暂不支持')
    await wrapper.get('[data-testid="retry-evidence"]').trigger('click'); await flushPromises()
    expect(wrapper.find('[data-testid="before-failed"]').exists()).toBe(true)
    wrapper.unmount()
  })
  it('rejects unrelated identities, unsafe links, impossible comparisons and inconsistent counts at HTTP boundary', async () => {
    const invalid = [
      (data: TaskEvidence) => { data.task_id = other },
      (data: TaskEvidence) => { data.failure_cases[0]!.source_file = '../private.txt' },
      (data: TaskEvidence) => { data.references[0]!.source_task_id = 'https://example.com' },
      (data: TaskEvidence) => { data.references[0]!.request_count = 5 },
      (data: TaskEvidence) => { data.tests.before!.passed = 10 },
      (data: TaskEvidence) => { data.tests.original_tests_unchanged = false },
      (data: TaskEvidence) => { data.tests.after!.tests = 13; data.tests.after!.skipped = 1 },
    ]
    for (const mutate of invalid) {
      const data = fixture(); mutate(data)
      const client = createHttpApi('', vi.fn().mockResolvedValue(new Response(JSON.stringify(data))))
      await expect(client.getTaskEvidence!(id)).rejects.toMatchObject({ status: 502 })
    }
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(fixture())))
    expect(await createHttpApi('', fetcher).getTaskEvidence!(id)).toEqual(fixture())
    expect(fetcher.mock.calls[0]![0]).toBe(`/api/tasks/${id}/evidence`)
  })
})
