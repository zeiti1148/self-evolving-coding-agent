import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import NewTaskView from './views/NewTaskView.vue'
import TaskView from './views/TaskView.vue'
import ExperiencesView from './views/ExperiencesView.vue'
import { apiContextKey, type ApiContext } from './api/context'
import { createHttpApi } from './api/http'
import { createMockApi } from './api/mock'
import './styles.css'

const mock = import.meta.env.VITE_USE_MOCK !== 'false'
const projectId = import.meta.env.VITE_SOURCE_PROJECT || 'java-null-greeting'
const projectLabel = import.meta.env.VITE_SOURCE_PROJECT_LABEL || 'Java 空指针修复样例'
const demo = mock ? createMockApi({ projectId }) : null
const context: ApiContext = { api: demo ?? createHttpApi(import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'),
  mock, projectId, projectLabel, setScenario: demo?.setScenario }
const router = createRouter({ history: createWebHashHistory(), routes: [
  { path: '/', component: NewTaskView, meta: { title: '新建任务' } },
  { path: '/tasks/:taskId', component: TaskView, meta: { title: '任务执行' } },
  { path: '/experiences', component: ExperiencesView, meta: { title: '经验记录' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
], scrollBehavior: () => ({ top: 0 }) })
router.afterEach(to => { document.title = `${String(to.meta.title ?? '工作台')} · Coding Agent` })
createApp(App).provide(apiContextKey, context).use(router).mount('#app')
