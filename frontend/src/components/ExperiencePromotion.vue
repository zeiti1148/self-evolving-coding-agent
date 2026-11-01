<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useApiContext } from '../api/context'
import { ApiError } from '../api/http'
import type { Experience, PromotionDraft, PromotionPreview } from '../api/contracts'
const props = defineProps<{ sourceId: string }>()
const emit = defineEmits<{ published: [Experience]; close: [] }>()
const { api } = useApiContext()
const preview = ref<PromotionPreview | null>(null), draft = ref<PromotionDraft | null>(null)
const error = ref(''), busy = ref(false), refreshRequired = ref(false), publishedId = ref('')
let generation = 0, controller: AbortController | undefined
const conditions = computed({ get: () => draft.value?.applicability.join('\n') ?? '', set: value => { if (draft.value) draft.value.applicability = value.split('\n').map(x => x.trim()).filter(Boolean) } })
const capabilities = computed({ get: () => draft.value?.required_capabilities.join(', ') ?? '', set: value => { if (draft.value) draft.value.required_capabilities = value.split(',').map(x => x.trim()).filter(Boolean) } })
const injection = computed(() => draft.value ? { ...preview.value?.injection_preview, ...draft.value,
  scope: 'global', reuse_status: 'active', issues: draft.value.issues.map(({ files, ...issue }) => issue) } : null)
async function load() {
  if (!api.previewPromotion) return
  controller?.abort(); const request = new AbortController(); controller = request
  const current = ++generation; busy.value = true; error.value = ''; publishedId.value = ''
  try {
    const value = await api.previewPromotion(props.sourceId, request.signal)
    if (current !== generation || request.signal.aborted) return
    preview.value = value; draft.value = value.draft ? JSON.parse(JSON.stringify(value.draft)) : null; refreshRequired.value = false
  } catch (cause) { if (current === generation && !request.signal.aborted) error.value = cause instanceof Error ? cause.message : '通用化预览失败' }
  finally { if (current === generation) busy.value = false }
}
async function publish() {
  if (!api.publishGlobal || !draft.value || busy.value || refreshRequired.value || !preview.value?.can_publish) return
  const current = generation, request = new AbortController(); controller = request; busy.value = true; error.value = ''
  try {
    const record = await api.publishGlobal(props.sourceId, JSON.parse(JSON.stringify(draft.value)), preview.value.current_global_id, request.signal)
    if (current !== generation || request.signal.aborted) return
    publishedId.value = record.id; emit('published', record)
  } catch (cause) {
    if (current !== generation || request.signal.aborted) return
    error.value = cause instanceof Error ? cause.message : '发布失败'
    if (cause instanceof ApiError && cause.status === 409) refreshRequired.value = true
  } finally { if (current === generation) busy.value = false }
}
watch(() => props.sourceId, () => { draft.value = null; preview.value = null; void load() }, { immediate: true })
onBeforeUnmount(() => { generation++; controller?.abort() })
</script>
<template>
  <section class="panel promotion-panel" aria-labelledby="promotion-title">
    <div class="panel-heading split"><h2 id="promotion-title">通用化预览与发布</h2><button class="text-button" @click="emit('close')">关闭</button></div>
    <div class="result-body">
      <p>保留来源记录，发布后另存一条通用经验。请编辑具体规则、边界和目标业务条件；草稿仅供审阅，发布前不会跨仓库注入。</p>
      <p class="field-hint">路径检查不能证明语义通用。其他仓库会收到下方预览内容，并仍须运行自身测试。</p>
      <p v-if="busy && !draft" role="status">正在准备草稿…</p>
      <p v-if="error" class="error-notice" role="alert">{{ error }}</p>
      <div v-if="refreshRequired" class="warning-notice">来源或发布版本已变化，当前草稿保留。<button class="text-button" @click="load">重新预览当前版本</button></div>
      <ul v-if="preview?.conflicts.length" class="warning-notice"><li v-for="conflict in preview.conflicts" :key="conflict">{{ conflict }}</li></ul>
      <form v-if="draft" class="promotion-form" @submit.prevent="publish">
        <label for="promotion-title-input">通用规则名称</label><input id="promotion-title-input" v-model="draft.task_summary" required maxlength="1000" :disabled="busy" />
        <label for="promotion-cause">根因</label><textarea id="promotion-cause" v-model="draft.root_cause" required rows="2" :disabled="busy" />
        <label for="promotion-solution">修复规则</label><textarea id="promotion-solution" v-model="draft.solution" required rows="3" :disabled="busy" />
        <label for="promotion-conditions">适用条件（每行一项）</label><textarea id="promotion-conditions" v-model="conditions" required rows="3" :disabled="busy" />
        <label for="promotion-capabilities">目标必须具备的能力（逗号分隔）</label><input id="promotion-capabilities" v-model="capabilities" :disabled="busy" />
        <fieldset v-for="(issue, index) in draft.issues" :key="index" class="promotion-issue"><legend>相关问题 {{ index + 1 }}</legend>
          <label :for="`issue-type-${index}`">异常类型</label><input :id="`issue-type-${index}`" v-model="issue.error_type" required :disabled="busy" />
          <label :for="`issue-cause-${index}`">问题根因</label><textarea :id="`issue-cause-${index}`" v-model="issue.root_cause" required :disabled="busy" />
          <label :for="`issue-solution-${index}`">处理步骤</label><textarea :id="`issue-solution-${index}`" v-model="issue.solution" required :disabled="busy" />
        </fieldset>
        <h3>其他仓库将收到的内容</h3><pre class="injection-preview" data-testid="injection-preview">{{ JSON.stringify(injection, null, 2) }}</pre>
        <p v-if="preview?.current_global_id" class="field-hint">修订当前版本 <code>{{ preview.current_global_id }}</code>；发布后旧版本保留查询并停止注入。</p>
        <button class="button primary" data-testid="publish-global" type="submit" :disabled="busy || refreshRequired || !!publishedId">{{ busy ? '正在发布…' : '发布通用经验' }}</button>
      </form>
      <p v-if="publishedId" class="success-notice" role="status">通用经验已发布。<RouterLink :to="{ path: '/experiences', query: { id: publishedId } }">查看新记录</RouterLink></p>
    </div>
  </section>
</template>
