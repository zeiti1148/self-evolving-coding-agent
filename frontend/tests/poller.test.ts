import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createTaskPoller } from '../src/features/taskPoller'
import type { AgentApi, TaskStatusResponse, TaskStep } from '../src/api/contracts'

const status = (id: string, state: TaskStatusResponse['status'] = 'running', count = 1): TaskStatusResponse => ({
  task_id: id, status: state, steps_completed: count, created_at: '2026-10-20T00:00:00Z',
  started_at: '2026-10-20T00:00:01Z', finished_at: state === 'running' ? null : '2026-10-20T00:00:02Z',
  result: null, error_message: null,
})
const step = (sequence: number): TaskStep => ({ sequence, completed_at: '2026-10-20T00:00:02Z',
  call: { id: `call-${sequence}`, name: 'read_file', arguments: {} },
  result: { call_id: `call-${sequence}`, tool_name: 'read_file', success: true, output: '  code\n', error_code: null, error_message: null, metadata: {} },
})
const baseApi = (): AgentApi => ({
  createTask: vi.fn(), listExperiences: vi.fn(),
  getTask: vi.fn().mockResolvedValue(status('a')),
  getSteps: vi.fn().mockResolvedValue({ task_id: 'a', status: 'running', steps: [step(1)] }),
})
beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

it('deduplicates and sorts completed steps and stops future polls', async () => {
  const api = baseApi()
  vi.mocked(api.getSteps).mockResolvedValue({ task_id: 'a', status: 'running', steps: [step(2), step(1), step(2)] })
  const update = vi.fn()
  const poller = createTaskPoller(api, { onSnapshot: update, onError: vi.fn() }, 100)
  poller.start('a')
  await vi.advanceTimersByTimeAsync(250)
  expect(update.mock.lastCall?.[1].map((item: TaskStep) => item.sequence)).toEqual([1, 2])
  poller.stop()
  const calls = vi.mocked(api.getTask).mock.calls.length
  await vi.advanceTimersByTimeAsync(1000)
  expect(api.getTask).toHaveBeenCalledTimes(calls)
})

it('keeps polling terminal status until the final completed steps arrive', async () => {
  const api = baseApi()
  vi.mocked(api.getTask).mockResolvedValue(status('a', 'succeeded', 2))
  vi.mocked(api.getSteps)
    .mockResolvedValueOnce({ task_id: 'a', status: 'running', steps: [step(1)] })
    .mockResolvedValue({ task_id: 'a', status: 'succeeded', steps: [step(1), step(2)] })
  const update = vi.fn()
  const poller = createTaskPoller(api, { onSnapshot: update, onError: vi.fn() }, 100)
  poller.start('a')
  await vi.advanceTimersByTimeAsync(1000)
  expect(api.getTask).toHaveBeenCalledTimes(2)
  expect(update.mock.lastCall?.[1]).toHaveLength(2)
  poller.stop()
})

it('aborts old requests and ignores late responses after changing tasks', async () => {
  const api = baseApi()
  let resolveOld!: (value: TaskStatusResponse) => void
  vi.mocked(api.getTask).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    .mockResolvedValue(status('b', 'failed', 0))
  vi.mocked(api.getSteps).mockResolvedValue({ task_id: 'b', status: 'failed', steps: [] })
  const update = vi.fn()
  const poller = createTaskPoller(api, { onSnapshot: update, onError: vi.fn() }, 100)
  poller.start('a')
  const oldSignal = vi.mocked(api.getTask).mock.calls[0]?.[1]
  poller.start('b')
  await vi.advanceTimersByTimeAsync(0)
  resolveOld(status('a'))
  await vi.advanceTimersByTimeAsync(0)
  expect(oldSignal?.aborted).toBe(true)
  expect(update.mock.calls.every(call => call[0].task_id === 'b')).toBe(true)
  expect(update.mock.calls[0]?.[0].task_id).toBe('b')
  poller.stop()
})

it('reports temporary errors and recovers on the next non-overlapping request', async () => {
  const api = baseApi()
  vi.mocked(api.getTask).mockRejectedValueOnce(new Error('offline'))
  const error = vi.fn()
  const update = vi.fn()
  const poller = createTaskPoller(api, { onSnapshot: update, onError: error }, 100)
  poller.start('a')
  await vi.advanceTimersByTimeAsync(150)
  expect(error).toHaveBeenCalledTimes(1)
  expect(update).toHaveBeenCalled()
  poller.stop()
})

it('publishes available results while the steps request is pending, then recovers from a steps error', async () => {
  const api = baseApi()
  const finished = status('a', 'failed', 1)
  vi.mocked(api.getTask).mockResolvedValue(finished)
  let rejectSteps!: (reason: Error) => void
  vi.mocked(api.getSteps).mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectSteps = reject }))
    .mockResolvedValue({ task_id: 'a', status: 'failed', steps: [step(1)] })
  const update = vi.fn()
  const error = vi.fn()
  const poller = createTaskPoller(api, { onSnapshot: update, onError: error }, 100)
  poller.start('a')
  await vi.advanceTimersByTimeAsync(1000)
  expect(update).toHaveBeenCalledWith(finished, [], false)
  expect(api.getTask).toHaveBeenCalledTimes(1)
  rejectSteps(new Error('steps temporarily unavailable'))
  await vi.advanceTimersByTimeAsync(0)
  expect(error).toHaveBeenCalledWith(expect.objectContaining({ message: 'steps temporarily unavailable' }))
  await vi.advanceTimersByTimeAsync(1000)
  expect(update.mock.lastCall?.[1]).toEqual([step(1)])
  expect(api.getTask).toHaveBeenCalledTimes(2)
  poller.stop()
})
