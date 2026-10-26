<script setup lang="ts">
import type { ExperienceKnowledge } from '../api/contracts'
defineProps<{ value: ExperienceKnowledge }>()
</script>
<template>
  <dl>
    <div><dt>问题根因</dt><dd>{{ value.root_cause }}</dd></div>
    <div><dt>修复方案</dt><dd>{{ value.solution }}</dd></div>
    <div v-if="value.applicability?.length"><dt>适用条件</dt><dd><ul class="knowledge-list"><li v-for="(condition, index) in value.applicability" :key="index">{{ condition }}</li></ul></dd></div>
    <div v-if="value.issues?.length"><dt>问题与修复顺序 · {{ value.issues.length }} 项</dt><dd><ol class="issue-list"><li v-for="(issue, index) in value.issues" :key="index"><h4>{{ issue.error_type }}</h4><p><strong>根因：</strong>{{ issue.root_cause }}</p><p><strong>措施：</strong>{{ issue.solution }}</p><ul v-if="issue.files.length" class="issue-files"><li v-for="file in issue.files" :key="file"><code>{{ file }}</code></li></ul></li></ol></dd></div>
    <div><dt>验证证据</dt><dd>{{ value.verification }}<ul v-if="value.evidence?.length" class="evidence-list"><li v-for="(evidence, index) in value.evidence" :key="index"><strong>{{ evidence.runner === 'pytest' ? 'pytest' : 'Maven' }} · {{ evidence.tests }} 项测试通过</strong><span>失败 {{ evidence.failures }} · 错误 {{ evidence.errors }} · 跳过 {{ evidence.skipped }}</span><span>工具调用 <code>{{ evidence.tool_call_id }}</code></span><span>来源任务 <code>{{ evidence.source_task_id }}</code></span></li></ul></dd></div>
    <div v-if="value.source_revision"><dt>来源版本</dt><dd><code>{{ value.source_revision }}</code></dd></div>
    <div v-if="value.merged_from?.length"><dt>合并来源 · 原记录保留</dt><dd><ul class="knowledge-list"><li v-for="id in value.merged_from" :key="id"><code>{{ id }}</code></li></ul></dd></div>
  </dl>
</template>
