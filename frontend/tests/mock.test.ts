import { beforeEach, describe, expect, it } from 'vitest'
import { createMockApi } from '../src/api/mock'

const request = { task_description: '修复 GreetingService 的 null 输入', source_project: 'java-null-greeting', max_steps: 20 }
beforeEach(() => sessionStorage.clear())

describe('mock lifecycle matches the shared models', () => {
  it('starts pending, exposes completed steps while running, and saves one experience', async () => {
    let now = Date.parse('2026-10-20T00:00:00Z')
    const api = createMockApi({ now: () => now, stepMs: 100 })
    const task = await api.createTask(request)
    expect(task.status).toBe('pending')
    expect((await api.getTask(task.task_id)).result).toBeNull()
    now += 300
    const running = await api.getTask(task.task_id)
    expect(running.status).toBe('running')
    expect((await api.getSteps(task.task_id)).steps.length).toBeGreaterThan(0)
    now += 5000
    const done = await api.getTask(task.task_id)
    expect(done.result).toMatchObject({ status: 'succeeded', tests_passed: true, repair_succeeded: true, experience_saved: true })
    const steps = (await api.getSteps(task.task_id)).steps
    expect(steps.at(-1)?.call.name).toBe('run_tests')
    expect(steps.at(-1)?.result.success).toBe(true)
    expect(steps.every(step => step.call.id === step.result.call_id && step.call.name === step.result.tool_name)).toBe(true)
    const source = steps.find(step => step.call.name === 'read_file')!.result.output
    const replacement = steps.find(step => step.call.name === 'write_file')!.call.arguments.content
    for (const file of [source, replacement]) {
      expect(file).toContain('package dev.selfevolving.benchmark;')
      expect(file).toContain('public class GreetingService')
    }
    await api.getTask(task.task_id)
    expect((await api.listExperiences(100, 0)).filter(item => item.source_task_id === task.task_id)).toHaveLength(1)
  })

  it.each([
    ['save-failure', 'succeeded', true, false],
    ['tests-failed', 'failed', false, null],
    ['stopped', 'stopped', null, null],
  ] as const)('represents %s without conflating task, test and experience outcomes', async (scenario, status, tests, saved) => {
    let now = 100000
    const api = createMockApi({ now: () => now, stepMs: 1 })
    api.setScenario(scenario)
    const created = await api.createTask(request)
    now += 100
    const response = await api.getTask(created.task_id)
    expect(response.result).toMatchObject({ status, tests_passed: tests, experience_saved: saved })
    if (saved === false) expect(response.result?.experience_save_error).toBeTruthy()
  })

  it('represents scheduling errors without inventing an AgentResult', async () => {
    let now = 100000
    const api = createMockApi({ now: () => now, stepMs: 1 })
    api.setScenario('schedule-error')
    const task = await api.createTask(request)
    now += 100
    expect(await api.getTask(task.task_id)).toMatchObject({ status: 'failed', result: null, error_message: expect.any(String) })
  })

  it('survives reinitialization in the same tab and handles missing tasks', async () => {
    const api = createMockApi()
    const created = await api.createTask(request)
    const reloaded = createMockApi()
    expect(reloaded.recoveryNotice).toBeNull()
    expect((await reloaded.getTask(created.task_id)).task_id).toBe(created.task_id)
    await expect(reloaded.getTask('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')).rejects.toMatchObject({ status: 404 })
  })

  it('skips damaged and duplicate records while preserving valid tasks and experiences', async () => {
    const api = createMockApi({ now: () => 100000, stepMs: 1 })
    const created = await api.createTask(request)
    const saved = JSON.parse(sessionStorage.getItem('coding-agent-demo-v1')!)
    saved.tasks.push(null, saved.tasks[0], { ...saved.tasks[0], id: '22222222-2222-4222-8222-222222222222', stepMs: 0 })
    saved.experiences.push(null, { id: 'broken' }, saved.experiences[0])
    sessionStorage.setItem('coding-agent-demo-v1', JSON.stringify(saved))
    const restored = createMockApi({ now: () => 100000 })
    expect(restored.recoveryNotice).toContain('异常记录')
    expect((await restored.getTask(created.task_id)).task_id).toBe(created.task_id)
    expect(await restored.listExperiences(100)).toHaveLength(7)
    await expect(restored.getTask('22222222-2222-4222-8222-222222222222')).rejects.toMatchObject({ status: 404 })
  })

  it.each(['{invalid', 'null', '{"version":2,"tasks":[],"experiences":[]}'])('recovers a malformed root snapshot: %s', async raw => {
    sessionStorage.setItem('coding-agent-demo-v1', raw)
    const restored = createMockApi()
    expect(restored.recoveryNotice).toContain('重建')
    expect(await restored.listExperiences()).toHaveLength(7)
    expect((await restored.createTask(request)).status).toBe('pending')
  })

  it('does not restore corrupted test evidence as a successful demo task', async () => {
    const api = createMockApi({ now: () => 100000, stepMs: 1 })
    const created = await api.createTask(request)
    const saved = JSON.parse(sessionStorage.getItem('coding-agent-demo-v1')!)
    saved.tasks[0].steps.pop()
    sessionStorage.setItem('coding-agent-demo-v1', JSON.stringify(saved))
    const restored = createMockApi({ now: () => 200000 })
    await expect(restored.getTask(created.task_id)).rejects.toMatchObject({ status: 404 })
    expect(restored.recoveryNotice).toBeTruthy()
  })

  it('paginates experience arrays and rejects unregistered projects', async () => {
    const api = createMockApi()
    const first = await api.listExperiences(5, 0)
    const second = await api.listExperiences(5, 5)
    expect(first).toHaveLength(5)
    expect(second.length).toBeGreaterThan(0)
    expect(second.some(item => first.some(other => item.id === other.id))).toBe(false)
    await expect(api.createTask({ ...request, source_project: 'C:/private/project' })).rejects.toMatchObject({ status: 400 })
  })
})
