/** Mirrors backend/shared/models.py; null facts are intentionally not booleans. */
export type TaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'stopped'
export type TerminalStatus = 'succeeded' | 'failed' | 'stopped'
export interface ToolCall { id: string; name: string; arguments: Record<string, unknown> }
export interface ToolResult {
  call_id: string; tool_name: string; success: boolean; output: string
  error_code: string | null; error_message: string | null; metadata: Record<string, unknown>
}
export interface TaskStep { sequence: number; call: ToolCall; result: ToolResult; completed_at: string }
export type ProjectLanguage = 'java' | 'python'
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
  capabilities?: string[]
}
export interface RepairIssue { error_type: string; root_cause: string; solution: string; files: string[] }
export interface VerificationEvidence {
  source_task_id: string; tool_call_id: string; runner: 'maven' | 'pytest'
  tests: number; failures: number; errors: number; skipped: number
}
export interface ExperienceKnowledge {
  task_summary: string; error_type: string | null; root_cause: string
  solution: string; verification: string; tags: string[]; source_task_id: string | null
  schema_version?: number; language?: ProjectLanguage; scope?: ExperienceScope
  repository_id?: string | null; source_revision?: string | null; applicability?: string[]
  issues?: RepairIssue[]; evidence?: VerificationEvidence[]; merged_from?: string[]
  batch_id?: string | null; reuse_status?: ReuseStatus; derived_from?: string[]
  supersedes_id?: string | null; required_capabilities?: string[]
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
}
export interface AgentResult {
  task_id: string; status: TerminalStatus; summary: string; steps: ToolCall[]; tool_results: ToolResult[]
  repair_succeeded: boolean; tests_passed: boolean | null; changed_files: string[]
  experience_saved: boolean | null; experience_id: string | null
  experience_ids?: string[]
  experience_save_error: string | null; error_message: string | null
}
export interface TaskCreateRequest { task_description: string; source_project: string; max_steps: number; execution_mode?: ExecutionMode }
export interface TaskCreateResponse { task_id: string; status: 'pending'; created_at: string }
export interface TaskStatusResponse {
  task_id: string; status: TaskStatus; steps_completed: number; created_at: string
  started_at: string | null; finished_at: string | null; result: AgentResult | null; error_message: string | null
}
export interface TaskStepsResponse { task_id: string; status: TaskStatus; steps: TaskStep[] }
export interface AgentApi {
  createTask(request: TaskCreateRequest, signal?: AbortSignal): Promise<TaskCreateResponse>
  getTask(taskId: string, signal?: AbortSignal): Promise<TaskStatusResponse>
  getSteps(taskId: string, signal?: AbortSignal): Promise<TaskStepsResponse>
  listTestSources?(taskId: string, signal?: AbortSignal): Promise<TestSourceIndex>
  getTestSource?(taskId: string, path: string, signal?: AbortSignal): Promise<TestSourceFile>
  listCodeChanges?(taskId: string, signal?: AbortSignal): Promise<CodeChangesIndex>
  getCodeDiff?(taskId: string, path: string, signal?: AbortSignal): Promise<CodeDiffFile>
  listExperiences(limit?: number, offset?: number, signal?: AbortSignal, filters?: ExperienceFilters): Promise<Experience[]>
  listProjects?(signal?: AbortSignal): Promise<RegisteredProject[]>
  previewMerge?(experienceIds: string[], signal?: AbortSignal): Promise<MergePreview>
  mergeExperiences?(experienceIds: string[], signal?: AbortSignal): Promise<Experience>
  getAgentEvents?(taskId: string, afterSequence?: number, signal?: AbortSignal): Promise<AgentEvent[]>
  getExperience?(id: string, signal?: AbortSignal): Promise<Experience>
  getProvenance?(id: string, signal?: AbortSignal): Promise<ProvenanceGraph>
  previewPromotion?(sourceId: string, signal?: AbortSignal): Promise<PromotionPreview>
  publishGlobal?(sourceId: string, draft: PromotionDraft, supersedesId?: string | null, signal?: AbortSignal): Promise<Experience>
}
export type MockScenario = 'success' | 'tests-failed' | 'stopped' | 'save-failure' | 'schedule-error'
