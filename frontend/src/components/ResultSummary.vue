<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { AgentResult } from '../api/contracts'
import { testCounts } from '../features/testPresentation'
import AppIcon from './AppIcon.vue'
const props = defineProps<{ result: AgentResult; testSourceAvailable?: boolean; codeDiffAvailable?: boolean }>()
const verificationCounts = computed(() => {
  const final = props.result.tool_results.at(-1)
  return final?.metadata.final_verification === true ? testCounts(final) : null
})
const conclusion = computed(() => {
  const result = props.result
  if (result.tests_passed === true) return result.status === 'succeeded' && result.repair_succeeded
    ? '程序最终测试已通过，修复成功。' : '最终测试已通过，但任务流程尚未完成。'
  if (result.tests_passed === false) return '程序最终测试未通过，修复尚未完成。'
  return '任务未完成验证，暂不能确认修复成功。'
})
</script>
<template>
  <section class="panel result-panel" aria-labelledby="result-title">
    <div class="panel-heading"><span class="section-icon"><AppIcon name="check" /></span><h2 id="result-title">最终结果</h2></div>
    <div class="result-body">
      <p class="result-summary">{{ conclusion }}</p>
      <slot name="evidence" />
      <p v-if="testSourceAvailable"><RouterLink :to="`/tasks/${result.task_id}/tests`" class="source-entry-link"><AppIcon name="code" :size="17" />查看测试源码</RouterLink></p>
      <p v-if="codeDiffAvailable"><RouterLink :to="`/tasks/${result.task_id}/changes`" class="source-entry-link"><AppIcon name="file" :size="17" />查看修复前后对比</RouterLink></p>
      <p v-if="verificationCounts" class="field-hint">最终验证：共 {{ verificationCounts.tests }} 项测试 · 失败 {{ verificationCounts.failures }} 项 · 异常 {{ verificationCounts.errors }} 项 · 跳过 {{ verificationCounts.skipped }} 项</p>
      <div class="fact-grid">
        <div><span>代码修复</span><strong :class="result.repair_succeeded ? 'text-success' : 'text-muted'">{{ result.repair_succeeded ? '修复成功' : '未确认修复成功' }}</strong></div>
        <div><span>最终测试</span><strong :class="result.tests_passed === true ? 'text-success' : result.tests_passed === false ? 'text-danger' : 'text-muted'">{{ result.tests_passed === true ? '测试通过' : result.tests_passed === false ? '测试未通过' : '未完成验证' }}</strong></div>
        <div><span>经验记录</span><strong :class="result.experience_saved === true ? 'text-success' : result.experience_saved === false ? 'text-warning' : 'text-muted'">{{ result.experience_saved === true ? '已保存经验' : result.experience_saved === false ? '保存失败' : '未尝试保存' }}</strong></div>
      </div>
      <p v-if="result.error_message" class="error-notice">{{ result.error_message }}</p>
      <p v-if="result.experience_save_error" class="warning-notice">{{ result.experience_save_error }}</p>
      <details class="process-summary"><summary>过程摘要（供参考）</summary><p class="field-hint">过程摘要可能早于最终测试；最终结论以上方验证结果为准。</p><p>{{ result.summary }}</p></details>
      <div class="changed-files"><h3>修改的文件 <span class="count-chip">{{ result.changed_files.length }}</span></h3><ul v-if="result.changed_files.length"><li v-for="file in result.changed_files" :key="file"><AppIcon name="file" :size="17" /><RouterLink v-if="codeDiffAvailable && /\.(java|py)$/i.test(file)" :to="{ path: `/tasks/${result.task_id}/changes`, query: { file } }" :aria-label="`查看 ${file} 的代码对比`"><code>{{ file }}</code></RouterLink><code v-else>{{ file }}</code></li></ul><p v-else class="text-muted">没有记录文件修改。</p></div>
      <div v-if="result.experience_saved" class="saved-experience"><div><AppIcon name="book" /><span>这次修复已进入经验库。</span></div><ul class="experience-links"><li v-for="(id, index) in (result.experience_ids ?? (result.experience_id ? [result.experience_id] : []))" :key="id"><RouterLink :to="{ path: '/experiences', query: { id } }">经验 {{ index + 1 }} <code>{{ id }}</code></RouterLink></li></ul></div>
    </div>
  </section>
</template>
