import type { AgentApi, TaskCreateRequest } from './contracts'
import { readAgentEvents, readCreatedTask, readExperience, readExperiences, readMergedExperience, readMergePreview, readProjects, readPromotionPreview, readProvenance, readSteps, readTask } from './validation'

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = 'ApiError' }
}

function errorMessage(status: number, payload: unknown, path: string): string {
  if (status >= 500) return '服务暂时不可用，请稍后重试。'
  if (status === 404 && path.startsWith('/api/projects')) return '项目登记接口不可用，请启动支持项目目录的后端。'
  if (status === 404) return path.startsWith('/api/experiences')
    ? '经验接口不存在，请确认后端地址和接口是否已实现。'
    : '找不到这个任务。服务重启后，任务记录可能已失效。'
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
  async function request<T>(path: string, read: (data: unknown) => T, method = 'GET', body?: object, signal?: AbortSignal): Promise<T> {
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
      if (!response.ok) throw new ApiError(response.status, errorMessage(response.status, data, path))
      try { return read(data) } catch { throw new ApiError(502, '服务返回的数据与接口约定不一致，请联系后端检查。') }
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
    createTask: (body, signal) => request('/api/tasks', readCreatedTask, 'POST', body, signal),
    getTask: (id, signal) => request(`/api/tasks/${encodeURIComponent(id)}`, data => readTask(data, id), 'GET', undefined, signal),
    getSteps: (id, signal) => request(`/api/tasks/${encodeURIComponent(id)}/steps`, data => readSteps(data, id), 'GET', undefined, signal),
    listExperiences: (limit = 20, offset = 0, signal, filters = {}) => {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
      for (const key of ['language', 'repository_id', 'scope', 'reuse_status', 'source_task_id', 'active_only'] as const) if (filters[key] !== undefined) params.set(key, String(filters[key]))
      return request(`/api/experiences?${params}`, readExperiences, 'GET', undefined, signal)
    },
    listProjects: signal => request('/api/projects', readProjects, 'GET', undefined, signal),
    getExperience: (id, signal) => request(`/api/experiences/${encodeURIComponent(id)}`, readExperience, 'GET', undefined, signal),
    getProvenance: (id, signal) => request(`/api/experiences/${encodeURIComponent(id)}/provenance`, data => readProvenance(data, id), 'GET', undefined, signal),
    getAgentEvents: (id, after = 0, signal) => request(`/api/tasks/${encodeURIComponent(id)}/agents?after_sequence=${after}&limit=100`, data => readAgentEvents(data, id, after), 'GET', undefined, signal),
    previewPromotion: (id, signal) => request('/api/experiences/promotion-preview', data => readPromotionPreview(data, id), 'POST', { source_id: id }, signal),
    publishGlobal: (id, draft, previous, signal) => request('/api/experiences/publish-global', readExperience, 'POST', { source_id: id, draft, supersedes_id: previous ?? null }, signal),
    previewMerge: (ids, signal) => {
      const sources = [...ids]
      return request('/api/experiences/merge-preview', data => readMergePreview(data, sources), 'POST', { experience_ids: sources }, signal)
    },
    mergeExperiences: (ids, signal) => {
      const sources = [...ids]
      return request('/api/experiences/merge', data => readMergedExperience(data, sources), 'POST', { experience_ids: sources }, signal)
    },
  }
}
