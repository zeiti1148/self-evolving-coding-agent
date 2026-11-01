import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CodeDiffView from '../src/views/CodeDiffView.vue'
import { apiContextKey } from '../src/api/context'
import { createHttpApi } from '../src/api/http'
import type { AgentApi, CodeChangeInfo, CodeDiffFile } from '../src/api/contracts'

const id = '11111111-1111-4111-8111-111111111111'
const other = '22222222-2222-4222-8222-222222222222'
const info: CodeChangeInfo = { path: 'service.py', change_type: 'modified', before_sha256: 'a'.repeat(64), after_sha256: 'b'.repeat(64), available: true, unavailable_reason: null }
const data: CodeDiffFile = { ...info, task_id: id, mode: 'snapshot', notice: '任务结束时保存的代码', before_content: '  old\r\n', after_content: '  <script>new</script>\r\n',
  additions: 1, deletions: 1, format_only: false, hunks: [{ old_start: 1, old_lines: 1, new_start: 1, new_lines: 1,
    lines: [{ kind: 'removed', old_line: 1, new_line: null, content: '  old' }, { kind: 'added', old_line: null, new_line: 1, content: '  <script>new</script>' }] }] }
function api(): AgentApi {
  return { createTask: vi.fn(), getTask: vi.fn(), getSteps: vi.fn(), listExperiences: vi.fn(),
    listCodeChanges: vi.fn(async task => ({ task_id: task, status: 'succeeded' as const, mode: 'snapshot' as const, notice: data.notice,
      files: [info, { ...info, path: '中文 file.py' }], omitted_files: 0 })),
    getCodeDiff: vi.fn(async (task, path) => ({ ...data, task_id: task, path })) }
}
async function setup(client = api(), path = `/tasks/${id}/changes?file=service.py`) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/tasks/:taskId/changes', component: CodeDiffView },
    { path: '/tasks/:taskId', component: { template: '<div>任务</div>' } },
    { path: '/tasks/:taskId/tests', component: { template: '<div>测试源码</div>' } },
  ] })
  await router.push(path)
  const wrapper = mount(CodeDiffView, { global: { plugins: [router], provide: {
    [apiContextKey as symbol]: { api: client, mock: false, projectId: 'sample', projectLabel: '样例' },
  } } })
  await flushPromises()
  return { wrapper, router, client }
}
afterEach(() => vi.unstubAllGlobals())

describe('code comparisons', () => {
  it('renders actual added/deleted lines as escaped text and offers both original versions', async () => {
    const { wrapper, router } = await setup()
    expect(wrapper.get('.diff-row.removed .diff-text').element.textContent).toBe('  old')
    expect(wrapper.get('.diff-row.added .diff-text').element.textContent).toBe('  <script>new</script>')
    expect(wrapper.find('.diff-text script').exists()).toBe(false)
    await wrapper.get('[data-view="before"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('.source-content code').element.textContent).toBe(data.before_content)
    expect(router.currentRoute.value.query.view).toBe('before')
    await wrapper.get('[data-view="after"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('.source-content code').element.textContent).toBe(data.after_content)
    wrapper.unmount()
  })

  it('copies a file-specific URL, switches files without refetching the index, and restores a direct view', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const { wrapper, router, client } = await setup()
    await wrapper.findAll('.source-file-list a')[1]!.trigger('click')
    await flushPromises()
    expect(client.listCodeChanges).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.query.file).toBe('中文 file.py')
    await wrapper.get('[data-copy-diff-link]').trigger('click')
    expect(new URL(writeText.mock.calls[0]![0]).searchParams.get('file')).toBe('中文 file.py')
    const path = router.currentRoute.value.fullPath + '&view=after'
    wrapper.unmount()
    const restored = await setup(client, path)
    expect(restored.wrapper.get('.source-content code').element.textContent).toBe(data.after_content)
    restored.wrapper.unmount()
  })

  it('never displays a late response from the previous task', async () => {
    const client = api()
    let complete!: (value: CodeDiffFile) => void
    vi.mocked(client.getCodeDiff!).mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
    const { wrapper, router } = await setup(client)
    await router.push(`/tasks/${other}/changes`)
    await flushPromises()
    complete({ ...data, notice: 'old task only' })
    await flushPromises()
    expect(wrapper.text()).not.toContain('old task only')
    expect(wrapper.get('.source-link-input').element).toHaveProperty('value', expect.stringContaining(other))
    wrapper.unmount()
  })

  it('shows unavailable evidence without substituting another file and retries a read failure', async () => {
    const client = api()
    vi.mocked(client.getCodeDiff!).mockRejectedValueOnce(new Error('代码快照无法核对'))
    const { wrapper } = await setup(client)
    expect(wrapper.get('[role="alert"]').text()).toContain('无法核对')
    expect(wrapper.find('.diff-table').exists()).toBe(false)
    await wrapper.get('[data-retry]').trigger('click')
    await flushPromises()
    expect(wrapper.find('.diff-table').exists()).toBe(true)
    wrapper.unmount()
    const unknown = await setup(client, `/tasks/${id}/changes?file=../private.py`)
    expect(unknown.wrapper.get('[role="alert"]').text()).toContain('不在')
    expect(unknown.wrapper.find('.diff-table').exists()).toBe(false)
    unknown.wrapper.unmount()
  })

  it('rejects wrong task/file identities and inconsistent diff counts at the HTTP boundary', async () => {
    for (const wrong of [{ task_id: other }, { path: 'other.py' }, { additions: 99 }, { before_content: null }]) {
      const client = createHttpApi('', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...data, ...wrong }))))
      await expect(client.getCodeDiff!(id, info.path)).rejects.toMatchObject({ status: 502 })
    }
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(data)))
    expect(await createHttpApi('', fetcher).getCodeDiff!(id, info.path)).toEqual(data)
    expect(fetcher.mock.calls[0]![0]).toBe(`/api/tasks/${id}/code-diff?path=service.py`)
  })
})
