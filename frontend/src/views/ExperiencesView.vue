<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { useApiContext } from '../api/context'
import type { Experience } from '../api/contracts'
import AppIcon from '../components/AppIcon.vue'
const { api } = useApiContext()
const PAGE_SIZE = 5
const items = shallowRef<Experience[]>([])
const offset = ref(0)
const hasNext = ref(false)
const loading = ref(true)
const error = ref('')
let requestedOffset = 0
let version = 0
let controller: AbortController | undefined
async function load(nextOffset: number) {
  controller?.abort()
  const active = new AbortController()
  controller = active
  const current = ++version
  requestedOffset = nextOffset
  loading.value = true; error.value = ''
  try {
    const data = await api.listExperiences(PAGE_SIZE + 1, nextOffset, active.signal)
    if (current !== version || active.signal.aborted) return
    items.value = data.slice(0, PAGE_SIZE); hasNext.value = data.length > PAGE_SIZE; offset.value = nextOffset
  } catch (cause) {
    if (current === version && !active.signal.aborted) error.value = cause instanceof Error ? cause.message : '读取经验失败，请重试。'
  } finally { if (current === version) loading.value = false }
}
onMounted(() => void load(0))
onBeforeUnmount(() => { version++; controller?.abort() })
function date(value: string) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
</script>
<template>
  <div class="page-heading"><div><h1>经验记录</h1><p>查看问题根因、修复方案和验证证据，为相似任务提供参考。</p></div><RouterLink to="/" class="button secondary"><AppIcon name="plus" :size="17" />新建任务</RouterLink></div>
  <section class="panel experience-panel" aria-labelledby="experience-title">
    <div class="panel-heading split"><div class="heading-inline"><AppIcon name="book" /><h2 id="experience-title">已积累的经验</h2></div><span class="text-muted">按创建时间倒序</span></div>
    <div v-if="loading" class="empty-state" role="status"><AppIcon name="clock" :size="28" /><p>正在读取经验记录…</p></div>
    <div v-else-if="error" class="empty-state"><div role="alert" class="error-notice">{{ error }}</div><button class="button secondary" type="button" data-testid="retry" @click="load(requestedOffset)"><AppIcon name="refresh" :size="16" />重新加载</button></div>
    <div v-else-if="!items.length" class="empty-state"><span class="empty-icon"><AppIcon name="book" :size="32" /></span><h2>{{ offset === 0 ? '还没有经验记录' : '这一页没有更多记录' }}</h2><p>完成一个修复任务并成功保存经验后，可以在这里查看。</p><RouterLink v-if="offset === 0" to="/" class="button primary">创建修复任务</RouterLink></div>
    <div v-else class="experience-list"><details v-for="item in items" :key="item.id" class="experience-item" data-testid="experience-item"><summary><span class="experience-icon"><AppIcon name="code" :size="21" /></span><div class="experience-title"><h3>{{ item.task_summary }}</h3><p>{{ item.error_type || '未分类错误' }}<span class="separator">/</span>{{ date(item.created_at) }}</p><div class="tags"><span v-for="tag in item.tags" :key="tag">{{ tag }}</span></div></div><span class="expand-label">查看详情<AppIcon name="chevron" :size="16" /></span></summary>
      <div class="experience-detail"><dl><div><dt>问题根因</dt><dd>{{ item.root_cause }}</dd></div><div><dt>修复方案</dt><dd>{{ item.solution }}</dd></div><div><dt>验证证据</dt><dd>{{ item.verification }}</dd></div></dl><div class="experience-provenance"><span>记录 ID <code>{{ item.id }}</code></span><RouterLink v-if="item.source_task_id" :to="`/tasks/${item.source_task_id}`">查看来源任务<AppIcon name="arrow" :size="14" /></RouterLink><span v-else>未关联来源任务</span></div></div>
    </details></div>
    <div class="pagination"><span>第 {{ Math.floor(offset / PAGE_SIZE) + 1 }} 页<span class="pagination-size">每页最多 {{ PAGE_SIZE }} 条</span></span><div><button class="button secondary small" type="button" :disabled="loading || offset === 0" @click="load(Math.max(0, offset - PAGE_SIZE))">上一页</button><button class="button secondary small" type="button" data-testid="next-page" :disabled="loading || !!error || !hasNext" @click="load(offset + PAGE_SIZE)">下一页</button></div></div>
  </section>
  <p class="library-note"><AppIcon name="shield" :size="17" />经验是修复参考，新的任务仍需独立测试验证。</p>
</template>
