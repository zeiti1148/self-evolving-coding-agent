import type { TaskMetrics } from '../api/contracts'
export const terminalMetric = (value: TaskMetrics) => !['pending', 'running'].includes(value.status)
export const groupLabel = (value: TaskMetrics) => value.comparison_group || value.runtime_label || (value.code_revision ? value.code_revision.slice(0, 8) : '未标注版本')
export function duration(value: number | null): string {
  if (value === null) return '尚无完整记录'
  if (value < 1000) return `${value} 毫秒`
  if (value < 60_000) return `${(value / 1000).toFixed(1)} 秒`
  return `${(value / 60_000).toFixed(1)} 分钟`
}
export function numeric(value: number | null): string { return value === null ? '未知' : value.toLocaleString('zh-CN') }
export function comparisonGroups(items: TaskMetrics[]) {
  const grouped = new Map<string, TaskMetrics[]>()
  for (const item of items) { const label = groupLabel(item); grouped.set(label, [...(grouped.get(label) || []), item]) }
  return [...grouped].map(([label, tasks]) => {
    const finished = tasks.filter(terminalMetric), successes = finished.filter(task => task.repair_verified).length
    const times = finished.flatMap(task => task.elapsed_ms === null ? [] : [task.elapsed_ms]).sort((a, b) => a - b)
    const middle = Math.floor(times.length / 2)
    const median = times.length ? times.length % 2 ? times[middle]! : (times[middle - 1]! + times[middle]!) / 2 : null
    const completeUsage = finished.filter(task => task.token_usage.total_tokens !== null)
    const testCoverage = finished.filter(task => typeof task.final_tests_passed === 'boolean').length
    const testPasses = finished.filter(task => task.final_tests_passed === true).length
    const completeTime = times.length === finished.length && finished.length > 0
    return { label, tasks, finished: finished.length, pending: tasks.length - finished.length, successes,
      rate: finished.length ? successes / finished.length : null, median: completeTime ? median : null,
      testPasses, testCoverage, testRate: finished.length && testCoverage === finished.length ? testPasses / finished.length : null,
      timeCoverage: times.length,
      tokens: completeUsage.length === finished.length && finished.length ? completeUsage.reduce((sum, task) => sum + task.token_usage.total_tokens!, 0) : null,
      tokenCoverage: completeUsage.length, reportedTokens: finished.reduce((sum, task) => sum + task.token_usage.reported_total_tokens, 0),
      experienceTokens: finished.every(task => task.experience.token_usage.total_tokens !== null) && finished.length
        ? finished.reduce((sum, task) => sum + task.experience.token_usage.total_tokens!, 0) : null,
    }
  })
}
