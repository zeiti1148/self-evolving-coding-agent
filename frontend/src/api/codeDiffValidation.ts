import type { CodeChangeInfo, CodeChangesIndex, CodeDiffFile, DiffHunk } from './contracts'

type Data = Record<string, unknown>
function check(value: unknown): asserts value { if (!value) throw new Error('Invalid code comparison response') }
function object(value: unknown): Data { check(value && typeof value === 'object' && !Array.isArray(value)); return value as Data }
const digest = (value: unknown) => value === null || typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const integer = (value: unknown, max = 8000) => Number.isInteger(value) && Number(value) >= 0 && Number(value) <= max
const lineNumber = (value: unknown) => value === null || integer(value, 4000) && Number(value) > 0
const text = (value: unknown) => typeof value === 'string' && value.trim().length > 0
const content = (value: unknown) => value === null || typeof value === 'string' && value.length <= 200000 && !value.includes('\0') && new TextEncoder().encode(value).length <= 200000
function change(value: unknown): CodeChangeInfo {
  const data = object(value)
  check(typeof data.path === 'string' && data.path.length <= 1000 && !/[\\:\u0000-\u001f]/.test(data.path)
    && data.path.split('/').every(part => part && part !== '.' && part !== '..'))
  check(['modified', 'added', 'deleted'].includes(String(data.change_type)) && digest(data.before_sha256) && digest(data.after_sha256))
  check(typeof data.available === 'boolean' && (data.available ? data.unavailable_reason === null : text(data.unavailable_reason)))
  if (data.available) {
    check(data.before_sha256 !== data.after_sha256)
    check(data.change_type === (data.before_sha256 === null ? 'added' : data.after_sha256 === null ? 'deleted' : 'modified'))
  }
  return data as unknown as CodeChangeInfo
}
function identity(data: Data, id: string) {
  check(/^[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}$/i.test(id) && data.task_id === id)
  check(data.mode === 'snapshot' || data.mode === 'recorded_writes')
  check(text(data.notice))
}
export function readCodeChanges(value: unknown, id: string): CodeChangesIndex {
  const data = object(value)
  identity(data, id)
  check(['succeeded', 'failed', 'stopped'].includes(String(data.status)))
  check(integer(data.omitted_files, 10000) && Array.isArray(data.files) && data.files.length <= 100)
  const files = data.files.map(change)
  check(new Set(files.map(file => file.path)).size === files.length)
  return { ...data, files } as unknown as CodeChangesIndex
}
export function readCodeDiff(value: unknown, id: string, path: string): CodeDiffFile {
  const data = object(value)
  identity(data, id)
  change(data)
  check(data.path === path && data.available === true && content(data.before_content) && content(data.after_content))
  check((data.before_content === null) === (data.before_sha256 === null) && (data.after_content === null) === (data.after_sha256 === null))
  check(integer(data.additions, 4000) && integer(data.deletions, 4000) && typeof data.format_only === 'boolean')
  check(Array.isArray(data.hunks) && data.hunks.length <= 4000)
  let additions = 0, deletions = 0, rows = 0
  const hunks = data.hunks.map(value => {
    const hunk = object(value)
    check(['old_start', 'old_lines', 'new_start', 'new_lines'].every(key => integer(hunk[key], 4000)))
    check(Array.isArray(hunk.lines))
    for (const value of hunk.lines) {
      const line = object(value)
      check(typeof line.content === 'string' && content(line.content) && lineNumber(line.old_line) && lineNumber(line.new_line))
      check(line.kind === 'context' || line.kind === 'added' || line.kind === 'removed')
      check(line.kind === 'added' ? line.old_line === null && line.new_line !== null
        : line.kind === 'removed' ? line.old_line !== null && line.new_line === null : line.old_line !== null && line.new_line !== null)
      if (line.kind === 'added') additions++
      if (line.kind === 'removed') deletions++
      rows++
    }
    return hunk as unknown as DiffHunk
  })
  check(rows <= 8000 && additions === data.additions && deletions === data.deletions)
  return { ...data, hunks } as unknown as CodeDiffFile
}
