import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import type { AgentApi, TaskMetrics } from '../src/api/contracts'
import { apiContextKey } from '../src/api/context'
import { createHttpApi } from '../src/api/http'
import { readTaskMetrics } from '../src/api/metricsValidation'
import { comparisonGroups } from '../src/features/taskMetrics'
import TaskMetricsPanel from '../src/components/TaskMetricsPanel.vue'

const ids = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222', '33333333-3333-4333-8333-333333333333']
const metric = (index = 0, changes: Partial<TaskMetrics> = {}): TaskMetrics => ({
  task_id: ids[index]!, task_url: null, status: 'succeeded', project_label: `工程 ${index + 1}`,
  runtime_label: '版本 A · 第 1 轮', code_revision: 'abcdef123456', comparison_group: '版本 A',
  repair_verified: true, final_tests_passed: true, elapsed_ms: 60000, model_requests: 2,
  token_usage: { total_tokens: 1000, reported_total_tokens: 1000, reported_requests: 2, total_requests: 2 },
  retrieval: { strategy: 'adaptive', injected_requests: 1, reference_count: 1,
    events: [{ reason: 'test_failure', error_types: ['AssertionError'], experience_ids: [ids[1]!] },
      { reason: 'tests_passed', error_types: [], experience_ids: [] }] },
  experience: { state: 'succeeded', elapsed_ms: 40000, model_requests: 1,
    token_usage: { total_tokens: 300, reported_total_tokens: 300, reported_requests: 1, total_requests: 1 },
    before_count: 2, after_count: 1, merged_count: 1, conflict_count: 0, resolved_count: 0, pending_confirmation_count: 0,
    experience_ids: [ids[0]!], notice: '保留两条原始来源，归并为一条已验证规则。' }, ...changes,
})

describe('metrics contract and actual usage accounting', () => {
  it('keeps incomplete usage distinct from zero and rejects invented complete totals', () => {
    const partial = metric(0, { token_usage: { total_tokens: null, reported_total_tokens: 650, reported_requests: 1, total_requests: 2 } })
    expect(readTaskMetrics(partial, ids[0]!).token_usage.total_tokens).toBeNull()
    expect(() => readTaskMetrics({ ...partial, token_usage: { ...partial.token_usage, total_tokens: 650 } }, ids[0]!)).toThrow()
    expect(() => readTaskMetrics({ ...partial, token_usage: { ...partial.token_usage, reported_requests: 3 } }, ids[0]!)).toThrow()
    expect(() => readTaskMetrics({ ...partial, model_requests: 0 }, ids[0]!)).toThrow()
    expect(() => readTaskMetrics({ ...partial, task_url: 'javascript:alert(1)' }, ids[0]!)).toThrow()
    expect(() => readTaskMetrics(partial, ids[1]!)).toThrow()
  })
  it('accepts unavailable request audits without converting them to a zero-call run', () => {
    const missing = metric(0, { model_requests: null,
      token_usage: { total_tokens: null, reported_total_tokens: 0, reported_requests: 0, total_requests: null } })
    expect(readTaskMetrics(missing, ids[0]!).model_requests).toBeNull()
    expect(() => readTaskMetrics({ ...missing, token_usage: { ...missing.token_usage, total_tokens: 0 } }, ids[0]!)).toThrow()
  })
  it('keeps failed and stopped tasks in the denominator and running tasks outside it', () => {
    const tasks = [metric(), metric(1, { status: 'failed', repair_verified: false, elapsed_ms: 120000 }),
      metric(2, { status: 'running', repair_verified: false, elapsed_ms: null })]
    const group = comparisonGroups(tasks)[0]!
    expect(group).toMatchObject({ finished: 2, successes: 1, pending: 1, rate: 0.5, median: 90000, tokens: 2000, experienceTokens: 600 })
    const stopped = comparisonGroups([tasks[0]!, metric(1, { status: 'stopped', repair_verified: false })])[0]!
    expect(stopped.rate).toBe(0.5)
  })
  it('separates passed final tests from a stopped workflow without dropping the stopped task', () => {
    const stopped = metric(1, { status: 'stopped', repair_verified: false, final_tests_passed: true })
    expect(comparisonGroups([metric(), stopped])[0]).toMatchObject({ finished: 2, successes: 1, rate: 0.5,
      testPasses: 2, testCoverage: 2, testRate: 1 })
    expect(comparisonGroups([metric(), metric(1, { final_tests_passed: undefined })])[0]?.testRate).toBeNull()
    expect(() => readTaskMetrics({ ...stopped, final_tests_passed: 'passed' }, ids[1]!)).toThrow()
  })
  it('does not turn missing token or duration evidence into an apparent saving', () => {
    const partial = metric(1, { elapsed_ms: null, token_usage: { total_tokens: null, reported_total_tokens: 20, reported_requests: 1, total_requests: 2 } })
    const group = comparisonGroups([metric(), partial])[0]!
    expect(group).toMatchObject({ tokens: null, median: null, reportedTokens: 1020, tokenCoverage: 1, timeCoverage: 1 })
    expect(comparisonGroups([metric(0, { status: 'pending' })])[0]?.rate).toBeNull()
  })
  it('uses server-owned peer URLs and validates both metrics endpoints', async () => {
    const row = metric(0, { task_url: 'http://127.0.0.1:5181/#/tasks/' + ids[0] })
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify(row), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([row]), { status: 200 }))
    const client = createHttpApi('', fetcher)
    expect((await client.getTaskMetrics!(ids[0]!)).task_url).toContain(':5181/')
    expect((await client.listTaskMetrics!()).length).toBe(1)
    expect(fetcher.mock.calls.map(call => String(call[0]))).toEqual([`/api/tasks/${ids[0]}/metrics`, '/api/task-metrics?limit=100'])
  })
})

async function setup(rows: TaskMetrics[], query = '', override: Partial<AgentApi> = {}) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/tasks/:taskId', component: TaskMetricsPanel }] })
  await router.push(`/tasks/${ids[0]}${query}`); await router.isReady()
  const api: AgentApi = { createTask: vi.fn(), getTask: vi.fn(), getSteps: vi.fn(), listExperiences: vi.fn(),
    getTaskMetrics: vi.fn(async id => { const found = rows.find(item => item.task_id === id); if (!found) throw new Error('该任务指标不可用'); return found }),
    listTaskMetrics: vi.fn(async () => rows), ...override }
  const wrapper = mount(TaskMetricsPanel, { props: { taskId: ids[0]!, status: rows[0]!.status, stepsCompleted: 4 },
    global: { plugins: [router], provide: { [apiContextKey as symbol]: { api, mock: false } } } })
  await flushPromises()
  return { wrapper, api, router }
}

describe('normal task metrics and comparison panel', () => {
  it('keeps the task page compact and shows real retrieval and experience changes on demand', async () => {
    const { wrapper, api } = await setup([metric()])
    expect(wrapper.text()).toContain('1.0 分钟')
    expect(wrapper.text()).toContain('40.0 秒')
    expect(wrapper.text()).toContain('处理前 2')
    expect(wrapper.text()).toContain('处理后 1')
    expect(wrapper.text()).toContain('测试通过，撤下参考')
    expect(wrapper.get('.metrics-retrieval').text()).toContain('1 份经验来源')
    expect(wrapper.get('.metrics-retrieval').text()).not.toContain('条不同引用')
    expect(wrapper.find('[data-testid="task-comparison"]').exists()).toBe(false)
    expect(api.listTaskMetrics).not.toHaveBeenCalled()
    expect(wrapper.get('.metrics-source-link').attributes('href')).toContain('/experiences?id=')
    wrapper.unmount()
  })
  it('keeps unknown retrieval source counts distinct from zero sources', async () => {
    const missing = metric(0, { retrieval: { ...metric().retrieval, reference_count: null } })
    const { wrapper } = await setup([missing])
    expect(wrapper.get('.metrics-retrieval').text()).toContain('来源数量未记录')
    expect(wrapper.get('.metrics-retrieval').text()).not.toContain('0 份经验来源')
    wrapper.unmount()
  })
  it('compares selected tasks inside the normal task route and preserves failed outcomes', async () => {
    const failure = metric(1, { status: 'failed', repair_verified: false })
    const { wrapper, api, router } = await setup([metric(), failure], `?compare=${ids[0]},${ids[1]}`)
    expect(api.listTaskMetrics).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[data-testid="metrics-group"]').text()).toContain('50%')
    expect(wrapper.text()).toContain('1 / 2 项已完成任务')
    expect(wrapper.findAll('.metrics-table tbody tr')).toHaveLength(2)
    const checkbox = wrapper.findAll('.metrics-option input')[1]!
    await checkbox.setValue(false); await flushPromises()
    expect(router.currentRoute.value.path).toBe(`/tasks/${ids[0]}`)
    expect(router.currentRoute.value.query.compare).toBe(ids[0])
    expect(wrapper.get('[data-testid="metrics-group"]').text()).toContain('100%')
    wrapper.unmount()
  })
  it('shows stopped-but-test-passing tasks accurately alongside both completion rates', async () => {
    const stopped = metric(1, { status: 'stopped', repair_verified: false, final_tests_passed: true })
    const { wrapper } = await setup([metric(), stopped], `?compare=${ids[0]},${ids[1]}`)
    const group = wrapper.get('[data-testid="metrics-group"]')
    expect(group.text()).toContain('完整流程成功率')
    expect(group.text()).toContain('50%')
    expect(group.text()).toContain('最终测试通过率100%（2/2）')
    const row = wrapper.findAll('.metrics-table tbody tr')[1]!
    expect(row.text()).toContain('流程停止')
    expect(row.text()).toContain('最终测试通过')
    expect(row.text()).not.toContain('最终测试未通过')
    expect(wrapper.text()).toContain('决策耗尽时可能已通过测试，但尚未保存经验')
    wrapper.unmount()
  })
  it('shows incomplete token coverage rather than zero cost', async () => {
    const partial = metric(0, { token_usage: { total_tokens: null, reported_total_tokens: 650, reported_requests: 1, total_requests: 2 } })
    const { wrapper } = await setup([partial], `?compare=${ids[0]}`)
    expect(wrapper.text()).toContain('未完整上报')
    expect(wrapper.text()).toContain('已记录 650 · 1/2 次请求有用量')
    expect(wrapper.get('[data-testid="metrics-group"]').text()).toContain('0/1 项用量完整')
    wrapper.unmount()
  })
  it('labels unknown model-call totals and missing request coverage explicitly', async () => {
    const missing = metric(0, { model_requests: null,
      token_usage: { total_tokens: null, reported_total_tokens: 0, reported_requests: 0, total_requests: null } })
    const { wrapper } = await setup([missing])
    expect(wrapper.text()).toContain('请求审计尚未记录')
    expect(wrapper.text()).toContain('等待请求审计写入')
    expect(wrapper.findAll('.metrics-facts strong')[2]?.text()).toBe('未记录')
    wrapper.unmount()
  })
  it('does not present a partial selection as a complete comparison after a read failure', async () => {
    const { wrapper } = await setup([metric()], `?compare=${ids[0]},${ids[1]}`)
    expect(wrapper.text()).toContain('该任务指标不可用')
    expect(wrapper.find('[data-testid="metrics-group"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('does not fetch or show metrics when this connection has no endpoint', async () => {
    const { wrapper } = await setup([metric()], '', { getTaskMetrics: undefined })
    expect(wrapper.find('[data-testid="task-metrics"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
