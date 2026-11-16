<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { ExperienceRecovery, KnowledgeCard } from '../api/contracts'
import { useApiContext } from '../api/context'
import ExperienceText from './ExperienceText.vue'
const props=defineProps<{card:KnowledgeCard}>()
const emit=defineEmits<{changed:[value:ExperienceRecovery]}>()
const {api}=useApiContext()
const rows=ref<ExperienceRecovery[]>([]),error=ref(''),busy=ref(false),note=ref(''),taskId=ref('')
const tasks=ref<{id:string;label:string;project_label:string;tests:number;created_at:string}[]>([])
const latest=computed(()=>rows.value[0])
const running=computed(()=>['pending','running','waiting_task'].includes(latest.value?.state||''))
const labels={pending:'等待验证',running:'正在验证',waiting_task:'等待修复任务',needs_evidence:'需要补充证据',needs_confirmation:'需要确认',succeeded:'已恢复限定范围复用',failed:'验证中断',invalidated:'暂未恢复复用',kept:'保留原状态'}
const stateLabels={active:'可复用',paused:'暂停复用',unverified:'待验证'}
let controller:AbortController|undefined,writeController:AbortController|undefined,timer:ReturnType<typeof setTimeout>|undefined,disposed=false,generation=0,last=''
async function load(){
  if(!api.listExperienceRecovery||disposed)return
  controller?.abort();const active=new AbortController();controller=active;const version=++generation
  try{const values=await api.listExperienceRecovery(props.card.id,active.signal);if(disposed||active.signal.aborted||version!==generation)return
    rows.value=values;error.value='';const current=values[0]
    if(current&&last!==current.updated_at+current.state){last=current.updated_at+current.state;emit('changed',current)}
    if(current&&['needs_evidence','needs_confirmation','failed'].includes(current.state)&&!tasks.value.length&&api.listRecoveryTasks)tasks.value=await api.listRecoveryTasks(props.card.id,active.signal)
  }catch(e){if(!active.signal.aborted&&!disposed)error.value=e instanceof Error?e.message:'读取补证记录失败'}
  finally{if(!disposed&&version===generation){if(timer)clearTimeout(timer);timer=setTimeout(load,5000)}}
}
async function action(callback:(signal:AbortSignal)=>Promise<unknown>){
  if(busy.value||disposed)return;busy.value=true;writeController=new AbortController()
  try{await callback(writeController.signal);if(!disposed)await load()}
  catch(e){if(!disposed)error.value=e instanceof Error?e.message:'操作失败，请重试'}
  finally{if(!disposed)busy.value=false}
}
function start(withTask=false){if(api.startExperienceRecovery)void action(signal=>api.startExperienceRecovery!(props.card.id,{retry:!!latest.value,...(withTask&&taskId.value?{task_id:taskId.value}:{})},signal))}
function confirm(accept:boolean){if(latest.value&&api.confirmExperienceRecovery)void action(signal=>api.confirmExperienceRecovery!(latest.value!.id,{version:latest.value!.version,note:note.value.trim(),accept},signal))}
function reopen(id:string,version:number){if(api.reopenConflict)void action(signal=>api.reopenConflict!(id,version,note.value.trim(),signal))}
watch(()=>props.card.id,()=>{rows.value=[];tasks.value=[];note.value='';taskId.value='';last='';void load()},{immediate:true})
onBeforeUnmount(()=>{disposed=true;generation++;controller?.abort();writeController?.abort();if(timer)clearTimeout(timer)})
</script>
<template>
  <section class="recovery-panel" aria-label="验证与恢复复用" data-testid="experience-recovery">
    <div class="recovery-heading"><h3>验证与恢复复用</h3><button v-if="api.startExperienceRecovery&&(card.state!=='active'||latest)" class="button primary small" :disabled="busy||running" @click="start()">{{running?'处理中…':latest?'重新验证与审阅':'补充验证并审阅'}}</button></div>
    <p v-if="!latest" class="field-hint">核对来源和完整测试，在独立副本中重复验证。证据充分时自动恢复到已验证的项目范围；不足时在这里补充或确认。</p>
    <p v-if="error" role="alert" class="error-notice">{{error}}</p>
    <template v-if="latest">
      <div class="recovery-result" :class="latest.state"><strong>{{labels[latest.state]}}</strong><small v-if="latest.state==='succeeded'">{{latest.actor==='system'?'系统自动处理':'用户确认后处理'}}</small><p>{{latest.reason}}</p></div>
      <div class="recovery-transition"><div><small>处理前</small><strong>{{stateLabels[latest.before.state]}}</strong><span>{{latest.before.evidence_count}} 份原验证依据</span></div><b>→</b><div><small>当前结果</small><strong>{{latest.state==='succeeded'?'限定范围可复用':labels[latest.state]}}</strong><span>{{latest.runs.length}} 次补充测试</span></div></div>
      <div v-if="latest.scope" class="reuse-boundary"><strong>{{latest.state==='succeeded'?'只在以下范围恢复':'上次验证范围（当前未授权）'}}</strong><p>{{latest.scope.project_label}}</p><dl><dt>仓库</dt><dd>{{latest.scope.repository_id}}</dd><dt>代码版本</dt><dd :title="latest.scope.source_revision">{{latest.scope.source_revision.split(';').at(-1)?.slice(0,28)}}…</dd><dt>使用条件</dt><dd>项目、版本和测试配置同时匹配；其他上下文继续隔离。</dd></dl></div>
      <RouterLink v-if="latest.task_id" :to="'/tasks/'+latest.task_id" class="recovery-link">查看补充修复任务 ↗</RouterLink>
      <div v-if="['needs_evidence','needs_confirmation','failed','kept'].includes(latest.state)&&!running" class="supply-evidence">
        <h4>补充实际工程证据</h4>
        <RouterLink v-if="!latest.task_id" class="button primary small" :to="{path:'/',query:{knowledge_recovery_id:latest.id}}">上传项目或选择项目，补充修复验证</RouterLink>
        <p v-else class="field-hint">可先在关联任务中完成验收和经验提取，再点击重新验证；也可关联另一项已完成任务。</p>
        <template v-if="tasks.length"><label for="recovery-evidence-task">关联已有的成功修复</label><select id="recovery-evidence-task" v-model="taskId"><option value="">请选择来源任务</option><option v-for="task in tasks" :key="task.id" :value="task.id">{{task.project_label}} · {{new Date(task.created_at).toLocaleString('zh-CN')}} · {{task.tests}} 项通过</option></select><button class="button secondary small" :disabled="busy||!taskId" @click="start(true)">验证这项任务与原经验的关系</button></template>
      </div>
      <div v-if="latest.can_confirm" class="recovery-confirmation"><h4>需要你确认来源关系</h4><p>测试已经通过，但系统无法确认新修复与旧经验是否对应。请比较方案；确认后仍只允许在新验证项目范围内使用。</p><div class="recovery-texts"><div><strong>旧经验</strong><ExperienceText :text="latest.before.summary" /></div><div v-if="latest.candidate"><strong>本次验证后的方案</strong><ExperienceText :text="latest.candidate.issues?.[latest.candidate_rule_index ?? 0]?.solution || latest.candidate.solution" /></div></div><label for="recovery-note">确认依据</label><textarea id="recovery-note" v-model="note" rows="3" maxlength="2000" placeholder="说明对应的业务条件、实现和验证证据"/><div class="recovery-actions"><button class="button primary small" :disabled="busy||!note.trim()" @click="confirm(true)">确认对应，按验证范围恢复</button><button class="button secondary small" :disabled="busy||!note.trim()" @click="confirm(false)">继续保留待验证</button></div></div>
      <div v-else-if="latest.state==='needs_confirmation'&&latest.reviews?.length" class="recovery-confirmation"><h4>重新审阅相关冲突</h4><p>现有证据未能排除重叠条件下的冲突，请补充判断依据后重新打开审阅。</p><label for="review-reopen-note">重新审阅依据</label><textarea id="review-reopen-note" v-model="note" rows="2" maxlength="2000"/><div v-for="review in latest.reviews" :key="review.id" class="recovery-actions"><button v-if="review.status==='resolved'" class="button secondary small" :disabled="busy||!note.trim()" @click="reopen(review.id,review.version)">重新打开此项冲突审阅</button><RouterLink :to="{path:'/experiences',query:{view:'activity'}}">前往冲突处理 ↗</RouterLink></div></div>
      <div v-if="latest.runs.length" class="recovery-test-summary"><div v-for="run in latest.runs" :key="run.id" :class="run.outcome"><small>补充测试 · 第 {{run.repeat}} 次</small><strong>{{run.outcome==='passed'?run.tests+' 项通过':run.outcome==='failed'?run.failures+' 项失败':'验证异常'}}</strong></div></div><details v-if="latest.after" class="recovery-details"><summary>方案与复用方式的前后对比 ⌄</summary><div class="recovery-texts"><div><strong>处理前 · {{stateLabels[latest.before.state]}}</strong><ExperienceText :text="latest.before.summary" :limit="160" /></div><div><strong>处理后 · 仅在验证范围内使用</strong><ExperienceText :text="latest.after.summary" :limit="160" /></div></div></details><details v-if="latest.runs.length" class="recovery-details"><summary>补充测试结果 · {{latest.runs.length}} 次 ⌄</summary><table><thead><tr><th>轮次</th><th>结果</th><th>测试规模</th></tr></thead><tbody><tr v-for="run in latest.runs" :key="run.id"><td>第 {{run.repeat}} 次</td><td>{{run.outcome==='passed'?'通过':run.outcome==='failed'?'失败':'异常'}}</td><td>{{run.tests}} 项 · {{run.failures}} 失败 · {{run.skipped}} 跳过</td></tr></tbody></table><details v-for="run in latest.runs" :key="run.id"><summary>第 {{run.repeat}} 次证据指纹与失败用例 ⌄</summary><code>{{run.artifact_sha256}}</code><ul><li v-for="test in run.failed_tests" :key="test">{{test}}</li></ul></details></details>
      <ol class="recovery-steps"><li v-for="(step,index) in latest.steps" :key="index"><span>{{index+1}}</span><div><p>{{step.reason}}</p><small>{{new Date(step.at).toLocaleString('zh-CN')}}</small></div></li></ol>
      <details v-if="rows.length>1" class="recovery-details"><summary>之前的处理记录 · {{rows.length-1}} 次 ⌄</summary><p v-for="row in rows.slice(1)" :key="row.id">{{new Date(row.created_at).toLocaleString('zh-CN')}} · {{labels[row.state]}} · {{row.reason}}</p></details>
    </template>
  </section>
</template>
<style scoped>
.recovery-panel{font-size:13px;min-width:0}.recovery-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.recovery-heading h3{font-size:15px}.recovery-result{padding:14px;border:1px solid var(--line);border-radius:10px;background:var(--surface-soft)}.recovery-result>small{display:block;color:var(--muted);font-size:10px;margin-top:4px}.recovery-test-summary{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:16px}.recovery-test-summary>div{display:grid;gap:4px;padding:10px 14px;border:1px solid var(--line);border-radius:8px}.recovery-test-summary small{font-size:10px;color:var(--muted)}.recovery-test-summary strong{font-size:13px}.recovery-test-summary .passed{background:var(--success-bg);color:var(--success)}.recovery-test-summary .failed,.recovery-test-summary .invalid{background:var(--warning-bg);color:var(--warning)}.recovery-result p{margin:5px 0 0;line-height:1.8}.recovery-result.succeeded{background:var(--success-bg);border-color:var(--success-border)}.recovery-result.needs_confirmation,.recovery-result.needs_evidence,.recovery-result.invalidated{background:var(--warning-bg);border-color:var(--warning-border)}.recovery-transition{display:grid;grid-template-columns:1fr auto 1fr;gap:15px;align-items:center;padding:18px 0}.recovery-transition>div{display:grid;gap:5px}.recovery-transition small,.recovery-transition span{font-size:11px;color:var(--muted)}.recovery-transition strong{font-size:14px}.recovery-transition>b{color:var(--accent)}.reuse-boundary{border-left:3px solid var(--accent);padding:12px 15px;background:var(--accent-soft);overflow-wrap:anywhere}.reuse-boundary dl{display:grid;grid-template-columns:65px minmax(0,1fr);gap:6px;font-size:11px}.reuse-boundary dd{margin:0}.reuse-boundary p{margin:5px 0}.recovery-link{display:block;margin:12px 0}.supply-evidence,.recovery-confirmation{border-top:1px solid var(--line);margin-top:18px;padding-top:14px}.supply-evidence h4,.recovery-confirmation h4{margin:0 0 10px}.supply-evidence label,.recovery-confirmation label{display:block;font-size:12px;margin:14px 0 6px}.supply-evidence select{font-size:12px;margin-bottom:10px}.recovery-details{margin-top:16px;font-size:12px;overflow-wrap:anywhere}.recovery-details>summary{color:var(--accent)}.recovery-details table{border-collapse:collapse;width:100%;margin:10px 0}.recovery-details td,.recovery-details th{padding:8px;text-align:left;border-bottom:1px solid var(--line)}.recovery-steps{padding:0;list-style:none;display:grid;gap:12px;margin-top:18px}.recovery-steps li{display:flex;gap:10px}.recovery-steps li>span{background:var(--accent-soft);color:var(--accent);width:22px;height:22px;border-radius:50%;text-align:center;flex-shrink:0}.recovery-steps p{margin:0;line-height:1.7;font-size:12px}.recovery-steps small{color:var(--muted);font-size:10px}.recovery-texts{display:grid;gap:12px}.recovery-texts>div{padding:12px;background:var(--surface-soft);border-radius:8px}.recovery-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}.recovery-confirmation textarea{width:100%}
@media(max-width:650px){.recovery-heading{align-items:start;flex-direction:column}.recovery-transition{gap:8px}.recovery-transition strong{font-size:12px}}
</style>
