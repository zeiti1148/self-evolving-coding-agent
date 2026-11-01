import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TestSourceView from '../src/views/TestSourceView.vue'
import ResultSummary from '../src/components/ResultSummary.vue'
import ExecutionSteps from '../src/components/ExecutionSteps.vue'
import { apiContextKey } from '../src/api/context'
import type { AgentApi, AgentResult, TaskStatusResponse, TaskStep, TestSourceFile } from '../src/api/contracts'

const taskId = '11111111-1111-4111-8111-111111111111'
const secondTask = '22222222-2222-4222-8222-222222222222'
const files = [
  { path: 'tests/test_a.py', language: 'python' as const, sha256: 'a'.repeat(64) },
  { path: 'tests/中文 test_b.py', language: 'python' as const, sha256: 'b'.repeat(64) },
]
const content = '  # 中文\r\nassert "<script>literal</script>"\r\n\r\n'
function source(id = taskId, path = files[0]!.path): TestSourceFile {
  return { task_id: id, ...files.find(file => file.path === path)!, content, size_bytes: new TextEncoder().encode(content).length }
}
function api(): AgentApi {
  return { createTask: vi.fn(), getTask: vi.fn(), getSteps: vi.fn(), listExperiences: vi.fn(),
    listTestSources: vi.fn(async id => ({ task_id: id, files })), getTestSource: vi.fn(async (id, path) => source(id, path)) }
}
async function setup(client: AgentApi, path = `/tasks/${taskId}/tests`, mock = false) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/tasks/:taskId/tests', component: TestSourceView },
    { path: '/tasks/:taskId', component: { template: '<div>任务</div>' } },
  ] })
  await router.push(path)
  const wrapper = mount(TestSourceView, { global: { plugins: [router], provide: {
    [apiContextKey as symbol]: { api: client, mock, projectId: 'sample', projectLabel: '样例' },
  } } })
  await flushPromises()
  return { router, wrapper }
}
afterEach(() => vi.unstubAllGlobals())

describe('task test source viewer', () => {
  it('opens a direct file link, preserves whitespace, escapes HTML and copies the same link', async () => {
    const client = api(), writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const { wrapper } = await setup(client, `/tasks/${taskId}/tests?${new URLSearchParams({ file: files[1]!.path })}`)
    expect(client.getTestSource).toHaveBeenCalledWith(taskId, files[1]!.path, expect.any(AbortSignal))
    expect(wrapper.get('.source-content code').element.textContent).toBe(content)
    expect(wrapper.find('.source-content script').exists()).toBe(false)
    expect(wrapper.get('.source-line-numbers').text()).toBe('1\n2\n3\n4')
    await wrapper.get('[data-copy-link]').trigger('click')
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining(`/tasks/${taskId}/tests?file=`))
    expect(new URL(writeText.mock.calls[0]![0]).searchParams.get('file')).toBe(files[1]!.path)
    expect(wrapper.text()).toContain('链接已复制')
    wrapper.unmount()
  })

  it('switches files via URL and can reload that exact file', async () => {
    const client = api(), { wrapper, router } = await setup(client)
    await wrapper.findAll('.source-file-list a')[1]!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.file).toBe(files[1]!.path)
    expect(client.getTestSource).toHaveBeenLastCalledWith(taskId, files[1]!.path, expect.any(AbortSignal))
    const url = router.currentRoute.value.fullPath
    wrapper.unmount()
    const again = await setup(client, url)
    expect(again.wrapper.get('.source-filename').text()).toBe(files[1]!.path)
    again.wrapper.unmount()
  })

  it('rejects an unknown linked file rather than silently showing a different one', async () => {
    const client = api(), { wrapper } = await setup(client, `/tasks/${taskId}/tests?file=../secret.py`)
    expect(wrapper.get('[role="alert"]').text()).toContain('不在')
    expect(client.getTestSource).not.toHaveBeenCalled()
    expect(wrapper.find('.source-content').exists()).toBe(false)
    wrapper.unmount()
  })

  it('clears previous content on read failure and supports retry', async () => {
    const client = api(), { wrapper, router } = await setup(client)
    vi.mocked(client.getTestSource!).mockRejectedValueOnce(new Error('测试文件已被改动'))
    await router.push({ query: { file: files[1]!.path } })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('已被改动')
    expect(wrapper.find('.source-content').exists()).toBe(false)
    await wrapper.get('[data-retry]').trigger('click')
    await flushPromises()
    expect(wrapper.get('.source-filename').text()).toBe(files[1]!.path)
    wrapper.unmount()
  })

  it('ignores an old response when the user opens another task', async () => {
    const client = api()
    let complete!: (value: TestSourceFile) => void
    vi.mocked(client.getTestSource!).mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
    const { wrapper, router } = await setup(client)
    await router.push(`/tasks/${secondTask}/tests`)
    await flushPromises()
    complete({ ...source(), content: 'old task private source', size_bytes: 23 })
    await flushPromises()
    expect(wrapper.text()).not.toContain('old task private source')
    expect(wrapper.get('.source-content code').element.textContent).toBe(content)
    expect(wrapper.get('.source-link-input').attributes('value') ?? (wrapper.get('.source-link-input').element as HTMLInputElement).value).toContain(secondTask)
    wrapper.unmount()
  })

  it('explains missing workspaces and does not invent source in browser-only mock mode', async () => {
    const client = api()
    vi.mocked(client.listTestSources!).mockRejectedValueOnce(new Error('任务工作副本已被清理。'))
    const missing = await setup(client)
    expect(missing.wrapper.get('[role="alert"]').text()).toContain('工作副本')
    expect(client.getTestSource).not.toHaveBeenCalled()
    missing.wrapper.unmount()
    const mock = await setup(client, `/tasks/${taskId}/tests`, true)
    expect(mock.wrapper.text()).toContain('模拟模式')
    expect(client.listTestSources).toHaveBeenCalledTimes(1)
    mock.wrapper.unmount()
  })

  it('links from the final result and test steps to their own task', async () => {
    const client = api(), { wrapper, router } = await setup(client)
    wrapper.unmount()
    const step: TaskStep = { sequence: 1, completed_at: '2026-11-01T10:00:00Z', call: { id: taskId, name: 'run_tests', arguments: {} },
      result: { call_id: taskId, tool_name: 'run_tests', success: false, output: 'failed', error_code: 'TEST_FAILED', error_message: '测试未通过', metadata: { changed_files: [] } } }
    const result: AgentResult = { task_id: taskId, status: 'failed', summary: '失败', steps: [step.call], tool_results: [step.result], repair_succeeded: false,
      tests_passed: false, changed_files: [], experience_saved: null, experience_id: null, experience_save_error: null, error_message: null }
    const task: TaskStatusResponse = { task_id: taskId, status: 'failed', steps_completed: 1, result, created_at: step.completed_at,
      started_at: step.completed_at, finished_at: step.completed_at, error_message: null }
    const final = mount(ResultSummary, { props: { result, testSourceAvailable: true }, global: { plugins: [router] } })
    const steps = mount(ExecutionSteps, { props: { task, steps: [step], mock: false, testSourceAvailable: true }, global: { plugins: [router] } })
    for (const view of [final, steps]) {
      const link = view.findAll('a').find(a => a.text().includes('查看测试源码'))!
      expect(link.attributes('href')).toContain(`/tasks/${taskId}/tests`)
      view.unmount()
    }
  })
})
