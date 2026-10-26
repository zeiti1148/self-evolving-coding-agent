import type { AgentApi, AgentResult, MockScenario, TaskStatusResponse } from './contracts'
import { ApiError } from './http'
import { javaFile, makeSteps, seedExperiences } from './fixtures'
import { restoreMockState, scenarios, type MockTask, type MockState } from './mockStorage'

const KEY = 'coding-agent-demo-v1'
export interface MockApi extends AgentApi { setScenario(scenario: MockScenario): void; readonly recoveryNotice: string | null }
interface Options { now?: () => number; stepMs?: number; projectId?: string; storage?: Storage | null }

export function createMockApi(options: Options = {}): MockApi {
  const now = options.now ?? Date.now
  const stepMs = options.stepMs ?? 1100
  const projectId = options.projectId ?? 'java-null-greeting'
  let storage: Storage | null = null
  try { storage = options.storage === undefined ? sessionStorage : options.storage } catch { /* Private mode: in-memory demo. */ }
  let state: MockState = { version: 1, tasks: [], experiences: seedExperiences(now()) }
  let recoveryNotice: string | null = null
  try {
    const raw = storage?.getItem(KEY)
    if (raw != null) ({ state, recoveryNotice } = restoreMockState(raw, state))
  } catch { /* Storage is unavailable; continue in memory. */ }
  let scenario: MockScenario = 'success'
  const persist = () => { try { storage?.setItem(KEY, JSON.stringify(state)) } catch { /* Demo still works in memory. */ } }
  const checkAbort = (signal?: AbortSignal) => { if (signal?.aborted) throw new DOMException('Aborted', 'AbortError') }
  function findTask(id: string): MockTask {
    const found = state.tasks.find(task => task.id === id)
    if (!found) throw new ApiError(404, '找不到这个任务。演示记录仅保留在当前浏览器标签页中。')
    return found
  }
  function snapshot(task: MockTask): TaskStatusResponse {
    const elapsed = Math.max(0, now() - task.createdAt)
    const terminal = elapsed >= (task.steps.length + 2) * task.stepMs
    const count = Math.min(task.steps.length, Math.max(0, Math.floor(elapsed / task.stepMs) - 1))
    const status = !terminal ? (elapsed < task.stepMs ? 'pending' : 'running')
      : task.scenario === 'stopped' ? 'stopped'
        : ['tests-failed', 'schedule-error'].includes(task.scenario) ? 'failed' : 'succeeded'
    let result: AgentResult | null = null
    if (terminal && task.scenario !== 'schedule-error') {
      const success = status === 'succeeded'
      const saved = success ? task.scenario !== 'save-failure' : null
      if (saved && !state.experiences.some(item => item.id === task.experienceId)) {
        state.experiences.unshift({ id: task.experienceId, task_summary: task.request.task_description.slice(0, 1000),
          error_type: 'NullPointerException', root_cause: 'name 为 null 时仍调用 trim()。',
          solution: '在字符串处理之前检查 null，返回默认访客问候。', verification: '演示结果：Maven 的两个用例均通过。',
          tags: ['java', '空值检查'], source_task_id: task.id,
          created_at: new Date(task.createdAt + (task.steps.length + 2) * task.stepMs).toISOString() })
        persist()
      }
      result = {
        task_id: task.id, status: status as AgentResult['status'],
        summary: success ? '已为 null 输入添加默认问候，普通字符串行为保持不变。'
          : status === 'stopped' ? '已达到演示决策预算，最终验证未完成。' : '修改后的测试仍未通过，需要继续检查修复方案。',
        steps: task.steps.map(step => step.call), tool_results: task.steps.map(step => step.result),
        repair_succeeded: success, tests_passed: status === 'stopped' ? null : success,
        changed_files: task.steps.some(step => step.call.name === 'write_file') ? [javaFile] : [],
        experience_saved: saved, experience_id: saved ? task.experienceId : null,
        experience_save_error: saved === false ? '模拟经验存储暂不可用，已验证的修复结果不受影响。' : null,
        error_message: success ? null : status === 'stopped' ? '达到模型决策步数上限。' : '最终测试未通过。',
      }
    }
    return { task_id: task.id, status, steps_completed: count,
      created_at: new Date(task.createdAt).toISOString(),
      started_at: elapsed < task.stepMs ? null : new Date(task.createdAt + task.stepMs).toISOString(),
      finished_at: terminal ? new Date(task.createdAt + (task.steps.length + 2) * task.stepMs).toISOString() : null,
      result, error_message: terminal && task.scenario === 'schedule-error' ? '演示工作副本创建失败，Agent 尚未开始执行。' : null }
  }
  return {
    recoveryNotice,
    setScenario(value) { if (scenarios.includes(value)) scenario = value },
    async createTask(request, signal) {
      checkAbort(signal)
      if (!request.task_description.trim() || request.task_description.trim().length > 50000 || !Number.isInteger(request.max_steps) || request.max_steps < 1 || request.max_steps > 100) throw new ApiError(422, '请填写任务描述，并将最大决策轮数设为 1 至 100。')
      if (request.source_project !== projectId) throw new ApiError(400, '项目未在服务端登记。')
      const selected = request.max_steps < 4 && scenario !== 'schedule-error' ? 'stopped' : scenario
      const createdAt = now()
      const task: MockTask = { id: crypto.randomUUID(), request: { ...request }, createdAt, stepMs, scenario: selected,
        steps: makeSteps(createdAt, stepMs, selected), experienceId: crypto.randomUUID() }
      state.tasks.push(task); persist()
      return { task_id: task.id, status: 'pending', created_at: new Date(createdAt).toISOString() }
    },
    async getTask(id, signal) { checkAbort(signal); return structuredClone(snapshot(findTask(id))) },
    async getSteps(id, signal) {
      checkAbort(signal)
      const task = findTask(id)
      const current = snapshot(task)
      return structuredClone({ task_id: id, status: current.status, steps: task.steps.slice(0, current.steps_completed) })
    },
    async listExperiences(limit = 20, offset = 0, signal, filters = {}) {
      checkAbort(signal)
      if (!Number.isInteger(limit) || limit < 1 || !Number.isInteger(offset) || offset < 0) throw new ApiError(422, '分页参数不合法。')
      state.tasks.forEach(snapshot)
      return structuredClone(state.experiences.filter(item => (!filters.language || (item.language ?? 'java') === filters.language)
        && (!filters.scope || (item.scope ?? 'global') === filters.scope)
        && (!filters.repository_id || item.repository_id === filters.repository_id))
        .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id)).slice(offset, offset + limit))
    },
  }
}
