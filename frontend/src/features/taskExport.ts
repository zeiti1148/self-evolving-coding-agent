import type { TaskStatusResponse, TaskStep } from '../api/contracts'

// This is a local review artifact, not an additional backend API contract.
export function createExecutionExport(task: TaskStatusResponse, steps: TaskStep[], mock: boolean, stepsLoaded = true) {
  return {
    format_version: 1, exported_at: new Date().toISOString(), mock,
    complete_steps: stepsLoaded && !['pending', 'running'].includes(task.status)
      && steps.length === task.steps_completed && steps.every((step, index) => step.sequence === index + 1),
    task, steps,
  }
}

export function downloadExecution(task: TaskStatusResponse, steps: TaskStep[], mock: boolean, stepsLoaded = true) {
  const content = JSON.stringify(createExecutionExport(task, steps, mock, stepsLoaded), null, 2)
  const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }))
  const link = document.createElement('a')
  try {
    link.href = url
    link.download = `task-${task.task_id.replace(/[^a-z0-9-]/gi, '_')}-execution.json`
    document.body.append(link)
    link.click()
  } finally {
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
