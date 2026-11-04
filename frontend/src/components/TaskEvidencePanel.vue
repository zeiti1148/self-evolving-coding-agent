<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useApiContext } from '../api/context'
import type { TaskEvidence, TaskStatus } from '../api/contracts'
import AppIcon from './AppIcon.vue'

const props = defineProps<{ taskId: string; status: TaskStatus; stepsCompleted: number }>()
const { api, mock } = useApiContext()
const evidence = shallowRef<TaskEvidence | null>(null), error = ref(''), loading = ref(false)
const tab = ref<'comparison' | 'references'>('comparison'), showFailures = ref(false), selected = ref(0)
let generation = 0, controller: AbortController | undefined
const failure = computed(() => evidence.value?.failure_cases[selected.value])
const labels = { diagnosis: '诊断', repair: '修复', review: '复核', single: '单 Agent' }
const compared = computed(() => evidence.value?.tests.comparable === true)
const beforeFailed = computed(() => evidence.value?.tests.before ? evidence.value.tests.before.failures + evidence.value.tests.before.errors : null)
const afterFailed = computed(() => evidence.value?.tests.after ? evidence.value.tests.after.failures + evidence.value.tests.after.errors : null)
const terminal = computed(() => !['pending', 'running'].includes(props.status))
function switchTab(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  tab.value = event.key === 'Home' ? 'comparison' : event.key === 'End' ? 'references' : tab.value === 'comparison' ? 'references' : 'comparison'
  const container = event.currentTarget as HTMLElement
  container.querySelector<HTMLButtonElement>(`#${tab.value}-tab`)?.focus()
}
function caseName(value: string) { return value.includes('::') ? value.split('::').at(-1) : value.split('.').at(-1) }
function sourceLink(item: NonNullable<typeof failure.value>) {
  return { path: `/tasks/${props.taskId}/tests`, query: { file: item.source_file!, ...(item.source_line ? { line: String(item.source_line) } : {}) } }
}
async function load() {
  const current = ++generation, id = props.taskId
  controller?.abort(); controller = new AbortController()
  const signal = controller.signal
  loading.value = true; error.value = ''
  try {
    if (mock || !api.getTaskEvidence) throw new Error('当前连接不提供真实修复证据摘要，请查看下方执行记录。')
    const result = await api.getTaskEvidence(id, signal)
    if (current === generation) evidence.value = result
  } catch (cause) {
    if (current === generation && !signal.aborted) error.value = cause instanceof Error ? cause.message : '修复证据读取失败。'
  } finally { if (current === generation) loading.value = false }
}
watch(() => props.taskId, () => { evidence.value = null; tab.value = 'comparison'; showFailures.value = false; selected.value = 0 })
watch(() => [props.taskId, props.status, props.stepsCompleted], () => { void load() }, { immediate: true })
onBeforeUnmount(() => { ++generation; controller?.abort() })
</script>

<template>
  <section class="task-evidence" aria-label="修复证据" data-testid="task-evidence">
    <div class="evidence-tabs" role="tablist" aria-label="查看修复证据" @keydown="switchTab">
      <button id="comparison-tab" role="tab" :tabindex="tab === 'comparison' ? 0 : -1" :aria-selected="tab === 'comparison'" aria-controls="comparison-panel" :class="{ selected: tab === 'comparison' }" data-testid="comparison-tab" @click="tab = 'comparison'"><AppIcon name="check" :size="17" />修复前后</button>
      <button id="references-tab" role="tab" :tabindex="tab === 'references' ? 0 : -1" :aria-selected="tab === 'references'" aria-controls="references-panel" :class="{ selected: tab === 'references' }" data-testid="references-tab" @click="tab = 'references'"><AppIcon name="book" :size="17" />本次参考<span v-if="evidence?.references.length" class="count-chip">{{ evidence.references.length }}</span></button>
    </div>
    <div v-if="error" class="evidence-unavailable" role="status"><p>{{ error }}</p><button type="button" class="text-button" data-testid="retry-evidence" @click="load">重新读取</button></div>
    <p v-else-if="loading && !evidence" class="evidence-unavailable" role="status">正在读取任务的测试与经验记录…</p>
    <template v-if="evidence">
      <div v-if="tab === 'comparison'" id="comparison-panel" role="tabpanel" aria-labelledby="comparison-tab" class="evidence-pane" data-testid="comparison-panel">
        <template v-if="compared && evidence.tests.before && evidence.tests.after">
          <div class="evidence-context"><strong>{{ evidence.project_label || '当前项目' }}</strong><span>{{ evidence.tests.before.tests }} 项原始测试 · 已核对未改动</span></div>
          <div class="test-comparison">
            <div class="test-phase before"><span>修复前</span><div><strong data-testid="before-failed">{{ beforeFailed }}</strong><span>项未通过</span></div><div class="test-bar" aria-hidden="true"><i :style="{ width: `${evidence.tests.before.passed / evidence.tests.before.tests * 100}%` }"></i></div><small>通过 {{ evidence.tests.before.passed }} · 失败 {{ evidence.tests.before.failures }} · 异常 {{ evidence.tests.before.errors }} · 跳过 {{ evidence.tests.before.skipped }}</small></div>
            <AppIcon name="arrow" :size="26" />
            <div class="test-phase after" :class="{ verified: evidence.tests.final_verified }"><span>修复后</span><div><strong data-testid="after-failed">{{ afterFailed }}</strong><span>项未通过</span><AppIcon v-if="evidence.tests.final_verified" name="check" :size="26" /></div><div class="test-bar" aria-hidden="true"><i :style="{ width: `${evidence.tests.after.passed / evidence.tests.after.tests * 100}%` }"></i></div><small>通过 {{ evidence.tests.after.passed }} · 失败 {{ evidence.tests.after.failures }} · 异常 {{ evidence.tests.after.errors }} · 跳过 {{ evidence.tests.after.skipped }}</small></div>
          </div>
          <p class="comparison-note"><AppIcon name="shield" :size="16" />{{ evidence.tests.final_verified ? '修复后由程序独立运行原始测试，全部通过。' : '前后使用同一套原始测试；最终验证尚未全部通过。' }}</p>
        </template>
        <div v-else class="evidence-unavailable"><p>{{ evidence.tests.unavailable_reason || (terminal ? '缺少完整的修复前后证据，暂不展示对比。' : '正在执行任务；完整测试结束后可查看前后对比。') }}</p><p v-if="evidence.tests.after" class="field-hint">当前最终记录：{{ evidence.tests.after.tests }} 项测试，通过 {{ evidence.tests.after.passed }} 项。</p></div>
        <button v-if="evidence.failure_cases.length" type="button" class="failure-toggle" :aria-expanded="showFailures" data-testid="toggle-failures" @click="showFailures = !showFailures"><span>{{ showFailures ? '收起修复前的问题' : '查看修复前的问题' }} <span class="count-chip">{{ evidence.failure_cases.length }}</span></span><AppIcon name="chevron" :size="16" :class="{ open: showFailures }" /></button>
        <div v-if="showFailures && failure" class="failure-explorer" data-testid="failure-explorer">
          <div class="failure-options" aria-label="未通过的测试"><button v-for="(item, index) in evidence.failure_cases" :key="`${item.test}:${index}`" type="button" :class="{ selected: selected === index }" :aria-pressed="selected === index" :data-testid="`failure-${index}`" @click="selected = index"><code>{{ caseName(item.test) }}</code><span>{{ item.error_type.split('.').at(-1) }}</span></button></div>
          <div class="failure-description" :key="selected"><span class="evidence-eyebrow">修复前的测试记录</span><h3>{{ failure.message }}</h3><code class="failure-name">{{ failure.test }}</code><p class="field-hint">异常类型：{{ failure.error_type }}</p><RouterLink v-if="failure.source_file" :to="sourceLink(failure)" class="button secondary small" data-testid="failure-source"><AppIcon name="code" :size="16" />查看对应测试{{ failure.source_line ? ` · 第 ${failure.source_line} 行` : '' }}</RouterLink><p v-else class="field-hint">原始记录未提供可定位的测试文件。</p></div>
        </div>
      </div>
      <div v-else id="references-panel" role="tabpanel" aria-labelledby="references-tab" class="evidence-pane" data-testid="references-panel">
        <p class="reference-intro">{{ evidence.references.length ? '这些修复经验已随本次请求提供给模型，可打开查看处理方法和来源。' : evidence.reference_notice }}</p>
        <article v-for="item in evidence.references" :key="`${item.experience_id}:${item.content_sha256}`" class="reference-evidence-card">
          <div class="reference-evidence-heading"><span class="section-icon"><AppIcon name="book" /></span><div><span class="evidence-eyebrow">{{ item.scope === 'global' ? '跨项目共享经验' : item.scope === 'project' ? '项目经验' : '已记录的经验' }}</span><h3>{{ item.title }}</h3></div><span class="reference-count">{{ item.request_count }} 次请求</span></div>
          <div class="reference-roles"><span v-for="role in item.roles" :key="role.role"><AppIcon name="check" :size="14" />{{ labels[role.role] }}<b>{{ role.request_count }} 次</b></span></div>
          <div class="reference-evidence-actions"><RouterLink v-if="item.detail_available" :to="{ path: '/experiences', query: { id: item.experience_id } }" class="button primary small" data-testid="reference-detail">查看修复方法<AppIcon name="arrow" :size="16" /></RouterLink><span v-else class="field-hint">经验详情当前不可用，保留请求时的引用记录。</span><RouterLink v-if="item.source_task_id" :to="`/tasks/${item.source_task_id}`" class="text-button" data-testid="reference-source">查看来源任务</RouterLink></div>
          <details class="reference-audit"><summary>查看引用凭据</summary><dl><dt>经验编号</dt><dd><code>{{ item.experience_id }}</code></dd><dt>请求时的内容校验值</dt><dd><code>{{ item.content_sha256 }}</code></dd></dl></details>
        </article>
        <p v-if="evidence.references.length" class="reference-boundary">{{ evidence.reference_notice }}</p>
      </div>
    </template>
  </section>
</template>
