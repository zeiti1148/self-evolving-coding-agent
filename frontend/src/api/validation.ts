import type { AgentResult, Experience, TaskCreateResponse, TaskStatusResponse, TaskStep, TaskStepsResponse, ToolCall, ToolResult } from './contracts'

// Guard the fields consumed by the UI. Public Pydantic models remain authoritative.
type Data = Record<string, unknown>
function check(valid: unknown): asserts valid {
  if (!valid) throw new Error('Invalid API response')
}
function object(value: unknown): Data {
  check(value !== null && typeof value === 'object' && !Array.isArray(value))
  return value as Data
}
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0
const uuid = (value: unknown) => typeof value === 'string' && /^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i.test(value)
const date = (value: unknown) => typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) && Number.isFinite(Date.parse(value))
const nullable = (value: unknown, valid: (value: unknown) => boolean) => value === null || valid(value)
const boolean = (value: unknown) => typeof value === 'boolean'
const status = (value: unknown) => typeof value === 'string' && ['pending', 'running', 'succeeded', 'failed', 'stopped'].includes(value)
const terminal = (value: unknown) => typeof value === 'string' && ['succeeded', 'failed', 'stopped'].includes(value)
function list<T>(value: unknown, read: (item: unknown) => T): T[] {
  check(Array.isArray(value))
  return value.map(read)
}
function readCall(value: unknown): ToolCall {
  const data: Data = { arguments: {}, ...object(value) }
  check(uuid(data.id) && text(data.name))
  object(data.arguments)
  return data as unknown as ToolCall
}
function readToolResult(value: unknown): ToolResult {
  const data: Data = { output: '', error_code: null, error_message: null, metadata: {}, ...object(value) }
  check(uuid(data.call_id) && text(data.tool_name) && boolean(data.success) && typeof data.output === 'string')
  object(data.metadata)
  check(nullable(data.error_code, text) && nullable(data.error_message, text))
  check(data.success ? data.error_code === null && data.error_message === null : text(data.error_code) && text(data.error_message))
  return data as unknown as ToolResult
}
function readStep(value: unknown): TaskStep {
  const data = object(value)
  check(Number.isInteger(data.sequence) && Number(data.sequence) >= 1 && date(data.completed_at))
  const call = readCall(data.call)
  const result = readToolResult(data.result)
  check(call.id === result.call_id && call.name === result.tool_name)
  return { ...data, call, result } as unknown as TaskStep
}
function readResult(value: unknown): AgentResult {
  const data: Data = { steps: [], tool_results: [], changed_files: [], tests_passed: null, experience_saved: null,
    experience_id: null, experience_save_error: null, error_message: null, ...object(value) }
  check(uuid(data.task_id) && terminal(data.status) && text(data.summary) && boolean(data.repair_succeeded))
  check(nullable(data.tests_passed, boolean) && nullable(data.experience_saved, boolean))
  check(nullable(data.experience_id, uuid) && nullable(data.experience_save_error, text) && nullable(data.error_message, text))
  const steps = list(data.steps, readCall)
  const tool_results = list(data.tool_results, readToolResult)
  list(data.changed_files, item => { check(text(item)); return item })
  check(new Set(steps.map(call => call.id)).size === steps.length)
  check(new Set(tool_results.map(result => result.call_id)).size === tool_results.length)
  check(steps.length === tool_results.length && steps.every(call => tool_results.some(result => result.call_id === call.id && result.tool_name === call.name)))
  check(!data.repair_succeeded || data.tests_passed === true)
  if (data.repair_succeeded) {
    const finalTest = steps.map(call => call.name).lastIndexOf('run_tests')
    check(finalTest >= 0 && tool_results.some(result => result.call_id === steps[finalTest]!.id && result.success))
    check(!steps.slice(finalTest + 1).some(call => call.name === 'write_file' || call.name === 'run_command'))
  }
  check(data.status !== 'succeeded' || data.repair_succeeded === true && data.tests_passed === true && data.experience_saved !== null)
  check(data.status === 'succeeded' || data.experience_saved === null)
  check(data.experience_saved === true ? uuid(data.experience_id) && data.experience_save_error === null
    : data.experience_saved === false ? data.experience_id === null && text(data.experience_save_error)
      : data.experience_id === null && data.experience_save_error === null)
  return { ...data, steps, tool_results } as unknown as AgentResult
}
export function readCreatedTask(value: unknown): TaskCreateResponse {
  const data: Data = { status: 'pending', ...object(value) }
  check(uuid(data.task_id) && data.status === 'pending' && date(data.created_at))
  return data as unknown as TaskCreateResponse
}
export function readTask(value: unknown, expectedId: string): TaskStatusResponse {
  const data: Data = { steps_completed: 0, started_at: null, finished_at: null, result: null, error_message: null, ...object(value) }
  check(uuid(data.task_id) && String(data.task_id).toLowerCase() === expectedId.toLowerCase() && status(data.status))
  check(Number.isInteger(data.steps_completed) && Number(data.steps_completed) >= 0 && date(data.created_at))
  check(nullable(data.started_at, date) && nullable(data.finished_at, date) && nullable(data.error_message, text))
  const result = data.result === null ? null : readResult(data.result)
  check(!result || result.task_id === data.task_id && result.status === data.status)
  check(data.status !== 'succeeded' || result !== null)
  check(data.status !== 'pending' || data.started_at === null && data.finished_at === null && result === null)
  check(data.status !== 'running' || data.started_at !== null && data.finished_at === null && result === null)
  check(!terminal(data.status) || data.finished_at !== null)
  return { ...data, result } as unknown as TaskStatusResponse
}
export function readSteps(value: unknown, expectedId: string): TaskStepsResponse {
  const data: Data = { steps: [], ...object(value) }
  check(uuid(data.task_id) && String(data.task_id).toLowerCase() === expectedId.toLowerCase() && status(data.status))
  const steps = list(data.steps, readStep)
  check(steps.every((step, index) => index === 0 || step.sequence > steps[index - 1]!.sequence))
  return { ...data, steps } as unknown as TaskStepsResponse
}
export function readExperiences(value: unknown): Experience[] {
  return list(value, item => {
    const data: Data = { error_type: null, tags: [], source_task_id: null, ...object(item) }
    check(uuid(data.id) && date(data.created_at))
    check(['task_summary', 'root_cause', 'solution', 'verification'].every(key => text(data[key])))
    check(nullable(data.error_type, text) && nullable(data.source_task_id, uuid))
    list(data.tags, tag => { check(text(tag)); return tag })
    return data as unknown as Experience
  })
}
