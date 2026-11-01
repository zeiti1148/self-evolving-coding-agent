<script setup lang="ts">
import type { ExperienceKnowledge } from '../api/contracts'
defineProps<{ value: ExperienceKnowledge }>()
const capabilityLabels: Record<string, string> = { 'text-input': '文本输入', 'integer-input': '整数输入', 'indexed-selection': '按下标选择' }
</script>
<template>
  <dl>
    <div><dt>复用状态</dt><dd>{{ value.reuse_status === 'candidate' ? '候选：尚未发布，不会自动注入' : value.reuse_status === 'legacy_unreviewed' || !value.reuse_status && value.scope !== 'project' ? '未审阅：不会自动注入' : value.scope === 'global' ? '已发布通用经验：仍需验证目标业务条件' : '已验证的仓库经验' }}</dd></div>
    <div><dt>问题根因</dt><dd>{{ value.root_cause }}</dd></div>
    <div><dt>修复方案</dt><dd>{{ value.solution }}</dd></div>
    <div v-if="value.applicability?.length"><dt>适用条件</dt><dd><ul class="knowledge-list"><li v-for="(condition, index) in value.applicability" :key="index">{{ condition }}</li></ul></dd></div>
    <div v-if="value.issues?.length"><dt>问题与修复顺序 · {{ value.issues.length }} 项</dt><dd><ol class="issue-list"><li v-for="(issue, index) in value.issues" :key="index"><h4>{{ issue.error_type }}</h4><p><strong>根因：</strong>{{ issue.root_cause }}</p><p><strong>措施：</strong>{{ issue.solution }}</p><ul v-if="issue.files.length" class="issue-files"><li v-for="file in issue.files" :key="file"><code>{{ file }}</code></li></ul></li></ol></dd></div>
    <div><dt>验证证据</dt><dd>{{ value.verification }}<ul v-if="value.evidence?.length" class="evidence-list"><li v-for="(evidence, index) in value.evidence" :key="index"><strong>{{ evidence.runner === 'pytest' ? 'pytest' : 'Maven' }} · {{ evidence.tests }} 项测试通过</strong><span>失败 {{ evidence.failures }} · 错误 {{ evidence.errors }} · 跳过 {{ evidence.skipped }}</span><span>工具调用 <code>{{ evidence.tool_call_id }}</code></span><span>来源任务 <code>{{ evidence.source_task_id }}</code></span></li></ul></dd></div>
    <div v-if="value.source_revision"><dt>来源版本</dt><dd><code>{{ value.source_revision }}</code></dd></div>
    <div v-if="value.merged_from?.length"><dt>合并来源 · 原记录保留</dt><dd><ul class="knowledge-list"><li v-for="id in value.merged_from" :key="id"><code>{{ id }}</code></li></ul></dd></div>
    <div v-if="value.required_capabilities?.length"><dt>目标所需能力</dt><dd>{{ value.required_capabilities.map(value => capabilityLabels[value] ?? value).join('、') }}</dd></div>
    <div v-if="value.derived_from?.length"><dt>通用化来源</dt><dd><RouterLink v-for="id in value.derived_from" :key="id" :to="{ path: '/experiences', query: { id } }"><code>{{ id }}</code></RouterLink></dd></div>
    <div v-if="value.supersedes_id"><dt>修订替代</dt><dd><RouterLink :to="{ path: '/experiences', query: { id: value.supersedes_id } }">{{ value.supersedes_id }}</RouterLink></dd></div>
    <div v-if="value.batch_id"><dt>提取批次</dt><dd><code>{{ value.batch_id }}</code><p class="field-hint">同一最终测试可被多条经验引用，不重复计算验证次数。</p></dd></div>
  </dl>
</template>
