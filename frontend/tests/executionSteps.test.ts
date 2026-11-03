import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ExecutionSteps from '../src/components/ExecutionSteps.vue'
import { makeSteps } from '../src/api/fixtures'
import type { TaskStatusResponse, TaskStep } from '../src/api/contracts'
import { createExecutionExport } from '../src/features/taskExport'

const steps = makeSteps(100000, 1, 'success')
const task: TaskStatusResponse = {
  task_id: '11111111-1111-4111-8111-111111111111', status: 'failed', steps_completed: 5,
  created_at: new Date(100000).toISOString(), started_at: new Date(100001).toISOString(),
  finished_at: new Date(100007).toISOString(), result: null, error_message: 'fixture',
}
const button = (wrapper: ReturnType<typeof mount>, label: string) => wrapper.findAll('button').find(item => item.text() === label)!

function testStep(sequence: number, final: boolean, success: boolean, errorCode = 'TEST_FAILED'): TaskStep {
  const id = `test-${sequence}`
  return { sequence, call: { id, name: 'run_tests', arguments: {} }, completed_at: task.finished_at!,
    result: { call_id: id, tool_name: 'run_tests', success, output: 'original test output',
      error_code: success ? null : errorCode, error_message: success ? null : '测试未通过',
      metadata: { changed_files: final ? ['src/Main.java'] : [], final_verification: final,
        tests: 18, failures: success ? 0 : 5, errors: success ? 0 : 9, skipped: 0 } } }
}

it('distinguishes original defects from passing final verification without hiding failed evidence', async () => {
  const original = testStep(1, false, false), final = testStep(8, true, true)
  const wrapper = mount(ExecutionSteps, { props: { task: { ...task, status: 'succeeded' }, steps: [original, final], mock: false } })
  expect(wrapper.findAll('summary')[0]!.text()).toContain('修复前检查')
  expect(wrapper.findAll('summary')[0]!.text()).toContain('发现原始缺陷')
  expect(wrapper.findAll('summary')[1]!.text()).toContain('程序最终验证')
  expect(wrapper.findAll('summary')[1]!.text()).toContain('测试通过')
  await wrapper.get('select').setValue('unsuccessful')
  expect(wrapper.findAll('.execution-list > li')).toHaveLength(1)
  expect(wrapper.get('.tool-output').text()).toBe(original.result.output)
  expect(original.result.success).toBe(false)
  wrapper.unmount()
})

it.each(['COMMAND_FAILED', 'COMMAND_TIMEOUT'])('does not present %s as successful defect reproduction', errorCode => {
  const wrapper = mount(ExecutionSteps, { props: { task, steps: [testStep(1, false, false, errorCode)], mock: false } })
  expect(wrapper.find('summary').text()).not.toContain('发现原始缺陷')
  expect(wrapper.find('summary').text()).toContain('测试执行异常')
  wrapper.unmount()
})

it('keeps a failed final verification distinct from initial defects and unknown old records', () => {
  const final = testStep(8, true, false), unknown = testStep(1, false, false)
  unknown.result.metadata = {}
  const wrapper = mount(ExecutionSteps, { props: { task, steps: [unknown, final], mock: false } })
  expect(wrapper.findAll('summary')[0]!.text()).not.toContain('修复前检查')
  expect(wrapper.findAll('summary')[1]!.text()).toContain('最终验证未通过')
  expect(wrapper.findAll('.step-marker')[1]!.classes()).toContain('step-failed')
  wrapper.unmount()
})

describe('execution log controls', () => {
  it('shows pending records without a false zero, then renders loaded steps or a verified empty result', async () => {
    const wrapper = mount(ExecutionSteps, { props: { task, steps: [], mock: false, stepsLoaded: false } })
    try {
      expect(wrapper.text()).toContain('正在读取执行步骤')
      expect(wrapper.text()).not.toContain('没有已完成的工具步骤')
      expect(wrapper.find('.count-chip').exists()).toBe(false)
      expect(wrapper.get('select').text()).not.toContain('（0）')
      await wrapper.setProps({ stepsLoaded: true })
      expect(wrapper.text()).toContain('正在读取执行步骤')
      expect(wrapper.find('.count-chip').exists()).toBe(false)
      await wrapper.setProps({ steps, stepsLoaded: true })
      expect(wrapper.findAll('.execution-list > li')).toHaveLength(steps.length)
      expect(wrapper.get('.count-chip').text()).toBe(String(steps.length))
      expect(wrapper.text()).not.toContain('正在读取执行步骤')
      await wrapper.setProps({ task: { ...task, steps_completed: 0 }, steps: [] })
      expect(wrapper.text()).toContain('没有已完成的工具步骤')
      expect(wrapper.get('.count-chip').text()).toBe('0')
    } finally { wrapper.unmount() }
  })

  it('filters unsuccessful steps using original numbers and remembers manual expansion across updates', async () => {
    const wrapper = mount(ExecutionSteps, { props: { task, steps, mock: true } })
    await wrapper.findAll('summary')[2]!.trigger('click')
    await wrapper.get('select').setValue('unsuccessful')
    expect(wrapper.findAll('.execution-list > li')).toHaveLength(1)
    expect(wrapper.get('.step-marker').text()).toBe('3')
    expect(wrapper.get('details').attributes('open')).toBeDefined()
    await wrapper.setProps({ steps: structuredClone(steps) })
    expect(wrapper.get('details').attributes('open')).toBeDefined()
    await wrapper.get('select').setValue('all')
    expect(wrapper.findAll('details')[2]!.attributes('open')).toBeDefined()
    await button(wrapper, '展开全部').trigger('click')
    expect(wrapper.findAll('details[open]')).toHaveLength(5)
    await button(wrapper, '收起全部').trigger('click')
    expect(wrapper.findAll('details[open]')).toHaveLength(0)
    await wrapper.get('select').setValue('unsuccessful')
    await button(wrapper, '展开全部').trigger('click')
    await wrapper.setProps({ task: { ...task, task_id: '22222222-2222-4222-8222-222222222222' } })
    expect(wrapper.findAll('details[open]')).toHaveLength(0)
    expect(wrapper.findAll('.execution-list > li')).toHaveLength(5)
    wrapper.unmount()
  })

  it('distinguishes an empty filter from a task with no steps', async () => {
    const wrapper = mount(ExecutionSteps, { props: { task: { ...task, steps_completed: 2 }, steps: steps.slice(0, 2), mock: false } })
    await wrapper.get('select').setValue('unsuccessful')
    expect(wrapper.text()).toContain('当前没有未成功的步骤')
    expect(wrapper.text()).not.toContain('没有已完成的工具步骤')
    expect(button(wrapper, '展开全部').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('downloads every loaded step even with a filter and releases its blob URL', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const create = vi.fn((_blob: Blob) => 'blob:test')
    const revoke = vi.fn()
    vi.stubGlobal('URL', class extends URL { static createObjectURL = create; static revokeObjectURL = revoke })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const wrapper = mount(ExecutionSteps, { props: { task, steps, mock: true } })
    try {
      await wrapper.get('select').setValue('unsuccessful')
      await button(wrapper, '下载执行记录').trigger('click')
      expect(click).toHaveBeenCalledOnce()
      const blob = create.mock.calls[0]?.[0] as unknown as Blob
      const content = await new Promise<string>(resolve => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob)
      })
      expect(JSON.parse(content)).toMatchObject({ mock: true, complete_steps: true, steps })
      vi.runAllTimers()
      expect(revoke).toHaveBeenCalledWith('blob:test')
    } finally { wrapper.unmount(); vi.useRealTimers(); vi.unstubAllGlobals() }
  })
})

it('exports honest snapshot completeness and preserves code whitespace', () => {
  expect(createExecutionExport(task, steps, false).complete_steps).toBe(true)
  expect(createExecutionExport(task, steps.slice(0, 3), false).complete_steps).toBe(false)
  expect(createExecutionExport({ ...task, status: 'running' }, steps, true).complete_steps).toBe(false)
  expect(createExecutionExport(task, steps, false, false).complete_steps).toBe(false)
  const exported = JSON.parse(JSON.stringify(createExecutionExport(task, steps, true)))
  expect(exported.steps[1].result.output).toBe(steps[1]!.result.output)
  expect(exported.steps[3].call.arguments.content).toBe(steps[3]!.call.arguments.content)
})
