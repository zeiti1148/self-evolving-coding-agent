/** Mirrors backend/shared/models.py; null facts are intentionally not booleans. */
export type TaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'stopped'
export type TerminalStatus = 'succeeded' | 'failed' | 'stopped'
export interface ToolCall { id: string; name: string; arguments: Record<string, unknown> }
export interface ToolResult {
  call_id: string; tool_name: string; success: boolean; output: string
  error_code: string | null; error_message: string | null; metadata: Record<string, unknown>
}
export interface TaskStep { sequence: number; call: ToolCall; result: ToolResult; completed_at: string }
export type ProjectLanguage = 'java' | 'python' | 'go' | 'cpp'
export interface TestSourceInfo { path: string; language: ProjectLanguage; sha256: string }
export interface TestSourceIndex { task_id: string; files: TestSourceInfo[] }
export interface TestSourceFile extends TestSourceInfo { task_id: string; content: string; size_bytes: number }
export interface CodeChangeInfo {
  path: string; change_type: 'modified' | 'added' | 'deleted'; before_sha256: string | null; after_sha256: string | null
  available: boolean; unavailable_reason: string | null
}
export type CodeDiffMode = 'snapshot' | 'recorded_writes'
export interface CodeChangesIndex { task_id: string; status: TaskStatus; mode: CodeDiffMode; notice: string; files: CodeChangeInfo[]; omitted_files: number }
export interface DiffLine { kind: 'context' | 'added' | 'removed'; old_line: number | null; new_line: number | null; content: string }
export interface DiffHunk { old_start: number; old_lines: number; new_start: number; new_lines: number; lines: DiffLine[] }
export interface CodeDiffFile extends CodeChangeInfo {
  task_id: string; mode: CodeDiffMode; notice: string; before_content: string | null; after_content: string | null
  additions: number; deletions: number; format_only: boolean; hunks: DiffHunk[]
}
export type ExperienceScope = 'global' | 'project'
export type ExecutionMode = 'single' | 'multi'
export type ReuseStatus = 'active' | 'candidate' | 'legacy_unreviewed'
export interface RegisteredProject {
  id: string; label: string; language: ProjectLanguage; repository_id: string
  source_revision: string | null; task_example: string
  origin?: 'registered' | 'upload'; source_digest?: string | null; runner_config?: RunnerConfig
  file_count?: number; excluded_entries?: number
  capabilities?: string[]
}
export interface RepairIssue { error_type: string; root_cause: string; solution: string; files: string[]; symbols?: string[]; depends_on?: number[] }
export interface VerificationEvidence {
  source_task_id: string; tool_call_id: string; runner: 'maven' | 'pytest' | 'go' | 'ctest'
  tests: number; failures: number; errors: number; skipped: number
}
export interface ExperienceKnowledge {
  task_summary: string; error_type: string | null; root_cause: string
  solution: string; verification: string; tags: string[]; source_task_id: string | null
  schema_version?: number; language?: ProjectLanguage; scope?: ExperienceScope
  repository_id?: string | null; source_revision?: string | null; applicability?: string[]
  issues?: RepairIssue[]; evidence?: VerificationEvidence[]; merged_from?: string[]
  batch_id?: string | null; reuse_status?: ReuseStatus; derived_from?: string[]
  supersedes_id?: string | null; required_capabilities?: string[]; constraints?: string[]; compatibility_key?: string | null
}
export interface Experience extends ExperienceKnowledge { id: string; created_at: string }
export interface ExperienceFilters { language?: ProjectLanguage; repository_id?: string; scope?: ExperienceScope
  reuse_status?: ReuseStatus; source_task_id?: string; active_only?: boolean }
export interface AgentEvent {
  event_id: string; task_id: string; agent_id: string; role: 'diagnosis' | 'repair' | 'review' | 'coordinator' | 'extractor'
  phase: string; status: 'started' | 'completed' | 'failed' | 'stopped'; sequence: number
  snapshot_revision: string | null; summary: string; created_at: string
  usage: { reported_tokens: number | null; elapsed_ms: number }
}
export interface PromotionDraft {
  task_summary: string; error_type: string | null; root_cause: string; solution: string; tags: string[]
  language: ProjectLanguage; applicability: string[]; issues: RepairIssue[]; required_capabilities: string[]
}
export interface PromotionPreview {
  can_publish: boolean; conflicts: string[]; source_id: string; current_global_id: string | null
  draft: PromotionDraft | null; injection_preview: Record<string, unknown> | null
}
export interface ProvenanceGraph { nodes: Experience[]; edges: { source: string; target: string; relation: string }[]; truncated: boolean }
export interface MergePreview {
  can_merge: boolean; conflicts: string[]; source_ids: string[]; preview: ExperienceKnowledge | null
  conflict_details?: MergeConflictDetail[]
}
export interface MergeConflictDetail {
  code: 'scope_mismatch' | 'applicability_mismatch' | 'status_mismatch' | 'capability_mismatch' | 'rule_overlap'
    | 'evidence_mismatch' | 'order_cycle' | 'content_limit' | 'source_unavailable' | 'unknown_relation' | 'version_mismatch' | 'semantic_equivalent'
  message: string; source_ids: string[]; field: string; suggestion: string
}
export interface ConsolidationReport { examined: number; merged_ids: string[]; conflict_ids: string[]; deferred_groups: number; next_cursor?: string | null; job_id?: string | null; pending_jobs?: number; failed_jobs?: number; unknown_pairs?: number }
export type ConflictStatus = 'pending' | 'resolved' | 'obsolete'
export interface ConflictResolution {
  version: number; resolution: 'prefer_source' | 'keep_separate' | 'confirm_equivalent' | 'confirm_compatible'; preferred_source_id: string | null; note: string
}
export interface ExperienceConflict {
  id: string; sources: Experience[]; details: MergeConflictDetail[]; status: ConflictStatus; version: number
  created_at: string; resolved_at: string | null; resolution: ConflictResolution['resolution'] | null
  preferred_source_id: string | null; note: string | null; rule_ids?: string[]; relation?: 'conflict' | 'unknown' | 'equivalent' | 'compatible'
  automatic_resolution?: AutomaticConflictResolution | null
}
export interface ConflictTestRun {
  id: string; target_source_id: string; implementation_source_id: string; repeat: number
  outcome: 'passed' | 'failed' | 'invalid'; tests: number; failures: number; errors: number; skipped: number
  test_identity: string; failed_tests: string[]; reproduces_baseline: boolean; artifact_sha256: string; artifact_path: string
}
export interface AutomaticConflictResolution {
  state: 'verifying' | 'resolved' | 'needs_confirmation'; actor: 'system'; policy: string; stage: string; reason: string
  targets: string[]; runs: ConflictTestRun[]; resolution: 'prefer_source' | 'keep_separate' | null
  preferred_source_id: string | null; started_at: string; updated_at: string; evidence_hash: string | null
}
export interface MaintenanceJob { id: string; rule_id: string; state: 'pending' | 'running' | 'succeeded' | 'failed'; attempts: number; examined: number; error: string | null; created_at: string; updated_at: string }
export interface RuleStatus { id: string; state: 'pending' | 'ready' | 'needs_review' | 'failed'; effective_status: 'active' | 'paused'; data: { issue: RepairIssue; applicability: string[]; constraints: string[] } }
export interface ExperienceMaintenanceStatus { experience_id: string; effective_status: 'active' | 'paused'; current_rule_ids: string[]; rules: RuleStatus[] }
export interface RuleSources { rule_id: string; source_ids: string[]; evidence_count: number; next_offset: number | null }
export interface ReviewEvent { version: number; action: 'resolve' | 'reopen'; data: { note?: string; resolution?: string; actor?: string; policy?: string; evidence_hash?: string }; created_at: string }
export interface ConflictFilters { language?: ProjectLanguage; repository_id?: string; status?: ConflictStatus; limit?: number; offset?: number }
export interface MergeSuggestion {
  source_ids: string[]; source_titles: string[]; language: ProjectLanguage; scope: ExperienceScope; repository_id: string | null
  reasons: ('shared_rules' | 'shared_files')[]; shared_issue_count: number; issue_count: number
  verification_count: number; revision_warning: boolean
}
export interface MergeSuggestions {
  suggestions: MergeSuggestion[]; scanned_count: number; eligible_count: number; checked_pairs: number
  rejected: { reason: 'conditions' | 'capabilities' | 'unrelated' | 'conflict'; count: number }[]
  truncation_reasons: ('scan_limit' | 'candidate_limit' | 'result_limit')[]
}
export interface AgentResult {
  task_id: string; status: TerminalStatus; summary: string; steps: ToolCall[]; tool_results: ToolResult[]
  repair_succeeded: boolean; tests_passed: boolean | null; changed_files: string[]
  experience_saved: boolean | null; experience_id: string | null
  experience_ids?: string[]
  experience_save_error: string | null; error_message: string | null
}
export interface TaskCreateRequest { task_description: string; source_project: string; max_steps: number; execution_mode?: ExecutionMode; knowledge_recovery_id?: string }
export interface TaskCreateResponse { task_id: string; status: 'pending'; created_at: string }
export interface TaskStatusResponse {
  task_id: string; status: TaskStatus; steps_completed: number; created_at: string
  started_at: string | null; finished_at: string | null; result: AgentResult | null; error_message: string | null
}
export interface TaskStepsResponse { task_id: string; status: TaskStatus; steps: TaskStep[] }
export interface TestRunEvidence {
  tool_call_id: string; sequence: number; runner: 'maven' | 'pytest' | 'go' | 'ctest'; tests: number
  passed: number; failures: number; errors: number; skipped: number
}
export interface TaskEvidence {
  task_id: string; status: TaskStatus; source_project: string | null; project_label: string | null
  tests: { before: TestRunEvidence | null; after: TestRunEvidence | null; final_verified: boolean
    original_tests_unchanged: boolean | null; comparable: boolean; unavailable_reason: string | null }
  failure_cases: { test: string; error_type: string; message: string; source_file: string | null; source_line: number | null }[]
  references: { experience_id: string; title: string; source_task_id: string | null; scope: ExperienceScope | null
    content_sha256: string; roles: { role: 'diagnosis' | 'repair' | 'review' | 'single'; request_count: number }[]
    request_count: number; detail_available: boolean }[]
  reference_recording: 'recorded' | 'unavailable'; reference_notice: string
}
export interface RunnerConfig { python_executable: string | null; python_version?: string | null; requirements_file?: string | null; python_paths: string[]; test_targets: string[]; deselect: string[]; pytest_plugins: string[]; timeout_seconds: number }
export interface ExperienceRetryState { task_id: string; state: 'idle' | 'running' | 'succeeded' | 'failed'; notice: string }

export interface AgentApi {
  getExperienceLibrary?(filters?: LibraryFilters, signal?: AbortSignal): Promise<ExperienceLibrary>
  listExperienceRecovery?(cardId:string,signal?:AbortSignal):Promise<ExperienceRecovery[]>
  listRecoveryTasks?(cardId:string,signal?:AbortSignal):Promise<{id:string;label:string;project_label:string;tests:number;created_at:string}[]>
  startExperienceRecovery?(cardId:string,body:{task_id?:string;retry?:boolean},signal?:AbortSignal):Promise<ExperienceRecovery>
  getExperienceRecovery?(id:string,signal?:AbortSignal):Promise<ExperienceRecovery>
  confirmExperienceRecovery?(id:string,body:{version:number;note:string;accept:boolean},signal?:AbortSignal):Promise<ExperienceRecovery>
  scanExperienceHistory?(retry?: boolean, signal?: AbortSignal): Promise<HistoryScan>
  listAutomaticMerges?(filters: ExperienceFilters & { experience_id?: string }, offset?: number, signal?: AbortSignal): Promise<AutomaticMergePage>
  retryAutomaticMerge?(id: string, signal?: AbortSignal): Promise<{ id: string }>
  uploadProject?(file: File, label: string, projectRoot: string, signal?: AbortSignal): Promise<RegisteredProject>
  configureProject?(id: string, config: RunnerConfig, signal?: AbortSignal): Promise<RegisteredProject>
  runtimeOptions?(signal?: AbortSignal): Promise<{ python: string[]; go: boolean; cpp: boolean; api_configuration?: {origin: string | null; model: string; source_file: string | null} }>

  createTask(request: TaskCreateRequest, signal?: AbortSignal): Promise<TaskCreateResponse>
  getTask(taskId: string, signal?: AbortSignal): Promise<TaskStatusResponse>
  getExperienceRetry?(taskId: string, signal?: AbortSignal): Promise<ExperienceRetryState>
  retryExperience?(taskId: string, signal?: AbortSignal): Promise<ExperienceRetryState>
  getSteps(taskId: string, signal?: AbortSignal): Promise<TaskStepsResponse>
  getTaskEvidence?(taskId: string, signal?: AbortSignal): Promise<TaskEvidence>
  listTestSources?(taskId: string, signal?: AbortSignal): Promise<TestSourceIndex>
  getTestSource?(taskId: string, path: string, signal?: AbortSignal): Promise<TestSourceFile>
  listCodeChanges?(taskId: string, signal?: AbortSignal): Promise<CodeChangesIndex>
  getCodeDiff?(taskId: string, path: string, signal?: AbortSignal): Promise<CodeDiffFile>
  listExperiences(limit?: number, offset?: number, signal?: AbortSignal, filters?: ExperienceFilters): Promise<Experience[]>
  listProjects?(signal?: AbortSignal): Promise<RegisteredProject[]>
  previewMerge?(experienceIds: string[], signal?: AbortSignal): Promise<MergePreview>
  getMergeSuggestions?(filters?: ExperienceFilters, signal?: AbortSignal): Promise<MergeSuggestions>
  mergeExperiences?(experienceIds: string[], signal?: AbortSignal): Promise<Experience>
  consolidateExperiences?(language: ProjectLanguage, repositoryId: string, signal?: AbortSignal, cursor?: string): Promise<ConsolidationReport>
  listConflicts?(filters?: ConflictFilters, signal?: AbortSignal): Promise<ExperienceConflict[]>
  resolveConflict?(id: string, resolution: ConflictResolution, signal?: AbortSignal): Promise<ExperienceConflict>
  listMaintenanceJobs?(signal?: AbortSignal, filters?: Pick<ExperienceFilters, 'language' | 'repository_id'>): Promise<MaintenanceJob[]>
  retryMaintenanceJob?(id: string, signal?: AbortSignal): Promise<MaintenanceJob>
  reopenConflict?(id: string, version: number, note: string, signal?: AbortSignal): Promise<ExperienceConflict>
  getReviewHistory?(id: string, signal?: AbortSignal): Promise<ReviewEvent[]>
  getExperienceMaintenance?(id: string, signal?: AbortSignal): Promise<ExperienceMaintenanceStatus>
  getRuleSources?(id: string, offset?: number, signal?: AbortSignal): Promise<RuleSources>
  getAgentEvents?(taskId: string, afterSequence?: number, signal?: AbortSignal): Promise<AgentEvent[]>
  getExperience?(id: string, signal?: AbortSignal): Promise<Experience>
  getProvenance?(id: string, signal?: AbortSignal): Promise<ProvenanceGraph>
  previewPromotion?(sourceId: string, signal?: AbortSignal): Promise<PromotionPreview>
  publishGlobal?(sourceId: string, draft: PromotionDraft, supersedesId?: string | null, signal?: AbortSignal): Promise<Experience>
}
export type MockScenario = 'success' | 'tests-failed' | 'stopped' | 'save-failure' | 'schedule-error'

export interface AutomaticMergeChange {
  id: string
  kind: 'equivalent_rules' | 'shared_principle' | 'review'
  state: 'pending' | 'running' | 'waiting' | 'succeeded' | 'rejected' | 'failed' | 'merged' | 'invalidated' | 'resolved' | 'obsolete'
  before: Experience[]
  after: {
    title: string; principle?: string; issue?: RepairIssue; applicability: string[]; constraints?: string[]; boundaries?: string[]
    bindings?: {source_id: string; supports: {field: string; quote: string; issue_index: number}[]}[]
    instances?: {experience_id: string; constraints: string[]; repository_id: string}[]
  } | null
  reason: string; evidence_count?: number; more_sources?: boolean
  review?: ExperienceConflict; history?: ReviewEvent[]
  proposal?: {reason: string} | null; review_result?: {reason: string} | null
}
export interface AutomaticMergeJob {
  id: string; source: Experience; state: 'pending' | 'running' | 'waiting' | 'succeeded' | 'failed'
  notice: string; created_at: string; updated_at: string; local_pending: boolean
  candidate_total: number; selected_candidates: number; deferred_candidates: number
  changes: AutomaticMergeChange[]
}
export interface AutomaticMergePage { items: AutomaticMergeJob[]; next_offset: number | null }

export interface HistoryScan {
  id: string; state: 'pending'|'running'|'succeeded'|'failed'; notice: string
  scanned: number; scheduled: number; deferred: number; groups: number; sources: number
  created_at: string; updated_at: string
}
export interface KnowledgeCard {
  id: string; kind: 'rule'|'archive'|'principle'|'validated'; state: 'active'|'paused'|'unverified'; title: string; summary: string
  root_cause: string; language: ProjectLanguage; error_type: string; applicability: string[]; constraints: string[]
  source_ids: string[]; sources: Experience[]; source_count: number; evidence_count: number; merged: boolean
  reason: string; grouping_reason?: string; created_at: string
  repository_ids?:string[];project_labels?:string[];tags?:string[];files?:string[];symbols?:string[];scope?:ExperienceScope
  reuse_mode?:'scoped';reuse_scope?:RecoveryScope
  recovery?:Pick<ExperienceRecovery,'id'|'state'|'reason'|'version'|'task_id'|'created_at'|'updated_at'>
}
export interface LibraryFilters extends ExperienceFilters { card_id?:string;q?:string;error_type?:string;state?:'active'|'paused'|'unverified';tag?:string;merged?:boolean;limit?:number;offset?:number;sort?:'recent'|'relevance'|'sources' }
export interface LibraryFacet {value:string;label:string;count:number}
export interface ExperienceLibrary {
  items: KnowledgeCard[]
  summary: {original_records: number; knowledge_cards: number; active: number; paused: number; unverified: number; merged: number; pending_reviews: number}
  history_scan: HistoryScan | null
  total?:number;offset?:number;limit?:number|null;next_offset?:number|null
  facets?:{languages:LibraryFacet[];repositories:LibraryFacet[];error_types:LibraryFacet[];tags:LibraryFacet[]}
}
export interface RecoveryScope {project_id:string;project_label:string;repository_id:string;compatibility_key:string;source_revision:string;runner_hash:string}
export interface ExperienceRecovery {
  id:string;card_id:string;state:'pending'|'running'|'waiting_task'|'needs_evidence'|'needs_confirmation'|'succeeded'|'failed'|'invalidated'|'kept'
  version:number;reason:string;task_id:string|null;created_at:string;updated_at:string;actor:'system'|'user'
  before:{state:'active'|'paused'|'unverified';summary:string;evidence_count:number}
  after?:{state:'active';reuse_mode:'scoped';summary:string;evidence_count:number;source_id:string}
  scope?:RecoveryScope;candidate?:Experience;candidate_rule_index?:number;can_confirm:boolean;review_ids:string[]
  reviews?:{id:string;status:'pending'|'resolved';version:number;resolution:string|null}[]
  runs:ConflictTestRun[];steps:{stage:string;reason:string;at:string}[]
}
