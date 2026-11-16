import type { TaskMetrics, TokenUsageSummary } from './contracts'
type Data = Record<string, unknown>
function check(value: unknown): asserts value { if (!value) throw new Error('Invalid task metrics') }
function object(value: unknown): Data { check(value && typeof value === 'object' && !Array.isArray(value)); return value as Data }
const number = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const optionalNumber = (value: unknown) => value === null || number(value)
const optionalText = (value: unknown) => value === null || typeof value === 'string'
const strings = (value: unknown) => Array.isArray(value) && value.every(item => typeof item === 'string')
function readUsage(value: unknown): TokenUsageSummary {
  const data = object(value)
  check(optionalNumber(data.total_tokens) && number(data.reported_total_tokens)
    && number(data.reported_requests) && optionalNumber(data.total_requests))
  if (data.total_requests !== null) check(Number(data.reported_requests) <= Number(data.total_requests))
  if (data.total_tokens !== null) check(data.total_requests !== null && data.reported_requests === data.total_requests && data.total_tokens === data.reported_total_tokens)
  return data as unknown as TokenUsageSummary
}
export function readTaskMetrics(value: unknown, expectedId: string): TaskMetrics {
  const data = object(value)
  check(data.task_id === expectedId && ['pending', 'running', 'succeeded', 'failed', 'stopped'].includes(String(data.status)))
  check(data.task_url === null || typeof data.task_url === 'string' && (/^https?:\/\//i.test(data.task_url) || /^\/(?!\/)/.test(data.task_url)))
  for (const key of ['project_label', 'runtime_label', 'code_revision', 'comparison_group']) check(optionalText(data[key]))
  check(typeof data.repair_verified === 'boolean' && optionalNumber(data.elapsed_ms) && optionalNumber(data.model_requests))
  if ('final_tests_passed' in data) check(data.final_tests_passed === null || typeof data.final_tests_passed === 'boolean')
  const token_usage = readUsage(data.token_usage), retrieval = object(data.retrieval), experience = object(data.experience)
  check(data.model_requests === token_usage.total_requests)
  check(optionalText(retrieval.strategy) && optionalNumber(retrieval.injected_requests) && optionalNumber(retrieval.reference_count))
  check(Array.isArray(retrieval.events))
  for (const value of retrieval.events) {
    const event = object(value)
    check(typeof event.reason === 'string' && strings(event.error_types) && strings(event.experience_ids))
  }
  check(typeof experience.state === 'string' && typeof experience.notice === 'string' && strings(experience.experience_ids))
  for (const key of ['elapsed_ms', 'before_count', 'after_count', 'merged_count', 'conflict_count', 'resolved_count', 'pending_confirmation_count']) check(optionalNumber(experience[key]))
  check(optionalNumber(experience.model_requests))
  const usage = readUsage(experience.token_usage)
  check(experience.model_requests === usage.total_requests)
  return { ...data, token_usage, retrieval, experience: { ...experience, token_usage: usage } } as unknown as TaskMetrics
}
export function readTaskMetricsList(value: unknown): TaskMetrics[] {
  check(Array.isArray(value))
  return value.map(item => { const data = object(item); check(typeof data.task_id === 'string'); return readTaskMetrics(item, data.task_id) })
}
