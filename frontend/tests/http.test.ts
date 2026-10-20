import { afterEach, describe, expect, it, vi } from 'vitest'
import { createHttpApi } from '../src/api/http'

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
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('[]')))
    const api = createHttpApi('', fetcher)
    await api.getTask('task-id')
    await api.getSteps('task-id')
    await api.listExperiences(6, 5)
    expect(fetcher.mock.calls.map(call => call[0])).toEqual([
      '/api/tasks/task-id', '/api/tasks/task-id/steps', '/api/experiences?limit=6&offset=5',
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
})
