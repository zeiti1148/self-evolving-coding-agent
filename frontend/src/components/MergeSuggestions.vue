<script setup lang="ts">
import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useApiContext } from '../api/context'
import type { ExperienceFilters, MergeSuggestions } from '../api/contracts'
import { excerpt } from '../utils/experiencePresentation'

const props = defineProps<{ filters: ExperienceFilters; refreshKey: number; disabled: boolean }>()
const emit = defineEmits<{ select: [ids: string[]] }>()
const { api } = useApiContext()
const data = shallowRef<MergeSuggestions | null>(null)
const loading = ref(false)
const error = ref('')
const rejectedLabels = { conditions: '适用条件不同', capabilities: '所需能力不同', unrelated: '缺少共同规则或文件', conflict: '规则、顺序、证据或内容容量不兼容' }
let version = 0
let controller: AbortController | undefined

async function load() {
  if (!api.getMergeSuggestions) return
  controller?.abort()
  const active = new AbortController(); controller = active
  const current = ++version
  loading.value = true; error.value = ''; data.value = null
  try {
    const response = await api.getMergeSuggestions({ ...props.filters }, active.signal)
    if (version === current && !active.signal.aborted) data.value = response
  } catch (cause) {
    if (version === current && !active.signal.aborted) error.value = cause instanceof Error ? cause.message : '读取合并建议失败，请重试。'
  } finally { if (version === current) loading.value = false }
}
watch([() => props.filters, () => props.refreshKey], () => void load(), { immediate: true, deep: true })
onBeforeUnmount(() => { version++; controller?.abort() })
</script>

<template>
  <section class="merge-preview suggestions-panel" aria-labelledby="suggestions-title">
    <div class="split">
      <h3 id="suggestions-title">兼容的历史来源</h3>
      <button class="button secondary small" type="button" data-testid="refresh-suggestions" :disabled="disabled || loading" @click="load">刷新建议</button>
    </div>
    <p>自动查找已验证经验中的兼容规则；查看预览并确认后才会保存。</p>
    <p v-if="loading" role="status">正在查找合并建议…</p>
    <p v-else-if="error" class="error-notice" role="alert" data-testid="suggestions-error">{{ error }}</p>
    <template v-else-if="data">
      <p class="text-muted">扫描 {{ data.scanned_count }} 条当前记录，分析符合筛选的 {{ data.eligible_count }} 条经验、{{ data.checked_pairs }} 个同范围配对。</p>
      <p v-if="data.truncation_reasons.length" class="suggestions-notice" role="status">部分结果：达到扫描、候选或建议数量上限。可缩小语言、仓库或范围筛选后重新查看。</p>
      <p v-if="!data.suggestions.length">没有兼容且相关的合并建议。条件不同的经验会分别保留。</p>
      <ul v-else class="suggestion-list">
        <li v-for="item in data.suggestions" :key="item.source_ids.join(',')" class="suggestion-item">
          <ul class="suggestion-sources"><li v-for="(title, index) in item.source_titles" :key="item.source_ids[index]"><RouterLink :to="{ path: '/experiences', query: { id: item.source_ids[index], view: 'originals' } }" :title="title">{{ excerpt(title, 80) }}</RouterLink></li></ul>
          <p>{{ ({ python: 'Python', java: 'Java', go: 'Go', cpp: 'C++' })[item.language] }} · {{ item.scope === 'global' ? '已发布通用经验' : item.repository_id }}</p>
          <p>{{ item.reasons.includes('shared_rules') ? `包含 ${item.shared_issue_count} 个相同问题规则` : '涉及相同文件的互补规则' }}；合并后 {{ item.issue_count }} 个问题，去重后 {{ item.verification_count }} 次验证调用。</p>
          <p v-if="item.revision_warning" class="suggestions-notice">来源版本不同或未知，请核对适用性。</p>
          <button class="button secondary small" type="button" data-testid="suggestion-preview" :disabled="disabled" @click="emit('select', [...item.source_ids])">查看合并预览</button>
        </li>
      </ul>
      <details v-if="data.rejected.length" class="suggestion-rejections">
        <summary>未推荐的原因</summary>
        <ul><li v-for="item in data.rejected" :key="item.reason">{{ rejectedLabels[item.reason] }}：{{ item.count }} 对</li></ul>
      </details>
    </template>
  </section>
</template>

<style scoped>
.suggestions-panel { margin-block: 1rem; }
.suggestion-list { list-style: none; padding: 0; }
.suggestion-item { padding-block: 1rem; border-bottom: 1px solid color-mix(in srgb, currentColor 15%, transparent); }
.suggestion-item strong { overflow-wrap: anywhere; }
.suggestion-sources { padding-left: 1.2rem; font-size: .875rem; line-height: 1.6; }
.suggestion-sources a { overflow-wrap: anywhere; }
.suggestion-item p { margin-block: .5rem; }
.suggestions-notice { font-size: .9rem; }
.suggestion-rejections { margin-top: 1rem; }
</style>
