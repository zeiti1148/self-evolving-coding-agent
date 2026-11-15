import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import type { AgentApi, ExperienceLibrary as LibraryData } from '../src/api/contracts'
import { apiContextKey } from '../src/api/context'
import { readExperienceLibrary } from '../src/api/libraryValidation'
import { createHttpApi } from '../src/api/http'
import ExperienceLibrary from '../src/components/ExperienceLibrary.vue'
import ExperiencesView from '../src/views/ExperiencesView.vue'

const stamp='2026-11-15T00:00:00Z',ids=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222']
const sources=ids.map((id,i)=>({id,created_at:stamp,task_summary:`历史修复 ${i+1}`,root_cause:'空输入未经判断',
  solution:i?'Check null before trimming the name.':'先处理空姓名，再调用 trim。',error_type:'NullPointerException',verification:'历史文本',tags:[],source_task_id:id}))
const data:LibraryData={items:[{id:'33333333-3333-4333-8333-333333333333',kind:'archive',state:'unverified',
  title:'历史经验归并',summary:sources[0]!.solution,root_cause:'空输入未经判断',language:'java',error_type:'NullPointerException',
  applicability:[],constraints:[],source_ids:ids,sources,source_count:2,evidence_count:0,merged:true,created_at:stamp,
  reason:'原任务和结构化测试证据缺失，归并后仍待验证。'}],summary:{original_records:2,knowledge_cards:1,active:0,paused:0,unverified:1,merged:1,pending_reviews:0},
  history_scan:{id:ids[0]!,state:'succeeded',notice:'2 份历史记录归并为 1 组待验证经验',scanned:2,scheduled:0,deferred:0,groups:1,sources:2,created_at:stamp,updated_at:stamp}}
const client=():AgentApi=>({createTask:vi.fn(),getTask:vi.fn(),getSteps:vi.fn(),listExperiences:vi.fn().mockResolvedValue(sources),
  getExperienceLibrary:vi.fn().mockResolvedValue(structuredClone(data)),scanExperienceHistory:vi.fn().mockResolvedValue(data.history_scan)})
async function setup(api=client(),parent=false){
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/experiences',component:ExperiencesView},{path:'/',component:{template:'<div />'}}]})
  await router.push('/experiences');await router.isReady()
  const wrapper=mount(parent?ExperiencesView:ExperienceLibrary,{props:parent?{}:{filters:{},refreshKey:0},global:{plugins:[router],provide:{[apiContextKey as symbol]:{api,mock:false}}}})
  await flushPromises();return{wrapper,api}
}
describe('searchable knowledge catalog',()=>{
  it('shows consolidated knowledge first and retains both original sources behind an explicit comparison',async()=>{
    const {wrapper,api}=await setup(client(),true)
    try{
      expect(wrapper.findComponent(ExperienceLibrary).exists()).toBe(true)
      expect(wrapper.findAll('[data-testid="knowledge-row"]')).toHaveLength(1)
      expect(wrapper.findAll('[data-testid="experience-item"]')).toHaveLength(0)
      expect(wrapper.text()).toContain('历史整理：2 份已检查')
      expect(wrapper.text()).toContain('缺少测试证据')
      expect(api.scanExperienceHistory).not.toHaveBeenCalled()
      expect(wrapper.get('[data-testid="metric-guide"]').text()).toContain('不能再次相加')
      expect(wrapper.get('[data-testid="metric-guide"]').text()).toContain('不代表原任务修复失败')
      await wrapper.get('.row-title').trigger('click')
      await wrapper.findAll('button').find(b=>b.text().startsWith('来源与合并'))!.trigger('click')
      expect(wrapper.text()).toContain(sources[0]!.solution);expect(wrapper.text()).toContain(sources[1]!.solution)
      expect(wrapper.text()).toContain('归并不等于验证通过：暂不自动复用。')
      await wrapper.findAll('button').find(b=>b.text()==='原始记录')!.trigger('click')
      expect(wrapper.findAll('[data-testid="experience-item"]')).toHaveLength(2)
    }finally{wrapper.unmount()}
  })
  it('sends combined search and category filters to the server instead of filtering a loaded page',async()=>{
    const {wrapper,api}=await setup()
    try{
      await wrapper.get('[data-testid="scan-history"]').trigger('click');await flushPromises()
      expect(api.scanExperienceHistory).toHaveBeenCalledWith(false,expect.any(AbortSignal))
      expect(api.createTask).not.toHaveBeenCalled()
      await wrapper.get('[aria-label="搜索经验"]').setValue('trim null')
      await wrapper.get('form').trigger('submit');await flushPromises()
      expect(api.getExperienceLibrary).toHaveBeenLastCalledWith(expect.objectContaining({q:'trim null',limit:10,offset:0}),expect.any(AbortSignal))
      vi.mocked(api.getExperienceLibrary!).mockResolvedValue({...structuredClone(data),items:[],total:0,offset:0,limit:10,next_offset:null})
      await wrapper.findAll('button').find(b=>b.text()==='可复用0')!.trigger('click');await flushPromises()
      expect(api.getExperienceLibrary).toHaveBeenLastCalledWith(expect.objectContaining({q:'trim null',state:'active'}),expect.any(AbortSignal))
      expect(wrapper.findAll('[data-testid="knowledge-row"]')).toHaveLength(0)
      expect(wrapper.text()).toContain('没有符合条件的经验')
    }finally{wrapper.unmount()}
  })
  it('loads the next server page and keeps the selected recovery accessible',async()=>{
    const api=client()
    vi.mocked(api.getExperienceLibrary!).mockResolvedValue({...structuredClone(data),total:12,offset:0,limit:10,next_offset:10})
    const {wrapper}=await setup(api)
    try {
      await wrapper.findAll('button').find(b=>b.text()==='下一页')!.trigger('click');await flushPromises()
      expect(api.getExperienceLibrary).toHaveBeenLastCalledWith(expect.objectContaining({offset:10,limit:10}),expect.any(AbortSignal))
      await wrapper.get('.row-action').trigger('click')
      expect(wrapper.get('[data-testid="experience-recovery"]').text()).toContain('验证与恢复复用')
      await wrapper.get('[aria-label="关闭经验详情"]').trigger('click')
      expect(wrapper.find('[data-testid="experience-recovery"]').exists()).toBe(false)
    }finally{wrapper.unmount()}
  })
  it('validates archive trust and uses the ordinary library and history routes',async()=>{
    expect(readExperienceLibrary(data).items).toHaveLength(1)
    const malformed=structuredClone(data);malformed.items[0]!.state='active'
    expect(()=>readExperienceLibrary(malformed)).toThrow()
    malformed.items[0]!.state='unverified';malformed.items[0]!.evidence_count=2
    expect(()=>readExperienceLibrary(malformed)).toThrow()
    const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(data))).mockResolvedValueOnce(new Response(JSON.stringify(data.history_scan)))
    const api=createHttpApi('',fetcher)
    await api.getExperienceLibrary!({language:'java'});await api.scanExperienceHistory!(false)
    expect(fetcher.mock.calls[0]![0]).toContain('/api/experiences/library?language=java')
    expect(fetcher.mock.calls[1]![0]).toBe('/api/experiences/history-scan')
  })
})
