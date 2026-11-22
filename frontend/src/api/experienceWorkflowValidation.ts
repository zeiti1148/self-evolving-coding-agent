import type { AutomaticMergePage, ConsolidationReport, ExperienceConflict, MaintenanceJob, ExperienceMaintenanceStatus, RuleSources, ReviewEvent } from './contracts'
import { readConflictDetails, readExperiences } from './validation'

function check(value: unknown): asserts value { if (!value) throw new Error('Invalid experience workflow response') }
function object(value: unknown): Record<string, unknown> {
  check(value && typeof value === 'object' && !Array.isArray(value))
  return value as Record<string, unknown>
}
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i.test(v)
const text = (v: unknown): v is string => typeof v === 'string' && !!v.trim()
const date = (v: unknown) => typeof v === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/i.test(v) && Number.isFinite(Date.parse(v))
function ids(value: unknown): string[] {
  check(Array.isArray(value) && value.every(uuid) && new Set(value).size === value.length)
  return value
}
export function readConsolidationReport(value: unknown): ConsolidationReport {
  const d = object(value)
  check(Number.isSafeInteger(d.examined) && Number(d.examined) >= 0)
  check(Number.isSafeInteger(d.deferred_groups) && Number(d.deferred_groups) >= 0)
  check(d.next_cursor == null || typeof d.next_cursor === 'string' && /^[0-9a-f]{64}$/.test(d.next_cursor))
  const additions: Partial<ConsolidationReport> = {}
  for (const key of ['pending_jobs', 'failed_jobs', 'unknown_pairs'] as const) {
    if (d[key] !== undefined) { check(Number.isSafeInteger(d[key]) && Number(d[key]) >= 0); additions[key] = Number(d[key]) }
  }
  if (d.job_id !== undefined) { check(d.job_id === null || uuid(d.job_id)); additions.job_id = d.job_id as string | null }
  return { ...additions, examined: Number(d.examined), deferred_groups: Number(d.deferred_groups),
    merged_ids: ids(d.merged_ids), conflict_ids: ids(d.conflict_ids), next_cursor: d.next_cursor as string | null ?? null }
}
export function readConflict(value: unknown, expectedId?: string): ExperienceConflict {
  const d = object(value), sources = readExperiences(d.sources)
  check(uuid(d.id) && (!expectedId || d.id === expectedId) && sources.length === 2 && sources[0]!.id !== sources[1]!.id)
  check(['pending', 'resolved', 'obsolete'].includes(String(d.status)) && Number.isSafeInteger(d.version) && Number(d.version) >= 1)
  check(date(d.created_at) && (d.resolved_at === null || date(d.resolved_at)))
  check(d.resolution === null || d.resolution === 'prefer_source' || ['keep_separate', 'confirm_equivalent', 'confirm_compatible'].includes(String(d.resolution)))
  check(d.preferred_source_id === null || sources.some(source => source.id === d.preferred_source_id))
  check(d.note === null || text(d.note))
  if (d.rule_ids !== undefined) { const rules = ids(d.rule_ids); check(rules.length === 0 || rules.length === 2) }
  if (d.relation !== undefined) check(['conflict', 'unknown', 'equivalent', 'compatible'].includes(String(d.relation)))
  if (d.status === 'pending') check(d.resolved_at === null && d.resolution === null && d.preferred_source_id === null && d.note === null)
  else check(date(d.resolved_at))
  if (d.status === 'resolved') {
    check(text(d.note) && d.resolution !== null && (d.resolution === 'prefer_source') === (d.preferred_source_id !== null))
  }
  const details = readConflictDetails(d.details, sources.map(source => source.id))
  check(details.length > 0)
  for (const detail of details.filter(item => item.source_ids.length === 1)) {
    const source = sources.find(item => item.id === detail.source_ids[0])
    const match = /^issues\[(\d+)\]\.solution$/.exec(detail.field)
    check(match && source?.issues?.[Number(match[1])]?.solution.includes(detail.suggestion))
  }
  if (d.automatic_resolution != null) {
    const auto = object(d.automatic_resolution), sourceIds = sources.map(s=>s.id)
    const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v)
    check(['verifying','resolved','needs_confirmation'].includes(String(auto.state)) && auto.actor === 'system')
    check(text(auto.policy) && text(auto.stage) && text(auto.reason) && date(auto.started_at) && date(auto.updated_at))
    check(auto.resolution === null || ['prefer_source','keep_separate'].includes(String(auto.resolution)))
    check(auto.preferred_source_id === null || sourceIds.includes(String(auto.preferred_source_id)))
    check(auto.evidence_hash === null || hash(auto.evidence_hash))
    check(Array.isArray(auto.targets) && auto.targets.every(text) && Array.isArray(auto.runs) && auto.runs.length <= 6)
    for (const raw of auto.runs) {
      const run = object(raw)
      check(uuid(run.id) && sourceIds.includes(String(run.target_source_id)) && sourceIds.includes(String(run.implementation_source_id)))
      check(['passed','failed','invalid'].includes(String(run.outcome)) && [1,2].includes(Number(run.repeat)))
      for (const key of ['tests','failures','errors','skipped']) check(Number.isSafeInteger(run[key]) && Number(run[key]) >= 0)
      check(hash(run.test_identity) && hash(run.artifact_sha256) && text(run.artifact_path))
      check(Array.isArray(run.failed_tests) && run.failed_tests.every(text) && typeof run.reproduces_baseline === 'boolean')
      if (run.outcome === 'passed') check(Number(run.tests)>0 && !run.failures && !run.errors && !run.skipped)
    }
    if (auto.state === 'resolved') check(auto.runs.length === 6 && auto.resolution !== null && hash(auto.evidence_hash))
  }
  return { ...d, sources, details } as unknown as ExperienceConflict
}
export function readConflicts(value: unknown): ExperienceConflict[] {
  check(Array.isArray(value) && value.length <= 100)
  const items = value.map(item => readConflict(item))
  check(new Set(items.map(item => item.id)).size === items.length)
  return items
}

export function readJobs(value: unknown): MaintenanceJob[] {
  check(Array.isArray(value) && value.length <= 100)
  return value.map(v => {
    const d = object(v)
    check(uuid(d.id) && uuid(d.rule_id) && ['pending', 'running', 'succeeded', 'failed'].includes(String(d.state)))
    check(Number.isSafeInteger(d.attempts) && Number(d.attempts) >= 0 && Number.isSafeInteger(d.examined) && Number(d.examined) >= 0)
    check(date(d.created_at) && date(d.updated_at) && (d.error === null || text(d.error)))
    return d as unknown as MaintenanceJob
  })
}
export function readMaintenance(value: unknown, expected: string): ExperienceMaintenanceStatus {
  const d = object(value)
  check(d.experience_id === expected && ['active', 'paused'].includes(String(d.effective_status)))
  const current = ids(d.current_rule_ids)
  check(Array.isArray(d.rules))
  for (const value of d.rules) {
    const r = object(value), data = object(r.data), issue = object(data.issue)
    check(uuid(r.id) && ['pending', 'ready', 'needs_review', 'failed'].includes(String(r.state)))
    check(['active', 'paused'].includes(String(r.effective_status)))
    check(['error_type', 'root_cause', 'solution'].every(k => text(issue[k])))
    for (const v of [issue.files, data.applicability, data.constraints]) check(Array.isArray(v) && v.every(text))
  }
  check(current.every(id => (d.rules as Record<string, unknown>[]).some(rule => rule.id === id && rule.effective_status === 'active')))
  return d as unknown as ExperienceMaintenanceStatus
}
export function readRuleSources(value: unknown, expected: string): RuleSources {
  const d = object(value)
  check(d.rule_id === expected && Number.isSafeInteger(d.evidence_count) && Number(d.evidence_count) >= 0)
  check(d.next_offset === null || Number.isSafeInteger(d.next_offset) && Number(d.next_offset) >= 0)
  ids(d.source_ids)
  return d as unknown as RuleSources
}
export function readReviewHistory(value: unknown): ReviewEvent[] {
  check(Array.isArray(value))
  return value.map(v => {
    const d = object(v), data = object(d.data)
    check(Number.isSafeInteger(d.version) && Number(d.version) >= 1 && ['resolve', 'reopen'].includes(String(d.action)) && date(d.created_at))
    check(data.note === undefined || text(data.note))
    return d as unknown as ReviewEvent
  })
}

export function readAutomaticMerges(value: unknown): AutomaticMergePage {
  const page = object(value)
  check(Array.isArray(page.items) && page.items.length <= 10)
  check(page.next_offset === null || Number.isSafeInteger(page.next_offset) && Number(page.next_offset) >= 0)
  for (const raw of page.items) {
    const job = object(raw), [source] = readExperiences([job.source])
    check(uuid(job.id) && source?.id === job.id && date(job.created_at) && date(job.updated_at))
    check(['pending','running','waiting','succeeded','failed'].includes(String(job.state)))
    check(typeof job.notice === 'string' && typeof job.local_pending === 'boolean')
    for (const key of ['candidate_total','selected_candidates','compared_candidates','deferred_candidates']) check(Number.isSafeInteger(job[key]) && Number(job[key]) >= 0)
    check(Array.isArray(job.changes))
    for (const rawChange of job.changes) {
      const change = object(rawChange)
      check(uuid(change.id) && ['equivalent_rules','shared_principle','review'].includes(String(change.kind)))
      check(['pending','running','waiting','succeeded','rejected','failed','merged','invalidated','resolved','obsolete'].includes(String(change.state)))
      check(readExperiences(change.before).length >= 2 && typeof change.reason === 'string')
      if (change.after !== null) {
        const after = object(change.after)
        check(text(after.title) && Array.isArray(after.applicability) && after.applicability.every(text))
        if (change.kind === 'shared_principle') {
          check(text(after.principle) && Array.isArray(after.boundaries) && after.boundaries.every(text))
          check(Array.isArray(after.bindings) && Array.isArray(after.instances))
          for (const rawBinding of after.bindings) {
            const binding = object(rawBinding)
            check(uuid(binding.source_id) && Array.isArray(binding.supports))
            for (const rawSupport of binding.supports) { const support = object(rawSupport); check(text(support.quote) && ['solution','root_cause'].includes(String(support.field))) }
          }
          for (const rawInstance of after.instances) { const instance = object(rawInstance); check(uuid(instance.experience_id) && Array.isArray(instance.constraints) && instance.constraints.every(text)) }
        } else { const issue = object(after.issue); check(text(issue.solution) && text(issue.root_cause) && Array.isArray(after.constraints) && after.constraints.every(text)) }
      }
      if (change.state === 'merged') check(change.after !== null)
      if (change.review) readConflict(change.review)
      if (change.history) readReviewHistory(change.history)
    }
  }
  return page as unknown as AutomaticMergePage
}
