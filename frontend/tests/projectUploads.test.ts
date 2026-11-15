import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { apiContextKey } from '../src/api/context'
import { createHttpApi } from '../src/api/http'
import type { AgentApi, RegisteredProject } from '../src/api/contracts'
import ProjectUpload from '../src/components/ProjectUpload.vue'
import NewTaskView from '../src/views/NewTaskView.vue'

const id = '11111111-1111-4111-8111-111111111111'
const project: RegisteredProject = {id:'upload-one',label:'Uploaded Go',language:'go',repository_id:'uploaded/one',source_revision:'tree:one',task_example:'Fix one',origin:'upload',runner_config:{python_executable:null,python_paths:['.'],test_targets:[],deselect:[],pytest_plugins:[],timeout_seconds:300}}
const baseApi = (): AgentApi => ({createTask:vi.fn(),getTask:vi.fn(),getSteps:vi.fn(),listExperiences:vi.fn(),runtimeOptions:vi.fn().mockResolvedValue({python:[],go:true,cpp:true})})
async function setup(api: AgentApi) {
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:NewTaskView},{path:'/tasks/:taskId',component:{template:'<div />'}}]})
  await router.push('/');await router.isReady()
  const wrapper=mount(NewTaskView,{global:{plugins:[router],provide:{[apiContextKey as symbol]:{api,mock:false,projectId:'old',projectLabel:'old'}}}})
  await flushPromises(); return {wrapper,router}
}

describe('uploaded project tasks',()=>{
  it('sends the actual ZIP bytes and preserves native language contracts',async()=>{
    const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(project)))
    const file=new File(['PK ZIP data'],'项目.zip',{type:'application/zip'})
    const uploaded=await createHttpApi('',fetcher).uploadProject!(file,'我的项目','project/go')
    expect(uploaded.language).toBe('go')
    expect(fetcher.mock.calls[0]![1].body).toBe(file)
    expect(fetcher.mock.calls[0]![1].headers['Content-Type']).toBe('application/zip')
    expect(fetcher.mock.calls[0]![0]).toContain('project_root=project%2Fgo')
  })
  it('invalidates a ready project until changed runner settings are saved',async()=>{
    const api=baseApi();api.uploadProject=vi.fn().mockResolvedValue(project);api.configureProject=vi.fn().mockResolvedValue({...project,id:'new-config'})
    const wrapper=mount(ProjectUpload,{global:{provide:{[apiContextKey as symbol]:{api,mock:false}}}})
    const file=new File(['zip'],'custom.zip')
    Object.defineProperty(wrapper.get('input[type=file]').element,'files',{value:[file]})
    await wrapper.get('input[type=file]').trigger('change')
    await wrapper.findAll('button').find(b=>b.text()==='导入项目')!.trigger('click');await flushPromises()
    expect(wrapper.emitted('ready')!.at(-1)).toEqual([project])
    await wrapper.get('input[type=number]').setValue(120)
    expect(wrapper.emitted('ready')!.at(-1)).toEqual([null])
    await wrapper.findAll('button').find(b=>b.text()==='保存测试配置')!.trigger('click');await flushPromises()
    expect(api.configureProject).toHaveBeenCalledWith('upload-one',expect.objectContaining({timeout_seconds:120}),expect.any(AbortSignal))
    expect(wrapper.emitted('ready')!.at(-1)).toEqual([expect.objectContaining({id:'new-config'})])
    wrapper.unmount()
  })
  it('submits an uploaded project through the regular task workflow',async()=>{
    const api=baseApi();api.uploadProject=vi.fn();api.createTask=vi.fn().mockResolvedValue({task_id:id,status:'pending',created_at:'2026-11-15T00:00:00Z'})
    const {wrapper,router}=await setup(api)
    expect((wrapper.get('#project-mode').element as HTMLSelectElement).value).toBe('upload')
    wrapper.getComponent(ProjectUpload).vm.$emit('ready',project)
    await wrapper.get('#description').setValue('Fix my Go project')
    await wrapper.get('form').trigger('submit');await flushPromises()
    expect(api.createTask).toHaveBeenCalledTimes(1)
    expect(api.createTask).toHaveBeenCalledWith({source_project:project.id,task_description:'Fix my Go project',max_steps:20,execution_mode:'single'},expect.any(AbortSignal))
    expect(router.currentRoute.value.path).toBe('/tasks/'+id)
    wrapper.unmount()
  })
})
