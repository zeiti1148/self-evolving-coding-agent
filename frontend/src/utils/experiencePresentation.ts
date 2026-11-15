import type { ConflictTestRun, Experience, KnowledgeCard } from '../api/contracts'

// Display excerpts only. Persisted wording and evidence remain untouched.
export function excerpt(value: string, limit = 72): string {
  const text = value.replace(/\s+/g, ' ').trim()
  const characters = Array.from(text)
  return characters.length > limit ? characters.slice(0, limit).join('') + '…' : text
}

export function experienceTitle(source: Pick<Experience, 'task_summary'>): string {
  const text = source.task_summary.trim()
  const first = text.split(/[：；。\n]/)[0] || text
  return excerpt(first.length >= 8 ? first : text, 62)
}

export function repositoryLabel(source: Pick<Experience, 'scope' | 'repository_id'>): string {
  if (source.scope !== 'project') return '通用经验'
  if (source.repository_id?.startsWith('uploaded/')) return `上传项目 · ${source.repository_id.slice(9, 17)}`
  return excerpt(source.repository_id || '仓库专属', 32)
}

export function knowledgeTitle(card: KnowledgeCard): string {
  if (card.kind === 'archive' && card.sources[0]) return experienceTitle(card.sources[0])
  if (card.kind === 'principle') return excerpt(card.title, 62)
  const firstClause = card.summary.replace(/`([^`]+)`/g, '$1').split(/[，；。\n]/)[0]
  return excerpt(firstClause || card.title, 58)
}

export function testCell(runs: ConflictTestRun[]) {
  if (!runs.length) return { state: 'pending', label: '尚无结果', detail: '等待验证' }
  const first = runs[0]!
  const same = runs.every(run => run.outcome === first.outcome && run.tests === first.tests
    && run.failures === first.failures && run.errors === first.errors && run.skipped === first.skipped
    && run.test_identity === first.test_identity
    && [...run.failed_tests].sort().join('\n') === [...first.failed_tests].sort().join('\n'))
  if (!same) return { state: 'mixed', label: '结果不一致', detail: `${runs.length} 次验证，请查看明细` }
  const repeated = runs.length > 1 ? `${runs.length} 次均` : ''
  if (first.outcome === 'passed' && !first.errors && !first.failures && !first.skipped)
    return { state: 'passed', label: `${repeated}通过`, detail: `${first.tests} / ${first.tests} 项通过` }
  if (first.outcome === 'failed')
    return { state: 'failed', label: `${repeated}失败`, detail: `${first.failures} / ${first.tests} 项失败` }
  return { state: 'mixed', label: '需核对', detail: '存在异常或未完成的测试' }
}
