import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import type { AgentApi, AutomaticMergePage, Experience } from '../src/api/contracts'
import { apiContextKey } from '../src/api/context'
import { createHttpApi } from '../src/api/http'
import { readAutomaticMerges } from '../src/api/experienceWorkflowValidation'
import ExperienceAutoMerge from '../src/components/ExperienceAutoMerge.vue'
import ExperiencesView from '../src/views/ExperiencesView.vue'

const ids = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222', '33333333-3333-4333-8333-333333333333']
const stamp = '2026-11-15T00:00:00Z'
const sources: Experience[] = ids.slice(0,2).map((id,index) => ({id, created_at:stamp, source_task_id:id,
  task_summary:index ? '预订人数修复' : '库存数量修复', root_cause:'缺失值被直接转换', error_type:'TypeError',
  solution:index ? '解析人数前使用预订默认值' : '在转换库存输入前处理缺失情况', verification:'实际验收通过', tags:['python']}))
const merged: AutomaticMergePage = {items:[{id:ids[1]!, source:sources[1]!, state:'succeeded', notice:'比较完成。',
  created_at:stamp, updated_at:stamp, local_pending:false, candidate_total:1, selected_candidates:1, compared_candidates:1, deferred_candidates:0,
  changes:[{id:ids[2]!, kind:'shared_principle', state:'merged', before:sources,
    after:{title:'缺失输入的转换顺序', principle:'转换前识别缺失输入，保留各项目约定的默认行为。', applicability:['可选数值输入'],
      boundaries:['不能跨项目覆盖默认值'], bindings:sources.map(s=>({source_id:s.id,supports:[{issue_index:0,field:'solution',quote:s.solution}]})),
      instances:sources.map((s,i)=>({experience_id:s.id,repository_id:i ? 'booking' : 'stock',constraints:[i ? '预订默认人数为 1' : '库存默认数量为 0']}))},
    reason:'两次任务独立支持同一处理顺序。'}]}],next_offset:null}
const client = (): AgentApi => ({createTask:vi.fn(),getTask:vi.fn(),getSteps:vi.fn(),listExperiences:vi.fn().mockResolvedValue([]),
  listAutomaticMerges:vi.fn().mockResolvedValue(structuredClone(merged)), retryAutomaticMerge:vi.fn().mockResolvedValue({id:ids[1]})})
async function setup(api=client(), parent=false) {
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/experiences',component:ExperiencesView},{path:'/',component:{template:'<div />'}},{path:'/tasks/:id',component:{template:'<div />'}}]})
  await router.push('/experiences');await router.isReady()
  const wrapper=mount(parent ? ExperiencesView : ExperienceAutoMerge,{props:parent ? {} : {filters:{},refreshKey:0},
    global:{plugins:[router],provide:{[apiContextKey as symbol]:{api,mock:false}}}})
  await flushPromises()
  return {wrapper,api}
}

describe('automatic merge in the experience library',()=>{
  it('slows completed records and backs off failed refreshes while preserving the last result',async()=>{
    vi.useFakeTimers()
    const api=client()
    const {wrapper}=await setup(api)
    try {
      await vi.advanceTimersByTimeAsync(5000)
      expect(api.listAutomaticMerges).toHaveBeenCalledTimes(1)
      vi.mocked(api.listAutomaticMerges!).mockRejectedValueOnce(new Error('请求超时'))
      await vi.advanceTimersByTimeAsync(25000);await flushPromises()
      expect(api.listAutomaticMerges).toHaveBeenCalledTimes(2)
      expect(wrapper.text()).toContain('已保留上次结果')
      expect(wrapper.text()).toContain('1 条共同规则')
      await vi.advanceTimersByTimeAsync(5000)
      expect(api.listAutomaticMerges).toHaveBeenCalledTimes(2)
      await vi.advanceTimersByTimeAsync(5000);await flushPromises()
      expect(api.listAutomaticMerges).toHaveBeenCalledTimes(3)
      expect(wrapper.text()).not.toContain('已保留上次结果')
    } finally {wrapper.unmount();vi.useRealTimers()}
  })

  it('surfaces deferred candidates in the summary instead of calling the job finished',async()=>{
    const api=client(), capped=structuredClone(merged)
    Object.assign(capped.items[0]!, {changes:[],state:'succeeded',candidate_total:7,
      selected_candidates:5,compared_candidates:5,deferred_candidates:2,
      notice:'已达到单次比较上限 20 对；剩余 2 对候选暂未自动比较。'})
    vi.mocked(api.listAutomaticMerges!).mockResolvedValue(capped)
    const {wrapper}=await setup(api)
    try {
      // The cap is visible without expanding the record.
      expect(wrapper.text()).toContain('比较完成（部分推迟）')
      expect(wrapper.text()).toContain('剩余 2 对候选暂未自动比较')
    } finally {wrapper.unmount()}
  })

  it('distinguishes no comparison target from a completed merge',async()=>{
    const api=client(), empty=structuredClone(merged)
    Object.assign(empty.items[0]!, {changes:[],candidate_total:0,selected_candidates:0,compared_candidates:0,
      notice:'当前没有更早且符合比较条件的经验；后续修复会自动与本条比较。'})
    vi.mocked(api.listAutomaticMerges!).mockResolvedValue(empty)
    const {wrapper}=await setup(api)
    try {
      expect(wrapper.text()).toContain('暂无比较对象')
      expect(wrapper.text()).toContain('原始经验已保存')
      expect(wrapper.text()).not.toContain('0 项处理结果')
      expect(wrapper.text()).not.toContain('比较完成')
    } finally {wrapper.unmount()}
  })

  it('shows both original tasks, the merged rule and preserved business boundaries in the normal page',async()=>{
    const {wrapper}=await setup(client(),true)
    try {
      expect(wrapper.findComponent(ExperienceAutoMerge).exists()).toBe(true)
      expect(wrapper.text()).toContain('合并前 · 原始经验')
      expect(wrapper.text()).toContain('库存数量修复')
      expect(wrapper.text()).toContain('预订人数修复')
      expect(wrapper.text()).toContain('1 条共同规则')
      expect(wrapper.text()).toContain('库存默认数量为 0')
      expect(wrapper.text()).toContain('预订默认人数为 1')
      expect(wrapper.text()).not.toContain('跨项目实验')
    } finally {wrapper.unmount()}
  })

  it('reads the normal experience endpoint and rejects a success with no merged content',async()=>{
    const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(merged)))
    const api=createHttpApi('',fetcher)
    expect((await api.listAutomaticMerges!({experience_id:ids[1]})).items).toHaveLength(1)
    expect(fetcher.mock.calls[0]![0]).toContain('/api/experiences/automation?')
    const malformed=structuredClone(merged);malformed.items[0]!.changes[0]!.after=null
    expect(()=>readAutomaticMerges(malformed)).toThrow()
  })

  it('updates a pending record automatically and stops polling after leaving the page',async()=>{
    vi.useFakeTimers()
    const api=client(), pending=structuredClone(merged)
    pending.items[0]!.state='pending';pending.items[0]!.changes=[]
    vi.mocked(api.listAutomaticMerges!).mockResolvedValueOnce(pending).mockResolvedValue(merged)
    const {wrapper}=await setup(api)
    try {
      expect(wrapper.text()).toContain('等待比较')
      await vi.advanceTimersByTimeAsync(5000);await flushPromises()
      expect(wrapper.text()).toContain('1 条共同规则')
      wrapper.unmount()
      const calls=vi.mocked(api.listAutomaticMerges!).mock.calls.length
      await vi.advanceTimersByTimeAsync(10000)
      expect(api.listAutomaticMerges).toHaveBeenCalledTimes(calls)
    } finally {vi.useRealTimers()}
  })

  it('shows conflict decisions and their audit trail directly beside the originals',async()=>{
    const data=structuredClone(merged), change=data.items[0]!.changes[0]!
    change.kind='review';change.state='resolved';change.after=null
    change.review={id:ids[2]!,sources,status:'resolved',version:2,created_at:stamp,resolved_at:stamp,
      resolution:'prefer_source',preferred_source_id:ids[0]!,note:'原始合同要求库存默认值为零',
      details:[{code:'rule_overlap',message:'行为冲突',field:'issues',source_ids:ids.slice(0,2),suggestion:'核对合同'},
        {code:'rule_overlap',message:'相互冲突的来源原文',field:'issues[0].solution',source_ids:[ids[0]!],suggestion:'保留库存默认值零'},
        {code:'evidence_mismatch',message:'独立模型复核',field:'issues',source_ids:ids.slice(0,2),suggestion:'同一合同下两个默认值互斥'}]}
    change.history=[{version:2,action:'resolve',data:{note:'原始合同要求库存默认值为零'},created_at:stamp}]
    const api=client();vi.mocked(api.listAutomaticMerges!).mockResolvedValue(data)
    const {wrapper}=await setup(api)
    try {
      expect(wrapper.text()).toContain('已采纳指定来源')
      expect(wrapper.text()).toContain('原始合同要求库存默认值为零')
      expect(wrapper.text()).toContain('保存处理结果')
      expect(wrapper.text()).toContain('处理前 · 冲突双方')
      expect(wrapper.text()).toContain('处理后：采纳此来源的规则')
      expect(wrapper.text()).toContain('冲突规则暂停复用，原始经验保留')
      expect(wrapper.find('blockquote').text()).toContain('保留库存默认值零')
      expect(wrapper.text()).toContain('同一合同下两个默认值互斥')
      expect(wrapper.text()).not.toContain('1 条共同规则')
    } finally {wrapper.unmount()}
  })

  it('only retries failed comparison and never submits another repair task',async()=>{
    const data=structuredClone(merged);data.items[0]!.state='failed'
    const change=data.items[0]!.changes[0]!;change.state='failed';change.after=null;change.reason='模型比较失败'
    const api=client();vi.mocked(api.listAutomaticMerges!).mockResolvedValue(data)
    const {wrapper}=await setup(api)
    try {
      await wrapper.findAll('button').find(b=>b.text()==='重试未完成的比较')!.trigger('click');await flushPromises()
      expect(api.retryAutomaticMerge).toHaveBeenCalledWith(ids[1],expect.any(AbortSignal))
      expect(api.createTask).not.toHaveBeenCalled()
      expect(wrapper.text()).not.toContain('1 条共同规则')
    } finally {wrapper.unmount()}
  })

  it('shows automatic cross-test evidence and distinguishes system decisions from human review',async()=>{
    const data=structuredClone(merged), change=data.items[0]!.changes[0]!
    const runs=ids.slice(0,2).flatMap((target,index)=>[0,1,2].map(repeat=>({
      id:`44444444-4444-4444-8444-${String(index*3+repeat).padStart(12,'0')}`,target_source_id:target,implementation_source_id:repeat ? ids[1-index]! : target,
      repeat:repeat||1,outcome:repeat ? 'failed' as const : 'passed' as const,tests:277,failures:repeat ? 1 : 0,errors:0,skipped:0,
      test_identity:'a'.repeat(64),failed_tests:repeat ? ['testing.test_mark.test_mark_mro'] : [],
      reproduces_baseline:!!repeat,artifact_sha256:'b'.repeat(64),artifact_path:'isolated/report.json',
    })))
    change.kind='review';change.state='resolved';change.after=null
    change.review={id:ids[2]!,sources,status:'resolved',version:2,created_at:stamp,resolved_at:stamp,
      resolution:'keep_separate',preferred_source_id:null,note:'测试证据支持隔离',
      details:[{code:'rule_overlap',message:'行为冲突',field:'issues',source_ids:ids.slice(0,2),suggestion:'核对合同'}],
      automatic_resolution:{state:'resolved',actor:'system',policy:'verified-cross-tests-v1',stage:'decision',
        reason:'双方在原上下文通过，交叉使用复现原始失败。',targets:[],runs,resolution:'keep_separate',
        preferred_source_id:null,started_at:stamp,updated_at:stamp,evidence_hash:'c'.repeat(64)}}
    change.history=[{version:2,action:'resolve',data:{actor:'system',policy:'verified-cross-tests-v1'},created_at:stamp}]
    expect(readAutomaticMerges(data).items[0]!.changes[0]!.review!.automatic_resolution!.runs).toHaveLength(6)
    const malformed=structuredClone(data);malformed.items[0]!.changes[0]!.review!.automatic_resolution!.runs.pop()
    expect(()=>readAutomaticMerges(malformed)).toThrow()
    const api=client();vi.mocked(api.listAutomaticMerges!).mockResolvedValue(data)
    const {wrapper}=await setup(api)
    try {
      expect(wrapper.text()).toContain('系统自动处理 · 已完成')
      expect(wrapper.text()).toContain('系统自动保存决定')
      expect(wrapper.findAll('.conflict-test-table tbody tr')).toHaveLength(6)
      expect(wrapper.findAll('.test-matrix td')).toHaveLength(4)
      expect(wrapper.findAll('.test-matrix td.failed')).toHaveLength(2)
      expect(wrapper.get('.test-details').attributes('open')).toBeUndefined()
      expect(wrapper.get('.test-matrix').text()).toContain('2 次均失败')
      expect(wrapper.text()).toContain('1 / 277 项失败')
      expect(wrapper.text()).toContain('复现了该任务原始失败用例')
      const review=data.items[0]!.changes[0]!.review!;review.status='pending';review.resolution=null;review.resolved_at=null;review.note=null
      review.automatic_resolution!.state='needs_confirmation';review.automatic_resolution!.resolution=null
      review.automatic_resolution!.reason='测试集合无法区分双方实现'
      vi.mocked(api.listAutomaticMerges!).mockResolvedValue(structuredClone(data))
      await wrapper.setProps({refreshKey:1});await flushPromises()
      expect(wrapper.text()).toContain('系统自动处理 · 需要用户确认')
      expect(wrapper.text()).toContain('系统未代替用户作出决定')
    } finally {wrapper.unmount()}
  })

  it('does not keep presenting an invalidated historical principle as a current merge',async()=>{
    const data=structuredClone(merged), change=data.items[0]!.changes[0]!
    change.state='invalidated';change.after=null;change.reason='来源有待审阅规则'
    const api=client();vi.mocked(api.listAutomaticMerges!).mockResolvedValue(data)
    const {wrapper}=await setup(api)
    try {expect(wrapper.text()).toContain('合并已暂停');expect(wrapper.text()).not.toContain('1 条共同规则')}
    finally {wrapper.unmount()}
  })
})
