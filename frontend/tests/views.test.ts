import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import NewTaskView from '../src/views/NewTaskView.vue'
import ExperiencesView from '../src/views/ExperiencesView.vue'
import ResultSummary from '../src/components/ResultSummary.vue'
import App from '../src/App.vue'
import TaskView from '../src/views/TaskView.vue'
import { createHttpApi } from '../src/api/http'
import { createMockApi } from '../src/api/mock'
import { seedExperiences } from '../src/api/fixtures'
import { apiContextKey } from '../src/api/context'
import type { AgentApi, AgentResult, Experience } from '../src/api/contracts'

const taskId = '11111111-1111-4111-8111-111111111111'
const api = (): AgentApi => ({ createTask: vi.fn(), getTask: vi.fn(), getSteps: vi.fn(), listExperiences: vi.fn().mockResolvedValue([]) })
async function setup(component: typeof NewTaskView | typeof ExperiencesView, client: AgentApi) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component }, { path: '/tasks/:taskId', component: { template: '<div>task</div>' } },
  ] })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(component, { global: { plugins: [router], provide: {
    [apiContextKey as symbol]: { api: client, mock: false, projectId: 'registered-java', projectLabel: 'Java 样例' },
  } } })
  return { wrapper, router }
}

describe('task submission', () => {
  it('validates required description and max steps before calling API', async () => {
    const client = api()
    const { wrapper } = await setup(NewTaskView, client)
    await wrapper.get('form').trigger('submit')
    expect(client.createTask).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('任务描述')
    await wrapper.get('textarea').setValue('修复 null 输入')
    await wrapper.get('input[type="number"]').setValue(101)
    await wrapper.get('form').trigger('submit')
    expect(client.createTask).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('100')
    wrapper.unmount()
  })

  it('sends the documented payload only once while submitting and opens returned task', async () => {
    const client = api()
    let complete!: (value: unknown) => void
    vi.mocked(client.createTask).mockImplementation(() => new Promise(resolve => { complete = resolve as (value: unknown) => void }))
    const { wrapper, router } = await setup(NewTaskView, client)
    await wrapper.get('textarea').setValue('  修复 null 输入  ')
    await wrapper.get('form').trigger('submit')
    await wrapper.get('form').trigger('submit')
    expect(client.createTask).toHaveBeenCalledTimes(1)
    expect(vi.mocked(client.createTask).mock.calls[0]?.[0]).toEqual({ task_description: '修复 null 输入', source_project: 'registered-java', max_steps: 20, execution_mode: 'single' })
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    complete({ task_id: taskId, status: 'pending', created_at: '2026-10-20T00:00:00Z' })
    await flushPromises()
    expect(router.currentRoute.value.path).toBe(`/tasks/${taskId}`)
    wrapper.unmount()
  })

  it('keeps the description and allows retry after submission failure', async () => {
    const client = api()
    vi.mocked(client.createTask).mockRejectedValue(new Error('服务暂时不可用'))
    const { wrapper } = await setup(NewTaskView, client)
    await wrapper.get('textarea').setValue('保留这段描述')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('服务暂时不可用')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('保留这段描述')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })
})

const result: AgentResult = {
  task_id: taskId, status: 'succeeded', summary: '修复已验证', repair_succeeded: true,
  tests_passed: true, changed_files: ['src/GreetingService.java'], experience_saved: false,
  experience_id: null, experience_save_error: '经验存储暂不可用', error_message: null,
  steps: [{ id: taskId, name: 'run_tests', arguments: {} }],
  tool_results: [{ call_id: taskId, tool_name: 'run_tests', success: true, output: 'Tests run: 2', error_code: null, error_message: null, metadata: {} }],
}
it('shows verified success separately from failed experience saving', () => {
  const wrapper = mount(ResultSummary, { props: { result } })
  expect(wrapper.text()).toContain('修复成功')
  expect(wrapper.text()).toContain('测试通过')
  expect(wrapper.text()).toContain('保存失败')
  expect(wrapper.text()).toContain('经验存储暂不可用')
  wrapper.unmount()
})
it('uses final test facts for the main conclusion while preserving the old pretest summary separately', () => {
  const summary = '代码修改已完成，等待程序独立运行最终测试。'
  const wrapper = mount(ResultSummary, { props: { result: { ...result, summary } } })
  expect(wrapper.get('.result-summary').text()).toContain('最终测试已通过')
  expect(wrapper.get('.result-summary').text()).not.toContain('等待')
  expect(wrapper.get('details').text()).toContain(summary)
  expect(wrapper.get('details').attributes('open')).toBeUndefined()
  wrapper.unmount()
})
it('does not use a model success claim when final tests failed', () => {
  const wrapper = mount(ResultSummary, { props: { result: { ...result, status: 'failed', tests_passed: false,
    repair_succeeded: false, summary: '模型声称修复成功。' } } })
  expect(wrapper.get('.result-summary').text()).toContain('最终测试未通过')
  expect(wrapper.get('.result-summary').text()).not.toContain('修复成功')
  wrapper.unmount()
})
it('does not turn null test and experience facts into failures', () => {
  const wrapper = mount(ResultSummary, { props: { result: {
    ...result, status: 'stopped', repair_succeeded: false, tests_passed: null,
    experience_saved: null, experience_save_error: null,
  } } })
  expect(wrapper.text()).toContain('未完成验证')
  expect(wrapper.text()).toContain('未尝试保存')
  expect(wrapper.text()).not.toContain('测试未通过')
  wrapper.unmount()
})

const entries: Experience[] = Array.from({ length: 6 }, (_, index) => ({
  id: String(index), task_summary: `经验 ${index}`, error_type: 'NullPointerException',
  root_cause: 'null 输入', solution: '检查输入', verification: '测试通过', tags: ['java'],
  source_task_id: taskId, created_at: '2026-10-20T00:00:00Z',
}))
it('paginates experience arrays without inventing a total count', async () => {
  const client = api()
  vi.mocked(client.listExperiences).mockResolvedValueOnce(entries).mockResolvedValueOnce(entries.slice(5))
  const { wrapper } = await setup(ExperiencesView, client)
  await flushPromises()
  expect(wrapper.findAll('[data-testid="experience-item"]')).toHaveLength(5)
  await wrapper.get('[data-testid="next-page"]').trigger('click')
  await flushPromises()
  expect(vi.mocked(client.listExperiences).mock.calls[1]?.slice(0, 2)).toEqual([6, 5])
  expect(wrapper.findAll('[data-testid="experience-item"]')).toHaveLength(1)
  expect(wrapper.get('[data-testid="next-page"]').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})
it('distinguishes a storage error from an empty experience library', async () => {
  const client = api()
  vi.mocked(client.listExperiences).mockRejectedValueOnce(new Error('服务暂时不可用'))
  const { wrapper } = await setup(ExperiencesView, client)
  await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('服务暂时不可用')
  expect(wrapper.text()).not.toContain('还没有经验记录')
  await wrapper.get('[data-testid="retry"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('还没有经验记录')
  wrapper.unmount()
})

it('moves keyboard focus to the main content without changing the current route', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div />' } },
    { path: '/experiences', component: { template: '<p>经验页面</p>' } },
    { path: '/tasks/:taskId', component: { template: '<div />' } },
  ] })
  await router.push('/experiences')
  const wrapper = mount(App, { attachTo: document.body, global: {
    plugins: [router], provide: { [apiContextKey as symbol]: { api: api(), mock: true } },
  } })
  try {
    await wrapper.get('.skip-link').trigger('click')
    expect(document.activeElement).toBe(wrapper.get('main').element)
    expect(router.currentRoute.value.path).toBe('/experiences')
  } finally { wrapper.unmount() }
})

it('switches themes using accessible buttons without navigating or losing form input', async () => {
  localStorage.setItem('coding-agent-theme', 'light')
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: NewTaskView }] })
  await router.push('/')
  const wrapper = mount(App, { global: { plugins: [router], provide: {
    [apiContextKey as symbol]: { api: api(), mock: true, projectId: 'java', projectLabel: 'Java 样例' },
  } } })
  try {
    await wrapper.get('textarea').setValue('切换主题时保留这段任务描述')
    const buttons = wrapper.findAll('.theme-picker button')
    expect(buttons[0]!.attributes('aria-pressed')).toBe('true')
    await buttons[1]!.trigger('click')
    expect(buttons[1]!.attributes('aria-pressed')).toBe('true')
    expect(buttons[0]!.attributes('aria-pressed')).toBe('false')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('切换主题时保留这段任务描述')
    expect(router.currentRoute.value.path).toBe('/')
    expect(localStorage.getItem('coding-agent-theme')).toBe('dark')
  } finally { wrapper.unmount(); localStorage.removeItem('coding-agent-theme'); delete document.documentElement.dataset.theme }
})

it('shows a readable contract error and recovers the experience page on retry', async () => {
  const records = seedExperiences(Date.parse('2026-10-22T00:00:00Z'))
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: records })))
    .mockResolvedValueOnce(new Response(JSON.stringify(records.slice(0, 6))))
  const { wrapper } = await setup(ExperiencesView, createHttpApi('', fetcher))
  try {
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('接口约定')
    expect(wrapper.text()).not.toContain('还没有经验记录')
    expect(wrapper.text()).not.toContain('slice is not a function')
    await wrapper.get('[data-testid="retry"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="experience-item"]')).toHaveLength(5)
    expect(wrapper.text()).toContain('为问候方法补充空值分支')
  } finally { wrapper.unmount() }
})

it('keeps a verified result visible while HTTP step loading fails and recovers on retry', async () => {
  let now = 100000
  const demo = createMockApi({ now: () => now, stepMs: 1, storage: null })
  const created = await demo.createTask({ task_description: 'fix', source_project: 'java-null-greeting', max_steps: 20 })
  now += 100
  const task = await demo.getTask(created.task_id)
  const steps = await demo.getSteps(created.task_id)
  let failSteps = true
  const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(url.endsWith('/steps')
    ? failSteps ? new Response('private traceback', { status: 500 }) : new Response(JSON.stringify(steps))
    : url.includes('/agents?') ? new Response('[]') : new Response(JSON.stringify(task))))
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/tasks/:taskId', component: TaskView },
    { path: '/', component: { template: '<div />' } },
    { path: '/experiences', component: { template: '<div />' } },
  ] })
  await router.push(`/tasks/${created.task_id}`)
  const wrapper = mount(TaskView, { global: { plugins: [router], provide: {
    [apiContextKey as symbol]: { api: createHttpApi('', fetcher), mock: false },
  } } })
  try {
    await flushPromises()
    expect(wrapper.text()).toContain('修复成功')
    expect(wrapper.text()).toContain('测试通过')
    expect(wrapper.html().indexOf('result-panel')).toBeLessThan(wrapper.html().indexOf('execution-panel'))
    expect(wrapper.get('[role="alert"]').text()).toContain('服务暂时不可用')
    expect(wrapper.text()).not.toContain('private traceback')
    failSteps = false
    await wrapper.get('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.findAll('.execution-list > li')).toHaveLength(5)
  } finally { wrapper.unmount() }
})
