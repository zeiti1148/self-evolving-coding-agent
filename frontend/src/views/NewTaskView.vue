<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useApiContext } from '../api/context'
import type { MockScenario } from '../api/contracts'
import AppIcon from '../components/AppIcon.vue'
const context = useApiContext()
const router = useRouter()
const description = ref('')
const maxSteps = ref<number | string>(20)
const scenario = ref<MockScenario>('success')
const submitting = ref(false)
const error = ref('')
let controller: AbortController | undefined
onBeforeUnmount(() => controller?.abort())
function fillExample() { description.value = '修复 GreetingService.greet 方法的空指针问题：当 name 为 null 时返回 "Hello, guest!"，保持普通字符串的问候行为不变，并运行测试验证。' }
async function submit() {
  if (submitting.value) return
  error.value = ''
  const task_description = description.value.trim()
  if (!task_description || task_description.length > 50000) { error.value = '请填写 1 至 50000 字的任务描述。'; return }
  const max_steps = Number(maxSteps.value)
  if (!Number.isInteger(max_steps) || max_steps < 1 || max_steps > 100) { error.value = '最大决策轮数必须是 1 至 100 的整数。'; return }
  submitting.value = true
  controller = new AbortController()
  if (context.mock) context.setScenario?.(scenario.value)
  try {
    const task = await context.api.createTask({ task_description, source_project: context.projectId, max_steps }, controller.signal)
    if (!controller.signal.aborted) await router.push(`/tasks/${task.task_id}`)
  } catch (cause) {
    if (!controller.signal.aborted) error.value = cause instanceof Error ? cause.message : '提交失败，请稍后重试。'
  } finally { submitting.value = false }
}
</script>
<template>
  <div class="page-heading"><div><h1>提交一个修复任务</h1><p>描述问题与预期行为，查看 Agent 如何逐步定位、修改和验证。</p></div><span class="subtle-label">Java / Maven / JUnit</span></div>
  <div class="create-layout">
    <form class="panel task-form" novalidate @submit.prevent="submit">
      <div class="panel-heading"><span class="section-icon"><AppIcon name="terminal" /></span><h2>任务配置</h2></div>
      <div class="form-body">
        <label for="project">目标项目</label>
        <select id="project" :value="context.projectId" :disabled="submitting"><option :value="context.projectId">{{ context.projectLabel }}</option></select>
        <p class="field-hint">使用已登记的项目样例，修复将在独立工作副本中进行。</p>
        <div class="label-row"><label for="description">任务描述 <span class="required">*</span></label><button class="text-button" type="button" :disabled="submitting" @click="fillExample">填入修复示例</button></div>
        <textarea id="description" v-model="description" rows="7" maxlength="50000" :disabled="submitting" placeholder="例如：greet 方法在传入 null 时抛出异常，请修复并保证原有测试通过。" aria-describedby="description-hint" />
        <div id="description-hint" class="field-hint split"><span>说明错误表现、预期结果和需要保留的行为。</span><span>{{ description.length }} / 50000</span></div>
        <div class="form-row"><div><label for="max-steps">最大决策轮数</label><input id="max-steps" v-model="maxSteps" type="number" min="1" max="100" step="1" :disabled="submitting" /><p class="field-hint">1–100 轮，工具步骤数量可能更多。</p></div>
          <div v-if="context.mock"><label for="scenario">演示场景</label><select id="scenario" v-model="scenario" :disabled="submitting"><option value="success">修复成功并保存经验</option><option value="tests-failed">最终测试未通过</option><option value="stopped">达到上限，停止执行</option><option value="save-failure">修复成功，经验保存失败</option><option value="schedule-error">任务调度失败</option></select></div></div>
        <div v-if="error" class="error-notice" role="alert"><AppIcon name="warning" />{{ error }}</div>
      </div>
      <div class="form-footer"><span><AppIcon name="shield" :size="17" />原始样例保持不变</span><button class="button primary" type="submit" :disabled="submitting"><AppIcon :name="submitting ? 'clock' : 'play'" :size="17" />{{ submitting ? '正在提交…' : '提交任务' }}</button></div>
    </form>
    <aside class="create-aside">
      <section class="workflow-note"><h2>一次修复的完整过程</h2><ol class="workflow-list"><li><span>1</span><div><h3>定位问题</h3><p>查看代码与测试输出，识别错误原因。</p></div></li><li><span>2</span><div><h3>修改与验证</h3><p>在工作副本中修改代码，运行测试验证结果。</p></div></li><li><span>3</span><div><h3>留下可复用的经验</h3><p>记录根因、修复方案与验证方法。</p></div></li></ol></section>
      <section class="code-preview"><div><AppIcon name="file" :size="16" /><span>GreetingService.java</span><span class="code-tag">示例</span></div><pre><span class="code-muted">// 待修复的空指针问题</span>
public String greet(String name) {
  return "Hello, "
    + name.trim() + "!";
}</pre><p><span class="small-dot"></span>null 输入缺少保护</p></section>
      <p class="aside-footnote">成功依据是最终测试结果。经验保存状态会独立记录。</p>
    </aside>
  </div>
</template>
