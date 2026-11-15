<script setup lang="ts">
import { computed } from 'vue'
import type { ExperienceConflict } from '../api/contracts'
import ExperienceText from './ExperienceText.vue'
import { testCell } from '../utils/experiencePresentation'
const props = defineProps<{ review: ExperienceConflict }>()
function sourceLabel(id: string) { return '来源 ' + (props.review.sources.findIndex(s=>s.id===id)+1) }
const matrix = computed(() => props.review.sources.map(source => ({ source, cells: props.review.sources.map(target => ({
  target, ...testCell(props.review.automatic_resolution?.runs.filter(run => run.implementation_source_id === source.id && run.target_source_id === target.id) ?? []),
})) })))
</script>

<template>
  <section v-if="review.automatic_resolution" class="conflict-automation" data-testid="conflict-automation">
    <div class="automation-heading"><h4>系统自动处理 · {{ {verifying:'验证中',resolved:'已完成',needs_confirmation:'需要用户确认'}[review.automatic_resolution.state] }}</h4><span>{{ review.automatic_resolution.runs.length }} 次实际测试</span></div>
    <ExperienceText :text="review.automatic_resolution.reason" :limit="100" />
    <div v-if="review.automatic_resolution.runs.length" class="test-matrix-wrap">
      <table class="test-matrix" data-testid="conflict-test-matrix">
        <caption>把双方实现放进各自测试中，检查能否互相替代</caption>
        <thead><tr><th scope="col">使用的实现 ↓</th><th v-for="source in review.sources" :key="source.id" scope="col">{{ sourceLabel(source.id) }}的测试</th></tr></thead>
        <tbody><tr v-for="row in matrix" :key="row.source.id"><th scope="row">{{ sourceLabel(row.source.id) }}的实现</th>
          <td v-for="cell in row.cells" :key="cell.target.id" :class="cell.state"><strong>{{ cell.label }}</strong><span>{{ cell.detail }}</span></td>
        </tr></tbody>
      </table>
    </div>
    <details v-if="review.automatic_resolution.runs.length" class="test-details">
      <summary>展开 {{ review.automatic_resolution.runs.length }} 次测试明细与失败用例 <span aria-hidden="true">⌄</span></summary>
      <p class="field-hint">每行是一次实际执行；交叉测试使用对方函数，保留目标项目原测试集。</p>
      <div class="conflict-test-table">
        <table><caption>全部测试执行记录</caption><thead><tr><th scope="col">实现 → 测试</th><th scope="col">轮次</th><th scope="col">真实结果</th></tr></thead>
          <tbody><tr v-for="run in review.automatic_resolution.runs" :key="run.id">
            <td>{{ sourceLabel(run.implementation_source_id) }} → {{ sourceLabel(run.target_source_id) }}</td>
            <td>{{ run.implementation_source_id === run.target_source_id ? '原上下文' : '交叉 ' + run.repeat }}</td>
            <td>{{ run.outcome === 'passed' ? run.tests + ' 项通过' : run.outcome === 'failed' ? run.failures + ' / ' + run.tests + ' 项失败' : '验证异常' }}</td>
          </tr></tbody>
        </table>
      </div>
      <details v-for="run in review.automatic_resolution.runs.filter(r=>r.failed_tests.length)" :key="run.id" class="failure-detail">
        <summary>{{ sourceLabel(run.implementation_source_id) }} → {{ sourceLabel(run.target_source_id) }} · 第 {{ run.repeat }} 次 · 失败用例 <span aria-hidden="true">⌄</span></summary>
        <ul><li v-for="test in run.failed_tests" :key="test">{{ test }}</li></ul>
        <p class="field-hint">{{ run.reproduces_baseline ? '复现了该任务原始失败用例' : '未能对应原始缺陷，需要确认' }}</p>
        <p class="field-hint">证据 SHA-256：{{ run.artifact_sha256 }}</p>
      </details>
    </details>
    <details class="automation-method"><summary>自动处理是怎么判断的？ <span aria-hidden="true">⌄</span></summary><ol>
      <li>核对原始任务、冻结源码和完整测试配置。</li>
      <li>双方实现分别运行各自测试，确认原经验有效。</li>
      <li>交换冲突函数，双方各重复验证两次。</li>
      <li>依据测试证据决定采纳、隔离或交给用户确认。</li>
    </ol></details>
    <p v-if="review.automatic_resolution.state === 'needs_confirmation' && review.status === 'pending'" class="confirmation-notice">系统未代替用户作出决定，请在本页“冲突处理”中核对证据并确认。</p>
  </section>
</template>

<style scoped>
.conflict-automation{margin:16px 0;min-width:0}.automation-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px}.automation-heading h4{font-size:13px;margin:0}.automation-heading>span{font-size:11px;color:var(--muted);white-space:nowrap}.test-matrix-wrap{margin:12px 0}.test-matrix{table-layout:fixed;width:100%;border-spacing:5px;border-collapse:separate;font-size:12px}.test-matrix caption{text-align:left;color:var(--muted);font-size:12px;margin-bottom:6px}.test-matrix th{font-weight:500;color:var(--muted);text-align:center;font-size:12px;padding:7px}.test-matrix th:first-child{width:25%}.test-matrix td{padding:12px;border-radius:8px;text-align:center;background:var(--surface-soft)}.test-matrix td strong,.test-matrix td span{display:block}.test-matrix td strong{font-size:15px;margin-bottom:2px}.test-matrix td span{font-size:12px}.test-matrix .passed{background:var(--success-bg);color:var(--success)}.test-matrix .failed{background:var(--danger-bg);color:var(--danger)}.test-matrix .mixed{background:var(--warning-bg);color:var(--warning)}.test-matrix .pending{color:var(--muted)}.test-details,.automation-method{border-top:1px solid var(--line);padding:10px 0;font-size:12px}.test-details>summary,.automation-method>summary,.failure-detail>summary{display:flex;justify-content:space-between;gap:8px;color:var(--accent)}.automation-method ol{padding-left:20px;line-height:1.9}.conflict-test-table{overflow-x:auto;margin:12px 0}.conflict-test-table table{border-collapse:collapse;width:100%;font-size:12px}.conflict-test-table caption{text-align:left;margin-bottom:8px;font-weight:600}.conflict-test-table th,.conflict-test-table td{text-align:left;padding:8px;border-bottom:1px solid var(--line)}.failure-detail{padding:10px 0;overflow-wrap:anywhere}.confirmation-notice{padding:12px;background:var(--warning-bg);color:var(--warning);border-radius:8px;font-size:12px}.field-hint{overflow-wrap:anywhere}
@media(max-width:600px){.test-matrix{border-spacing:3px}.test-matrix th{font-size:11px;padding:4px}.test-matrix td{padding:10px 3px}.test-matrix td strong{font-size:13px}.test-matrix td span{font-size:10px}.automation-heading{flex-wrap:wrap}}
</style>
