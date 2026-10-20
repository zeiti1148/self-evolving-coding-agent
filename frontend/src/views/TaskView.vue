<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useApiContext } from '../api/context'
import type { TaskStatusResponse, TaskStep } from '../api/contracts'
import { createTaskPoller } from '../features/taskPoller'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ResultSummary from '../components/ResultSummary.vue'
const route = useRoute()
const { api } = useApiContext()
const taskId = computed(() => String(route.params.taskId ?? ''))
const task = shallowRef<TaskStatusResponse | null>(null)
const steps = shallowRef<TaskStep[]>([])
const error = ref('')
const loading = ref(true)
const copied = ref(false)
let copyTimer: ReturnType<typeof setTimeout> | undefined
const poller = createTaskPoller(api, {
  onSnapshot(status, completed) { task.value = status; steps.value = completed; loading.value = false; error.value = '' },
  onError(cause) { error.value = cause.message; loading.value = false },
})
const terminal = computed(() => task.value && !['pending', 'running'].includes(task.value.status))
watch(taskId, id => { task.value = null; steps.value = []; error.value = ''; loading.value = true; poller.start(id) }, { immediate: true })
onBeforeUnmount(() => { poller.stop(); clearTimeout(copyTimer) })
function retry() { error.value = ''; loading.value = true; poller.retry() }
async function copyId() {
  try { await navigator.clipboard.writeText(taskId.value); copied.value = true; copyTimer = setTimeout(() => { copied.value = false }, 1800) }
  catch { error.value = '复制不可用，请手动选择任务 ID。' }
}
const labels: Record<string, string> = { list_files: '查看项目结构', read_file: '读取源代码', write_file: '修改工作副本', run_tests: '运行测试', run_command: '执行受控命令' }
function time(value: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
</script>
<template>
  <div class="page-heading"><div><h1>任务执行</h1><p>跟踪已完成的工具调用，检查修改后的验证结果。</p></div><RouterLink to="/" class="button secondary"><AppIcon name="plus" :size="17" />新建任务</RouterLink></div>
  <div class="task-overview panel"><div class="task-identity"><span class="section-icon"><AppIcon name="terminal" :size="24" /></span><div><h2>Java 代码修复任务</h2><div class="task-id"><code>{{ taskId }}</code><button type="button" class="text-button" @click="copyId">{{ copied ? '已复制' : '复制 ID' }}</button></div></div></div><StatusBadge v-if="task" :status="task.status" /><span v-else class="text-muted">{{ loading ? '正在读取…' : '暂不可用' }}</span></div>
  <div v-if="error" class="error-notice error-with-action" role="alert"><span>{{ error }}</span><button class="button secondary small" type="button" @click="retry">重新读取</button></div>
  <div v-if="loading && !task" class="panel empty-state" role="status"><AppIcon name="clock" :size="28" /><p>正在读取任务状态…</p></div>
  <template v-if="task">
    <div class="task-metadata"><div><span>创建时间</span><strong>{{ time(task.created_at) }}</strong></div><div><span>开始时间</span><strong>{{ time(task.started_at) }}</strong></div><div><span>{{ terminal ? '结束时间' : '已完成步骤' }}</span><strong>{{ terminal ? time(task.finished_at) : `${task.steps_completed} 步` }}</strong></div></div>
    <section class="panel execution-panel" aria-labelledby="steps-title">
      <div class="panel-heading split"><div class="heading-inline"><AppIcon name="code" /><h2 id="steps-title">执行过程</h2><span class="count-chip">{{ steps.length }}</span></div><span class="text-muted" role="status">{{ terminal ? '执行已结束' : task.status === 'pending' ? '等待开始' : '自动更新中' }}</span></div>
      <div v-if="!steps.length" class="empty-state"><AppIcon name="clock" :size="30" /><h3>{{ terminal ? '没有已完成的工具步骤' : '等待第一步执行完成' }}</h3><p>{{ terminal ? '请查看下方的任务结果或错误说明。' : '工具调用结束后，执行结果会显示在这里。' }}</p></div>
      <ol v-else class="execution-list"><li v-for="step in steps" :key="step.sequence">
        <div class="step-marker" :class="{ 'step-failed': !step.result.success }">{{ step.sequence }}</div>
        <details class="step-detail"><summary><div><strong>{{ labels[step.call.name] ?? step.call.name }}</strong><code class="tool-name">{{ step.call.name }}</code></div><span class="step-outcome" :class="step.result.success ? 'text-success' : 'text-danger'">{{ step.result.success ? '已完成' : '未成功' }}<AppIcon name="chevron" :size="15" /></span></summary>
          <p class="step-time">完成于 {{ time(step.completed_at) }}</p>
          <h3 class="output-label">调用参数</h3><pre class="parameter-output">{{ JSON.stringify(step.call.arguments, null, 2) }}</pre>
          <h3 class="output-label">执行输出</h3><pre class="tool-output">{{ step.result.output || '无文本输出' }}</pre>
          <p v-if="step.result.metadata.truncated" class="warning-notice">输出已被工具截断，当前展示部分内容。</p>
          <p v-if="step.result.error_message" class="step-error">{{ step.result.error_code }}：{{ step.result.error_message }}</p>
        </details>
      </li></ol>
      <div v-if="!terminal" class="execution-footnote"><span class="activity-dot"></span>等待后续步骤。中间一次工具失败不代表最终修复失败。</div>
    </section>
    <ResultSummary v-if="task.result" :result="task.result" />
    <section v-else-if="terminal" class="panel result-body"><h2>任务未完成</h2><p class="error-notice">{{ task.error_message || '任务已结束，但没有返回完整执行结果。' }}</p><p class="text-muted">没有最终测试证据，不能确认修复成功。</p></section>
  </template>
</template>
