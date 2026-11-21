<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import type { AutomaticMergeJob, AutomaticMergeChange, ExperienceFilters } from '../api/contracts'
import { useApiContext } from '../api/context'
import ExperienceKnowledge from './ExperienceKnowledge.vue'
import ConflictAutomation from './ConflictAutomation.vue'
import ExperienceText from './ExperienceText.vue'
import { experienceTitle } from '../utils/experiencePresentation'
import AppIcon from './AppIcon.vue'
import { createPagePolling } from '../utils/pagePolling'

const props = defineProps<{ filters: ExperienceFilters; experienceId?: string; refreshKey: number }>()
const emit = defineEmits<{ changed: [] }>()
const { api } = useApiContext()
const items = ref<AutomaticMergeJob[]>([]), error = ref(''), loading = ref(false), writing = ref('')
const offset = ref(0), nextOffset = ref<number | null>(null)
let disposed = false, version = 0, signature = ''
let controller: AbortController | undefined, writeController: AbortController | undefined
const polling = createPagePolling()
let failures = 0
const stateLabel: Record<string,string> = {
  pending:'等待比较', running:'正在比较', waiting:'等待整理或审阅', succeeded:'比较完成', failed:'比较失败',
  merged:'已合并', rejected:'分别保留', invalidated:'合并已暂停', resolved:'已处理', obsolete:'来源已停用',
}
function jobOutcome(job: AutomaticMergeJob) {
  if (job.local_pending) return '规则整理中'
  if (job.changes.some(change => change.state === 'merged')) return '已自动合并'
  if (job.state === 'succeeded' && !job.changes.length) return '暂无比较对象'
  return stateLabel[job.state]
}
function jobSummary(job: AutomaticMergeJob) {
  const merged = job.changes.find(change => change.state === 'merged')
  if (merged) return `${merged.before.length} 份来源 → 1 条规则`
  return job.changes.length ? `${job.changes.length} 项处理结果` : '原始经验已保存'
}
function outcome(change: AutomaticMergeChange) {
  if (!change.review) return change.reason
  const review = change.review
  if (review.status === 'obsolete') return '来源已在其他审阅中停用，当前无需重复处理。'
  if (review.status === 'pending' && review.automatic_resolution?.state === 'verifying') return '系统正在验证双方真实测试证据，完成后自动决定；证据不足时会请求确认。'
  if (review.status === 'pending') return review.relation === 'conflict' ? '检测到行为冲突，相关规则已暂停复用，等待审阅。' : '关系尚不确定，保留原经验，等待核对适用条件。'
  return ({prefer_source:'已采纳指定来源，另一条规则已暂停复用。',keep_separate:'两条记录分别保留，相关规则均暂停复用。',
    confirm_equivalent:'已确认等价，满足条件的规则合并引用。',confirm_compatible:'已确认兼容，两条规则分别保留。'})[review.resolution ?? 'keep_separate']
}
function conflictQuotes(change: AutomaticMergeChange, sourceId: string) {
  return change.review?.details.filter(detail => detail.source_ids.length === 1 && detail.source_ids[0] === sourceId && detail.field?.endsWith('.solution')) ?? []
}
function orderedChanges(changes: AutomaticMergeChange[]) {
  const priority = (change: AutomaticMergeChange) => change.review?.automatic_resolution ? 0 : change.kind === 'review' ? 1 : 2
  return [...changes].sort((a,b) => priority(a) - priority(b))
}
function resultLabel(change:AutomaticMergeChange){
  if(change.state==='merged')return '1 条共同经验'
  if(change.review?.resolution==='keep_separate')return '隔离冲突，保留双方'
  if(change.review?.resolution==='prefer_source')return '保留已采纳规则'
  if(change.review?.resolution==='confirm_equivalent')return '确认等价，合并引用'
  if(change.review?.resolution==='confirm_compatible')return '确认兼容，分别保留'
  if(change.review?.automatic_resolution?.state==='verifying')return '正在验证'
  if(change.review?.automatic_resolution?.state==='needs_confirmation')return '证据不足，需要确认'
  if(change.state==='pending'||change.state==='waiting')return '等待处理'
  return ({rejected:'分别保留',invalidated:'合并已暂停',obsolete:'无需重复处理',failed:'需要重试'} as Record<string,string>)[change.state]||'查看处理结果'
}
async function load(page = offset.value, silent = false) {
  if (!api.listAutomaticMerges || disposed) return
  polling.cancel()
  controller?.abort(); const active = new AbortController(); controller = active; const current = ++version
  if (!silent) loading.value = true
  try {
    const data = await api.listAutomaticMerges({...props.filters, ...(props.experienceId ? {experience_id: props.experienceId} : {})}, page, active.signal)
    if (disposed || active.signal.aborted || current !== version) return
    const nextSignature = JSON.stringify(data)
    if (signature && signature !== nextSignature) emit('changed')
    signature = nextSignature; items.value = data.items; offset.value = page; nextOffset.value = data.next_offset; error.value = ''; failures = 0
  } catch (cause) {
    if (!active.signal.aborted && !disposed && current === version) {
      failures++
      const reason = cause instanceof Error ? cause.message : '读取自动比较结果失败。'
      error.value = items.value.length ? `本次刷新未完成，已保留上次结果，将稍后重试。${reason}` : reason
    }
  } finally {
    if (!disposed && current === version) {
      loading.value = false
      const working = items.value.some(job => job.local_pending || ['pending','running','waiting'].includes(job.state)
        || job.changes.some(change => ['pending','running','waiting'].includes(change.state) || change.review?.automatic_resolution?.state === 'verifying'))
      polling.schedule(() => void load(offset.value, true), failures ? Math.min(30000, 5000 * 2 ** Math.min(failures, 3)) : working ? 5000 : 30000)
    }
  }
}
async function retry(id: string) {
  if (!api.retryAutomaticMerge || writing.value) return
  writing.value = id; writeController = new AbortController()
  try { await api.retryAutomaticMerge(id, writeController.signal); if (!disposed) await load(offset.value, true) }
  catch (cause) { if (!disposed) error.value = cause instanceof Error ? cause.message : '重试失败。' }
  finally { if (!disposed) writing.value = '' }
}
watch(() => [props.filters, props.experienceId, props.refreshKey], () => { signature = ''; void load(0) }, {immediate:true, deep:true})
onBeforeUnmount(() => { disposed = true; ++version; controller?.abort(); writeController?.abort(); polling.dispose() })
</script>

<template>
  <section v-if="api.listAutomaticMerges" class="auto-merge-panel" aria-labelledby="auto-merge-title">
    <div class="auto-merge-heading"><div><h2 id="auto-merge-title">自动比较与合并</h2><p>先看处理结论，再核对方案差异与测试证据。</p></div>
      <button class="button secondary small" type="button" :disabled="loading" @click="load()">刷新结果</button>
    </div>
    <p v-if="props.experienceId" class="field-hint">当前展示这条经验的自动处理记录。</p>
    <p v-if="error" class="error-notice" role="alert">{{ error }}</p>
    <p v-if="loading && !items.length" role="status">正在读取自动比较记录…</p>
    <p v-else-if="!items.length && !error" class="auto-merge-empty">项目修复成功并保存经验后，这里会显示自动比较结果。</p>
    <details v-for="(job, index) in items" :key="job.id" :open="index === 0" class="auto-merge-job" data-testid="auto-merge-job">
      <summary>
        <div class="job-summary"><strong>{{ experienceTitle(job.source) }}</strong><small>{{ new Date(job.created_at).toLocaleString('zh-CN') }} · {{ jobSummary(job) }}</small></div>
        <div class="job-actions"><span class="auto-merge-state" :class="job.state">{{ jobOutcome(job) }}</span><span class="disclosure-action"><span class="when-closed">展开</span><span class="when-open">收起</span><AppIcon name="chevron-down" class="disclosure-chevron" :size="20" /></span></div>
      </summary>
      <div class="job-content">
      <div class="auto-merge-context"><RouterLink :to="'/tasks/' + job.source.source_task_id">来源修复任务 ↗</RouterLink><span v-if="job.candidate_total">候选 {{ job.candidate_total }} 条 · 已比较 {{ job.compared_candidates }}<span v-if="job.deferred_candidates"> · 其余 {{ job.deferred_candidates }} 条未比较</span></span></div>
      <p v-if="job.local_pending || job.state==='running' || job.state==='pending'" class="field-hint">{{ job.notice || '正在比较经验与测试证据，结果自动更新。' }}</p>
      <p v-if="!job.changes.length && !job.local_pending && job.state === 'succeeded'" class="field-hint">{{ job.notice || '当前没有符合条件的比较对象；后续修复会自动与本条比较。' }}</p>
      <details v-for="(change, changeIndex) in orderedChanges(job.changes)" :key="change.id" :open="changeIndex === 0" class="auto-merge-change" data-testid="auto-merge-change">
        <summary><strong>{{ change.kind === 'shared_principle' ? '跨项目共同规则' : change.kind === 'equivalent_rules' ? '同仓库等价规则' : '规则冲突与关系' }}</strong><span class="change-outcome">{{ resultLabel(change) }} <AppIcon name="chevron-down" class="disclosure-chevron" :size="20" /></span></summary>
        <div class="change-result" :class="{merged:change.state==='merged',isolated:change.review?.resolution==='keep_separate'}">
          <span class="result-eyebrow">{{ change.review ? '处理结果' : '合并结果' }} · {{ change.before.length }} 份来源</span>
          <h3>{{ resultLabel(change) }}</h3>
          <template v-if="change.after && change.state==='merged'">
            <h4>{{ change.after.title }}</h4>
            <ExperienceText :text="change.after.principle || change.after.issue?.solution || ''" :limit="180" />
            <span class="result-footnote">{{ change.before.length }} 份来源 → 1 条{{ change.kind==='shared_principle'?'共同':'规范' }}规则 · 原始经验与业务约束保留</span>
          </template>
          <ExperienceText v-else :text="outcome(change)" :limit="140" />
          <ExperienceText v-if="change.review?.note && !change.review.automatic_resolution" :text="'处理依据：'+change.review.note" :limit="140" />
        </div>
        <ConflictAutomation v-if="change.review" :review="change.review" />
        <div class="source-comparison-heading"><h3>{{ change.kind==='review'?'处理前 · 冲突双方':'合并前 · 原始经验' }}</h3><span>先看方案差异，完整记录可展开</span></div>
        <div class="source-differences" data-testid="source-differences">
          <article v-for="(source,sourceIndex) in change.before" :key="source.id" class="auto-merge-source" :class="source.id===job.source.id?'source-current':'source-existing'">
            <div class="source-label-row"><span class="auto-merge-source-label"><span class="source-number">{{ String(sourceIndex+1).padStart(2,'0') }}</span><template v-if="change.review">来源 {{ change.review.sources.findIndex(s=>s.id===source.id)+1 }} · </template>{{ source.id===job.source.id?'本次修复':'已有经验' }}</span><span v-if="source.evidence?.length" class="source-tested">{{ source.evidence.map(e=>e.tests+' 项通过').join(' / ') }}</span></div>
            <h4>{{ experienceTitle(source) }}</h4>
            <blockquote v-for="detail in conflictQuotes(change,source.id)" :key="detail.field"><ExperienceText :text="detail.suggestion" :limit="120" /></blockquote>
            <ExperienceText v-if="!conflictQuotes(change,source.id).length" :text="source.solution" :limit="120" />
            <p v-if="change.review?.status==='resolved' && change.review.resolution==='prefer_source'" class="auto-merge-decision">{{ change.review.preferred_source_id===source.id?'处理后：采纳此来源的规则':'处理后：冲突规则暂停复用，原始经验保留' }}</p>
            <details class="source-original"><summary>完整方案、条件与证据 <AppIcon name="chevron-down" class="disclosure-chevron" :size="18" /></summary><p>{{ source.task_summary }}</p><ExperienceKnowledge :value="source" /></details>
            <RouterLink :to="{path:'/experiences',query:{id:source.id,view:'originals'}}">查看来源记录 ↗</RouterLink>
          </article>
        </div>
        <p v-if="change.more_sources" class="field-hint">展示前 20 份来源，完整来源可在详情中分页查看。</p>
        <details v-if="change.after && change.state==='merged'" class="decision-audit">
          <summary>适用条件、业务边界与合并引证 <AppIcon name="chevron-down" class="disclosure-chevron" :size="18" /></summary>
          <h4>适用条件</h4><ul><li v-for="text in change.after.applicability" :key="text">{{ text }}</li></ul>
          <h4>保留的业务约束</h4><ul><li v-for="text in [...(change.after.constraints||[]),...(change.after.boundaries||[])]" :key="text">{{ text }}</li></ul>
          <div v-for="instance in change.after.instances" :key="instance.experience_id"><strong>{{ instance.repository_id }}</strong><ul><li v-for="text in instance.constraints" :key="text">{{ text }}</li></ul></div>
          <div v-for="binding in change.after.bindings" :key="binding.source_id"><blockquote v-for="(support,i) in binding.supports" :key="i">{{ support.quote }}</blockquote></div>
          <p v-if="change.evidence_count">保留 {{ change.evidence_count }} 份独立测试证据。</p>
          <p>{{ change.reason }}</p><p v-if="change.review_result">独立复核：{{ change.review_result.reason }}</p>
        </details>
        <details v-if="change.review" class="decision-audit"><summary>冲突依据与决定历史 <AppIcon name="chevron-down" class="disclosure-chevron" :size="18" /></summary>
          <template v-for="(detail,detailIndex) in change.review.details.filter(d=>d.source_ids.length>1)" :key="detailIndex"><h4>{{ detail.message }}</h4><p>{{ detail.suggestion }}</p></template>
          <p v-if="change.review.note">处理依据：{{ change.review.note }}</p>
          <ul v-if="change.history?.length" class="auto-merge-history"><li v-for="event in change.history" :key="event.version">{{ event.action==='reopen'?'撤销并重新审阅':event.data.actor==='system'?'系统自动保存决定':'保存处理结果' }} · {{ new Date(event.created_at).toLocaleString('zh-CN') }}<p>{{ event.data.note }}</p></li></ul>
        </details>
        <p v-if="change.kind==='review' && change.state==='pending' && change.review?.automatic_resolution?.state!=='verifying'" class="confirmation-action">需要确认：请在下方“冲突处理”中核对证据并保存决定。</p>
      </details>
      <details class="job-audit"><summary>完整任务描述与比较记录 <AppIcon name="chevron-down" class="disclosure-chevron" :size="18" /></summary><p>{{ job.source.task_summary }}</p><p>{{ job.notice }}</p></details>
      <button v-if="job.state==='failed' && api.retryAutomaticMerge" type="button" class="button secondary small" :disabled="!!writing" @click="retry(job.id)">{{ writing===job.id?'正在提交…':'重试未完成的比较' }}</button>
      </div>
    </details>
    <div v-if="offset || nextOffset!==null" class="auto-merge-pagination"><button class="text-button" type="button" :disabled="loading||!offset" @click="load(Math.max(0,offset-5))">上一页</button><span>第 {{ offset/5+1 }} 页</span><button class="text-button" type="button" :disabled="loading||nextOffset===null" @click="nextOffset!==null && load(nextOffset)">下一页</button></div>
  </section>
</template>

<style scoped>
.auto-merge-panel{margin:0;padding:20px;border:0;background:transparent}
.auto-merge-heading h2{font-size:16px}.auto-merge-heading p{margin:4px 0 12px;font-size:12px}
.auto-merge-job{padding:0;margin-top:24px;border:1px solid var(--line);border-radius:14px;background:var(--surface);box-shadow:var(--panel-shadow);overflow:hidden}
.auto-merge-job[open]{border-color:var(--accent-border)}
.auto-merge-job>summary{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:20px;align-items:center;padding:20px 22px;transition:background 160ms ease}
.auto-merge-job>summary:hover{background:var(--hover)}
.auto-merge-job[open]>summary{background:var(--accent-soft);border-bottom:1px solid var(--accent-border)}
.auto-merge-job>summary:focus-visible{outline-offset:-4px;border-radius:12px}
.job-summary{min-width:0}.job-summary strong{font-size:15px;line-height:1.7;display:block;overflow-wrap:anywhere}.job-summary small{font-size:11px;color:var(--muted);display:block;margin-top:5px}
.job-actions{display:flex;align-items:center;gap:16px;flex-shrink:0}.auto-merge-state{padding:5px 10px;border:1px solid var(--accent-border);font-size:11px;line-height:1.5}
.disclosure-action{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--accent);font-weight:600;white-space:nowrap}
.disclosure-action .disclosure-chevron{box-sizing:content-box;padding:7px;border:1px solid var(--accent-border);border-radius:8px;background:var(--surface)}
.job-content{padding:18px 20px 20px;background:var(--surface-soft)}
.auto-merge-context{margin:0 0 14px;gap:12px;font-size:11px;display:flex;flex-wrap:wrap}
.auto-merge-change{margin-top:18px;padding:18px;border:1px solid var(--line);border-radius:12px;background:var(--surface);box-shadow:0 2px 5px #00000004}
.auto-merge-change>summary{font-size:12px;min-height:28px;gap:16px}.auto-merge-change>summary:focus-visible{outline-offset:4px;border-radius:3px}
.auto-merge-change[open]>summary{padding-bottom:14px;border-bottom:1px solid var(--line)}
.change-outcome{color:var(--accent);display:flex;align-items:center;gap:12px}
.change-result{border:1px solid var(--accent-border);border-left:4px solid var(--accent);padding:18px 20px;background:var(--accent-soft);border-radius:8px;margin:18px 0 24px}
.change-result.isolated{border-color:var(--warning-border);border-left-color:var(--warning);background:var(--warning-bg)}
.result-eyebrow,.result-footnote{font-size:11px;color:var(--muted)}.result-footnote{display:block;margin-top:12px}.change-result h3{font-size:20px;margin:4px 0 8px}.change-result h4{font-size:13px;margin:8px 0}
.source-comparison-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:20px 0 12px}.source-comparison-heading h3{font-size:14px;margin:0}.source-comparison-heading>span{font-size:11px;color:var(--muted)}
.source-differences{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;align-items:start}
.auto-merge-source{padding:16px;margin:0;min-width:0;border:1px solid var(--accent-border);border-top:4px solid var(--accent);border-radius:10px;background:var(--accent-soft)}
.source-existing{border-color:var(--source-existing-border);border-top-color:var(--source-existing-ink);background:var(--source-existing-bg)}
.source-label-row{display:flex;justify-content:space-between;align-items:center;gap:10px;padding-bottom:12px;margin-bottom:12px;border-bottom:1px solid var(--accent-border)}
.source-existing .source-label-row{border-color:var(--source-existing-border)}
.auto-merge-source-label{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600;color:var(--accent)}
.source-existing .auto-merge-source-label{color:var(--source-existing-ink)}
.source-number{display:inline-grid;place-items:center;min-width:26px;height:26px;border-radius:6px;background:var(--surface);font-size:11px;font-variant-numeric:tabular-nums}
.auto-merge-source h4{font-size:12px;font-weight:500;line-height:1.7;margin:10px 0;color:var(--muted)}
.source-tested{font-size:11px;white-space:nowrap;color:var(--success)}.auto-merge-source blockquote{margin:8px 0;padding:0;border:0}.auto-merge-source>a{font-size:11px;margin-top:12px}
.source-original{margin-top:16px;padding:0 12px;background:var(--surface);border:1px solid var(--line);border-radius:8px}
.source-original summary,.decision-audit>summary,.job-audit>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;color:var(--accent);font-size:12px;min-height:42px}
.source-original[open]{padding-bottom:14px}.source-original[open]>summary{border-bottom:1px solid var(--line);margin-bottom:12px}
.source-original>p{font-size:13px;line-height:1.8}
.decision-audit,.job-audit{padding:0 14px;border:1px solid var(--line);border-radius:8px;background:var(--surface-soft);margin-top:16px;font-size:12px;line-height:1.8;overflow-wrap:anywhere}
.job-audit{background:var(--surface)}.decision-audit[open],.job-audit[open]{padding-bottom:14px}
.decision-audit[open]>summary,.job-audit[open]>summary{border-bottom:1px solid var(--line);margin-bottom:12px}
.decision-audit h4{font-size:12px;margin:12px 0 4px}.confirmation-action{background:var(--warning-bg);color:var(--warning);padding:12px;border-radius:8px;font-size:12px}.auto-merge-history{padding-left:18px}
@media(max-width:900px){.auto-merge-job>summary{grid-template-columns:1fr;gap:12px}.job-actions{justify-content:space-between}.source-differences{gap:14px}}
@media(max-width:760px){.source-differences{grid-template-columns:1fr}.auto-merge-panel{padding:10px 0}.auto-merge-job>summary{padding:16px}.job-content{padding:14px}.auto-merge-change{padding:12px}.auto-merge-change>summary{flex-wrap:wrap;gap:8px}.change-outcome{justify-content:space-between;flex:1}.source-comparison-heading{align-items:start;flex-direction:column;gap:4px}.change-result{padding:14px;margin-bottom:20px}.change-result h3{font-size:18px}.job-summary strong{font-size:13px}.source-label-row{flex-wrap:wrap}.auto-merge-source{padding:14px}.source-original{padding-right:10px;padding-left:10px}}
@media(prefers-reduced-motion:reduce){.auto-merge-job>summary{transition:none}}
</style>
