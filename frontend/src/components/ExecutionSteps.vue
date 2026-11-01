<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { TaskStatusResponse, TaskStep } from '../api/contracts'
import { downloadExecution } from '../features/taskExport'
import { originalDefect, testCounts, testOutcome, testPhase } from '../features/testPresentation'
import AppIcon from './AppIcon.vue'
const props = withDefaults(defineProps<{ task: TaskStatusResponse; steps: TaskStep[]; mock: boolean; stepsLoaded?: boolean }>(), { stepsLoaded: true })
const filter = ref('all')
const expanded = ref(new Set<number>())
const downloadError = ref('')
const terminal = computed(() => !['pending', 'running'].includes(props.task.status))
const unsuccessful = computed(() => props.steps.filter(step => !step.result.success))
const visible = computed(() => filter.value === 'unsuccessful' ? unsuccessful.value : props.steps)
const allExpanded = computed(() => visible.value.length > 0 && visible.value.every(step => expanded.value.has(step.sequence)))
watch(() => props.task.task_id, () => { expanded.value = new Set(); filter.value = 'all'; downloadError.value = '' })
function toggle(sequence: number) { if (expanded.value.has(sequence)) expanded.value.delete(sequence); else expanded.value.add(sequence) }
function toggleAll() {
  const collapse = allExpanded.value
  visible.value.forEach(step => { if (collapse) expanded.value.delete(step.sequence); else expanded.value.add(step.sequence) })
}
function download() {
  downloadError.value = ''
  try { downloadExecution(props.task, props.steps, props.mock, props.stepsLoaded) }
  catch { downloadError.value = '下载未能开始，请重试或检查浏览器下载设置。' }
}
const labels: Record<string, string> = { list_files: '查看项目结构', read_file: '读取源代码', write_file: '修改工作副本', run_tests: '运行测试', run_command: '执行受控命令' }
const testLabels = { before: '修复前检查', during: '修复过程测试', final: '程序最终验证' }
function testExplanation(step: TaskStep) {
  if (!step.result.success && step.result.error_code !== 'TEST_FAILED') return '本次测试执行发生异常，请检查错误和原始输出；不能据此判断代码是否修复成功。'
  if (testPhase(step, props.steps) === 'before') return '这是修改代码前的检查，用来定位原始缺陷。失败记录会保留，后续修复情况请看任务的最终结果。'
  if (testPhase(step, props.steps) === 'final') return '这是程序独立执行的验证。若有多次验证，任务最终结论以最后一次为准。'
  return '这是修复过程中的一次测试。它反映当时的代码状态，最终结论请看程序最终验证。'
}
function time(value: string) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
</script>
<template>
  <section class="panel execution-panel" aria-labelledby="steps-title">
    <div class="panel-heading split"><div class="heading-inline"><AppIcon name="code" /><h2 id="steps-title">执行过程</h2><span class="count-chip">{{ steps.length }}</span></div><span class="text-muted" role="status">{{ terminal ? '执行已结束' : task.status === 'pending' ? '等待开始' : '自动更新中' }}</span></div>
    <div class="execution-toolbar">
      <label class="step-filter">步骤筛选<select v-model="filter"><option value="all">全部步骤（{{ steps.length }}）</option><option value="unsuccessful">未成功（{{ unsuccessful.length }}）</option></select></label>
      <div class="execution-actions"><button type="button" class="button secondary small" :disabled="!visible.length" @click="toggleAll">{{ allExpanded ? '收起全部' : '展开全部' }}</button><button type="button" class="button secondary small" @click="download"><AppIcon name="file" :size="16" />下载执行记录</button></div>
    </div>
    <p class="execution-export-note">下载包含任务状态和全部已读取步骤，不受筛选影响；未结束或未读全时会标记为不完整。</p>
    <p v-if="downloadError" class="error-notice" role="alert">{{ downloadError }}</p>
    <div v-if="!steps.length" class="empty-state"><AppIcon name="clock" :size="30" /><h3>{{ terminal ? '没有已完成的工具步骤' : '等待第一步执行完成' }}</h3><p>{{ terminal ? '请查看下方的任务结果或错误说明。' : '工具调用结束后，执行结果会显示在这里。' }}</p></div>
    <div v-else-if="!visible.length" class="empty-state" role="status"><AppIcon name="check" :size="28" /><h3>当前没有未成功的步骤</h3><p>可切换到全部步骤查看执行过程；最终修复情况以下方结果为准。</p></div>
    <ol v-else class="execution-list"><li v-for="step in visible" :key="step.sequence" :value="step.sequence">
      <div class="step-marker" :class="{ 'step-failed': !step.result.success && !originalDefect(step, steps), 'step-original-defect': originalDefect(step, steps) }">{{ step.sequence }}</div>
      <details class="step-detail" :open="expanded.has(step.sequence)"><summary @click.prevent="toggle(step.sequence)"><div><strong>{{ step.call.name === 'run_tests' ? testLabels[testPhase(step, steps)] : labels[step.call.name] ?? step.call.name }}</strong><code class="tool-name">{{ step.call.name }}</code></div><span class="step-outcome" :class="step.result.success ? 'text-success' : originalDefect(step, steps) ? 'text-warning' : 'text-danger'">{{ testOutcome(step, steps) }}<AppIcon name="chevron" :size="15" /></span></summary>
        <p v-if="step.call.name === 'run_tests'" class="step-explanation">{{ testExplanation(step) }}</p>
        <p v-if="testCounts(step.result)" class="step-test-counts">共 {{ testCounts(step.result)!.tests }} 项测试 · 失败 {{ testCounts(step.result)!.failures }} 项 · 异常 {{ testCounts(step.result)!.errors }} 项 · 跳过 {{ testCounts(step.result)!.skipped }} 项</p>
        <p class="step-time">完成于 {{ time(step.completed_at) }}</p>
        <h3 class="output-label">调用参数</h3><pre class="parameter-output">{{ JSON.stringify(step.call.arguments, null, 2) }}</pre>
        <h3 class="output-label">执行输出</h3><pre class="tool-output">{{ step.result.output || '无文本输出' }}</pre>
        <p v-if="step.result.metadata.truncated" class="warning-notice">输出已被工具截断，当前展示部分内容。</p>
        <p v-if="step.result.error_message" class="step-error">{{ step.result.error_code }}：{{ step.result.error_message }}</p>
      </details>
    </li></ol>
    <div v-if="!terminal" class="execution-footnote"><span class="activity-dot"></span>等待后续步骤。中间一次工具失败不代表最终修复失败。</div>
  </section>
</template>
