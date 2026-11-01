<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useApiContext } from '../api/context'
import type { TestSourceFile, TestSourceInfo } from '../api/contracts'
import AppIcon from '../components/AppIcon.vue'

const route = useRoute(), router = useRouter()
const { api, mock } = useApiContext()
const taskId = computed(() => String(route.params.taskId ?? ''))
const files = shallowRef<TestSourceInfo[]>([])
const source = shallowRef<TestSourceFile | null>(null)
const loading = ref(false), error = ref(''), copyMessage = ref('')
let controller: AbortController | undefined
let generation = 0
let filesTaskId: string | null = null
const lineCount = computed(() => source.value?.content.split(/\r\n|\r|\n/).length ?? 0)
const lineNumbers = computed(() => Array.from({ length: lineCount.value }, (_, index) => index + 1).join('\n'))
const sourceLocation = (file: string) => ({ path: `/tasks/${taskId.value}/tests`, query: { file } })
const directLink = computed(() => source.value
  ? new URL(router.resolve(sourceLocation(source.value.path)).href, window.location.href).href : '')

async function load(refresh = false) {
  const current = ++generation
  controller?.abort()
  controller = new AbortController()
  const signal = controller.signal
  if (refresh || filesTaskId !== taskId.value) { files.value = []; filesTaskId = null }
  source.value = null; error.value = ''; copyMessage.value = ''; loading.value = true
  try {
    if (mock || !api.listTestSources || !api.getTestSource) throw new Error('当前模拟模式没有真实测试源码，请连接后端后打开实际任务。')
    const id = taskId.value, requested = route.query.file
    if (requested !== undefined && (typeof requested !== 'string' || !requested)) throw new Error('测试文件链接不完整，请从任务页面重新打开。')
    if (filesTaskId !== id) {
      const index = await api.listTestSources(id, signal)
      if (current !== generation) return
      files.value = index.files; filesTaskId = id
    }
    const path = requested ?? files.value[0]?.path
    if (path === undefined) return
    const selected = files.value.find(file => file.path === path)
    if (!selected) throw new Error('链接中的文件不在该任务的测试源码中。')
    const data = await api.getTestSource(id, path, signal)
    if (current !== generation) return
    if (data.sha256 !== selected.sha256) throw new Error('测试文件信息已变化，请重新读取。')
    source.value = data
  } catch (cause) {
    if (current === generation && !signal.aborted) error.value = cause instanceof Error ? cause.message : '测试源码读取失败，请重试。'
  } finally {
    if (current === generation) loading.value = false
  }
}
async function copyLink() {
  const current = generation
  try {
    await navigator.clipboard.writeText(directLink.value)
    if (current === generation) copyMessage.value = '链接已复制'
  } catch {
    if (current === generation) copyMessage.value = '无法自动复制，请选中下方链接手动复制。'
  }
}
watch([taskId, () => route.query.file], () => { void load() }, { immediate: true })
onBeforeUnmount(() => { ++generation; controller?.abort() })
</script>

<template>
  <div class="page-heading"><div><h1>测试源码</h1><p>查看这个任务用于验证修复的测试代码。</p></div>
    <RouterLink :to="`/tasks/${taskId}`" class="button secondary">返回任务</RouterLink>
  </div>
  <p class="source-task-id text-muted">任务 <code>{{ taskId }}</code></p>
  <div v-if="error" class="error-notice error-with-action" role="alert"><span>{{ error }}</span>
    <button class="button secondary small" type="button" data-retry @click="load(true)">重新读取</button>
  </div>
  <p v-if="loading" class="panel empty-state" role="status">正在读取测试源码…</p>
  <div v-else-if="!error && !files.length" class="panel empty-state"><AppIcon name="file" :size="28" /><h2>没有可展示的测试源码</h2><p>当前支持 Java 和 Python 的测试文件。</p></div>
  <div v-if="files.length" class="source-layout">
    <nav class="panel source-file-panel" aria-label="测试文件"><div class="panel-heading"><h2>测试文件 <span class="count-chip">{{ files.length }}</span></h2></div>
      <ul class="source-file-list"><li v-for="file in files" :key="file.path">
        <RouterLink :to="sourceLocation(file.path)" :class="{ selected: source?.path === file.path }" :aria-current="source?.path === file.path ? 'page' : undefined"><AppIcon name="file" :size="17" /><span>{{ file.path }}</span></RouterLink>
      </li></ul>
    </nav>
    <section v-if="source" class="panel source-reader" aria-labelledby="source-title">
      <div class="source-reader-heading"><div><h2 id="source-title" class="source-filename">{{ source.path }}</h2><p class="field-hint">{{ source.language === 'java' ? 'Java' : 'Python' }} · {{ lineCount }} 行 · {{ source.size_bytes }} 字节</p></div>
        <button class="button secondary small" type="button" data-copy-link @click="copyLink">复制源码链接</button>
      </div>
      <div class="source-link-bar"><label for="source-direct-link">直接访问链接</label><input id="source-direct-link" class="source-link-input" :value="directLink" readonly @focus="($event.target as HTMLInputElement).select()" /><p v-if="copyMessage" class="field-hint" role="status">{{ copyMessage }}</p></div>
      <p class="source-verification">来自该任务的工作副本，已核对与任务创建时一致。查看源码不会重新运行测试。</p>
      <div class="source-code-block" tabindex="0" role="region" aria-label="测试源码内容"><pre class="source-line-numbers" aria-hidden="true">{{ lineNumbers }}</pre><pre class="source-content"><code>{{ source.content }}</code></pre></div>
    </section>
  </div>
</template>
