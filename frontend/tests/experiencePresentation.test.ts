import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { ConflictTestRun } from '../src/api/contracts'
import { testCell } from '../src/utils/experiencePresentation'
import ExperienceText from '../src/components/ExperienceText.vue'

const run: ConflictTestRun = { id:'one', target_source_id:'a', implementation_source_id:'b', repeat:1,
  outcome:'failed',tests:277,failures:2,errors:0,skipped:0,test_identity:'suite',failed_tests:['first','second'],
  reproduces_baseline:true,artifact_sha256:'hash',artifact_path:'report' }

describe('concise experience presentation preserves evidence',()=>{
  it('does not present different repeated failures as a stable cross-test result',()=>{
    expect(testCell([run,{...run,id:'two',repeat:2}])).toMatchObject({state:'failed',label:'2 次均失败',detail:'2 / 277 项失败'})
    expect(testCell([run,{...run,id:'two',repeat:2,failed_tests:['first','other']}]).state).toBe('mixed')
    expect(testCell([run,{...run,id:'two',repeat:2,test_identity:'changed-suite'}]).state).toBe('mixed')
    expect(testCell([run,{...run,id:'two',repeat:2,outcome:'passed',failures:0,failed_tests:[]}]).state).toBe('mixed')
  })
  it('distinguishes missing evidence and skipped tests from a complete passing run',()=>{
    expect(testCell([]).state).toBe('pending')
    expect(testCell([{...run,outcome:'passed',failures:0,skipped:1,failed_tests:[]}]).state).toBe('mixed')
    expect(testCell([{...run,outcome:'invalid'}]).state).toBe('mixed')
    expect(testCell([run]).label).toBe('失败')
  })
  it('keeps the entire original available instead of losing conditions beyond the excerpt',async()=>{
    const text='对输入执行处理。'.repeat(20)+'\n限制：不能改写调用方数据，也不能改变异常类型。'
    const wrapper=mount(ExperienceText,{props:{text,limit:60}})
    try{
      expect(wrapper.get('p').text()).not.toContain('不能改变异常类型')
      expect(wrapper.get('button').attributes('aria-expanded')).toBe('false')
      await wrapper.get('button').trigger('click')
      expect(wrapper.get('p').element.textContent).toBe(text)
      expect(wrapper.get('button').attributes('aria-expanded')).toBe('true')
      await wrapper.setProps({text:'新的原文'})
      expect(wrapper.find('button').exists()).toBe(false)
      expect(wrapper.get('p').text()).toBe('新的原文')
    }finally{wrapper.unmount()}
  })
})
