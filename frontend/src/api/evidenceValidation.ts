import type { TaskEvidence, TestRunEvidence } from './contracts'

type Data = Record<string, unknown>
function check(value: unknown): asserts value { if (!value) throw new Error('Invalid task evidence') }
function object(value: unknown): Data { check(value && typeof value === 'object' && !Array.isArray(value)); return value as Data }
const text = (value: unknown, max = 2000) => typeof value === 'string' && value.trim().length > 0 && value.length <= max
const uuid = (value: unknown) => typeof value === 'string' && /^[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}$/i.test(value)
const integer = (value: unknown, min = 0) => Number.isSafeInteger(value) && Number(value) >= min && Number(value) <= 1e7
const nullable = (value: unknown, valid: (item: unknown) => boolean) => value === null || valid(value)
function relativePath(value: unknown): boolean {
  return typeof value === 'string' && value.length > 0 && value.length <= 1000 && !/[\\:\u0000-\u001f]/.test(value)
    && value.split('/').every(part => part !== '' && part !== '.' && part !== '..')
}
function run(value: unknown): TestRunEvidence | null {
  if (value === null) return null
  const data = object(value)
  check(uuid(data.tool_call_id) && integer(data.sequence, 1) && ['maven', 'pytest'].includes(String(data.runner)))
  check(integer(data.tests, 1) && ['passed', 'failures', 'errors', 'skipped'].every(key => integer(data[key])))
  check(Number(data.passed) + Number(data.failures) + Number(data.errors) + Number(data.skipped) === data.tests)
  return data as unknown as TestRunEvidence
}
export function readTaskEvidence(value: unknown, taskId: string): TaskEvidence {
  const data = object(value), tests = object(data.tests)
  check(uuid(data.task_id) && data.task_id === taskId && ['pending', 'running', 'succeeded', 'failed', 'stopped'].includes(String(data.status)))
  check(nullable(data.source_project, text) && nullable(data.project_label, text))
  const before = run(tests.before), after = run(tests.after)
  check(typeof tests.final_verified === 'boolean' && typeof tests.comparable === 'boolean'
    && nullable(tests.original_tests_unchanged, value => typeof value === 'boolean') && nullable(tests.unavailable_reason, text))
  if (tests.comparable) check(before && after && before.tests === after.tests && before.runner === after.runner
    && before.sequence < after.sequence && before.tool_call_id !== after.tool_call_id && tests.original_tests_unchanged === true)
  if (tests.final_verified) check(after && after.failures === 0 && after.errors === 0 && after.skipped === 0 && after.passed === after.tests)
  check(Array.isArray(data.failure_cases) && data.failure_cases.length <= 100)
  const failure_cases = data.failure_cases.map(value => {
    const item = object(value)
    check(text(item.test, 300) && text(item.error_type, 200) && text(item.message))
    check(nullable(item.source_file, relativePath) && nullable(item.source_line, value => integer(value, 1)))
    check(item.source_line === null || item.source_file !== null)
    return item
  })
  check(Array.isArray(data.references) && data.references.length <= 100)
  const references = data.references.map(value => {
    const item = object(value)
    check(uuid(item.experience_id) && text(item.title) && nullable(item.source_task_id, uuid)
      && nullable(item.scope, value => value === 'global' || value === 'project')
      && typeof item.content_sha256 === 'string' && /^[a-f0-9]{64}$/.test(item.content_sha256)
      && integer(item.request_count, 1) && typeof item.detail_available === 'boolean')
    check(Array.isArray(item.roles) && item.roles.length > 0 && item.roles.length <= 4)
    const roles = item.roles.map(value => { const role = object(value); check(['diagnosis', 'repair', 'review', 'single'].includes(String(role.role)) && integer(role.request_count, 1)); return role })
    check(new Set(roles.map(role => role.role)).size === roles.length && roles.reduce((sum, role) => sum + Number(role.request_count), 0) === item.request_count)
    return { ...item, roles } as Data
  })
  check(new Set(references.map(item => `${item.experience_id}:${item.content_sha256}`)).size === references.length)
  check(['recorded', 'unavailable'].includes(String(data.reference_recording)) && text(data.reference_notice))
  check(data.reference_recording === 'recorded' || !references.length)
  return { ...data, tests: { ...tests, before, after }, failure_cases, references } as unknown as TaskEvidence
}
