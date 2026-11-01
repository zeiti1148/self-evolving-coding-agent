<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppIcon from './components/AppIcon.vue'
import { useApiContext } from './api/context'
import { useTheme } from './features/theme'
const { theme, setTheme } = useTheme()
const { mock, demoNotice } = useApiContext()
const route = useRoute()
const key = `coding-agent-last-task-${mock ? 'mock' : 'http'}`
const lastTask = ref('')
const mainContent = ref<HTMLElement | null>(null)
try { lastTask.value = sessionStorage.getItem(key) ?? '' } catch { /* Optional convenience. */ }
watch(() => route.params.taskId, id => {
  if (typeof id === 'string') { lastTask.value = id; try { sessionStorage.setItem(key, id) } catch { /* Optional. */ } }
}, { immediate: true })
</script>
<template>
  <a class="skip-link" href="#main-content" @click.prevent="mainContent?.focus()">跳到主要内容</a>
  <div class="app-shell">
    <aside class="sidebar">
      <RouterLink class="brand" to="/" aria-label="Coding Agent 首页"><span class="brand-mark"><AppIcon name="terminal" :size="23" /></span><span><strong>Coding Agent</strong><small>代码修复工作台</small></span></RouterLink>
      <div class="workspace-label">工作空间</div>
      <nav aria-label="主要导航">
        <RouterLink to="/" class="nav-link" exact-active-class="active"><AppIcon name="plus" />新建任务</RouterLink>
        <RouterLink v-if="lastTask" :to="`/tasks/${lastTask}`" class="nav-link" active-class="active"><AppIcon name="play" />任务执行</RouterLink>
        <span v-else class="nav-link unavailable" title="提交任务后查看执行过程"><AppIcon name="play" />任务执行</span>
        <RouterLink to="/experiences" class="nav-link" active-class="active"><AppIcon name="book" />经验记录</RouterLink>
      </nav>
      <div class="sidebar-bottom"><div class="project-mark"><AppIcon name="code" /><span>持续积累经验的<br /><strong>代码修复助手</strong></span></div><p>从修复过程积累经验<br />为下一次任务提供参考</p><span class="version">实训原型 / V3</span></div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="breadcrumbs">工作台 <AppIcon name="chevron" :size="13" /><span>{{ route.meta.title }}</span></div>
        <div class="topbar-actions">
          <div class="theme-picker" role="group" aria-label="页面主题">
            <button type="button" :aria-pressed="theme === 'light'" @click="setTheme('light')"><AppIcon name="sun" :size="16" />浅色</button>
            <button type="button" :aria-pressed="theme === 'dark'" @click="setTheme('dark')"><AppIcon name="moon" :size="16" />深色</button>
          </div>
          <span class="mode-badge" :class="{ live: !mock }"><i></i>{{ mock ? '模拟演示' : '后端连接模式' }}</span>
        </div>
      </header>
      <main id="main-content" ref="mainContent" tabindex="-1">
        <div v-if="mock" class="demo-notice"><AppIcon name="code" :size="17" /><span>当前展示模拟执行过程与经验数据，不会实际调用模型或修改 Java 文件。</span></div>
        <p v-if="mock && demoNotice" class="warning-notice" role="status">{{ demoNotice }}</p>
        <RouterView />
      </main>
      <footer class="workspace-footer"><span>具备经验积累能力的代码修复助手</span><span>任务执行 · 测试验证 · 经验积累</span></footer>
    </div>
  </div>
</template>
