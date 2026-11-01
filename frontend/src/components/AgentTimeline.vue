<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useApiContext } from '../api/context'
import type { AgentEvent, TaskStatus } from '../api/contracts'
const props = defineProps<{ taskId: string; status: TaskStatus }>()
const { api, mock } = useApiContext()
const events = ref<AgentEvent[]>([]), error = ref('')
const labels = { diagnosis: '诊断', repair: '修复提案', review: '独立复核', coordinator: '程序协调器', extractor: '经验提取' }
const statuses = { started: '进行中', completed: '已完成', failed: '失败或拒绝', stopped: '已停止' }
let version = 0, busy = false, reloadNeeded = false, timer: ReturnType<typeof setTimeout> | undefined, controller: AbortController | undefined
const active = () => ['pending', 'running'].includes(props.status)
async function load() {
  if (!api.getAgentEvents) return
  if (busy) { reloadNeeded = true; return }
  reloadNeeded = false
  const current = version, id = props.taskId
  const request = new AbortController(); controller = request; busy = true
  try {
    const page = await api.getAgentEvents(id, events.value.at(-1)?.sequence ?? 0, request.signal)
    if (current !== version || request.signal.aborted) return
    const byId = new Map(events.value.map(event => [event.event_id, event]))
    for (const event of page) byId.set(event.event_id, event)
    events.value = [...byId.values()].sort((a, b) => a.sequence - b.sequence)
    error.value = ''
    if (page.length === 100 || active()) timer = setTimeout(load, page.length === 100 ? 0 : 1200)
  } catch (cause) {
    if (current === version && !request.signal.aborted) {
      error.value = cause instanceof Error ? cause.message : '读取角色过程失败'
      if (active()) timer = setTimeout(load, 2000)
    }
  } finally {
    if (current === version) {
      busy = false
      if (reloadNeeded) { clearTimeout(timer); void load() }
    }
  }
}
watch(() => props.taskId, () => { version++; clearTimeout(timer); controller?.abort(); busy = false; reloadNeeded = false; events.value = []; error.value = ''; void load() }, { immediate: true })
watch(() => props.status, () => { if (!active()) { clearTimeout(timer); void load() } })
onBeforeUnmount(() => { version++; clearTimeout(timer); controller?.abort() })
</script>
<template>
  <section v-if="api.getAgentEvents" class="panel agent-timeline" aria-label="角色协作过程">
    <div class="panel-heading"><h2>角色协作过程</h2><span class="text-muted">{{ mock ? '浏览器演示数据' : '已持久化的角色事实' }}</span></div>
    <div class="result-body">
      <p class="field-hint">角色共用任务预算。复核通过后才应用修改，修复是否成功以程序最终测试为准。</p>
      <p v-if="error" class="error-notice" role="alert">{{ error }} <button class="text-button" @click="load">重新读取</button></p>
      <p v-if="!events.length && !error" class="text-muted">暂无角色事件。单 Agent 任务的过程见工具步骤。</p>
      <ol v-else class="agent-events"><li v-for="event in events" :key="event.event_id" data-testid="agent-event">
        <div class="agent-event-heading"><strong>{{ labels[event.role] }}</strong><span :class="event.status === 'failed' ? 'text-warning' : 'text-muted'">{{ statuses[event.status] }}</span><time>{{ new Date(event.created_at).toLocaleTimeString('zh-CN') }}</time></div>
        <p>{{ event.summary }}</p><small v-if="event.status !== 'started' && event.role !== 'coordinator'" class="text-muted">{{ event.usage.elapsed_ms }} ms · {{ event.usage.reported_tokens === null ? '用量未报告' : `${event.usage.reported_tokens} tokens` }}</small>
        <details v-if="event.snapshot_revision"><summary>快照与角色标识</summary><code>{{ event.snapshot_revision }}</code><p><code>{{ event.agent_id }}</code></p></details>
      </li></ol>
    </div>
  </section>
</template>
