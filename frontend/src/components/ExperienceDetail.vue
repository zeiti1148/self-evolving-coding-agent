<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useApiContext } from '../api/context'
import type { Experience, ProvenanceGraph } from '../api/contracts'
import ExperienceKnowledge from './ExperienceKnowledge.vue'
const props = defineProps<{ identifier: string }>()
const emit = defineEmits<{ promote: [string] }>()
const { api } = useApiContext()
const record = ref<Experience | null>(null), graph = ref<ProvenanceGraph | null>(null), error = ref('')
let generation = 0, controller: AbortController | undefined
async function load() {
  controller?.abort(); controller = new AbortController(); const request = controller, current = ++generation
  record.value = null; graph.value = null; error.value = ''
  try {
    if (!api.getExperience) throw new Error('当前服务不支持经验详情，请升级后端。')
    const value = await api.getExperience(props.identifier, request.signal)
    if (current !== generation || request.signal.aborted) return
    record.value = value
    if (api.getProvenance) {
      const provenance = await api.getProvenance(props.identifier, request.signal)
      if (current === generation && !request.signal.aborted) graph.value = provenance
    }
  } catch (cause) { if (current === generation && !request.signal.aborted) error.value = cause instanceof Error ? cause.message : '经验详情不可用' }
}
watch(() => props.identifier, load, { immediate: true })
onBeforeUnmount(() => { generation++; controller?.abort() })
const relations: Record<string, string> = { merged_from: '合并自', derived_from: '通用化自', supersedes_id: '修订替代' }
</script>
<template>
  <section class="panel direct-experience" aria-label="经验详情与来源">
    <div class="panel-heading split"><h2>经验详情与来源</h2><RouterLink to="/experiences">返回经验列表</RouterLink></div>
    <div class="result-body">
      <p v-if="error" class="error-notice" role="alert">{{ error }} <button class="text-button" @click="load">重新读取</button></p>
      <template v-if="record"><h3>{{ record.task_summary }}</h3><p class="field-hint"><code>{{ record.id }}</code></p><ExperienceKnowledge :value="record" />
        <RouterLink v-if="record.source_task_id" :to="`/tasks/${record.source_task_id}`">查看来源任务</RouterLink>
        <button v-if="api.previewPromotion" class="button secondary small" :disabled="!record.evidence?.length || record.reuse_status === 'candidate'" @click="emit('promote', record.id)">通用化预览</button>
      </template>
      <div v-if="graph" class="provenance-graph"><h3>来源关系</h3><p v-if="!graph.edges.length" class="text-muted">原始任务经验，没有合并或通用化前序。</p>
        <ul><li v-for="edge in graph.edges" :key="`${edge.source}:${edge.relation}:${edge.target}`"><RouterLink :to="{ path: '/experiences', query: { id: edge.source } }">{{ edge.source.slice(0, 8) }}</RouterLink> {{ relations[edge.relation] }} <RouterLink :to="{ path: '/experiences', query: { id: edge.target } }">{{ edge.target.slice(0, 8) }}</RouterLink></li></ul>
        <p v-if="graph.truncated" class="warning-notice">来源较多，仅显示前 100 个节点及 10 层。可打开来源记录继续查看。</p>
      </div>
    </div>
  </section>
</template>
