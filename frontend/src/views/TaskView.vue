<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useApiContext } from '../api/context'
import type { TaskStatusResponse, TaskStep } from '../api/contracts'
import { createTaskPoller } from '../features/taskPoller'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ResultSummary from '../components/ResultSummary.vue'
import ExecutionSteps from '../components/ExecutionSteps.vue'
const route = useRoute()
const { api, mock } = useApiContext()
const taskId = computed(() => String(route.params.taskId ?? ''))
const task = shallowRef<TaskStatusResponse | null>(null)
const steps = shallowRef<TaskStep[]>([])
const error = ref('')
const loading = ref(true)
const copied = ref(false)
const stepsLoaded = ref(false)
let copyTimer: ReturnType<typeof setTimeout> | undefined
const poller = createTaskPoller(api, {
  onSnapshot(status, completed, loaded) { task.value = status; steps.value = completed; stepsLoaded.value = loaded; loading.value = false; error.value = '' },
  onError(cause) { error.value = cause.message; loading.value = false; stepsLoaded.value = false },
})
const terminal = computed(() => task.value && !['pending', 'running'].includes(task.value.status))
watch(taskId, id => { task.value = null; steps.value = []; stepsLoaded.value = false; error.value = ''; loading.value = true; poller.start(id) }, { immediate: true })
onBeforeUnmount(() => { poller.stop(); clearTimeout(copyTimer) })
function retry() { error.value = ''; loading.value = true; poller.retry() }
async function copyId() {
  try { await navigator.clipboard.writeText(taskId.value); copied.value = true; copyTimer = setTimeout(() => { copied.value = false }, 1800) }
  catch { error.value = '复制不可用，请手动选择任务 ID。' }
}
function time(value: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
</script>
<template>
  <div class="page-heading"><div><h1>任务执行</h1><p>跟踪已完成的工具调用，检查修改后的验证结果。</p></div><RouterLink to="/" class="button secondary"><AppIcon name="plus" :size="17" />新建任务</RouterLink></div>
  <div class="task-overview panel"><div class="task-identity"><span class="section-icon"><AppIcon name="terminal" :size="24" /></span><div><h2>Java 代码修复任务</h2><div class="task-id"><code>{{ taskId }}</code><button type="button" class="text-button" @click="copyId">{{ copied ? '已复制' : '复制 ID' }}</button></div></div></div><StatusBadge v-if="task" :status="task.status" /><span v-else class="text-muted">{{ loading ? '正在读取…' : '暂不可用' }}</span></div>
  <div v-if="error" class="error-notice error-with-action" role="alert"><span>{{ error }}</span><button class="button secondary small" type="button" @click="retry">重新读取</button></div>
  <div v-if="loading && !task" class="panel empty-state" role="status"><AppIcon name="clock" :size="28" /><p>正在读取任务状态…</p></div>
  <template v-if="task">
    <div class="task-metadata"><div><span>创建时间</span><strong>{{ time(task.created_at) }}</strong></div><div><span>开始时间</span><strong>{{ time(task.started_at) }}</strong></div><div><span>{{ terminal ? '结束时间' : '已完成步骤' }}</span><strong>{{ terminal ? time(task.finished_at) : `${task.steps_completed} 步` }}</strong></div></div>
    <ExecutionSteps :task="task" :steps="steps" :mock="mock" :steps-loaded="stepsLoaded" />
    <ResultSummary v-if="task.result" :result="task.result" />
    <section v-else-if="terminal" class="panel result-body"><h2>任务未完成</h2><p class="error-notice">{{ task.error_message || '任务已结束，但没有返回完整执行结果。' }}</p><p class="text-muted">没有最终测试证据，不能确认修复成功。</p></section>
  </template>
</template>
