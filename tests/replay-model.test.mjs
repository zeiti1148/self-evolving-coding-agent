import test from 'node:test'
import assert from 'node:assert/strict'
import { diffLines, fileState, writeComparison, finalTest, frame } from '../docs/demo/replay-model.mjs'

const write = (content, success = true) => ({call:{name:'write_file',arguments:{path:'./src/A.java',content}},result:{success,metadata:{}}})
test('replay applies successful writes in order and never leaks the final file into an earlier step', () => {
  const record={initial_files:{'src/A.java':'old'},steps:[write('first'),write('failed',false),write('second')]}
  assert.equal(fileState(record,0)['src/A.java'],'old')
  assert.equal(writeComparison(record,1).before,'first')
  assert.equal(writeComparison(record,1).applied,false)
  assert.equal(writeComparison(record,2).before,'first')
  assert.equal(fileState(record,3)['src/A.java'],'second')
})
test('line diff correctly accounts for replacements, insertions, deletion, and CRLF', () => {
  const rows=diffLines('a\r\nb\r\nc','a\nx\nc\nd')
  assert.deepEqual(rows.filter(x=>x.kind==='removed').map(x=>x.text),['b'])
  assert.deepEqual(rows.filter(x=>x.kind==='added').map(x=>x.text),['x','d'])
  assert.deepEqual(rows.filter(x=>x.kind!=='added').map(x=>x.text),['a','b','c'])
  assert.deepEqual(rows.filter(x=>x.kind!=='removed').map(x=>x.text),['a','x','c','d'])
})
test('large files use the bounded full-text fallback', () => assert.equal(diffLines('a\n'.repeat(500),'b\n'.repeat(500)),null))
test('only explicit system verification is shown as final; baseline is separate from agent steps', () => {
  const intermediate={call:{name:'run_tests'},result:{success:true,metadata:{}}}
  const final={call:{name:'run_tests'},result:{success:false,metadata:{final_verification:true}}}
  const record={baseline:{success:false},steps:[intermediate,final]}
  assert.equal(finalTest(record),final)
  assert.equal(frame(record,0).kind,'baseline')
  assert.equal(frame(record,1).title,'Agent 调用 Maven 测试')
  assert.equal(frame(record,2).title,'程序执行独立最终测试')
  assert.equal(frame(record,3).kind,'summary')
})
