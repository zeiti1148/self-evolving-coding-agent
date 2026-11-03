import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import NewTaskView from './views/NewTaskView.vue'
import TaskView from './views/TaskView.vue'
import TestSourceView from './views/TestSourceView.vue'
import CodeDiffView from './views/CodeDiffView.vue'
import ExperiencesView from './views/ExperiencesView.vue'
import { apiContextKey, createApiContext } from './api/context'
import './styles.css'

const context = createApiContext(import.meta.env)
const router = createRouter({ history: createWebHashHistory(), routes: [
  { path: '/', component: NewTaskView, meta: { title: '新建任务' } },
  { path: '/tasks/:taskId', component: TaskView, meta: { title: '任务执行' } },
  { path: '/tasks/:taskId/tests', component: TestSourceView, meta: { title: '测试源码' } },
  { path: '/tasks/:taskId/changes', component: CodeDiffView, meta: { title: '代码对比' } },
  { path: '/experiences', component: ExperiencesView, meta: { title: '经验记录' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
], scrollBehavior: (to, from, savedPosition) => {
  if (savedPosition) return savedPosition
  // Changing a code view or selected file should keep the reader at the code.
  return to.path === from.path && /\/(changes|tests)$/.test(to.path) ? false : { top: 0 }
} })
router.afterEach(to => { document.title = `${String(to.meta.title ?? '工作台')} · Coding Agent` })
createApp(App).provide(apiContextKey, context).use(router).mount('#app')
