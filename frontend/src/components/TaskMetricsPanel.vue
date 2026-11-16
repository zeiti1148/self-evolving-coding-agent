<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useApiContext } from '../api/context'
import type { TaskMetrics, TaskStatus } from '../api/contracts'
import { comparisonGroups, duration, groupLabel, numeric, terminalMetric } from '../features/taskMetrics'
import AppIcon from './AppIcon.vue'

const props = defineProps<{ taskId: string; status: TaskStatus; stepsCompleted: number }>()
const { api, mock } = useApiContext(), route = useRoute(), router = useRouter()
const metric = shallowRef<TaskMetrics | null>(null), choices = shallowRef<TaskMetrics[]>([])
const comparison = ref(false), loading = ref(false), comparing = ref(false), error = ref(''), comparisonError = ref(''), search = ref('')
const selected = ref<string[]>([]), selectedMetrics = shallowRef<TaskMetrics[]>([])
let currentController: AbortController | undefined, comparisonController: AbortController | undefined
let currentGeneration = 0, comparisonGeneration = 0, timer: ReturnType<typeof setTimeout> | undefined
const available = computed(() => !mock && !!api.getTaskMetrics)
const groups = computed(() => selectedMetrics.value.length === selected.value.length && !comparisonError.value ? comparisonGroups(selectedMetrics.value) : [])
const filteredChoices = computed(() => choices.value.filter(item => `${item.project_label || ''} ${item.runtime_label || ''} ${groupLabel(item)} ${item.task_id}`.toLowerCase().includes(search.value.trim().toLowerCase())))
const reasonLabels: Record<string, string> = { task_error: '任务包含具体异常', test_failure: '当前测试失败', tests_passed: '测试通过，撤下参考', no_failure_evidence: '暂无失败证据', test_execution_error: '测试执行异常，停止检索' }
const eventCounts = computed(() => {
  const counts = new Map<string, number>()
  for (const event of metric.value?.retrieval.events || []) counts.set(event.reason, (counts.get(event.reason) || 0) + 1)
  return [...counts].map(([reason, count]) => ({ reason, label: reasonLabels[reason] || reason, count }))
})
const strategyLabels: Record<string, string> = { adaptive: '自适应', contextual: '上下文', baseline: '基础' }
const stateLabels: Record<string, string> = { pending: '等待处理', waiting: '等待规则验证', running: '处理中', succeeded: '处理完成', failed: '处理失败', not_supported: '此版本未提供自动处理', unsupported: '此版本未提供自动处理', not_recorded: '没有处理记录', unavailable: '没有处理记录', not_applicable: '无需处理', idle: '未开始' }
function outcome(item: TaskMetrics) {
  if (!terminalMetric(item)) return '进行中'
  if (item.repair_verified) return '流程完成'
  return item.status === 'stopped' ? '流程停止' : item.status === 'failed' ? '流程失败' : '未确认完成'
}
function finalTestLabel(item: TaskMetrics) { return item.final_tests_passed === true ? '最终测试通过' : item.final_tests_passed === false ? '最终测试未通过' : '最终测试未完整记录' }
function taskLink(item: TaskMetrics) { return item.task_url || `#/tasks/${item.task_id}` }
function experienceLink(item: TaskMetrics, id: string) {
  const url = new URL(item.task_url || location.href, location.href)
  url.hash = `/experiences?id=${encodeURIComponent(id)}`
  return url.href
}
function backgroundPending(item: TaskMetrics) { return ['pending', 'running', 'waiting'].includes(item.experience.state) }
async function load() {
  if (!available.value || !api.getTaskMetrics) return
  const generation = ++currentGeneration
  currentController?.abort(); currentController = new AbortController(); clearTimeout(timer)
  loading.value = true; error.value = ''
  try {
    const value = await api.getTaskMetrics(props.taskId, currentController.signal)
    if (generation !== currentGeneration) return
    metric.value = value
    selectedMetrics.value = selectedMetrics.value.map(item => item.task_id === value.task_id ? value : item)
    if (!terminalMetric(value) || backgroundPending(value)) timer = setTimeout(() => { void load() }, 5000)
  } catch (cause) { if (generation === currentGeneration && !currentController.signal.aborted) error.value = cause instanceof Error ? cause.message : '读取指标失败' }
  finally { if (generation === currentGeneration) loading.value = false }
}
async function loadComparison() {
  if (!available.value || !api.getTaskMetrics) return
  const generation = ++comparisonGeneration
  comparisonController?.abort(); comparisonController = new AbortController()
  const signal = comparisonController.signal
  comparing.value = true; comparisonError.value = ''
  selectedMetrics.value = []
  try {
    const listed = api.listTaskMetrics ? await api.listTaskMetrics(100, signal) : []
    if (generation !== comparisonGeneration) return
    choices.value = listed
    const index = new Map(listed.map(item => [item.task_id, item]))
    if (metric.value) index.set(metric.value.task_id, metric.value)
    const loaded = await Promise.all(selected.value.map(id => index.get(id) || api.getTaskMetrics!(id, signal)))
    if (generation !== comparisonGeneration) return
    selectedMetrics.value = loaded
    choices.value = [...new Map([...listed, ...loaded].map(item => [item.task_id, item])).values()]
  } catch (cause) { if (generation === comparisonGeneration && !signal.aborted) comparisonError.value = cause instanceof Error ? cause.message : '读取对比任务失败' }
  finally { if (generation === comparisonGeneration) comparing.value = false }
}
function syncSelection() {
  const map = new Map([...choices.value, ...(metric.value ? [metric.value] : [])].map(item => [item.task_id, item]))
  selectedMetrics.value = selected.value.flatMap(id => map.has(id) ? [map.get(id)!] : [])
  void router.replace({ query: { ...route.query, compare: selected.value.length ? selected.value.join(',') : undefined } })
}
function toggle(id: string) {
  if (selected.value.includes(id)) selected.value = selected.value.filter(value => value !== id)
  else if (selected.value.length < 24) selected.value = [...selected.value, id]
  syncSelection()
}
async function openComparison() {
  comparison.value = !comparison.value
  if (!comparison.value) return
  if (!selected.value.length) { selected.value = [props.taskId]; syncSelection() }
  await loadComparison()
}
function selectVisible() {
  selected.value = [...new Set([...selected.value, ...filteredChoices.value.map(item => item.task_id)])].slice(0, 24)
  syncSelection()
}
watch(() => props.taskId, () => { metric.value = null; error.value = ''; clearTimeout(timer) })
watch(() => [props.taskId, props.status, props.stepsCompleted], () => { void load() }, { immediate: true })
watch(() => route.query.compare, value => {
  const ids = typeof value === 'string' ? [...new Set(value.split(',').filter(id => /^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i.test(id)))].slice(0, 24) : []
  if (ids.join(',') === selected.value.join(',')) return
  selected.value = ids
  if (ids.length) { comparison.value = true; void loadComparison() }
  else selectedMetrics.value = []
}, { immediate: true })
onBeforeUnmount(() => { ++currentGeneration; ++comparisonGeneration; currentController?.abort(); comparisonController?.abort(); clearTimeout(timer) })
</script>

<template>
  <section v-if="available" class="panel task-metrics-panel" data-testid="task-metrics">
    <div class="panel-heading metrics-heading"><span class="section-icon"><AppIcon name="clock" /></span><h2>运行指标</h2><button type="button" class="text-button metrics-compare-toggle" :aria-expanded="comparison" @click="openComparison">{{ comparison ? '收起任务对比' : '对比其他任务' }}</button></div>
    <div class="metrics-body">
      <div v-if="error" class="error-notice error-with-action" role="alert"><span>{{ error }}</span><button type="button" class="text-button" @click="load">重新读取指标</button></div>
      <p v-if="loading && !metric" role="status" class="text-muted">正在读取真实运行记录…</p>
      <template v-if="metric">
        <div class="metrics-context"><span>{{ metric.project_label || '当前任务' }}</span><span v-if="metric.runtime_label">{{ metric.runtime_label }}</span><code v-if="metric.code_revision" :title="metric.code_revision">{{ metric.code_revision.slice(0, 8) }}</code></div>
        <div class="metrics-facts">
          <div><span>修复耗时</span><strong>{{ duration(metric.elapsed_ms) }}</strong></div>
          <div><span>修复 token</span><strong>{{ metric.token_usage.total_tokens === null ? '未完整上报' : numeric(metric.token_usage.total_tokens) }}</strong><small v-if="metric.token_usage.total_tokens === null"><template v-if="metric.token_usage.total_requests === null">请求审计尚未记录</template><template v-else>已记录 {{ numeric(metric.token_usage.reported_total_tokens) }} · {{ metric.token_usage.reported_requests }}/{{ metric.token_usage.total_requests }} 次请求有用量</template></small></div>
          <div><span>模型调用</span><strong>{{ metric.model_requests === null ? '未记录' : `${metric.model_requests} 次` }}</strong><small>{{ metric.model_requests === null ? '等待请求审计写入' : '包含失败请求' }}</small></div>
          <div><span>后台经验处理耗时</span><strong>{{ duration(metric.experience.elapsed_ms) }}</strong><small>{{ stateLabels[metric.experience.state] || metric.experience.state }}</small></div>
        </div>
        <div class="metrics-retrieval"><strong>经验检索</strong><span>{{ metric.retrieval.strategy ? (strategyLabels[metric.retrieval.strategy] || metric.retrieval.strategy) : '策略未记录' }}</span><span>{{ metric.retrieval.injected_requests === null ? '注入次数未记录' : `${metric.retrieval.injected_requests} 次请求实际带入经验` }}</span><span>{{ metric.retrieval.reference_count === null ? '来源数量未记录' : `${metric.retrieval.reference_count} 份经验来源` }}</span></div>
        <details v-if="eventCounts.length" class="metrics-trace"><summary>查看检索触发与停止记录</summary><ul><li v-for="event in eventCounts" :key="event.reason"><span>{{ event.label }}</span><strong>{{ event.count }} 次检查</strong></li></ul><p class="field-hint">这里记录检索决策检查次数；实际模型接收次数以上方请求记录为准。</p></details>
        <div class="metrics-experience"><div class="metrics-experience-title"><strong>经验处理</strong><span>{{ stateLabels[metric.experience.state] || metric.experience.state }}</span></div><p v-if="metric.experience.before_count !== null || metric.experience.after_count !== null" class="metrics-before-after"><span>处理前 <b>{{ numeric(metric.experience.before_count) }}</b></span><AppIcon name="arrow" :size="18" /><span>处理后 <b>{{ numeric(metric.experience.after_count) }}</b></span></p><div class="metrics-experience-counts"><span v-if="metric.experience.merged_count !== null">合并 {{ metric.experience.merged_count }}</span><span v-if="metric.experience.conflict_count !== null">涉及待审/冲突 {{ metric.experience.conflict_count }}</span><span v-if="metric.experience.resolved_count !== null">已处理 {{ metric.experience.resolved_count }}</span><span v-if="metric.experience.pending_confirmation_count !== null">待确认 {{ metric.experience.pending_confirmation_count }}</span></div><p v-if="metric.experience.notice" class="field-hint">{{ metric.experience.notice }}</p><p class="field-hint">后台模型 {{ metric.experience.model_requests === null ? '未记录' : `${metric.experience.model_requests} 次` }} · token {{ metric.experience.token_usage.total_tokens === null ? '未完整上报' : numeric(metric.experience.token_usage.total_tokens) }}，与修复用量分别统计。</p><a v-for="(id, index) in metric.experience.experience_ids" :key="id" class="metrics-source-link" :href="experienceLink(metric, id)">查看经验 {{ index + 1 }} 的前后变化与冲突证据 ↗</a></div>
      </template>

      <section v-if="comparison" class="metrics-comparison" aria-label="任务对比" data-testid="task-comparison">
        <div class="metrics-comparison-heading"><div><h3>对比真实任务</h3><p class="field-hint">勾选已有任务，分别查看最终测试通过率和完整流程成功率。失败、停止均计入分母，进行中的任务单列。</p></div><button type="button" class="button secondary small" :disabled="comparing" @click="loadComparison">{{ comparing ? '正在读取…' : '刷新对比' }}</button></div>
        <p v-if="comparisonError" class="error-notice" role="alert">{{ comparisonError }}；未能读取的任务不会当作零消耗或成功任务。</p>
        <details class="metrics-picker" :open="selectedMetrics.length < 2"><summary>选择对比任务 · 已选 {{ selected.length }} 项</summary><div class="metrics-picker-actions"><label>筛选项目或执行组<input v-model="search" type="search" placeholder="输入项目、版本、轮次或任务 ID" /></label><button type="button" class="text-button" @click="selectVisible">选择筛选结果</button><button type="button" class="text-button" @click="selected = []; syncSelection()">清空</button></div><div class="metrics-options"><label v-for="item in filteredChoices" :key="item.task_id" class="metrics-option"><input type="checkbox" :checked="selected.includes(item.task_id)" :disabled="!selected.includes(item.task_id) && selected.length >= 24" :aria-label="`对比 ${item.project_label || item.task_id} ${item.runtime_label || ''}`" @change="toggle(item.task_id)" /><span><strong>{{ item.project_label || item.task_id.slice(0, 8) }}</strong><small>{{ item.runtime_label || groupLabel(item) }} · {{ outcome(item) }} · {{ item.task_id.slice(0, 8) }}</small></span></label><p v-if="!filteredChoices.length && !comparing" class="text-muted">没有符合条件的任务。</p></div><p class="field-hint">显示最近 100 项任务，每次最多对比 24 项。</p></details>
        <div v-if="groups.length" class="metrics-group-grid">
          <article v-for="group in groups" :key="group.label" class="metrics-group" data-testid="metrics-group"><h4>{{ group.label }}</h4><div class="metrics-rate"><strong>{{ group.rate === null ? '—' : `${Math.round(group.rate * 100)}%` }}</strong><span>完整流程成功率<br />{{ group.successes }} / {{ group.finished }} 项已完成任务</span></div><progress :value="group.successes" :max="group.finished || 1" :aria-label="`${group.label} 完整流程成功率`" /><p v-if="group.pending" class="field-hint">另有 {{ group.pending }} 项进行中，尚未计入成功率。</p><dl><div><dt>最终测试通过率</dt><dd>{{ group.testRate === null ? '未完整记录' : `${Math.round(group.testRate * 100)}%（${group.testPasses}/${group.finished}）` }}</dd></div><div><dt>修复耗时中位数</dt><dd>{{ duration(group.median) }}</dd></div><div><dt>修复 token 合计</dt><dd>{{ group.tokens === null ? '未完整上报' : numeric(group.tokens) }}</dd></div><div><dt>后台经验 token 合计</dt><dd>{{ group.experienceTokens === null ? '未完整上报' : numeric(group.experienceTokens) }}</dd></div></dl><p v-if="!group.finished" class="field-hint">任务尚未完成，结束后再统计耗时与用量。</p><p v-else-if="group.tokens === null" class="field-hint">{{ group.tokenCoverage }}/{{ group.finished }} 项用量完整；已上报 {{ numeric(group.reportedTokens) }} token。</p></article>
        </div>
        <div v-if="selectedMetrics.length" class="metrics-table-wrap"><table class="metrics-table"><caption>每项真实任务的执行记录</caption><thead><tr><th>任务 / 执行版本</th><th>任务结果</th><th>修复耗时</th><th>修复 token</th><th>实际经验注入</th><th>经验处理</th></tr></thead><tbody><tr v-for="item in selectedMetrics" :key="item.task_id"><td data-label="任务"><a :href="taskLink(item)">{{ item.project_label || item.task_id.slice(0, 8) }} ↗</a><small>{{ item.runtime_label || groupLabel(item) }} · {{ item.task_id.slice(0, 8) }}</small></td><td data-label="任务结果"><span :class="item.repair_verified ? 'text-success' : terminalMetric(item) ? 'text-warning' : 'text-muted'">{{ outcome(item) }}</span><small v-if="item.final_tests_passed !== undefined" :class="item.final_tests_passed === true ? 'text-success' : item.final_tests_passed === false ? 'text-danger' : 'text-muted'">{{ finalTestLabel(item) }}</small></td><td data-label="修复耗时">{{ duration(item.elapsed_ms) }}</td><td data-label="修复 token">{{ item.token_usage.total_tokens === null ? '未完整上报' : numeric(item.token_usage.total_tokens) }}</td><td data-label="实际经验注入">{{ item.retrieval.injected_requests === null ? '未记录' : `${item.retrieval.injected_requests} 次请求` }}</td><td data-label="经验处理"><span>{{ stateLabels[item.experience.state] || item.experience.state }}</span><small v-if="item.experience.merged_count !== null || item.experience.conflict_count !== null">合并 {{ numeric(item.experience.merged_count) }} · 涉及待审/冲突 {{ numeric(item.experience.conflict_count) }}</small></td></tr></tbody></table></div>
        <p v-if="selectedMetrics.length" class="field-hint metrics-comparison-notice">最终测试通过不代表完整流程完成：决策耗尽时可能已通过测试，但尚未保存经验。完整流程成功要求任务成功结束且验收证据完整。任务规模不同会影响耗时与 token，少量样本不代表普遍效果；未知用量不按 0 计算，后台处理时间单独统计。</p>
      </section>
    </div>
  </section>
</template>

<style scoped>
.task-metrics-panel { margin-bottom: 24px; }
.metrics-heading { gap: 12px; }.metrics-heading h2 { flex: 1; }.metrics-compare-toggle { font-size: 13px; }
.metrics-body { padding: 22px 25px; }.metrics-context { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; color: var(--muted); font-size: 12px; margin-bottom: 16px; }.metrics-context > :first-child { color: var(--ink); font-weight: 600; }
.metrics-facts { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 14px; }.metrics-facts > div { display: flex; flex-direction: column; gap: 8px; padding: 17px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-soft); }.metrics-facts span,.metrics-facts small { font-size: 12px; color: var(--muted); }.metrics-facts strong { font-size: 22px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }.metrics-facts small { font-size: 11px; line-height: 1.5; }
.metrics-retrieval { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-top: 20px; font-size: 12px; }.metrics-retrieval strong { font-size: 13px; }.metrics-retrieval span { color: var(--muted); }
.metrics-trace { margin-top: 12px; font-size: 12px; }.metrics-trace summary,.metrics-picker summary { cursor: pointer; color: var(--accent); }.metrics-trace ul { list-style: none; margin: 12px 0; padding: 0; max-width: 470px; }.metrics-trace li { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--line); }.metrics-trace strong { font-weight: 500; }
.metrics-experience { margin-top: 20px; border-top: 1px solid var(--line); padding-top: 16px; }.metrics-experience-title { display: flex; gap: 14px; align-items: center; font-size: 13px; }.metrics-experience-title span { color: var(--muted); font-size: 12px; }.metrics-before-after { display: flex; align-items: center; gap: 18px; margin: 15px 0; font-size: 13px; }.metrics-before-after b { font-size: 20px; margin-left: 7px; }.metrics-experience-counts { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 12px; font-size: 12px; }.metrics-source-link { display: inline-block; margin: 10px 18px 0 0; font-size: 12px; }
.metrics-comparison { margin-top: 26px; padding-top: 23px; border-top: 1px solid var(--line); }.metrics-comparison-heading { display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; }.metrics-comparison-heading h3 { margin: 0; font-size: 17px; }.metrics-comparison-heading .field-hint { margin: 7px 0 15px; }.metrics-picker { padding: 13px 15px; border: 1px solid var(--line); border-radius: 9px; font-size: 13px; }.metrics-picker-actions { display: flex; align-items: flex-end; gap: 15px; margin: 15px 0 12px; }.metrics-picker-actions label { display: grid; gap: 6px; flex: 1; font-size: 12px; }.metrics-picker-actions input { width: 100%; min-width: 0; }.metrics-picker-actions button { white-space: nowrap; padding-bottom: 10px; }.metrics-options { max-height: 250px; overflow: auto; display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; }.metrics-option { display: flex; align-items: flex-start; gap: 10px; padding: 10px; background: var(--surface-soft); border-radius: 6px; cursor: pointer; }.metrics-option input { width: 16px; height: 16px; flex: 0 0 auto; margin-top: 2px; }.metrics-option span { display: grid; min-width: 0; gap: 5px; }.metrics-option strong { font-size: 12px; overflow-wrap: anywhere; }.metrics-option small { font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }
.metrics-group-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 16px; margin: 20px 0; }.metrics-group { padding: 20px; border: 1px solid var(--line); border-radius: 10px; }.metrics-group h4 { margin: 0 0 18px; font-size: 16px; }.metrics-rate { display: flex; gap: 18px; align-items: center; }.metrics-rate > strong { font-size: 36px; color: var(--accent); font-variant-numeric: tabular-nums; }.metrics-rate span { font-size: 12px; color: var(--muted); line-height: 1.7; }.metrics-group progress { appearance: none; width: 100%; height: 7px; border: none; border-radius: 6px; background: var(--surface-soft); margin: 14px 0; }.metrics-group progress::-webkit-progress-bar { background: var(--surface-soft); border-radius: 6px; }.metrics-group progress::-webkit-progress-value { background: var(--accent); border-radius: 6px; }.metrics-group dl { margin: 0; font-size: 12px; }.metrics-group dl > div { display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; }.metrics-group dt { color: var(--muted); }.metrics-group dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }
.metrics-table-wrap { overflow-x: auto; }.metrics-table { width: 100%; border-collapse: collapse; text-align: left; font-size: 12px; }.metrics-table caption { text-align: left; font-size: 13px; font-weight: 600; padding: 10px 0; }.metrics-table th { color: var(--muted); font-weight: 500; background: var(--surface-soft); }.metrics-table th,.metrics-table td { padding: 12px 10px; border-bottom: 1px solid var(--line); }.metrics-table td { font-variant-numeric: tabular-nums; }.metrics-table td:first-child { max-width: 250px; overflow-wrap: anywhere; }.metrics-table small { display: block; margin-top: 5px; color: var(--muted); font-size: 11px; }.metrics-comparison-notice { margin-top: 14px; line-height: 1.7; }
@media (max-width: 1050px) { .metrics-facts { grid-template-columns: repeat(2,minmax(0,1fr)); }.metrics-facts strong { font-size: 20px; } }
@media (max-width: 680px) { .metrics-body { padding: 18px; }.metrics-heading { flex-wrap: wrap; }.metrics-compare-toggle { font-size: 12px; }.metrics-facts { gap: 8px; }.metrics-facts > div { padding: 12px; }.metrics-facts strong { font-size: 17px; }.metrics-retrieval { gap: 9px; }.metrics-group-grid,.metrics-options { grid-template-columns: 1fr; }.metrics-picker-actions { flex-wrap: wrap; gap: 10px; }.metrics-picker-actions label { flex-basis: 100%; }.metrics-picker-actions button { padding-bottom: 0; }.metrics-comparison-heading { flex-wrap: wrap; gap: 0; }.metrics-comparison-heading button { margin-bottom: 14px; }.metrics-table-wrap { overflow: visible; }.metrics-table thead { display: none; }.metrics-table,.metrics-table tbody,.metrics-table tr,.metrics-table td { display: block; }.metrics-table tr { border: 1px solid var(--line); border-radius: 8px; margin-bottom: 10px; padding: 9px 12px; }.metrics-table td { padding: 7px 0; border: none; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 7px; }.metrics-table td::before { content: attr(data-label); color: var(--muted); }.metrics-table td:first-child { display: block; max-width: none; }.metrics-table td:first-child::before { display: none; }.metrics-table td small { flex-basis: 100%; text-align: right; }.metrics-table td:first-child small { text-align: left; }.metrics-table caption { display: block; }.metrics-before-after { gap: 12px; } }
</style>
