<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useApiContext } from '../api/context'
import type { CodeChangesIndex, CodeDiffFile } from '../api/contracts'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'

const route = useRoute(), router = useRouter()
const { api, mock } = useApiContext()
const taskId = computed(() => String(route.params.taskId ?? ''))
const index = shallowRef<CodeChangesIndex | null>(null), file = shallowRef<CodeDiffFile | null>(null)
const loading = ref(false), error = ref(''), copyMessage = ref('')
const wrapLines = ref(true)
const labels = { modified: '已修改', added: '新增', deleted: '删除' }
const view = computed(() => route.query.view === 'before' || route.query.view === 'after' ? route.query.view : 'diff')
const originalContent = computed(() => view.value === 'before' ? file.value?.before_content : file.value?.after_content)
const lineNumbers = computed(() => Array.from({ length: (originalContent.value ?? '').split(/\r\n|\r|\n/).length }, (_, i) => i + 1).join('\n'))
const location = (path: string, selectedView = view.value) => ({ path: `/tasks/${taskId.value}/changes`, query: { file: path, ...(selectedView === 'diff' ? {} : { view: selectedView }) } })
const directLink = computed(() => file.value ? new URL(router.resolve(location(file.value.path)).href, window.location.href).href : '')
let controller: AbortController | undefined, generation = 0

async function load(refresh = false) {
  const current = ++generation
  controller?.abort(); controller = new AbortController()
  const signal = controller.signal, id = taskId.value, requested = route.query.file
  file.value = null; error.value = ''; copyMessage.value = ''; loading.value = true
  if (refresh || index.value?.task_id !== id) index.value = null
  try {
    if (mock || !api.listCodeChanges || !api.getCodeDiff) throw new Error('当前模式没有真实代码对比，请连接后端后打开实际任务。')
    if (requested !== undefined && (typeof requested !== 'string' || !requested)) throw new Error('文件链接不完整，请从任务页面重新打开。')
    if (!index.value) {
      const listing = await api.listCodeChanges(id, signal)
      if (current !== generation) return
      index.value = listing
    }
    const path = requested ?? index.value.files[0]?.path
    if (path === undefined) return
    const selected = index.value.files.find(item => item.path === path)
    if (!selected) throw new Error('链接中的文件不在该任务记录的代码改动中。')
    if (!selected.available) throw new Error(selected.unavailable_reason ?? '该文件缺少可核对的历史内容。')
    const data = await api.getCodeDiff(id, path, signal)
    if (current !== generation) return
    if (data.before_sha256 !== selected.before_sha256 || data.after_sha256 !== selected.after_sha256) throw new Error('文件版本信息已变化，请重新读取。')
    file.value = data
  } catch (cause) {
    if (current === generation && !signal.aborted) error.value = cause instanceof Error ? cause.message : '代码对比读取失败，请重试。'
  } finally { if (current === generation) loading.value = false }
}
async function copyLink() {
  const current = generation, link = directLink.value
  try { await navigator.clipboard.writeText(link); if (current === generation && link === directLink.value) copyMessage.value = '链接已复制' }
  catch { if (current === generation) copyMessage.value = '无法自动复制，请选中下方链接手动复制。' }
}
function changeView(next: 'diff' | 'before' | 'after') { if (file.value) { copyMessage.value = ''; void router.replace(location(file.value.path, next)) } }
function format(content: string | null) {
  if (content === null) return '文件不存在'
  if (!content) return '空文件'
  const kind = content.includes('\r\n') ? (content.replaceAll('\r\n', '').includes('\n') ? '混合换行' : 'CRLF 换行') : 'LF 换行'
  return `${kind} · ${/[\r\n]$/.test(content) ? '有末尾换行' : '无末尾换行'}`
}
watch([taskId, () => route.query.file], () => { void load() }, { immediate: true })
onBeforeUnmount(() => { ++generation; controller?.abort() })
</script>

<template>
  <div class="page-heading"><div><h1>修复前后代码对比</h1><p>看清每个文件具体改了什么。</p></div><RouterLink :to="`/tasks/${taskId}`" class="button secondary">返回任务</RouterLink></div>
  <div class="diff-task-info"><span class="source-task-id text-muted">任务 <code>{{ taskId }}</code></span><StatusBadge v-if="index" :status="index.status" /><RouterLink :to="`/tasks/${taskId}/tests`">查看测试源码</RouterLink></div>
  <p v-if="index" class="diff-notice" :class="index.mode === 'recorded_writes' ? 'warning-notice' : 'field-hint'">{{ index.notice }}</p>
  <p v-if="index?.omitted_files" class="warning-notice">还有 {{ index.omitted_files }} 个文件超过本页展示数量限制。</p>
  <div v-if="error" class="error-notice error-with-action" role="alert"><span>{{ error }}</span><button class="button secondary small" type="button" data-retry @click="load(true)">重新读取</button></div>
  <p v-if="loading" class="panel empty-state" role="status">正在读取代码对比…</p>
  <div v-else-if="index && !index.files.length && !error" class="panel empty-state"><AppIcon name="code" :size="28" /><h2>没有可展示的代码改动</h2><p>当前支持任务记录中的 Java 和 Python 业务源码。</p></div>
  <div v-if="index?.files.length" class="source-layout">
    <nav class="panel source-file-panel" aria-label="修改的文件"><div class="panel-heading"><h2>修改文件 <span class="count-chip">{{ index.files.length }}</span></h2></div>
      <ul class="source-file-list"><li v-for="item in index.files" :key="item.path"><RouterLink :to="location(item.path)" :class="{ selected: file?.path === item.path }" :aria-current="file?.path === item.path ? 'page' : undefined"><AppIcon name="file" :size="17" /><span>{{ item.path }}<small class="diff-file-kind">{{ item.available ? labels[item.change_type] : '无法核对' }}</small></span></RouterLink></li></ul>
    </nav>
    <section v-if="file" class="panel source-reader diff-reader" aria-labelledby="diff-title">
      <div class="source-reader-heading"><div><h2 id="diff-title" class="source-filename">{{ file.path }}</h2><p class="diff-counts"><span class="diff-added-count">+ 新增 {{ file.additions }} 行</span><span class="diff-removed-count">− 删除 {{ file.deletions }} 行</span></p></div><button class="button secondary small" type="button" data-copy-diff-link @click="copyLink">复制对比链接</button></div>
      <div class="source-link-bar"><label for="diff-link">直接访问链接</label><input id="diff-link" class="source-link-input" :value="directLink" readonly @focus="($event.target as HTMLInputElement).select()" /><p v-if="copyMessage" class="field-hint" role="status">{{ copyMessage }}</p></div>
      <div class="diff-view-picker" role="group" aria-label="代码查看方式"><button v-for="item in [{ id: 'diff' as const, text: '只看改动' }, { id: 'before' as const, text: '修复前原文' }, { id: 'after' as const, text: '修复后原文' }]" :key="item.id" class="button secondary small" type="button" :data-view="item.id" :aria-pressed="view === item.id" @click="changeView(item.id)">{{ item.text }}</button></div>
      <label v-if="view === 'diff'" class="diff-wrap-control"><input v-model="wrapLines" type="checkbox" />长行自动折行</label>
      <p class="source-verification">修复前：{{ format(file.before_content) }}；修复后：{{ format(file.after_content) }}。</p>
      <p v-if="file.format_only" class="diff-format-note warning-notice">仅换行格式或末尾换行发生变化，可切换原文查看完整内容。</p>
      <div v-if="view === 'diff'" class="diff-scroll" tabindex="0" role="region" aria-label="代码增删对比">
        <table v-if="file.hunks.length" class="diff-table" :class="{ wrapped: wrapLines }"><colgroup><col style="width:48px" /><col style="width:48px" /><col style="width:44px" /><col /></colgroup><thead><tr><th scope="col">原行</th><th scope="col">新行</th><th scope="col">变更</th><th scope="col">代码</th></tr></thead>
          <tbody v-for="(hunk, i) in file.hunks" :key="i"><tr class="diff-hunk"><td colspan="4">修复前第 {{ hunk.old_start }} 行起 · 修复后第 {{ hunk.new_start }} 行起</td></tr>
            <tr v-for="(line, row) in hunk.lines" :key="row" class="diff-row" :class="line.kind"><td class="diff-line-number">{{ line.old_line }}</td><td class="diff-line-number">{{ line.new_line }}</td><td class="diff-marker" :aria-label="line.kind === 'added' ? '新增' : line.kind === 'removed' ? '删除' : '未改动'">{{ line.kind === 'added' ? '+' : line.kind === 'removed' ? '−' : ' ' }}</td><td class="diff-text">{{ line.content }}</td></tr>
          </tbody>
        </table><p v-else class="source-verification">{{ file.format_only ? '没有文字行增删。' : file.change_type === 'added' ? '新增了一个空文件。' : '删除了一个空文件。' }}</p>
      </div>
      <p v-else-if="originalContent === null" class="panel empty-state">该文件在{{ view === 'before' ? '修复前' : '修复后' }}不存在。</p>
      <div v-else class="source-code-block" tabindex="0" role="region" :aria-label="view === 'before' ? '修复前源码' : '修复后源码'"><pre class="source-line-numbers" aria-hidden="true">{{ lineNumbers }}</pre><pre class="source-content"><code>{{ originalContent }}</code></pre></div>
    </section>
  </div>
</template>
