<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useApiContext } from '../api/context'
import type { ExperienceRetryState } from '../api/contracts'
const props = defineProps<{ taskId: string }>()
const emit = defineEmits<{ updated: [] }>()
const { api, mock } = useApiContext()
const status = ref<ExperienceRetryState | null>(null)
const error = ref('')
const submitting = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let controller = new AbortController()
function accept(value: ExperienceRetryState) {
  status.value = value
  clearTimeout(timer)
  if (value.state === 'running') timer = setTimeout(read, 1500)
  if (value.state === 'succeeded') emit('updated')
}
async function read() {
  if (!api.getExperienceRetry || mock) return
  const id = props.taskId, signal = controller.signal
  try { const value = await api.getExperienceRetry(id, signal); if (!signal.aborted) accept(value) }
  catch (cause) { if (!signal.aborted) error.value = cause instanceof Error ? cause.message : '读取经验提取状态失败。' }
}
async function retry() {
  if (!api.retryExperience || mock) return
  const signal = controller.signal
  submitting.value = true; error.value = ''
  try { const value = await api.retryExperience(props.taskId, signal); if (!signal.aborted) accept(value) }
  catch (cause) { if (!signal.aborted) error.value = cause instanceof Error ? cause.message : '启动经验提取失败。' }
  finally { if (!signal.aborted) submitting.value = false }
}
watch(() => props.taskId, () => { clearTimeout(timer); controller.abort(); controller = new AbortController(); status.value = null; error.value = ''; submitting.value = false; void read() }, { immediate: true })
onBeforeUnmount(() => { clearTimeout(timer); controller.abort() })
</script>
<template>
  <div v-if="!mock && api.retryExperience" class="warning-notice">
    <p>最终测试已通过，可以依据保留的修复记录重新提取经验。原任务状态与修复过程保留。</p>
    <p v-if="status?.notice" role="status">{{ status.notice }}</p>
    <p v-if="error" role="alert">{{ error }} <button class="text-button" type="button" @click="read">重新读取</button></p>
    <button class="button secondary small" type="button" :disabled="submitting || status?.state === 'running' || status?.state === 'succeeded'" @click="retry">{{ submitting || status?.state === 'running' ? '正在提取经验…' : '重新提取经验' }}</button>
  </div>
</template>
