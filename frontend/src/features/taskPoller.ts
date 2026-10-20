import type { AgentApi, TaskStatusResponse, TaskStep } from '../api/contracts'

export function createTaskPoller(api: AgentApi, callbacks: {
  onSnapshot(status: TaskStatusResponse, steps: TaskStep[]): void
  onError(error: Error): void
}, intervalMs = 1200) {
  let generation = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller: AbortController | undefined
  let taskId = ''
  let collected = new Map<number, TaskStep>()
  function stop() {
    generation++
    clearTimeout(timer)
    controller?.abort()
  }
  async function tick(version: number) {
    const active = new AbortController()
    controller = active
    let again = true
    try {
      const status = await api.getTask(taskId, active.signal)
      if (version !== generation) return
      callbacks.onSnapshot(status, [...collected.values()].sort((a, b) => a.sequence - b.sequence))
      const response = await api.getSteps(taskId, active.signal)
      if (version !== generation) return
      response.steps.forEach(step => collected.set(step.sequence, step))
      const steps = [...collected.values()].sort((a, b) => a.sequence - b.sequence)
      callbacks.onSnapshot(status, steps)
      again = ['pending', 'running'].includes(status.status)
        || ['pending', 'running'].includes(response.status) || steps.length < status.steps_completed
    } catch (error) {
      if (version !== generation || active.signal.aborted) return
      callbacks.onError(error instanceof Error ? error : new Error('读取任务失败，请重试。'))
      again = ![404, 422].includes((error as { status?: number })?.status ?? 0)
    } finally {
      if (version === generation && again) timer = setTimeout(() => void tick(version), intervalMs)
    }
  }
  function start(id: string) { stop(); taskId = id; collected = new Map(); void tick(generation) }
  return { start, stop, retry: () => start(taskId) }
}
