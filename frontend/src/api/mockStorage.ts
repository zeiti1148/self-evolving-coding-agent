import type { Experience, MockScenario, TaskCreateRequest, TaskStep } from './contracts'
import { readCreatedTask, readExperiences, readSteps } from './validation'

export const scenarios: MockScenario[] = ['success', 'tests-failed', 'stopped', 'save-failure', 'schedule-error']
export interface MockTask { id: string; request: TaskCreateRequest; createdAt: number; stepMs: number; scenario: MockScenario; steps: TaskStep[]; experienceId: string }
export interface MockState { version: 1; tasks: MockTask[]; experiences: Experience[] }
function check(valid: unknown): asserts valid { if (!valid) throw new Error('Invalid demo cache') }
function readMockTask(value: unknown): MockTask {
  check(value !== null && typeof value === 'object')
  const task = value as MockTask
  check(typeof task.createdAt === 'number' && Number.isFinite(task.createdAt) && typeof task.stepMs === 'number' && Number.isFinite(task.stepMs) && task.stepMs > 0)
  check(scenarios.includes(task.scenario) && Array.isArray(task.steps))
  const created_at = new Date(task.createdAt).toISOString()
  readCreatedTask({ task_id: task.id, created_at })
  readCreatedTask({ task_id: task.experienceId, created_at })
  check(Number.isFinite(new Date(task.createdAt + (task.steps.length + 2) * task.stepMs).getTime()))
  const request = task.request
  check(request && typeof request.task_description === 'string' && request.task_description.trim().length > 0 && request.task_description.trim().length <= 50000)
  check(typeof request.source_project === 'string' && request.source_project.trim().length > 0 && Number.isInteger(request.max_steps) && request.max_steps >= 1 && request.max_steps <= 100)
  const steps = readSteps({ task_id: task.id, status: 'pending', steps: task.steps }, task.id).steps
  // The demo infers its outcome from the scenario, so its persisted evidence must match it.
  const names = task.scenario === 'schedule-error' ? [] : task.scenario === 'stopped'
    ? ['list_files', 'read_file', 'run_tests', 'run_tests'] : ['list_files', 'read_file', 'run_tests', 'write_file', 'run_tests']
  check(steps.length === names.length && new Set(steps.map(step => step.call.id)).size === steps.length)
  check(steps.every((step, index) => step.sequence === index + 1 && step.call.name === names[index]
    && step.result.success === !(index === 2 || index === 3 && task.scenario === 'stopped' || index === 4 && task.scenario === 'tests-failed')))
  return { ...task, steps }
}

export function restoreMockState(raw: string, fallback: MockState): { state: MockState; recoveryNotice: string | null } {
  try {
    const data = JSON.parse(raw)
    check(data?.version === 1 && Array.isArray(data.tasks) && Array.isArray(data.experiences))
    let damaged = false
    function recover<T extends { id: string }>(values: unknown[], read: (value: unknown) => T): T[] {
      const ids = new Set<string>()
      return values.flatMap(value => {
        try {
          const record = read(value)
          const id = record.id.toLowerCase()
          check(!ids.has(id)); ids.add(id)
          return [record]
        } catch { damaged = true; return [] }
      })
    }
    const tasks = recover(data.tasks, readMockTask)
    const experiences = recover(data.experiences, value => readExperiences([value])[0]!)
    return { state: { version: 1, tasks, experiences }, recoveryNotice: damaged ? '部分演示缓存无法读取，已跳过异常记录，其余记录保留。' : null }
  } catch {
    return { state: fallback, recoveryNotice: '演示缓存格式异常，已重建演示数据。' }
  }
}
