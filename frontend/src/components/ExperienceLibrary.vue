<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { ExperienceFilters, ExperienceLibrary, ExperienceRecovery as Recovery, KnowledgeCard, LibraryFilters, ProjectLanguage } from '../api/contracts'
import { useApiContext } from '../api/context'
import ExperienceText from './ExperienceText.vue'
import ExperienceRecovery from './ExperienceRecovery.vue'
import { experienceTitle, knowledgeTitle } from '../utils/experiencePresentation'
const props = defineProps<{ filters: ExperienceFilters; refreshKey: number }>()
const emit = defineEmits<{ changed: []; activity: [] }>()
const { api } = useApiContext(), route = useRoute(), router = useRouter()
const query = (key: string) => typeof route.query[key] === 'string' ? String(route.query[key]) : ''
const data = ref<ExperienceLibrary | null>(null), error = ref(''), loading = ref(false), busy = ref(false)
const search = ref(query('q')), language = ref(query('language')), repository = ref(query('repository'))
const errorType = ref(query('error')), tag = ref(query('tag')), status = ref(['active','paused','unverified','merged'].includes(query('status')) ? query('status') : 'all')
const sort = ref(['relevance','sources'].includes(query('sort')) ? query('sort') : 'recent'), offset = ref(Math.max(0, Math.floor(Number(query('offset')) || 0)))
const selected = ref<KnowledgeCard | null>(null), detailTab = ref('content'), dialog = ref<HTMLDialogElement>()
const pageSize = 10, labels = { active: '可复用', paused: '暂停复用', unverified: '待验证' }
const languageLabels: Record<string, string> = { python: 'Python', java: 'Java', go: 'Go', cpp: 'C++' }
let controller: AbortController | undefined, writeController: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined, debounce: ReturnType<typeof setTimeout> | undefined, generation = 0, disposed = false
const scanning = computed(() => ['pending', 'running'].includes(data.value?.history_scan?.state ?? ''))
const counts = computed(() => data.value?.summary)
const total = computed(() => data.value?.total ?? data.value?.items.length ?? 0)
const filtered = computed(() => !!(search.value || language.value || repository.value || errorType.value || tag.value || status.value !== 'all'))
const categories = computed(() => [
  { key: 'all', label: '全部', count: counts.value?.knowledge_cards ?? 0 },
  { key: 'active', label: '可复用', count: counts.value?.active ?? 0 },
  { key: 'unverified', label: '待验证', count: counts.value?.unverified ?? 0 },
  { key: 'paused', label: '暂停复用', count: counts.value?.paused ?? 0 },
  { key: 'merged', label: '已归并', count: counts.value?.merged ?? 0 },
])
function parameters(): LibraryFilters {
  return { ...props.filters, ...(search.value.trim() ? { q: search.value.trim() } : {}),
    ...(language.value ? { language: language.value as ProjectLanguage } : {}),
    ...(repository.value ? { repository_id: repository.value } : {}), ...(errorType.value ? { error_type: errorType.value } : {}),
    ...(tag.value ? { tag: tag.value } : {}), ...(status.value === 'merged' ? { merged: true } : status.value !== 'all' ? { state: status.value as LibraryFilters['state'] } : {}),
    sort: sort.value as LibraryFilters['sort'], limit: pageSize, offset: offset.value }
}
function remember() {
  void router.replace({ query: { ...route.query, view: 'knowledge', q: search.value || undefined, language: language.value || undefined,
    repository: repository.value || undefined, error: errorType.value || undefined, tag: tag.value || undefined,
    status: status.value === 'all' ? undefined : status.value, sort: sort.value === 'recent' ? undefined : sort.value,
    offset: offset.value || undefined, card: selected.value?.id } })
}
async function open(card: KnowledgeCard, tab = 'content') {
  selected.value = card; detailTab.value = tab; remember(); await nextTick()
  if (dialog.value && !dialog.value.open) dialog.value.showModal?.()
}
function close() { dialog.value?.close?.(); selected.value = null; remember() }
async function load() {
  if (!api.getExperienceLibrary || disposed) return
  controller?.abort(); const active = new AbortController(); controller = active; const current = ++generation
  loading.value = true
  try {
    const value = await api.getExperienceLibrary(parameters(), active.signal)
    if (disposed || active.signal.aborted || generation !== current) return
    data.value = value; error.value = ''
    const updated = value.items.find(card => card.id === selected.value?.id)
    if (updated) selected.value = updated
    if (!selected.value && query('card')) {
      const card = value.items.find(item => item.id === query('card')) || (await api.getExperienceLibrary({card_id:query('card'),limit:1},active.signal)).items[0]
      if (card && !disposed && !active.signal.aborted && current===generation) await open(card, 'recovery')
    }
    if (!value.items.length && offset.value && total.value <= offset.value) { offset.value = 0; remember(); void load() }
  } catch (cause) { if (!active.signal.aborted && !disposed) error.value = cause instanceof Error ? cause.message : '经验目录读取失败' }
  finally { if (!disposed && generation === current) { loading.value = false; if (timer) clearTimeout(timer); timer = setTimeout(load, 5000) } }
}
function apply() { if (debounce) clearTimeout(debounce); offset.value = 0; remember(); void load() }
function reset() { search.value = ''; language.value = ''; repository.value = ''; errorType.value = ''; tag.value = ''; status.value = 'all'; apply() }
function page(next: number) { offset.value = next; remember(); void load() }
async function scan() {
  if (!api.scanExperienceHistory || busy.value || scanning.value) return
  busy.value = true; writeController = new AbortController()
  try { await api.scanExperienceHistory(data.value?.history_scan?.state === 'failed', writeController.signal); if (!disposed) { await load(); emit('changed') } }
  catch (cause) { if (!disposed) error.value = cause instanceof Error ? cause.message : '整理未完成' }
  finally { if (!disposed) busy.value = false }
}
function recovered(value: Recovery) {
  if (selected.value?.id === value.card_id) selected.value = { ...selected.value, recovery: value,
    ...(value.state === 'succeeded' && value.after ? { state: value.after.state, summary: value.after.summary, reuse_mode: 'scoped' as const, reuse_scope: value.scope } : {}) }
  void load()
}
watch(search, () => { if (debounce) clearTimeout(debounce); debounce = setTimeout(apply, 300) })
watch(() => [props.filters, props.refreshKey], load, { immediate: true, deep: true })
onBeforeUnmount(() => { disposed = true; generation++; controller?.abort(); writeController?.abort(); if (timer) clearTimeout(timer); if (debounce) clearTimeout(debounce) })
</script>

<template>
  <section class="knowledge-catalog" aria-label="经验分类检索">
    <form class="catalog-search" @submit.prevent="apply">
      <label class="search-field"><span>搜索经验</span><input v-model="search" type="search" maxlength="300" placeholder="问题、函数、文件、标签或解决方案" aria-label="搜索经验" /></label>
      <button class="button primary" type="submit">搜索</button><button v-if="filtered" class="text-button" type="button" @click="reset">清除筛选</button>
    </form>
    <div class="catalog-filters">
      <label>项目<select v-model="repository" aria-label="按项目筛选" @change="apply"><option value="">全部项目</option><option v-for="item in data?.facets?.repositories" :key="item.value" :value="item.value">{{item.label}} ({{item.count}})</option></select></label>
      <label>语言<select v-model="language" aria-label="按语言筛选" @change="apply"><option value="">全部语言</option><option v-for="item in data?.facets?.languages" :key="item.value" :value="item.value">{{languageLabels[item.value] || item.label}} ({{item.count}})</option></select></label>
      <label>问题类型<select v-model="errorType" aria-label="按问题类型筛选" @change="apply"><option value="">全部类型</option><option v-for="item in data?.facets?.error_types" :key="item.value" :value="item.value">{{item.label}} ({{item.count}})</option></select></label>
      <label>技术标签<select v-model="tag" aria-label="按技术标签筛选" @change="apply"><option value="">全部标签</option><option v-for="item in data?.facets?.tags" :key="item.value" :value="item.value">{{item.label}} ({{item.count}})</option></select></label>
    </div>
    <div class="catalog-status-row"><nav aria-label="按复用状态筛选"><button v-for="category in categories" :key="category.key" :aria-pressed="status===category.key" @click="status=category.key;apply()">{{category.label}}<b>{{category.count}}</b></button></nav><select v-model="sort" aria-label="经验排序" @change="apply"><option value="recent">最近更新</option><option value="relevance">相关程度</option><option value="sources">来源数量</option></select></div>
    <p v-if="error" class="error-notice" role="alert">{{error}} <button class="text-button" @click="load">重新读取</button></p>
    <div class="catalog-table-wrap" :aria-busy="loading">
      <table class="catalog-table"><thead><tr><th>经验 / 修复目标</th><th>所属项目</th><th>状态</th><th>来源 / 验证</th><th><span class="sr-only">操作</span></th></tr></thead><tbody>
        <tr v-for="card in data?.items" :key="card.id" data-testid="knowledge-row">
          <td><button class="row-title" @click="open(card)">{{knowledgeTitle(card)}}</button><div class="row-context"><span>{{languageLabels[card.language]}}</span><span>{{card.error_type || '未分类'}}</span><span v-if="card.symbols?.length" :title="card.symbols.join('、')">{{card.symbols[0]}}</span></div></td>
          <td class="project-cell"><span :title="card.project_labels?.join('、')">{{card.project_labels?.[0] || (card.kind==='principle'?'跨项目原则':'未关联项目')}}</span><small v-if="(card.project_labels?.length || 0)>1">另 {{card.project_labels!.length-1}} 个项目</small></td>
          <td><span class="state-tag" :class="card.state">{{card.reuse_mode==='scoped'?'限定范围可复用':labels[card.state]}}</span><small v-if="card.recovery&&['pending','running','waiting_task'].includes(card.recovery.state)">正在补充验证</small></td>
          <td class="evidence-cell"><span>{{card.source_count}} 份来源<span v-if="card.merged"> · 已归并</span></span><small>{{card.evidence_count?card.evidence_count+' 份验证依据':'缺少测试证据'}}</small></td>
          <td><button class="text-button row-action" @click="open(card,card.state==='active'?'content':'recovery')">{{card.state==='active'?'查看':'补证 / 恢复'}}<span aria-hidden="true"> ↗</span></button></td>
        </tr>
      </tbody></table>
      <p v-if="!data&&!error" class="catalog-empty" role="status">正在读取经验目录…</p>
      <div v-else-if="!data?.items.length" class="catalog-empty"><p>没有符合条件的经验</p><button v-if="filtered" class="text-button" @click="reset">清除筛选</button></div>
    </div>
    <div class="catalog-pagination"><span>共 {{total}} 条<span v-if="total"> · 第 {{Math.floor(offset/pageSize)+1}} / {{Math.max(1,Math.ceil(total/pageSize))}} 页</span><span v-if="loading&&data"> · 更新中</span></span><div><button class="button secondary small" :disabled="!offset||loading" @click="page(Math.max(0,offset-pageSize))">上一页</button><button class="button secondary small" :disabled="loading||data?.next_offset==null" @click="page(data!.next_offset!)">下一页</button></div></div>
    <div class="catalog-secondary"><details data-testid="metric-guide"><summary>状态与计数说明</summary><dl><dt>可复用</dt><dd>验证通过的规则。限定范围可复用要求项目、代码版本和测试配置一致，其他项目仍需补证。</dd><dt>待验证</dt><dd>缺少可核对的真实测试。关联正常修复任务，完成验证后才能进入自动修复。</dd><dt>暂停复用</dt><dd>存在冲突或证据失效，暂不向修复任务提供；不代表原任务修复失败。点击“补证 / 恢复”重新验证。</dd><dt>已归并</dt><dd>多份来源整理为一条知识，原文仍可追溯。它是以上状态的子集，不能再次相加。</dd></dl><p>计数随项目、语言、类型、标签变化；状态和搜索进一步缩小结果。一次修复可以产生多条经验。</p></details><button class="text-button" @click="emit('activity')">合并与冲突记录</button><button class="text-button" :disabled="busy||scanning" @click="scan" data-testid="scan-history">{{busy||scanning?'正在整理…':'整理历史经验'}}</button></div>
    <p v-if="data?.history_scan" class="scan-notice" role="status"><template v-if="data.history_scan.state==='succeeded'">历史整理：{{data.history_scan.scanned}} 份已检查 · {{data.history_scan.groups}} 组归并，当前复用状态见目录。</template><template v-else>{{data.history_scan.notice}}</template></p>
    <dialog ref="dialog" class="knowledge-detail" aria-labelledby="knowledge-detail-title" @cancel.prevent="close" @click="($event.target===dialog)&&close()">
      <template v-if="selected"><header class="detail-heading"><div><span class="state-tag" :class="selected.state">{{selected.reuse_mode==='scoped'?'限定范围可复用':labels[selected.state]}}</span><h2 id="knowledge-detail-title">{{knowledgeTitle(selected)}}</h2><p>{{selected.project_labels?.join(' · ') || selected.title}}</p></div><button class="close-detail" aria-label="关闭经验详情" autofocus @click="close">×</button></header>
      <nav class="detail-tabs" aria-label="经验详情栏目"><button :aria-pressed="detailTab==='content'" @click="detailTab='content'">方案与边界</button><button :aria-pressed="detailTab==='recovery'" @click="detailTab='recovery'">验证与恢复</button><button :aria-pressed="detailTab==='sources'" @click="detailTab='sources'">来源与合并 <b>{{selected.source_count}}</b></button></nav>
      <div class="detail-body">
        <section v-if="detailTab==='content'" class="content-detail"><h3>解决方案</h3><ExperienceText :text="selected.summary" :limit="260" /><p class="detail-reason">{{selected.reason}}</p><h3>根因</h3><ExperienceText :text="selected.root_cause" :limit="180" /><h3>适用条件</h3><ul v-if="selected.applicability.length"><li v-for="value in selected.applicability" :key="value">{{value}}</li></ul><p v-else>需结合目标项目验证。</p><h3 v-if="selected.constraints.length">业务约束</h3><ul><li v-for="value in selected.constraints" :key="value">{{value}}</li></ul><div class="detail-tags"><button v-for="value in selected.tags" :key="value" @click="tag=value;close();apply()">{{value}}</button></div><button class="button secondary small" @click="detailTab='recovery'">查看验证过程与复用范围</button></section>
        <ExperienceRecovery v-if="detailTab==='recovery'" :key="selected.id" :card="selected" @changed="recovered" />
        <section v-if="detailTab==='sources'" class="source-detail"><div v-if="selected.merged" class="merge-result"><strong>{{selected.source_count}} 份原始来源 → 1 条知识</strong><p>{{selected.grouping_reason || selected.reason}}</p><p v-if="selected.state==='unverified'">归并不等于验证通过：暂不自动复用。</p></div><h3>处理后的方案</h3><ExperienceText :text="selected.summary" :limit="180" /><h3>原始来源 · 完整保留</h3><article v-for="(source,index) in selected.sources" :key="source.id"><div class="source-heading"><b>{{index+1}}. {{experienceTitle(source)}}</b><small>{{new Date(source.created_at).toLocaleDateString('zh-CN')}}</small></div><ExperienceText :text="source.solution" :limit="140" /><RouterLink :to="{path:'/experiences',query:{id:source.id,view:'originals'}}">查看原文与来源任务 ↗</RouterLink></article></section>
      </div></template>
    </dialog>
  </section>
</template>

<style scoped>
.knowledge-catalog{min-width:0;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:22px}.catalog-search{display:flex;align-items:end;gap:12px}.search-field{flex:1}.search-field>span{display:block;font-weight:600;font-size:13px;margin-bottom:8px}.catalog-search input{width:100%;margin:0;height:40px;padding:8px 12px}.catalog-search>.button{height:40px}.catalog-filters{display:grid;grid-template-columns:1.6fr 1fr 1.3fr 1fr;gap:14px;margin:14px 0 16px}.catalog-filters label{font-size:11px;color:var(--muted)}.catalog-filters select{display:block;margin:6px 0 0;width:100%;font-size:12px;min-width:0;padding:8px 10px}.catalog-status-row{display:flex;align-items:center;justify-content:space-between;gap:14px;border-bottom:1px solid var(--line);padding-bottom:12px}.catalog-status-row nav{display:flex;gap:4px;flex-wrap:wrap}.catalog-status-row nav button{padding:8px 12px;font-size:12px;color:var(--muted);background:transparent;border-radius:6px;white-space:nowrap}.catalog-status-row nav b{margin-left:8px;font-size:11px;background:var(--surface-soft);padding:1px 5px;border-radius:4px}.catalog-status-row nav button[aria-pressed=true]{color:var(--accent);background:var(--accent-soft);font-weight:600}.catalog-status-row>select{width:auto;max-width:130px;font-size:12px;padding:6px 10px;margin:0}.catalog-table-wrap{overflow:auto;min-height:150px}.catalog-table{border-collapse:collapse;table-layout:fixed;width:100%;font-size:12px}.catalog-table th{text-align:left;font-weight:500;font-size:11px;color:var(--muted);padding:12px 10px;border-bottom:1px solid var(--line)}.catalog-table th:first-child{width:41%;padding-left:0}.catalog-table th:nth-child(2){width:17%}.catalog-table th:nth-child(3){width:16%}.catalog-table th:nth-child(4){width:16%}.catalog-table th:last-child{width:10%}.catalog-table td{padding:11px 10px;vertical-align:middle;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.catalog-table td:first-child{padding-left:0}.catalog-table td:last-child{text-align:right;padding-right:0}.catalog-table tr:last-child td{border-bottom:0}.catalog-table tbody tr:hover{background:var(--surface-soft)}.row-title{background:none;color:var(--ink);padding:0;font-size:13px;font-weight:600;text-align:left;line-height:1.65;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;max-width:100%}.row-title:hover{color:var(--accent)}.row-context{display:flex;gap:10px;margin-top:5px;font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden}.row-context span:last-child{overflow:hidden;text-overflow:ellipsis}.catalog-table small{display:block;color:var(--muted);font-size:10px;margin-top:5px}.project-cell>span{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.6}.state-tag{display:inline-block;font-size:10px;white-space:nowrap;border-radius:4px;padding:4px 7px;color:var(--success);background:var(--success-bg)}.state-tag.paused{color:var(--danger);background:var(--danger-bg)}.state-tag.unverified{color:var(--warning);background:var(--warning-bg)}.row-action{font-size:11px;white-space:nowrap}.catalog-pagination{display:flex;justify-content:space-between;align-items:center;border-top:1px solid var(--line);padding-top:14px;color:var(--muted);font-size:11px}.catalog-pagination>div{display:flex;gap:8px}.catalog-empty{padding:35px;text-align:center;color:var(--muted);font-size:13px}.catalog-secondary{display:flex;gap:20px;align-items:start;border-top:1px solid var(--line);padding-top:16px;margin-top:18px;font-size:11px;color:var(--muted)}.catalog-secondary>details{flex:1;max-width:700px}.catalog-secondary summary{cursor:pointer}.catalog-secondary dl{display:grid;grid-template-columns:65px minmax(0,1fr);gap:10px;line-height:1.8}.catalog-secondary dd{margin:0}.catalog-secondary>button{font-size:11px;flex-shrink:0}.scan-notice{font-size:10px;color:var(--muted);margin:10px 0 0}.knowledge-detail{inset:0 0 0 auto;margin:0;width:min(640px,100vw);max-width:100vw;height:100dvh;max-height:100dvh;border:0;border-left:1px solid var(--line);background:var(--surface);color:var(--ink);padding:0;box-shadow:-12px 0 60px #0002;overflow:auto}.knowledge-detail::backdrop{background:#102c4566}.detail-heading{display:flex;justify-content:space-between;align-items:start;gap:16px;padding:26px 28px 18px}.detail-heading>div{min-width:0}.detail-heading h2{font-size:18px;line-height:1.7;margin:10px 0 6px;overflow-wrap:anywhere}.detail-heading p{font-size:11px;color:var(--muted);overflow-wrap:anywhere}.close-detail{background:var(--surface-soft);color:var(--muted);font-size:26px;border-radius:8px;line-height:1;width:32px;height:32px;flex-shrink:0}.detail-tabs{display:flex;gap:20px;padding:0 28px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--surface);z-index:1}.detail-tabs button{padding:14px 0;font-size:12px;background:none;color:var(--muted);border-bottom:2px solid transparent}.detail-tabs button[aria-pressed=true]{border-color:var(--accent);color:var(--accent);font-weight:600}.detail-tabs b{font-size:10px;margin-left:4px}.detail-body{padding:24px 28px 40px;font-size:13px;overflow-wrap:anywhere;line-height:1.8}.detail-body h3{font-size:13px;margin:20px 0 8px}.detail-body h3:first-child{margin-top:0}.content-detail ul{padding-left:20px}.detail-reason{color:var(--muted);font-size:12px}.detail-tags{display:flex;gap:6px;flex-wrap:wrap;margin:20px 0}.detail-tags button{font-size:11px;border:1px solid var(--line);background:var(--surface-soft);color:var(--accent);padding:3px 7px;border-radius:4px}.merge-result{padding:14px;background:var(--accent-soft);border-radius:8px;font-size:12px}.merge-result p{margin:6px 0 0}.source-detail article{border-top:1px solid var(--line);padding:16px 0}.source-heading{display:flex;gap:12px;justify-content:space-between;margin-bottom:6px;font-size:12px}.source-heading small{flex-shrink:0;color:var(--muted);font-size:10px}.source-detail article>a{font-size:11px;display:block;margin-top:8px}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
@media(max-width:1000px){.catalog-table td,.catalog-table th{padding-left:6px;padding-right:6px}.catalog-filters{gap:10px}.row-action{font-size:10px}}
@media(max-width:650px){.knowledge-catalog{padding:14px}.catalog-search{gap:8px;flex-wrap:wrap}.search-field{flex-basis:70%}.catalog-filters{grid-template-columns:1fr 1fr;gap:12px}.catalog-status-row{align-items:start;flex-direction:column;gap:10px}.catalog-status-row nav button{padding:6px 8px;font-size:11px}.catalog-status-row nav b{margin-left:4px}.catalog-table th:first-child{width:63%}.catalog-table th:nth-child(3){width:37%}.catalog-table th:nth-child(2),.catalog-table th:nth-child(4),.catalog-table th:last-child,.catalog-table td:nth-child(2),.catalog-table td:nth-child(4),.catalog-table td:last-child{display:none}.catalog-table td{padding-top:14px;padding-bottom:14px}.row-title{font-size:12px}.row-context{font-size:9px}.state-tag{font-size:9px;padding:3px 5px}.catalog-secondary{gap:12px;flex-wrap:wrap}.catalog-secondary details{flex-basis:100%}.catalog-pagination .button{padding:6px 9px;font-size:10px}.detail-heading{padding:20px}.detail-heading h2{font-size:16px}.detail-tabs{padding:0 20px;gap:16px}.detail-body{padding:20px}.detail-tabs button{font-size:11px}}
</style>
