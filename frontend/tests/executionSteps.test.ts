import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ExecutionSteps from '../src/components/ExecutionSteps.vue'
import { makeSteps } from '../src/api/fixtures'
import type { TaskStatusResponse } from '../src/api/contracts'
import { createExecutionExport } from '../src/features/taskExport'

const steps = makeSteps(100000, 1, 'success')
const task: TaskStatusResponse = {
  task_id: '11111111-1111-4111-8111-111111111111', status: 'failed', steps_completed: 5,
  created_at: new Date(100000).toISOString(), started_at: new Date(100001).toISOString(),
  finished_at: new Date(100007).toISOString(), result: null, error_message: 'fixture',
}
const button = (wrapper: ReturnType<typeof mount>, label: string) => wrapper.findAll('button').find(item => item.text() === label)!

describe('execution log controls', () => {
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
    const wrapper = mount(ExecutionSteps, { props: { task, steps: steps.slice(0, 2), mock: false } })
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
