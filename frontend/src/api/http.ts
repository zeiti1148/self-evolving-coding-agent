import type { AgentApi, TaskCreateRequest } from './contracts'
import { readAgentEvents, readCreatedTask, readExperience, readExperiences, readMergedExperience, readMergePreview, readProjects, readPromotionPreview, readProvenance, readSteps, readTask } from './validation'
import { readTestSource, readTestSources } from './validation'
import { readCodeChanges, readCodeDiff } from './codeDiffValidation'

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = 'ApiError' }
}

const fieldLabels: Record<string, string> = {
  task_description: '任务描述', source_project: '源项目', max_steps: '决策步数', execution_mode: '执行模式',
  language: '编程语言', scope: '使用范围', repository_id: '所属仓库', reuse_status: '复用状态',
  experience_ids: '所选经验', source_id: '来源经验', draft: '发布草稿', supersedes_id: '被替代版本',
  task_summary: '规则名称', root_cause: '根因', solution: '修复方案', applicability: '适用条件',
  error_type: '异常类型', issues: '相关问题', files: '文件', tags: '标签', required_capabilities: '目标所需能力',
  limit: '每页数量', offset: '起始位置', after_sequence: '起始序号', source_task_id: '来源任务', active_only: '仅显示有效经验',
  path: '测试文件',
}

function errorMessage(status: number, payload: unknown, path: string): string {
  if (status >= 500) return '服务暂时不可用，请稍后重试。'
  if (status === 404 && /\/code-(changes|diff)(?:\?|$)/.test(path)) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : null
    return typeof detail === 'string' && /[\u4e00-\u9fff]/.test(detail) ? detail : '代码对比暂不可用，请确认任务工作副本仍在，并重启已更新的后端。'
  }
  if (status === 404 && /\/test-sources?(?:\?|$)/.test(path)) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : null
    return typeof detail === 'string' && /[\u4e00-\u9fff]/.test(detail) ? detail
      : '测试源码暂不可用，请确认任务工作副本仍在，并重启已更新的后端。'
  }
  if (status === 404 && path.startsWith('/api/projects')) return '项目登记接口不可用，请启动支持项目目录的后端。'
  if (status === 404) return path.startsWith('/api/experiences')
    ? '经验接口不存在，请确认后端地址和接口是否已实现。'
    : '找不到这个任务。服务重启后，任务记录可能已失效。'
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = payload.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map(item => {
      const field = Array.isArray(item?.loc) ? item.loc.slice(1).map((part: string | number) =>
        typeof part === 'number' ? `第 ${part + 1} 项` : fieldLabels[part] ?? part).join(' / ') : '请求'
      const messages: Record<string, string> = {
        missing: '此项不能为空', string_too_short: `至少需要 ${item?.ctx?.min_length} 个字符`,
        string_too_long: `最多允许 ${item?.ctx?.max_length} 个字符`,
        less_than_equal: `不能大于 ${item?.ctx?.le}`, greater_than_equal: `不能小于 ${item?.ctx?.ge}`,
        int_parsing: '请输入整数', int_type: '请输入整数', bool_parsing: '请选择是或否',
        literal_error: '请选择有效选项', extra_forbidden: '不支持此参数', uuid_parsing: '编号格式不正确',
        too_short: `至少需要 ${item?.ctx?.min_length} 项`, too_long: `最多允许 ${item?.ctx?.max_length} 项`,
      }
      const message = typeof item?.msg === 'string' && /[\u4e00-\u9fff]/.test(item.msg)
        ? item.msg.replace(/^Value error, /, '') : messages[item?.type] ?? '内容不符合要求，请检查输入'
      return `${field}：${message}`
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
    listTestSources: (id, signal) => request(`/api/tasks/${encodeURIComponent(id)}/test-sources`, data => readTestSources(data, id), 'GET', undefined, signal),
    listCodeChanges: (id, signal) => request(`/api/tasks/${encodeURIComponent(id)}/code-changes`, data => readCodeChanges(data, id), 'GET', undefined, signal),
    getCodeDiff: (id, path, signal) => request(`/api/tasks/${encodeURIComponent(id)}/code-diff?${new URLSearchParams({ path })}`, data => readCodeDiff(data, id, path), 'GET', undefined, signal),
    getTestSource: (id, path, signal) => request(`/api/tasks/${encodeURIComponent(id)}/test-source?${new URLSearchParams({ path })}`, data => readTestSource(data, id, path), 'GET', undefined, signal),
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
