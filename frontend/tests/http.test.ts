import { afterEach, describe, expect, it, vi } from 'vitest'
import { createHttpApi } from '../src/api/http'
import { createMockApi } from '../src/api/mock'

const taskId = '11111111-1111-4111-8111-111111111111'
const pending = { task_id: taskId, status: 'pending', created_at: '2026-10-20T00:00:00Z' }

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('HTTP contract', () => {
  it('posts exactly the shared task fields and accepts a 202 response', async () => {
    const response = { task_id: '11111111-1111-4111-8111-111111111111', status: 'pending', created_at: '2026-10-20T00:00:00Z' }
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(response), { status: 202 }))
    const api = createHttpApi('http://localhost:8000/', fetcher)
    const request = { task_description: '修复空指针', source_project: 'java-null-greeting', max_steps: 20 }
    expect(await api.createTask(request)).toEqual(response)
    const [url, options] = fetcher.mock.calls[0]!
    expect(url).toBe('http://localhost:8000/api/tasks')
    expect(options.method).toBe('POST')
    expect(JSON.parse(options.body)).toEqual(request)
  })

  it('uses only the documented read endpoints and pagination parameters', async () => {
    const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(new Response(JSON.stringify(
      url.includes('/experiences') ? [] : url.endsWith('/steps') ? { task_id: taskId, status: 'pending', steps: [] } : pending,
    ))))
    const api = createHttpApi('', fetcher)
    await api.getTask(taskId)
    await api.getSteps(taskId)
    await api.listExperiences(6, 5)
    expect(fetcher.mock.calls.map(call => call[0])).toEqual([
      `/api/tasks/${taskId}`, `/api/tasks/${taskId}/steps`, '/api/experiences?limit=6&offset=5',
    ])
  })

  it('formats FastAPI 422 details and preserves the HTTP status', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [
      { loc: ['body', 'max_steps'], msg: 'Input should be less than or equal to 100' },
    ] }), { status: 422 }))
    await expect(createHttpApi('', fetcher).getTask('x')).rejects.toMatchObject({ status: 422, message: expect.stringContaining('max_steps') })
  })

  it('does not display raw server error pages', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('<html>private traceback</html>', { status: 500 }))
    await expect(createHttpApi('', fetcher).getTask('x')).rejects.toMatchObject({ status: 500, message: '服务暂时不可用，请稍后重试。' })
  })

  it('cancels a pending request when the caller aborts', async () => {
    const fetcher = vi.fn().mockImplementation((_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    const controller = new AbortController()
    const pending = createHttpApi('', fetcher).getTask('x', controller.signal)
    controller.abort()
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('times out a stalled request so polling can retry instead of hanging', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn().mockImplementation((_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    const pending = expect(createHttpApi('', fetcher).getTask('x')).rejects.toMatchObject({
      status: 0, message: expect.stringContaining('请求超时'),
    })
    await vi.advanceTimersByTimeAsync(15000)
    await pending
    expect(fetcher.mock.calls[0]?.[1].signal.aborted).toBe(true)
  })

  it.each([null, {}, { items: [] }, [{ id: taskId }]])('rejects malformed experience data instead of displaying an empty library', async data => {
    const api = createHttpApi('', vi.fn().mockResolvedValue(new Response(JSON.stringify(data))))
    await expect(api.listExperiences()).rejects.toMatchObject({ status: 502, message: expect.stringContaining('接口') })
  })

  it('rejects a successful create response without a task identity', async () => {
    const api = createHttpApi('', vi.fn().mockResolvedValue(new Response('{}', { status: 202 })))
    await expect(api.createTask({ task_description: 'fix', source_project: 'java', max_steps: 20 })).rejects.toMatchObject({ status: 502 })
  })

  it.each([{ ...pending, status: 'unknown' }, { ...pending, status: ['pending'] }, { ...pending, task_id: '22222222-2222-4222-8222-222222222222' }])('rejects an incompatible task response', async data => {
    const api = createHttpApi('', vi.fn().mockResolvedValue(new Response(JSON.stringify(data))))
    await expect(api.getTask(taskId)).rejects.toMatchObject({ status: 502 })
  })

  it('preserves tool output and rejects a mismatched call/result pair', async () => {
    let now = 100000
    const demo = createMockApi({ now: () => now, stepMs: 1, storage: null })
    const task = await demo.createTask({ task_description: 'fix', source_project: 'java-null-greeting', max_steps: 20 })
    now += 100
    const steps = await demo.getSteps(task.task_id)
    steps.steps[0]!.result.output = '  <script>plain text</script>\n\n'
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(steps))))
    const api = createHttpApi('', fetcher)
    expect((await api.getSteps(task.task_id)).steps[0]!.result.output).toBe('  <script>plain text</script>\n\n')
    steps.steps[0]!.result.call_id = taskId
    await expect(api.getSteps(task.task_id)).rejects.toMatchObject({ status: 502 })
  })

  it('explains a missing experience endpoint without blaming a lost task', async () => {
    const api = createHttpApi('', vi.fn().mockResolvedValue(new Response('{}', { status: 404 })))
    await expect(api.listExperiences()).rejects.toMatchObject({ status: 404, message: expect.stringContaining('经验接口') })
  })

  it.each(['no-test', 'failed-test', 'write-after-test'])('rejects claimed repair success with invalid final test evidence: %s', async mutation => {
    let now = 100000
    const demo = createMockApi({ now: () => now, stepMs: 1, storage: null })
    const created = await demo.createTask({ task_description: 'fix', source_project: 'java-null-greeting', max_steps: 20 })
    now += 100
    const task = await demo.getTask(created.task_id)
    const result = task.result!
    if (mutation === 'no-test') { result.steps = []; result.tool_results = [] }
    else if (mutation === 'failed-test') {
      Object.assign(result.tool_results.at(-1)!, { success: false, error_code: 'TEST_FAILED', error_message: 'failed' })
    } else {
      result.steps.push({ id: taskId, name: 'write_file', arguments: { path: 'src/Test.java', content: 'changed again' } })
      result.tool_results.push({ call_id: taskId, tool_name: 'write_file', success: true, output: '', error_code: null, error_message: null, metadata: {} })
    }
    const api = createHttpApi('', vi.fn().mockResolvedValue(new Response(JSON.stringify(task))))
    await expect(api.getTask(created.task_id)).rejects.toMatchObject({ status: 502 })
  })
})
