import type { AgentApi, TaskCreateRequest } from './contracts'

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = 'ApiError' }
}

function errorMessage(status: number, payload: unknown): string {
  if (status >= 500) return '服务暂时不可用，请稍后重试。'
  if (status === 404) return '找不到这个任务。服务重启后，任务记录可能已失效。'
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = payload.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map(item => {
      const field = Array.isArray(item?.loc) ? item.loc.slice(1).join('.') : '请求'
      return `${field}：${String(item?.msg ?? '参数不合法')}`
    }).join('；')
  }
  return `请求未成功（${status}），请检查输入后重试。`
}

export function createHttpApi(baseUrl: string, fetcher: typeof fetch = globalThis.fetch): AgentApi {
  const base = baseUrl.replace(/\/$/, '')
  async function request<T>(path: string, method = 'GET', body?: TaskCreateRequest, signal?: AbortSignal): Promise<T> {
    const controller = new AbortController()
    let timedOut = false
    const abort = () => controller.abort()
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    signal?.addEventListener('abort', abort, { once: true })
    const timeout = setTimeout(() => { timedOut = true; controller.abort() }, 15000)
    try {
      const response = await fetcher(base + path, {
        method, signal: controller.signal, headers: { 'Content-Type': 'application/json' },
        ...(body ? { body: JSON.stringify(body) } : {}),
      })
      let data: unknown
      try { data = await response.json() } catch {
        if (response.ok) throw new ApiError(502, '服务返回了无法读取的数据，请稍后重试。')
      }
      if (!response.ok) throw new ApiError(response.status, errorMessage(response.status, data))
      return data as T
    } catch (error) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      if (timedOut) throw new ApiError(0, '请求超时，请检查服务连接后重试。')
      if (error instanceof ApiError) throw error
      throw new ApiError(0, '无法连接服务，请检查后端地址和网络。')
    } finally {
      clearTimeout(timeout)
      signal?.removeEventListener('abort', abort)
    }
  }
  return {
    createTask: (body, signal) => request('/api/tasks', 'POST', body, signal),
    getTask: (id, signal) => request(`/api/tasks/${encodeURIComponent(id)}`, 'GET', undefined, signal),
    getSteps: (id, signal) => request(`/api/tasks/${encodeURIComponent(id)}/steps`, 'GET', undefined, signal),
    listExperiences: (limit = 20, offset = 0, signal) => request(`/api/experiences?limit=${limit}&offset=${offset}`, 'GET', undefined, signal),
  }
}
