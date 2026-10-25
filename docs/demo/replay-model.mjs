// Pure replay logic, also exercised by node --test. No DOM or network access.
export function pathKey(value) {
  return typeof value === 'string' ? value.replaceAll('\\', '/').replace(/^(\.\/)+/, '') : ''
}

export function fileState(recording, throughStep) {
  const files = { ...recording.initial_files }
  for (const step of recording.steps.slice(0, throughStep)) {
    if (step.call.name === 'write_file' && step.result.success && typeof step.call.arguments.content === 'string') {
      files[pathKey(step.call.arguments.path)] = step.call.arguments.content
    }
  }
  return files
}

export function writeComparison(recording, index) {
  const step = recording.steps[index]
  const name = pathKey(step.call.arguments.path)
  const before = fileState(recording, index)[name]
  return { path: name, before: before ?? null, after: String(step.call.arguments.content ?? ''), applied: step.result.success }
}

// Overall repair: original source versus the last successful write to each file.
export function repairEdits(recording) {
  const edits = new Map()
  recording.steps.forEach((step, index) => {
    if (step.call.name !== 'write_file' || !step.result.success || typeof step.call.arguments.content !== 'string') return
    const path = pathKey(step.call.arguments.path)
    const previous = edits.get(path)
    edits.set(path, { path, before: recording.initial_files[path] ?? '', after: step.call.arguments.content,
      steps: [...(previous?.steps ?? []), index + 1] })
  })
  return [...edits.values()]
}

export function diffLines(before, after) {
  const left = before.replaceAll('\r\n', '\n').split('\n')
  const right = after.replaceAll('\r\n', '\n').split('\n')
  // Avoid quadratic work for imported large files. Display complete text instead.
  if (left.length * right.length > 160000) return null
  const grid = Array.from({ length: left.length + 1 }, () => new Uint32Array(right.length + 1))
  for (let i = left.length - 1; i >= 0; i--) for (let j = right.length - 1; j >= 0; j--) {
    grid[i][j] = left[i] === right[j] ? grid[i + 1][j + 1] + 1 : Math.max(grid[i + 1][j], grid[i][j + 1])
  }
  const rows = []
  let i = 0, j = 0
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      rows.push({ kind: 'same', oldLine: ++i, newLine: ++j, text: left[i - 1] })
    } else if (i < left.length && (j === right.length || grid[i + 1][j] >= grid[i][j + 1])) {
      rows.push({ kind: 'removed', oldLine: ++i, newLine: null, text: left[i - 1] })
    } else {
      rows.push({ kind: 'added', oldLine: null, newLine: ++j, text: right[j - 1] })
    }
  }
  return rows
}

export function finalTest(recording) {
  return [...recording.steps].reverse().find(step => step.call.name === 'run_tests' && step.result.metadata.final_verification === true) ?? null
}

export function frame(recording, cursor) {
  if (cursor === 0) return { kind: 'baseline', title: '修复前：先复现测试失败', result: recording.baseline }
  if (cursor > recording.steps.length) return { kind: 'summary', title: '独立终验与经验记录' }
  const step = recording.steps[cursor - 1]
  const title = step.result.metadata.final_verification ? '程序执行独立最终测试' : ({
    list_files: 'Agent 查看项目结构', read_file: 'Agent 阅读源代码与验收条件',
    write_file: 'Agent 写入代码修改', run_tests: 'Agent 调用 Maven 测试', run_command: 'Agent 执行受控命令',
  }[step.call.name] ?? step.call.name)
  return { kind: step.call.name, title, step, result: step.result }
}
