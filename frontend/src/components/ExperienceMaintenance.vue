<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue'
import type { ConflictStatus, ConflictResolution, ExperienceConflict, ExperienceFilters, MaintenanceJob, ReviewEvent } from '../api/contracts'
import { useApiContext } from '../api/context'
import { ApiError } from '../api/http'
import ExperienceKnowledge from './ExperienceKnowledge.vue'
import ConflictAutomation from './ConflictAutomation.vue'
import ExperienceText from './ExperienceText.vue'
import { experienceTitle } from '../utils/experiencePresentation'

const props = defineProps<{ filters: ExperienceFilters; disabled?: boolean }>()
const emit = defineEmits<{ changed: []; busy: [value: boolean] }>()
const { api } = useApiContext()
const supported = computed(() => !!api.consolidateExperiences && !!api.listConflicts && !!api.resolveConflict)
const status = ref<ConflictStatus>('pending')
const rows = shallowRef<ExperienceConflict[]>([])
const loading = ref(false), writing = ref(false), error = ref(''), notice = ref('')
const offset = ref(0), hasNext = ref(false)
const nextCursor = ref<string | null>(null)
const jobs = ref<MaintenanceJob[]>([])
const histories = reactive<Record<string, ReviewEvent[]>>({})
const reopenNotes = reactive<Record<string, string>>({})
let jobsTimer: ReturnType<typeof setTimeout> | undefined
let jobsController: AbortController | undefined
const drafts = reactive<Record<string, { choice: string; note: string }>>({})
const stale = reactive(new Set<string>())
const busy = computed(() => writing.value || props.disabled)
const canConsolidate = computed(() => props.filters.language && props.filters.repository_id && props.filters.scope !== 'global')
let version = 0
let controller: AbortController | undefined
let writeController: AbortController | undefined
let disposed = false

async function load(nextOffset = 0) {
  if (!api.listConflicts) return
  controller?.abort()
  const active = new AbortController(); controller = active
  const current = ++version
  loading.value = true; error.value = ''
  try {
    const data = await api.listConflicts({ language: props.filters.language, repository_id: props.filters.repository_id,
      status: status.value, limit: 6, offset: nextOffset }, active.signal)
    if (active.signal.aborted || current !== version) return
    rows.value = data.slice(0, 5); hasNext.value = data.length > 5; offset.value = nextOffset
    void loadJobs()
    for (const row of rows.value) { drafts[row.id] ??= { choice: '', note: '' }; stale.delete(row.id) }
  } catch (cause) {
    if (!active.signal.aborted && current === version) error.value = cause instanceof Error ? cause.message : '读取冲突失败。'
  } finally { if (current === version) loading.value = false }
}
async function write(action: (signal: AbortSignal) => Promise<string>, conflictId?: string) {
  if (busy.value) return
  writing.value = true; emit('busy', true); error.value = ''; notice.value = ''
  writeController = new AbortController()
  try {
    const message = await action(writeController.signal)
    if (disposed) return
    notice.value = message
    emit('changed')
    await load()
  } catch (cause) {
    if (disposed) return
    error.value = cause instanceof Error ? cause.message : '保存失败，请重试。'
    if (conflictId && cause instanceof ApiError && cause.status === 409) {
      stale.add(conflictId)
      error.value += ' 当前处理依据已保留，请刷新冲突后重新核对。'
    }
  } finally { writing.value = false; if (!disposed) emit('busy', false) }
}
async function loadJobs() {
  if (!api.listMaintenanceJobs || disposed) return
  if (jobsTimer) clearTimeout(jobsTimer)
  jobsController?.abort(); jobsController = new AbortController(); const active = jobsController
  try {
    const result = await api.listMaintenanceJobs(active.signal, { language: props.filters.language, repository_id: props.filters.repository_id })
    if (active.signal.aborted || disposed) return
    jobs.value = result
    if (result.some(job => job.state === 'pending' || job.state === 'running')) jobsTimer = setTimeout(loadJobs, 2000)
  } catch { /* Review remains usable if optional progress is temporarily unavailable. */ }
}
function retryJob(id: string) {
  if (!api.retryMaintenanceJob) return
  void write(async signal => { await api.retryMaintenanceJob!(id, signal); await loadJobs(); return '已重新排队，原始经验保留。' })
}
async function history(row: ExperienceConflict) {
  if (!api.getReviewHistory) return
  try { histories[row.id] = await api.getReviewHistory(row.id, controller?.signal) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '读取决定历史失败。' }
}
function reopen(row: ExperienceConflict) {
  const note = reopenNotes[row.id]?.trim()
  if (!api.reopenConflict || !note || stale.has(row.id)) return
  void write(async signal => {
    await api.reopenConflict!(row.id, row.version, note, signal)
    delete histories[row.id]; delete reopenNotes[row.id]
    return '已撤销当前决定并重新审阅；相关规则会重新检查。'
  }, row.id)
}
function consolidate() {
  if (!canConsolidate.value || !api.consolidateExperiences) return
  const language = props.filters.language!, repository = props.filters.repository_id!
  void write(async signal => {
    const result = await api.consolidateExperiences!(language, repository, signal, nextCursor.value ?? undefined)
    nextCursor.value = result.next_cursor ?? null
    return `累计比较 ${result.examined} 对规则，${result.merged_ids.length} 项规则具有多个来源，发现 ${result.conflict_ids.length} 项待处理冲突。`
      + (result.deferred_groups ? ` ${result.deferred_groups} 个分组超出合并限制，已保留原记录，请手动审阅。` : '')
      + (result.next_cursor ? ' 还有分组未检查，请点击继续整理。' : '')
      + ` 待整理 ${result.pending_jobs ?? 0} 项，失败 ${result.failed_jobs ?? 0} 项，关系待确认 ${result.unknown_pairs ?? 0} 项。`
  })
}
function resolve(row: ExperienceConflict) {
  const draft = drafts[row.id]
  if (!api.resolveConflict || !draft?.choice || !draft.note.trim() || stale.has(row.id)) return
  void write(async signal => {
    await api.resolveConflict!(row.id, { version: row.version,
      resolution: ['keep_separate', 'confirm_equivalent', 'confirm_compatible'].includes(draft.choice) ? draft.choice as ConflictResolution['resolution'] : 'prefer_source',
      preferred_source_id: ['keep_separate', 'confirm_equivalent', 'confirm_compatible'].includes(draft.choice) ? null : draft.choice, note: draft.note.trim() }, signal)
    delete drafts[row.id]
    return '冲突处理已保存，原始记录与处理依据均已保留。采纳的经验仍需满足其他复用条件。'
  }, row.id)
}
watch(() => props.filters, () => { nextCursor.value = null; notice.value = ''; void load() }, { immediate: true, deep: true })
watch(status, () => { notice.value = ''; void load() })
onBeforeUnmount(() => { disposed = true; ++version; controller?.abort(); writeController?.abort(); jobsController?.abort(); if (jobsTimer) clearTimeout(jobsTimer) })
</script>

<template>
  <section v-if="supported && filters.scope !== 'global'" class="experience-maintenance" aria-labelledby="maintenance-title">
    <div class="maintenance-heading"><div><h2 id="maintenance-title">冲突处理与整理状态</h2>
      <p>新保存的经验会自动检查。原始记录先保存，后台检查后才参与复用。规则关系分为已确认冲突和待确认建议。</p></div>
      <button class="button secondary small" type="button" data-testid="consolidate-experiences"
        :disabled="busy || !canConsolidate" @click="consolidate">{{ writing ? '正在保存…' : nextCursor ? '继续整理本仓库' : '自动整理本仓库' }}</button>
    </div>
    <p v-if="!canConsolidate" class="text-muted">在上方选择语言、填写仓库 ID 并应用筛选，即可整理历史经验。</p>
    <details v-if="api.listMaintenanceJobs" class="conflict-card"><summary>后台整理进度 · 当前页 {{ jobs.filter(j => j.state === 'pending' || j.state === 'running').length }} 项待完成</summary>
      <p class="field-hint">最多显示 50 项，优先展示失败及待完成任务。</p>
      <button class="text-button" type="button" @click="loadJobs">刷新进度</button>
      <ul class="knowledge-list"><li v-for="job in jobs" :key="job.id">{{ ({ pending: '等待整理', running: '正在检查', succeeded: '检查完成', failed: '处理失败' })[job.state] }} · 已比较 {{ job.examined }} 对规则 · 重试 {{ job.attempts }} 次
        <button v-if="job.state === 'failed' && api.retryMaintenanceJob" class="text-button" type="button" :disabled="busy" @click="retryJob(job.id)">重试整理</button>
      </li></ul>
    </details>
    <div class="maintenance-controls">
      <label for="conflict-status">处理状态</label>
      <select id="conflict-status" v-model="status" :disabled="busy"><option value="pending">待处理</option><option value="resolved">已处理</option><option value="obsolete">来源已停用</option></select>
      <button class="text-button" type="button" :disabled="busy || loading" data-testid="refresh-conflicts" @click="load(offset)">刷新冲突</button>
    </div>
    <p v-if="notice" class="success-notice" role="status">{{ notice }}</p>
    <p v-if="error" class="error-notice" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在读取冲突记录…</p>
    <p v-else-if="!error && !rows.length" class="text-muted">当前筛选下没有{{ status === 'pending' ? '待处理' : status === 'resolved' ? '已处理' : '来源已停用的' }}冲突。</p>
    <div v-if="!loading" class="conflict-list">
      <details v-for="row in rows" :key="row.id" class="conflict-card" data-testid="conflict-card">
        <summary>{{ row.details[0]?.message }}<span class="text-muted"> · 查看来源对比</span></summary>
        <p v-if="row.rule_ids?.length" class="field-hint">本次决定作用于这对规则及依赖它们的派生规则；原始经验保留。</p>
        <ConflictAutomation :review="row" />
        <details class="maintenance-evidence"><summary>冲突检测依据与原文引证 ⌄</summary><ul class="conflict-reasons"><li v-for="(detail, index) in row.details" :key="index"><strong>{{ detail.message }}</strong><p>{{ detail.suggestion }}</p></li></ul></details>
        <div class="conflict-comparison"><article v-for="(source, index) in row.sources" :key="source.id">
          <h3>来源 {{ index + 1 }} · {{ experienceTitle(source) }}</h3>
          <ExperienceText :text="source.solution" :limit="120" />
          <RouterLink :to="{ path: '/experiences', query: { id: source.id, view: 'originals' } }">查看经验与来源图</RouterLink>
          <details class="maintenance-evidence"><summary>完整方案、条件与证据 ⌄</summary><p>{{ source.task_summary }}</p><ExperienceKnowledge :value="source" /></details>
        </article></div>
        <form v-if="row.status === 'pending' && row.automatic_resolution?.state !== 'verifying' && drafts[row.id]" class="conflict-resolution" @submit.prevent="resolve(row)">
          <label :for="`choice-${row.id}`">处理方式</label>
          <select :id="`choice-${row.id}`" v-model="drafts[row.id]!.choice" :disabled="busy || stale.has(row.id)" required>
            <option value="" disabled>核对来源后选择</option>
            <option v-for="(source, index) in row.sources" :key="source.id" :value="source.id">采纳来源 {{ index + 1 }}，停用另一条</option>
            <option value="keep_separate">保留两条记录，均暂停复用</option>
            <option v-if="row.rule_ids?.length" value="confirm_equivalent">确认规则等价，允许合并引用</option>
            <option v-if="row.rule_ids?.length" value="confirm_compatible">确认规则兼容，分别保留</option>
          </select>
          <label :for="`note-${row.id}`">处理依据</label>
          <textarea :id="`note-${row.id}`" v-model="drafts[row.id]!.note" :disabled="busy" maxlength="2000" rows="3" required placeholder="说明核对的业务条件或测试证据" />
          <button class="button primary small" data-testid="resolve-conflict" :disabled="busy || stale.has(row.id) || !drafts[row.id]!.choice || !drafts[row.id]!.note.trim()">保存处理结果</button>
        </form>
        <p v-else-if="row.status !== 'pending'" class="conflict-audit">{{ row.status === 'obsolete' ? '来源已在其他冲突处理中停用，本项无需再次处理。' : row.resolution === 'keep_separate' ? '两条原记录已保留，相关规则均暂停复用。' : row.resolution === 'confirm_equivalent' ? '已确认等价。' : row.resolution === 'confirm_compatible' ? '已确认兼容，分别保留。' : `已采纳来源 ${row.sources.findIndex(source => source.id === row.preferred_source_id) + 1}。` }}
          <span v-if="row.note">处理依据：{{ row.note }}</span><span v-if="row.resolved_at">{{ new Date(row.resolved_at).toLocaleString('zh-CN') }}</span>
        </p>
        <div v-if="row.status === 'resolved' && api.reopenConflict" class="conflict-resolution">
          <label :for="`reopen-${row.id}`">撤销并重新审阅的依据</label><textarea :id="`reopen-${row.id}`" v-model="reopenNotes[row.id]" maxlength="2000" rows="2" />
          <button class="button secondary small" type="button" :disabled="busy || stale.has(row.id) || !reopenNotes[row.id]?.trim()" @click="reopen(row)">撤销当前决定</button>
        </div>
        <template v-if="api.getReviewHistory"><button class="text-button" type="button" @click="history(row)">查看决定历史</button>
          <ol v-if="histories[row.id]"><li v-for="event in histories[row.id]" :key="event.version">版本 {{ event.version }} · {{ event.action === 'reopen' ? '重新审阅' : '处理决定' }} · {{ event.data.note }}</li></ol>
        </template>
      </details>
    </div>
    <div v-if="offset || hasNext" class="pagination"><span>冲突第 {{ Math.floor(offset / 5) + 1 }} 页</span><div>
      <button class="button secondary small" :disabled="busy || loading || !offset" @click="load(offset - 5)">上一页冲突</button>
      <button class="button secondary small" :disabled="busy || loading || !!error || !hasNext" @click="load(offset + 5)">下一页冲突</button>
    </div></div>
  </section>
</template>
<style scoped>
.maintenance-evidence{margin:12px 0;font-size:12px;overflow-wrap:anywhere}.maintenance-evidence>summary{color:var(--accent)}.conflict-comparison h3{font-size:13px;line-height:1.7}.conflict-comparison article>a{display:inline-block;font-size:12px;margin:8px 0}
</style>
