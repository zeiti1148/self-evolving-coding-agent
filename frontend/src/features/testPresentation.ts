import type { TaskStep, ToolResult } from '../api/contracts'

export function testPhase(step: TaskStep, steps: TaskStep[]): 'before' | 'during' | 'final' {
  if (step.result.metadata.final_verification === true) return 'final'
  const changed = step.result.metadata.changed_files
  // Missing metadata in an older record does not prove that its source was unchanged.
  if (Array.isArray(changed) && changed.length === 0
    && !steps.some(previous => previous.sequence < step.sequence && previous.call.name === 'write_file')) return 'before'
  return 'during'
}

export function originalDefect(step: TaskStep, steps: TaskStep[]): boolean {
  return step.call.name === 'run_tests' && !step.result.success && step.result.error_code === 'TEST_FAILED'
    && testPhase(step, steps) === 'before'
}

export function testOutcome(step: TaskStep, steps: TaskStep[]): string {
  if (step.call.name !== 'run_tests') return step.result.success ? '已完成' : '未成功'
  if (step.result.success) return '测试通过'
  if (step.result.error_code !== 'TEST_FAILED') return '测试执行异常'
  if (originalDefect(step, steps)) return '发现原始缺陷'
  return testPhase(step, steps) === 'final' ? '最终验证未通过' : '仍有测试未通过'
}

export function testCounts(result: ToolResult) {
  if (result.tool_name !== 'run_tests') return null
  const { tests, failures, errors, skipped } = result.metadata
  const valid = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
  if (!valid(tests) || !valid(failures) || !valid(errors) || !valid(skipped)
    || tests === 0 || failures + errors + skipped > tests) return null
  return { tests, failures, errors, skipped }
}
