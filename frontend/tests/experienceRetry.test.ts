import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { apiContextKey } from '../src/api/context'
import { createHttpApi } from '../src/api/http'
import { readTask } from '../src/api/validation'
import ExperienceRetry from '../src/components/ExperienceRetry.vue'

const id = '11111111-1111-4111-8111-111111111111'
describe('verified repair experience recovery', () => {
  it('does not call the model on read and emits refresh after explicit retry', async () => {
    const api = {getExperienceRetry:vi.fn().mockResolvedValue({task_id:id,state:'idle',notice:''}),
      retryExperience:vi.fn().mockResolvedValue({task_id:id,state:'succeeded',notice:'经验已保存'})}
    const wrapper = mount(ExperienceRetry, {props:{taskId:id}, global:{provide:{[apiContextKey as symbol]:{api,mock:false}}}})
    await flushPromises()
    expect(api.retryExperience).not.toHaveBeenCalled()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(api.retryExperience).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('updated')).toHaveLength(1)
    wrapper.unmount()
  })
  it('uses the ordinary task endpoint and rejects unrelated task responses', async () => {
    const fetcher = vi.fn().mockResolvedValue({ok:true,json:async()=>({task_id:id,state:'running',notice:'正在提取'})})
    const api = createHttpApi('', fetcher)
    await api.retryExperience!(id)
    expect(fetcher.mock.calls[0]![0]).toBe(`/api/tasks/${id}/experience-retry`)
    expect(fetcher.mock.calls[0]![1].method).toBe('POST')
    fetcher.mockResolvedValue({ok:true,json:async()=>({task_id:'wrong',state:'succeeded',notice:''})})
    await expect(api.getExperienceRetry!(id)).rejects.toThrow('接口约定')
  })
  it('accepts saved knowledge on a stopped task only with program-owned final verification', () => {
    const call = {id,name:'run_tests',arguments:{}}
    const result = {call_id:id,tool_name:'run_tests',success:true,output:'',error_code:null,error_message:null,metadata:{final_verification:true,tests:1}}
    const task = {task_id:id,status:'stopped',created_at:'2026-11-15T00:00:00Z',finished_at:'2026-11-15T00:01:00Z',
      result:{task_id:id,status:'stopped',summary:'决策次数已用完。',repair_succeeded:true,tests_passed:true,
        experience_saved:true,experience_ids:[id],steps:[call],tool_results:[result]}}
    expect(readTask(task,id).result?.experience_saved).toBe(true)
    result.metadata.final_verification = false
    expect(()=>readTask(task,id)).toThrow()
  })
})
