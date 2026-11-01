import type { AgentEvent, AgentResult, Experience, ExperienceKnowledge, MergePreview, PromotionDraft, PromotionPreview, ProvenanceGraph, RegisteredProject, TaskCreateResponse, TaskStatusResponse, TaskStep, TaskStepsResponse, ToolCall, ToolResult } from './contracts'
import type { TestSourceFile, TestSourceIndex, TestSourceInfo } from './contracts'

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
function sameIds(actual: readonly string[], expected: readonly string[]): boolean {
  const normalized = actual.map(id => id.toLowerCase())
  const wanted = new Set(expected.map(id => id.toLowerCase()))
  return normalized.length === expected.length && new Set(normalized).size === normalized.length
    && wanted.size === expected.length && normalized.every(id => wanted.has(id))
}
function readSourceInfo(value: unknown): TestSourceInfo {
  const data = object(value)
  check(text(data.path) && data.path.length <= 1000 && !/[\\:\u0000-\u001f]/.test(data.path)
    && data.path.split('/').every(part => part !== '' && part !== '.' && part !== '..'))
  check(data.language === 'java' || data.language === 'python')
  check(typeof data.sha256 === 'string' && /^[a-f0-9]{64}$/.test(data.sha256))
  return data as unknown as TestSourceInfo
}
export function readTestSources(value: unknown, taskId: string): TestSourceIndex {
  const data = object(value)
  check(uuid(data.task_id) && data.task_id === taskId)
  const files = list(data.files, readSourceInfo)
  check(files.length <= 1000 && new Set(files.map(file => file.path)).size === files.length)
  return { task_id: taskId, files }
}
export function readTestSource(value: unknown, taskId: string, path: string): TestSourceFile {
  const data = object(value)
  readSourceInfo(data)
  check(uuid(data.task_id) && data.task_id === taskId && data.path === path)
  check(typeof data.content === 'string' && data.content.length <= 200000 && !data.content.includes('\u0000'))
  check(Number.isInteger(data.size_bytes) && Number(data.size_bytes) >= 0 && Number(data.size_bytes) <= 200000
    && new TextEncoder().encode(data.content).length === data.size_bytes)
  return data as unknown as TestSourceFile
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
  const raw = object(value)
  const data: Data = { steps: [], tool_results: [], changed_files: [], tests_passed: null, experience_saved: null,
    experience_id: null, experience_save_error: null, error_message: null, ...raw }
  const experience_ids = list('experience_ids' in raw ? raw.experience_ids : data.experience_id ? [data.experience_id] : [], item => { check(uuid(item)); return item as string })
  if (!('experience_id' in raw)) data.experience_id = experience_ids[0] ?? null
  check(experience_ids.length <= 5 && new Set(experience_ids.map(id => id.toLowerCase())).size === experience_ids.length)
  check(data.experience_id === (experience_ids[0] ?? null))
  check(data.experience_saved === true || experience_ids.length === 0)
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
  return { ...data, steps, tool_results, experience_ids } as unknown as AgentResult
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
function readKnowledge(value: unknown): ExperienceKnowledge {
  const data: Data = { error_type: null, tags: [], source_task_id: null, schema_version: 1,
    language: 'java', scope: 'global', repository_id: null, source_revision: null,
    applicability: [], issues: [], evidence: [], merged_from: [], batch_id: null, derived_from: [], supersedes_id: null,
    required_capabilities: [], ...object(value) }
  data.reuse_status ??= data.scope === 'project' ? 'active' : data.schema_version === 3 ? 'candidate' : 'legacy_unreviewed'
  check(['task_summary', 'root_cause', 'solution', 'verification'].every(key => text(data[key])))
  check(nullable(data.error_type, text) && nullable(data.source_task_id, uuid))
  check(data.schema_version === 1 || data.schema_version === 2 || data.schema_version === 3)
  check(['active', 'candidate', 'legacy_unreviewed'].includes(String(data.reuse_status)))
  check(nullable(data.batch_id, uuid) && nullable(data.supersedes_id, uuid))
  list(data.derived_from, item => { check(uuid(item)); return item })
  list(data.required_capabilities, item => { check(text(item)); return item })
  check(data.language === 'java' || data.language === 'python')
  check(data.scope === 'global' || data.scope === 'project')
  check(nullable(data.repository_id, text) && nullable(data.source_revision, text))
  check(data.scope === 'project' ? text(data.repository_id) : data.repository_id === null)
  for (const field of ['tags', 'applicability']) list(data[field], item => { check(text(item)); return item })
  const mergedFrom = list(data.merged_from, id => { check(uuid(id)); return id as string })
  check(mergedFrom.length <= 10 && sameIds(mergedFrom, mergedFrom))
  list(data.issues, value => {
    const issue = object(value)
    check(['error_type', 'root_cause', 'solution'].every(key => text(issue[key])))
    list(issue.files, file => { check(text(file)); return file })
    return issue
  })
  list(data.evidence, value => {
    const evidence = object(value)
    check(uuid(evidence.source_task_id) && uuid(evidence.tool_call_id))
    check(evidence.runner === 'maven' || evidence.runner === 'pytest')
    check(evidence.runner === (data.language === 'python' ? 'pytest' : 'maven'))
    check(Number.isInteger(evidence.tests) && Number(evidence.tests) > 0)
    check(evidence.failures === 0 && evidence.errors === 0 && evidence.skipped === 0)
    return evidence
  })
  check(data.schema_version !== 3 || data.reuse_status !== 'active' || (data.evidence as unknown[]).length > 0)
  return data as unknown as ExperienceKnowledge
}
export function readExperience(value: unknown): Experience {
  const data = object(value)
  check(uuid(data.id) && date(data.created_at))
  return { ...data, ...readKnowledge(data) } as unknown as Experience
}
export function readExperiences(value: unknown): Experience[] { return list(value, readExperience) }
export function readMergedExperience(value: unknown, expectedIds: readonly string[]): Experience {
  const result = readExperience(value)
  check(expectedIds.length >= 2 && expectedIds.length <= 10 && sameIds(result.merged_from ?? [], expectedIds))
  check(!expectedIds.some(id => id.toLowerCase() === result.id.toLowerCase()))
  return result
}
export function readProjects(value: unknown): RegisteredProject[] {
  const projects = list(value, value => {
    const data = object(value)
    check(text(data.id) && text(data.label) && text(data.repository_id))
    check(data.language === 'java' || data.language === 'python')
    check(nullable(data.source_revision, text) && typeof data.task_example === 'string')
    if (data.capabilities !== undefined) list(data.capabilities, item => { check(text(item)); return item })
    return data as unknown as RegisteredProject
  })
  check(new Set(projects.map(project => project.id)).size === projects.length)
  return projects
}

export function readAgentEvents(value: unknown, taskId: string, afterSequence = 0): AgentEvent[] {
  const events = list(value, item => {
    const data = object(item), usage = object(data.usage)
    check(uuid(data.event_id) && data.task_id === taskId && uuid(data.agent_id))
    check(['diagnosis', 'repair', 'review', 'coordinator', 'extractor'].includes(String(data.role)))
    check(['started', 'completed', 'failed', 'stopped'].includes(String(data.status)))
    check(Number.isInteger(data.sequence) && Number(data.sequence) > afterSequence && text(data.phase) && text(data.summary))
    check(date(data.created_at) && nullable(data.snapshot_revision, text))
    check(usage.reported_tokens === null || Number.isInteger(usage.reported_tokens) && Number(usage.reported_tokens) >= 0)
    check(Number.isInteger(usage.elapsed_ms) && Number(usage.elapsed_ms) >= 0)
    return data as unknown as AgentEvent
  })
  check(events.length <= 100 && new Set(events.map(event => event.event_id)).size === events.length)
  check(events.every((event, index) => index === 0 || event.sequence > events[index-1]!.sequence))
  return events
}

export function readPromotionPreview(value: unknown, sourceId: string): PromotionPreview {
  const data = object(value)
  check(boolean(data.can_publish) && data.source_id === sourceId && nullable(data.current_global_id, uuid))
  const conflicts = list(data.conflicts, item => { check(text(item)); return item })
  let draft: PromotionDraft | null = null
  if (data.draft !== null) {
    const raw = object(data.draft)
    readKnowledge({ ...raw, verification: 'Draft', scope: 'global', schema_version: 3, reuse_status: 'candidate' })
    check(Array.isArray(raw.applicability) && raw.applicability.length > 0 && Array.isArray(raw.issues) && raw.issues.length > 0)
    draft = raw as unknown as PromotionDraft
  }
  const injection_preview = data.injection_preview === null ? null : object(data.injection_preview)
  check(data.can_publish ? draft && injection_preview && conflicts.length === 0 : conflicts.length > 0 && !draft)
  return { can_publish: data.can_publish, conflicts, source_id: sourceId, current_global_id: data.current_global_id as string | null, draft, injection_preview }
}

export function readProvenance(value: unknown, rootId: string): ProvenanceGraph {
  const data = object(value), nodes = readExperiences(data.nodes)
  check(boolean(data.truncated) && nodes.length <= 100 && nodes.some(node => node.id === rootId))
  const edges = list(data.edges, item => {
    const edge = object(item)
    check(nodes.some(node => node.id === edge.source) && nodes.some(node => node.id === edge.target))
    check(['merged_from', 'derived_from', 'supersedes_id'].includes(String(edge.relation)))
    return edge as unknown as ProvenanceGraph['edges'][number]
  })
  return { nodes, edges, truncated: data.truncated }
}
export function readMergePreview(value: unknown, expectedIds?: readonly string[]): MergePreview {
  const data = object(value)
  check(boolean(data.can_merge))
  const conflicts = list(data.conflicts, item => { check(text(item)); return item })
  const source_ids = list(data.source_ids, item => { check(uuid(item)); return item as string })
  check(source_ids.length >= 2 && source_ids.length <= 10 && sameIds(source_ids, source_ids))
  if (expectedIds) check(sameIds(source_ids, expectedIds))
  const preview = data.preview === null ? null : readKnowledge(data.preview)
  check(data.can_merge ? preview !== null && conflicts.length === 0 : preview === null && conflicts.length > 0)
  if (preview) check(sameIds(preview.merged_from ?? [], source_ids))
  return { can_merge: data.can_merge, conflicts, source_ids, preview }
}
