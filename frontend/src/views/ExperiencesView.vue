<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import ExperienceDetail from '../components/ExperienceDetail.vue'
import ExperiencePromotion from '../components/ExperiencePromotion.vue'
import ExperienceMaintenance from '../components/ExperienceMaintenance.vue'
import ExperienceAutoMerge from '../components/ExperienceAutoMerge.vue'
import ExperienceLibrary from '../components/ExperienceLibrary.vue'
import { useApiContext } from '../api/context'
import type { Experience, ExperienceFilters, ExperienceScope, MergePreview, ProjectLanguage, ReuseStatus } from '../api/contracts'
import AppIcon from '../components/AppIcon.vue'
import ExperienceKnowledge from '../components/ExperienceKnowledge.vue'
import MergeSuggestionsPanel from '../components/MergeSuggestions.vue'
import { excerpt, experienceTitle, repositoryLabel } from '../utils/experiencePresentation'
const { api } = useApiContext()
const route = useRoute()
const detailId = computed(() => typeof route?.query.id === 'string' ? route.query.id : '')
const hasLibrary=!!api.getExperienceLibrary
const requestedView=()=>['knowledge','activity','originals'].includes(String(route.query.view))?String(route.query.view):detailId.value?'activity':hasLibrary?'knowledge':'originals'
const view=ref(requestedView())
watch([detailId,()=>route.query.view],()=>{view.value=requestedView()})
const promotionId = ref('')
const reuseStatus = ref<ReuseStatus | ''>('')
const PAGE_SIZE = 5
const items = shallowRef<Experience[]>([])
const offset = ref(0)
const hasNext = ref(false)
const loading = ref(true)
const error = ref('')
const language = ref<ProjectLanguage | ''>('')
const scope = ref<ExperienceScope | ''>('')
const repositoryId = ref('')
const filters = ref<ExperienceFilters>({})
const selected = ref<string[]>([])
const mergeSupported = computed(() => !!api.previewMerge && !!api.mergeExperiences)
const preview = shallowRef<MergePreview | null>(null)
const mergeBusy = ref(false)
const maintenanceBusy = ref(false)
const maintenanceRevision = ref(0)
const workflowBusy = computed(() => mergeBusy.value || maintenanceBusy.value)
const mergeError = ref('')
const mergeSuccess = ref('')
const suggestionRefresh = ref(0)
const suggestionsOpen = ref(false)
let requestedOffset = 0
let version = 0
let mergeVersion = 0
let controller: AbortController | undefined
let mergeController: AbortController | undefined
async function load(nextOffset: number) {
  controller?.abort()
  const active = new AbortController()
  controller = active
  const current = ++version
  requestedOffset = nextOffset
  loading.value = true; error.value = ''
  try {
    const data = await api.listExperiences(PAGE_SIZE + 1, nextOffset, active.signal, filters.value)
    if (current !== version || active.signal.aborted) return
    items.value = data.slice(0, PAGE_SIZE); hasNext.value = data.length > PAGE_SIZE; offset.value = nextOffset
  } catch (cause) {
    if (current === version && !active.signal.aborted) error.value = cause instanceof Error ? cause.message : '读取经验失败，请重试。'
  } finally { if (current === version) loading.value = false }
}
function invalidatePreview() {
  ++mergeVersion; mergeController?.abort(); preview.value = null; mergeError.value = ''; mergeSuccess.value = ''; mergeBusy.value = false
}
function toggleSelection(id: string) {
  if (workflowBusy.value) return
  if (!selected.value.includes(id) && selected.value.length >= 10) return
  selected.value = selected.value.includes(id) ? selected.value.filter(value => value !== id) : [...selected.value, id]
  invalidatePreview()
}
function applyFilters() {
  if (workflowBusy.value) return
  filters.value = { ...(language.value ? { language: language.value } : {}),
    ...(scope.value ? { scope: scope.value } : {}),
    ...(reuseStatus.value ? { reuse_status: reuseStatus.value } : {}),
    ...(repositoryId.value.trim() && scope.value !== 'global' ? { repository_id: repositoryId.value.trim() } : {}) }
  promotionId.value = ''; selected.value = []; invalidatePreview(); void load(0)
}
function resetFilters() { language.value = ''; scope.value = ''; repositoryId.value = ''; reuseStatus.value = ''; applyFilters() }
function selectSuggestion(ids: string[]) {
  if (workflowBusy.value) return
  selected.value = [...ids]
  void prepareMerge()
}
function publicationUpdated() { maintenanceChanged() }
function toggleSuggestions(event: Event) { suggestionsOpen.value = (event.currentTarget as HTMLDetailsElement).open }
async function prepareMerge() {
  if (!api.previewMerge || workflowBusy.value || selected.value.length < 2 || selected.value.length > 10) return
  invalidatePreview()
  const current = ++mergeVersion
  const active = new AbortController(); mergeController = active
  mergeBusy.value = true
  try {
    const response = await api.previewMerge([...selected.value], active.signal)
    if (current === mergeVersion && !active.signal.aborted) preview.value = response
  } catch (cause) {
    if (current === mergeVersion && !active.signal.aborted) mergeError.value = cause instanceof Error ? cause.message : '合并预览失败，请重试。'
  } finally { if (current === mergeVersion) mergeBusy.value = false }
}
async function confirmMerge() {
  if (!api.mergeExperiences || workflowBusy.value || !preview.value?.can_merge) return
  const ids = [...preview.value.source_ids]
  if (ids.length !== selected.value.length || !ids.every(id => selected.value.includes(id))) { invalidatePreview(); return }
  const current = ++mergeVersion
  const active = new AbortController(); mergeController = active
  mergeBusy.value = true; mergeError.value = ''
  try {
    const merged = await api.mergeExperiences(ids, active.signal)
    if (current !== mergeVersion || active.signal.aborted) return
    selected.value = []; preview.value = null
    mergeSuccess.value = `合并经验已生成：${merged.task_summary}。原始记录与验证证据已保留。`
    suggestionRefresh.value++
    maintenanceRevision.value++
    await load(0)
  } catch (cause) {
    if (current === mergeVersion && !active.signal.aborted) {
      mergeError.value = cause instanceof Error ? cause.message : '保存合并经验失败，请重新预览。'
      preview.value = null
      suggestionRefresh.value++
    }
  } finally { if (current === mergeVersion) mergeBusy.value = false }
}
onMounted(() => void load(0))
function maintenanceChanged() { maintenanceRevision.value++; suggestionRefresh.value++; selected.value = []; invalidatePreview(); void load(0) }
watch(view,value=>{suggestionsOpen.value=false;if(value==='originals')void load(0)})
onBeforeUnmount(() => { version++; mergeVersion++; controller?.abort(); mergeController?.abort() })
function date(value: string) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
</script>
<template>
  <div class="page-heading" :class="{'catalog-page-heading':view==='knowledge'}"><div><h1>经验记录</h1><p>按项目、语言和问题检索；查看合并、补证与恢复过程。</p></div><RouterLink to="/" class="button secondary"><AppIcon name="plus" :size="17" />新建任务</RouterLink></div>
  <nav v-if="hasLibrary" class="experience-view-tabs" aria-label="经验展示方式"><button :aria-current="view==='knowledge'?'page':undefined" @click="view='knowledge'">经验检索</button><button :aria-current="view==='activity'?'page':undefined" @click="view='activity'">合并与冲突</button><button :aria-current="view==='originals'?'page':undefined" @click="view='originals'">原始记录</button></nav>
  <ExperienceLibrary v-if="hasLibrary&&view==='knowledge'" :filters="filters" :refresh-key="maintenanceRevision" @changed="maintenanceChanged" @activity="view='activity'" />
  <ExperienceDetail v-if="detailId&&(!hasLibrary||view==='originals')" :identifier="detailId" @promote="promotionId = $event" />
  <ExperiencePromotion v-if="promotionId" :key="promotionId" :source-id="promotionId" @close="promotionId = ''" @published="publicationUpdated" />
  <section v-if="!hasLibrary||view!=='knowledge'" class="panel experience-panel" aria-labelledby="experience-title">
    <div class="panel-heading split"><div class="heading-inline"><AppIcon name="book" /><h2 id="experience-title">{{hasLibrary?(view==='activity'?'自动处理记录':'原始来源记录'):'已积累的经验'}}</h2></div><span class="text-muted">{{view==='activity'?'查看前后变化与处理依据':'完整保留，按创建时间倒序'}}</span></div>
    <details class="filter-disclosure"><summary>筛选记录 <span v-if="Object.keys(filters).length">· {{ Object.keys(filters).length }} 项已生效</span><span aria-hidden="true">⌄</span></summary>
    <form class="experience-filters" @submit.prevent="applyFilters">
      <div><label for="experience-language">语言</label><select id="experience-language" v-model="language" :disabled="workflowBusy"><option value="">全部语言</option><option value="java">Java</option><option value="python">Python</option><option value="go">Go</option><option value="cpp">C++</option></select></div>
      <div><label for="experience-scope">适用范围</label><select id="experience-scope" v-model="scope" :disabled="workflowBusy"><option value="">全部范围</option><option value="global">通用经验</option><option value="project">仓库专属</option></select></div>
      <div><label for="experience-reuse-status">复用状态</label><select id="experience-reuse-status" v-model="reuseStatus" :disabled="workflowBusy"><option value="">全部状态</option><option value="active">已验证 / 已发布</option><option value="candidate">候选，尚未发布</option><option value="legacy_unreviewed">旧记录，尚未审阅</option></select></div>
      <div class="repository-filter"><label for="experience-repository">仓库 ID</label><input id="experience-repository" v-model="repositoryId" placeholder="输入完整仓库 ID" :disabled="workflowBusy || scope === 'global'" maxlength="200" /></div>
      <div class="filter-actions"><button class="button secondary small" type="submit" :disabled="loading || workflowBusy">应用筛选</button><button class="text-button" type="button" :disabled="workflowBusy" @click="resetFilters">重置</button></div>
    </form></details>
    <ExperienceAutoMerge v-if="!hasLibrary||view==='activity'" :filters="detailId?{}:filters" :experience-id="detailId||undefined" :refresh-key="maintenanceRevision" @changed="load(0)" />
    <ExperienceMaintenance v-if="!hasLibrary||view==='activity'" :filters="filters" :disabled="mergeBusy" @busy="maintenanceBusy = $event" @changed="maintenanceChanged" />
    <div v-if="!hasLibrary||view==='originals'">
    <details v-if="mergeSupported && api.getMergeSuggestions" class="manual-suggestions" data-testid="manual-merge-suggestions" @toggle="toggleSuggestions">
      <summary><span>手动合并建议</span><span class="text-muted">按需检查历史来源，预览后确认</span></summary>
      <MergeSuggestionsPanel v-if="suggestionsOpen" :filters="filters" :refresh-key="suggestionRefresh" :disabled="workflowBusy" @select="selectSuggestion" />
    </details>
    <div v-if="mergeSupported" class="merge-toolbar"><span>已选择 {{ selected.length }} / 10 条<span class="text-muted">，选择至少 2 条后预览</span></span><div><button class="button secondary small" type="button" data-testid="preview-merge" :disabled="selected.length < 2 || workflowBusy" @click="prepareMerge">{{ mergeBusy ? '处理中…' : '预览经验合并' }}</button><button v-if="selected.length" class="text-button" type="button" :disabled="workflowBusy" @click="selected = []; invalidatePreview()">清空选择</button></div></div>
    <div v-if="mergeError" class="merge-message error-notice" role="alert">{{ mergeError }}</div>
    <p v-if="mergeSuccess" class="merge-message success-notice" role="status">{{ mergeSuccess }}</p>
    <section v-if="preview" class="merge-preview" aria-labelledby="merge-title" data-testid="merge-preview"><h2 id="merge-title">合并预览</h2><p>选中 {{ preview.source_ids.length }} 条来源记录。确认后生成一条新经验，保留来源记录。</p>
      <div v-if="!preview.can_merge" class="merge-conflicts error-notice" role="alert"><strong>当前记录不能合并</strong><ul><li v-for="(conflict, index) in preview.conflicts" :key="index">{{ conflict }}</li></ul></div>
      <ul v-if="preview.conflict_details?.length" class="conflict-reasons"><li v-for="(detail, index) in preview.conflict_details" :key="index">{{ detail.suggestion }}</li></ul>
      <div v-if="preview.preview" class="experience-detail"><h3>{{ preview.preview.task_summary }}</h3><ExperienceKnowledge :value="preview.preview" /></div>
      <button class="button primary" type="button" data-testid="confirm-merge" :disabled="!preview.can_merge || workflowBusy" @click="confirmMerge">{{ mergeBusy ? '正在保存…' : '确认生成合并经验' }}</button>
    </section>
    <div v-if="loading" class="empty-state" role="status"><AppIcon name="clock" :size="28" /><p>正在读取经验记录…</p></div>
    <div v-else-if="error" class="empty-state"><div role="alert" class="error-notice">{{ error }}</div><button class="button secondary" type="button" data-testid="retry" @click="load(requestedOffset)"><AppIcon name="refresh" :size="16" />重新加载</button></div>
    <div v-else-if="!items.length" class="empty-state"><span class="empty-icon"><AppIcon name="book" :size="32" /></span><h2>{{ Object.keys(filters).length ? '没有符合筛选条件的经验' : offset === 0 ? '还没有经验记录' : '这一页没有更多记录' }}</h2><p>完成一个修复任务并成功保存经验后，可以在这里查看。</p><RouterLink v-if="offset === 0 && !Object.keys(filters).length" to="/" class="button primary">创建修复任务</RouterLink></div>
    <div v-else class="experience-list"><article v-for="item in items" :key="item.id" class="experience-entry">
      <label v-if="mergeSupported" class="merge-selection"><input type="checkbox" :checked="selected.includes(item.id)" :disabled="workflowBusy || selected.length >= 10 && !selected.includes(item.id)" :aria-label="`选择合并：${item.task_summary}`" @change="toggleSelection(item.id)" /><span class="sr-only">选择合并</span></label>
      <details class="experience-item" data-testid="experience-item"><summary>
        <div class="experience-title">
          <h3>{{ experienceTitle(item) }}</h3>
          <p class="record-meta"><span>{{ ({python:'Python',java:'Java',go:'Go',cpp:'C++'})[item.language||'java'] }}</span><span>{{ repositoryLabel(item) }}</span><span v-if="item.issues?.length">{{ item.issues.length }} 个问题</span><span>{{ date(item.created_at) }}</span></p>
          <p class="record-preview">{{ excerpt(item.root_cause,100) }}</p>
        </div><span class="expand-label">详情<AppIcon name="chevron" :size="16" /></span>
      </summary>
        <div class="experience-detail"><h3 class="original-title">{{ item.task_summary }}</h3><p class="record-full-meta">{{ item.scope==='project'?'仓库专属：'+item.repository_id:'通用经验' }} · {{ item.error_type||'未分类错误' }}</p><div class="tags"><span v-for="tag in item.tags" :key="tag">{{ tag }}</span></div><ExperienceKnowledge :value="item" /><div class="experience-actions"><RouterLink :to="{ path: '/experiences', query: { id: item.id, view: 'originals' } }">详情与来源图</RouterLink><button v-if="api.previewPromotion" class="button secondary small" data-testid="promote-experience" :disabled="!item.evidence?.length || item.reuse_status === 'candidate'" @click="promotionId = item.id">通用化预览</button></div><div class="experience-provenance"><span>记录 ID <code>{{ item.id }}</code></span><RouterLink v-if="item.source_task_id" :to="`/tasks/${item.source_task_id}`">查看来源任务<AppIcon name="arrow" :size="14" /></RouterLink><span v-else>未关联单个来源任务</span></div></div>
      </details>
    </article></div>
    <div class="pagination"><span>第 {{ Math.floor(offset / PAGE_SIZE) + 1 }} 页<span class="pagination-size">每页最多 {{ PAGE_SIZE }} 条</span></span><div><button class="button secondary small" type="button" :disabled="loading || offset === 0 || workflowBusy" @click="load(Math.max(0, offset - PAGE_SIZE))">上一页</button><button class="button secondary small" type="button" data-testid="next-page" :disabled="loading || !!error || !hasNext || workflowBusy" @click="load(offset + PAGE_SIZE)">下一页</button></div></div>
    </div>
  </section>
  <p class="library-note"><AppIcon name="book" :size="17" />仓库专属经验用于对应项目；只有已发布、同语言且满足能力条件的通用经验可跨仓库注入，目标任务仍须验证业务条件。</p>
</template>

<style scoped>
.manual-suggestions{margin:16px 24px;border:1px solid var(--line);border-radius:10px}.manual-suggestions>summary{display:flex;flex-wrap:wrap;gap:6px 16px;padding:12px 16px;font-size:12px}.manual-suggestions>summary>span:first-child{font-weight:600}.manual-suggestions :deep(.suggestions-panel){margin:0;border:0;border-top:1px solid var(--line);border-radius:0;padding:16px}
.catalog-page-heading{margin-bottom:16px}.catalog-page-heading h1{font-size:26px}.catalog-page-heading p{font-size:12px;margin-top:4px}.experience-view-tabs{display:flex;gap:6px;padding:5px;background:var(--surface);border:1px solid var(--line);border-radius:12px;margin-bottom:16px;width:fit-content;max-width:100%}.experience-view-tabs button{padding:8px 20px;font-size:12px;border-radius:8px;background:transparent;color:var(--muted);font-weight:600;white-space:nowrap}.experience-view-tabs button[aria-current=page]{background:var(--accent);color:var(--on-accent)}
@media(max-width:600px){.experience-view-tabs{width:100%}.experience-view-tabs button{padding:10px 7px;flex:1;font-size:12px}}

.filter-disclosure{border-bottom:1px solid var(--line)}.filter-disclosure>summary{display:flex;align-items:center;gap:8px;padding:12px 24px;font-size:12px;color:var(--muted)}.filter-disclosure>summary>span:last-child{margin-left:auto}.experience-entry{display:flex;gap:12px;align-items:flex-start;padding:18px 22px;border-bottom:1px solid var(--line)}.experience-entry:last-child{border-bottom:0}.merge-selection{padding:3px 0 0;margin:0;flex:0 0 20px}.merge-selection input{margin:0;width:16px;height:16px}.experience-item{flex:1;min-width:0}.experience-item>summary{padding:0;gap:16px}.experience-title h3{font-size:14px;font-weight:600;line-height:1.6;margin:0}.experience-title .record-meta{display:flex;gap:5px 12px;flex-wrap:wrap;margin-top:6px;font-size:11px}.record-meta>span+span{position:relative}.experience-title .record-preview{margin-top:5px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}.experience-item[open]{background:transparent}.experience-item[open]>summary{padding-bottom:16px}.experience-detail{padding:0 0 10px;min-width:0}.original-title{font-size:14px;line-height:1.8;overflow-wrap:anywhere}.record-full-meta{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.experience-detail .tags{margin-bottom:20px}.expand-label{margin:3px 0 0}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:600px){.experience-entry{padding:15px 14px;gap:8px}.experience-title h3{font-size:13px}.experience-title .record-meta{font-size:10px;gap:3px 8px}.filter-disclosure>summary{padding:12px 16px}.experience-item>summary{gap:8px}.experience-detail{padding:0 0 10px}.expand-label{font-size:0}}
</style>
