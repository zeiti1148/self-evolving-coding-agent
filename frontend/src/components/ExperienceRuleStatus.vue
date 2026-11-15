<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import type { ExperienceMaintenanceStatus, RuleSources } from '../api/contracts'
import { useApiContext } from '../api/context'
const props = defineProps<{ id: string }>()
const { api } = useApiContext()
const status = ref<ExperienceMaintenanceStatus>(), error = ref('')
const sources = ref<Record<string, RuleSources>>({})
let controller: AbortController | undefined
const labels = { pending: '等待检查', ready: '检查完成', needs_review: '资料不足，待审阅', failed: '整理失败' }
async function refresh() {
  if (!api.getExperienceMaintenance) return
  controller?.abort(); controller = new AbortController(); const active = controller
  try {
    const value = await api.getExperienceMaintenance(props.id, active.signal)
    if (!active.signal.aborted) { status.value = value; error.value = '' }
  } catch (cause) { if (!active.signal.aborted) error.value = cause instanceof Error ? cause.message : '规则状态暂不可用' }
}
async function loadSources(id: string) {
  if (!api.getRuleSources) return
  const active = controller, originalId = props.id
  try {
    const previous = sources.value[id]
    const result = await api.getRuleSources(id, previous?.next_offset ?? 0, active?.signal)
    if (active?.signal.aborted || originalId !== props.id) return
    sources.value[id] = { ...result, source_ids: [...new Set([...(previous?.source_ids ?? []), ...result.source_ids])] }
  } catch (cause) { if (!active?.signal.aborted && originalId === props.id) error.value = cause instanceof Error ? cause.message : '来源暂不可用' }
}
watch(() => props.id, () => { status.value = undefined; sources.value = {}; void refresh() }, { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <section v-if="api.getExperienceMaintenance" class="rule-status" aria-label="当前规则状态">
    <div class="maintenance-heading"><strong>当前规则状态</strong><button class="text-button" type="button" @click="refresh">刷新状态</button></div>
    <p v-if="error" role="status" class="field-hint">{{ error }}</p>
    <template v-if="status">
      <p>原始记录已保留。{{ status.current_rule_ids.length }} 条规则通过当前复用检查；引用时仍会核对目标条件。</p>
      <ul class="knowledge-list"><li v-for="rule in status.rules" :key="rule.id">
        <strong>{{ rule.data.issue.error_type }}</strong> · {{ labels[rule.state] }}{{ rule.state === 'ready' && rule.effective_status === 'paused' ? '，因审阅或依赖暂停复用' : '' }}
        <p>{{ rule.data.issue.solution }}</p>
        <button v-if="api.getRuleSources" class="text-button" type="button" @click="loadSources(rule.id)">{{ sources[rule.id]?.next_offset ? '继续读取来源' : '查看来源与证据数量' }}</button>
        <div v-if="sources[rule.id]"><p>{{ sources[rule.id]!.evidence_count }} 次独立验证记录</p>
          <RouterLink v-for="id in sources[rule.id]!.source_ids" :key="id" :to="{ path: '/experiences', query: { id } }">{{ id }} </RouterLink>
        </div>
      </li></ul>
    </template>
  </section>
</template>
