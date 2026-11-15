import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import ExperiencesView from '../src/views/ExperiencesView.vue'
import { apiContextKey } from '../src/api/context'
import { createHttpApi } from '../src/api/http'
import { readMergeSuggestions } from '../src/api/validation'
import type { AgentApi, ExperienceKnowledge, MergeSuggestions } from '../src/api/contracts'

const a = '11111111-1111-4111-8111-111111111111'
const b = '22222222-2222-4222-8222-222222222222'
const c = '33333333-3333-4333-8333-333333333333'
const response: MergeSuggestions = { scanned_count: 2, eligible_count: 2, checked_pairs: 1, rejected: [], truncation_reasons: [],
  suggestions: [{ source_ids: [a, b], source_titles: ['输入校验 A', '输入校验 B'], language: 'python', scope: 'project', repository_id: 'repo-a',
    reasons: ['shared_rules'], shared_issue_count: 1, issue_count: 1, verification_count: 2, revision_warning: false }] }
const knowledge: ExperienceKnowledge = { task_summary: '合并预览', root_cause: '未校验', solution: '校验输入', verification: '测试通过', tags: [],
  error_type: null, source_task_id: null, merged_from: [a, b] }
function api(): AgentApi {
  return { createTask: vi.fn(), getTask: vi.fn(), getSteps: vi.fn(), listExperiences: vi.fn().mockResolvedValue([]),
    getMergeSuggestions: vi.fn().mockResolvedValue(structuredClone(response)),
    previewMerge: vi.fn().mockResolvedValue({ can_merge: true, conflicts: [], source_ids: [a, b], preview: knowledge }),
    mergeExperiences: vi.fn().mockResolvedValue({ ...knowledge, id: c, created_at: '2026-11-14T12:00:00Z' }) }
}
async function setup(client = api(), openSuggestions = true) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: ExperiencesView }] })
  await router.push('/'); await router.isReady()
  const wrapper = mount(ExperiencesView, { global: { plugins: [router], provide: {
    [apiContextKey as symbol]: { api: client, mock: false, projectId: 'test', projectLabel: 'test' },
  } } })
  await flushPromises()
  if (openSuggestions) {
    const disclosure = wrapper.get('[data-testid="manual-merge-suggestions"]')
    ;(disclosure.element as HTMLDetailsElement).open = true
    await disclosure.trigger('toggle'); await flushPromises()
  }
  return { wrapper, client }
}

describe('merge suggestions with explicit confirmation', () => {
  it('loads when the manual section opens, previews on selection and saves only on confirmation', async () => {
    const { wrapper, client } = await setup()
    try {
      expect(client.getMergeSuggestions).toHaveBeenCalledWith({}, expect.any(AbortSignal))
      expect(wrapper.text()).toContain('输入校验 A')
      expect(client.mergeExperiences).not.toHaveBeenCalled()
      await wrapper.get('[data-testid="suggestion-preview"]').trigger('click'); await flushPromises()
      expect(client.previewMerge).toHaveBeenCalledWith([a, b], expect.any(AbortSignal))
      expect(client.mergeExperiences).not.toHaveBeenCalled()
      await wrapper.get('[data-testid="confirm-merge"]').trigger('click'); await flushPromises()
      expect(client.mergeExperiences).toHaveBeenCalledTimes(1)
      expect(client.getMergeSuggestions).toHaveBeenCalledTimes(2)
    } finally { wrapper.unmount() }
  })

  it('keeps the searchable catalog as default and loads suggestions only on explicit expansion', async () => {
    const client = api()
    client.getExperienceLibrary = vi.fn().mockResolvedValue({ items: [], history_scan: null,
      summary: { original_records: 0, knowledge_cards: 0, active: 0, paused: 0, unverified: 0, merged: 0, pending_reviews: 0 } })
    const { wrapper } = await setup(client, false)
    try {
      expect(wrapper.find('[aria-label="搜索经验"]').exists()).toBe(true)
      expect(wrapper.find('[data-testid="manual-merge-suggestions"]').exists()).toBe(false)
      expect(client.getMergeSuggestions).not.toHaveBeenCalled()
      await wrapper.findAll('button').find(button => button.text() === '原始记录')!.trigger('click')
      const disclosure = wrapper.get('[data-testid="manual-merge-suggestions"]')
      expect((disclosure.element as HTMLDetailsElement).open).toBe(false)
      expect(client.getMergeSuggestions).not.toHaveBeenCalled()
      ;(disclosure.element as HTMLDetailsElement).open = true
      await disclosure.trigger('toggle'); await flushPromises()
      expect(client.getMergeSuggestions).toHaveBeenCalledTimes(1)
      await wrapper.findAll('button').find(button => button.text() === '经验检索')!.trigger('click')
      expect(vi.mocked(client.getMergeSuggestions!).mock.calls[0]![1]!.aborted).toBe(true)
      await wrapper.findAll('button').find(button => button.text() === '原始记录')!.trigger('click')
      expect(wrapper.find('[data-testid="suggestion-preview"]').exists()).toBe(false)
    } finally { wrapper.unmount() }
  })

  it.each([['go', 'Go'], ['cpp', 'C++']] as const)('displays %s suggestions with the correct language label', async (language, label) => {
    const client = api()
    vi.mocked(client.getMergeSuggestions!).mockResolvedValue({ ...response, suggestions: [{ ...response.suggestions[0]!, language }] })
    const { wrapper } = await setup(client)
    try { expect(wrapper.get('.suggestion-item').text()).toContain(`${label} · repo-a`) }
    finally { wrapper.unmount() }
  })

  it('drops a stale response when applied filters change', async () => {
    const client = api()
    let resolveOld!: (value: MergeSuggestions) => void
    vi.mocked(client.getMergeSuggestions!).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    const { wrapper } = await setup(client)
    try {
      await wrapper.get('#experience-language').setValue('python')
      await wrapper.get('form').trigger('submit'); await flushPromises()
      expect(client.getMergeSuggestions).toHaveBeenLastCalledWith({ language: 'python' }, expect.any(AbortSignal))
      resolveOld({ ...response, suggestions: [{ ...response.suggestions[0]!, source_titles: ['过期标题', '旧内容'] }] })
      await flushPromises()
      expect(wrapper.text()).not.toContain('过期标题')
      expect(wrapper.text()).toContain('输入校验 A')
    } finally { wrapper.unmount() }
  })

  it('explains no suggestions and truncation', async () => {
    const client = api()
    vi.mocked(client.getMergeSuggestions!).mockResolvedValue({ ...response, suggestions: [],
      rejected: [{ reason: 'conditions', count: 1 }], truncation_reasons: ['candidate_limit'] })
    const { wrapper } = await setup(client)
    try {
      expect(wrapper.text()).toContain('没有兼容且相关的合并建议')
      expect(wrapper.text()).toContain('适用条件不同')
      expect(wrapper.text()).toContain('部分结果')
      expect(wrapper.find('form').exists()).toBe(true)
    } finally { wrapper.unmount() }
  })

  it('supports independent retry and aborts on unmount', async () => {
    const client = api()
    vi.mocked(client.getMergeSuggestions!).mockRejectedValueOnce(new Error('建议暂不可用'))
    const { wrapper } = await setup(client)
    try {
      expect(wrapper.get('[data-testid="suggestions-error"]').text()).toContain('建议暂不可用')
      await wrapper.get('[data-testid="refresh-suggestions"]').trigger('click'); await flushPromises()
      expect(wrapper.text()).toContain('输入校验 A')
    } finally { wrapper.unmount() }
    expect(vi.mocked(client.getMergeSuggestions!).mock.calls.at(-1)![1]!.aborted).toBe(true)
  })

  it('does not save after an expired preview', async () => {
    const client = api()
    vi.mocked(client.previewMerge!).mockResolvedValue({ can_merge: false, conflicts: ['来源已被替代'], source_ids: [a, b], preview: null })
    const { wrapper } = await setup(client)
    try {
      await wrapper.get('[data-testid="suggestion-preview"]').trigger('click'); await flushPromises()
      expect(wrapper.text()).toContain('来源已被替代')
      expect(wrapper.get('[data-testid="confirm-merge"]').attributes('disabled')).toBeDefined()
      expect(client.mergeExperiences).not.toHaveBeenCalled()
    } finally { wrapper.unmount() }
  })
})

describe('merge suggestions HTTP contract', () => {
  it.each(['java', 'python', 'go', 'cpp'] as const)('accepts %s suggestions', language => {
    const payload = { ...response, suggestions: [{ ...response.suggestions[0]!, language }] }
    expect(readMergeSuggestions(payload)).toEqual(payload)
  })
  it('sends supported filters and accepts a valid response', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(response), { status: 200 }))
    const client = createHttpApi('/bridge', fetcher)
    expect(await client.getMergeSuggestions!({ language: 'python', repository_id: 'repo a', active_only: false })).toEqual(response)
    const url = String(fetcher.mock.calls[0]![0])
    expect(url).toContain('/api/experiences/merge-suggestions?')
    expect(url).toContain('language=python')
    expect(url).toContain('repository_id=repo+a')
    expect(url).not.toContain('active_only')
    expect(fetcher.mock.calls[0]![1]?.method).toBe('GET')
  })

  it.each([
    { suggestions: [{ ...response.suggestions[0], source_ids: [a, a] }] },
    { suggestions: [{ ...response.suggestions[0], source_ids: [a, 'invalid'] }] },
    { suggestions: [{ ...response.suggestions[0], source_titles: ['missing title'] }] },
    { suggestions: [{ ...response.suggestions[0], reasons: ['guessed_similarity'] }] },
    { suggestions: [{ ...response.suggestions[0], verification_count: -1 }] },
    { eligible_count: 201 }, { scanned_count: true }, { checked_pairs: 0.5 },
    { truncation_reasons: ['unknown'] }, { rejected: [{ reason: 'guessed', count: 1 }] },
  ])('rejects malformed payload %#', changes => {
    expect(() => readMergeSuggestions({ ...response, ...changes })).toThrow()
  })

  it('maps malformed responses to a contract error', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('{}', { status: 200 }))
    await expect(createHttpApi('', fetcher).getMergeSuggestions!()).rejects.toMatchObject({ status: 502 })
  })
})
