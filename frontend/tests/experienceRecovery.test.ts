import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { apiContextKey } from '../src/api/context'
import type { AgentApi, ExperienceRecovery as Recovery, KnowledgeCard } from '../src/api/contracts'
import ExperienceRecovery from '../src/components/ExperienceRecovery.vue'
import NewTaskView from '../src/views/NewTaskView.vue'
import { readRecovery } from '../src/api/libraryValidation'
const id='11111111-1111-4111-8111-111111111111',cardId='22222222-2222-4222-8222-222222222222'
const stamp='2026-11-15T12:00:00Z'
const recovery:Recovery={id,card_id:cardId,state:'needs_evidence',version:2,reason:'原任务证据缺失',task_id:null,created_at:stamp,updated_at:stamp,
  actor:'system',before:{state:'unverified',summary:'先检查空值再调用 trim',evidence_count:0},can_confirm:false,review_ids:[],runs:[],steps:[]}
const card:KnowledgeCard={id:cardId,kind:'archive',state:'unverified',title:'处理空值',summary:'先检查空值',root_cause:'空值未处理',language:'java',error_type:'NullPointerException',applicability:[],constraints:[],source_ids:[],sources:[],source_count:2,evidence_count:0,merged:true,reason:'缺少证据',created_at:stamp}
const client=():AgentApi=>({createTask:vi.fn(),getTask:vi.fn(),getSteps:vi.fn(),listExperiences:vi.fn(),
  listExperienceRecovery:vi.fn().mockResolvedValue([structuredClone(recovery)]),getExperienceRecovery:vi.fn().mockResolvedValue(structuredClone(recovery)),
  listRecoveryTasks:vi.fn().mockResolvedValue([]),startExperienceRecovery:vi.fn().mockResolvedValue(recovery),confirmExperienceRecovery:vi.fn()})
async function setup(api:AgentApi,task=false) {
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:NewTaskView},{path:'/experiences',component:{template:'<div />'}},{path:'/tasks/:id',component:{template:'<div />'}}]})
  await router.push(task?'/?knowledge_recovery_id='+id:'/experiences');await router.isReady()
  const wrapper=mount(task?NewTaskView:ExperienceRecovery,{props:task?{}:{card},global:{plugins:[router],provide:{[apiContextKey as symbol]:{api,mock:false,projectId:'greeting',projectLabel:'Greeting project'}}}})
  await flushPromises();return{wrapper,router}
}
describe('normal evidence recovery workflow',()=>{
  it.each([
    '本条证据已变化，请重新验证。',
    '本条验证已通过，关联规则待补证；已保留本条证据，无须重复验证。',
  ])('preserves the server reason for unavailable reuse: %s',async(reason)=>{
    const api=client()
    vi.mocked(api.listExperienceRecovery!).mockResolvedValue([{...recovery,state:'invalidated',reason}])
    const {wrapper}=await setup(api)
    try {
      expect(wrapper.get('.recovery-result strong').text()).toBe('暂未恢复复用')
      expect(wrapper.get('.recovery-result p').text()).toBe(reason)
      expect(wrapper.get('.recovery-transition').text()).toContain('暂未恢复复用')
      expect(wrapper.text()).not.toContain('证据已失效')
      expect(api.startExperienceRecovery).not.toHaveBeenCalled()
      expect(api.confirmExperienceRecovery).not.toHaveBeenCalled()
    }finally{wrapper.unmount()}
  })
  it('offers an ordinary task for missing evidence without a force-enable action',async()=>{
    const api=client(),{wrapper}=await setup(api)
    try {
      expect(wrapper.text()).toContain('需要补充证据')
      expect(wrapper.get('a').attributes('href')).toContain('knowledge_recovery_id='+id)
      expect(wrapper.find('textarea').exists()).toBe(false)
      expect(api.startExperienceRecovery).not.toHaveBeenCalled()
      expect(api.confirmExperienceRecovery).not.toHaveBeenCalled()
      await wrapper.findAll('button').find(b=>b.text()==='重新验证与审阅')!.trigger('click');await flushPromises()
      expect(api.startExperienceRecovery).toHaveBeenCalledWith(cardId,{retry:true},expect.any(AbortSignal))
    }finally{wrapper.unmount()}
  })
  it('binds a repair request to the recovery after loading its real state',async()=>{
    const api=client();vi.mocked(api.createTask).mockResolvedValue({task_id:cardId,status:'pending',created_at:stamp})
    const {wrapper,router}=await setup(api,true)
    try {
      expect(wrapper.text()).toContain('本次修复将为已有经验补充验证')
      expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toContain(recovery.before.summary)
      await wrapper.get('form').trigger('submit');await flushPromises()
      expect(api.createTask).toHaveBeenCalledWith(expect.objectContaining({source_project:'greeting',knowledge_recovery_id:id}),expect.any(AbortSignal))
      expect(router.currentRoute.value.path).toBe('/tasks/'+cardId)
    }finally{wrapper.unmount()}
  })
  it('blocks submission for a recovery already bound to a task',async()=>{
    const api=client();vi.mocked(api.getExperienceRecovery!).mockResolvedValue({...recovery,task_id:cardId,state:'waiting_task'})
    const {wrapper}=await setup(api,true)
    try {await wrapper.get('form').trigger('submit');expect(api.createTask).not.toHaveBeenCalled();expect(wrapper.text()).toContain('状态已改变')}
    finally{wrapper.unmount()}
  })
  it('does not accept a claimed success without test evidence and a reuse boundary',()=>{
    expect(readRecovery(recovery).state).toBe('needs_evidence')
    expect(()=>readRecovery({...recovery,state:'succeeded'})).toThrow('Recovery lacks evidence')
  })
})
