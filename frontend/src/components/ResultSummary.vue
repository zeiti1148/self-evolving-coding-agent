<script setup lang="ts">
import { RouterLink } from 'vue-router'
import type { AgentResult } from '../api/contracts'
import AppIcon from './AppIcon.vue'
defineProps<{ result: AgentResult }>()
</script>
<template>
  <section class="panel result-panel" aria-labelledby="result-title">
    <div class="panel-heading"><span class="section-icon"><AppIcon name="check" /></span><h2 id="result-title">最终结果</h2></div>
    <div class="result-body">
      <p class="result-summary">{{ result.summary }}</p>
      <div class="fact-grid">
        <div><span>代码修复</span><strong :class="result.repair_succeeded ? 'text-success' : 'text-muted'">{{ result.repair_succeeded ? '修复成功' : '未确认修复成功' }}</strong></div>
        <div><span>最终测试</span><strong :class="result.tests_passed === true ? 'text-success' : result.tests_passed === false ? 'text-danger' : 'text-muted'">{{ result.tests_passed === true ? '测试通过' : result.tests_passed === false ? '测试未通过' : '未完成验证' }}</strong></div>
        <div><span>经验记录</span><strong :class="result.experience_saved === true ? 'text-success' : result.experience_saved === false ? 'text-warning' : 'text-muted'">{{ result.experience_saved === true ? '已保存经验' : result.experience_saved === false ? '保存失败' : '未尝试保存' }}</strong></div>
      </div>
      <p v-if="result.error_message" class="error-notice">{{ result.error_message }}</p>
      <p v-if="result.experience_save_error" class="warning-notice">{{ result.experience_save_error }}</p>
      <div class="changed-files"><h3>修改的文件 <span class="count-chip">{{ result.changed_files.length }}</span></h3><ul v-if="result.changed_files.length"><li v-for="file in result.changed_files" :key="file"><AppIcon name="file" :size="17" /><code>{{ file }}</code></li></ul><p v-else class="text-muted">没有记录文件修改。</p></div>
      <div v-if="result.experience_saved" class="saved-experience"><div><AppIcon name="book" /><span>这次修复已进入经验库。</span></div><ul class="experience-links"><li v-for="(id, index) in (result.experience_ids ?? (result.experience_id ? [result.experience_id] : []))" :key="id"><RouterLink :to="{ path: '/experiences', query: { id } }">经验 {{ index + 1 }} <code>{{ id }}</code></RouterLink></li></ul></div>
    </div>
  </section>
</template>
