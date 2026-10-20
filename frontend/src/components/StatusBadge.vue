<script setup lang="ts">
import type { TaskStatus } from '../api/contracts'
defineProps<{ status: TaskStatus }>()
const labels: Record<TaskStatus, string> = { pending: '等待执行', running: '正在执行', succeeded: '执行成功', failed: '执行失败', stopped: '已停止' }
</script>
<template><span class="status-badge" :class="status"><i aria-hidden="true"></i>{{ labels[status] }}</span></template>
