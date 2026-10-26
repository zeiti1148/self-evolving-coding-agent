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
export type ExperienceScope = 'global' | 'project'
export interface RegisteredProject {
  id: string; label: string; language: ProjectLanguage; repository_id: string
  source_revision: string | null; task_example: string
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
}
export interface Experience extends ExperienceKnowledge { id: string; created_at: string }
export interface ExperienceFilters { language?: ProjectLanguage; repository_id?: string; scope?: ExperienceScope }
export interface MergePreview {
  can_merge: boolean; conflicts: string[]; source_ids: string[]; preview: ExperienceKnowledge | null
}
export interface AgentResult {
  task_id: string; status: TerminalStatus; summary: string; steps: ToolCall[]; tool_results: ToolResult[]
  repair_succeeded: boolean; tests_passed: boolean | null; changed_files: string[]
  experience_saved: boolean | null; experience_id: string | null
  experience_save_error: string | null; error_message: string | null
}
export interface TaskCreateRequest { task_description: string; source_project: string; max_steps: number }
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
  listExperiences(limit?: number, offset?: number, signal?: AbortSignal, filters?: ExperienceFilters): Promise<Experience[]>
  listProjects?(signal?: AbortSignal): Promise<RegisteredProject[]>
  previewMerge?(experienceIds: string[], signal?: AbortSignal): Promise<MergePreview>
  mergeExperiences?(experienceIds: string[], signal?: AbortSignal): Promise<Experience>
}
export type MockScenario = 'success' | 'tests-failed' | 'stopped' | 'save-failure' | 'schedule-error'
