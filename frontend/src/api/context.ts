import { inject, type InjectionKey } from 'vue'
import type { AgentApi, MockScenario } from './contracts'
import { createHttpApi } from './http'
import { createMockApi } from './mock'

export interface ApiContext {
  api: AgentApi; mock: boolean; projectId: string; projectLabel: string
  setScenario?: (scenario: MockScenario) => void
  demoNotice?: string | null
}
export const apiContextKey: InjectionKey<ApiContext> = Symbol('agent-api')
export function createApiContext(env: Record<string, string | undefined>): ApiContext {
  const mock = env.VITE_USE_MOCK !== 'false'
  const projectId = env.VITE_SOURCE_PROJECT || 'java-null-greeting'
  const projectLabel = env.VITE_SOURCE_PROJECT_LABEL || 'Java 空指针修复样例'
  const demo = mock ? createMockApi({ projectId }) : null
  return { api: demo ?? createHttpApi(env.VITE_API_BASE_URL?.trim() ?? ''),
    mock, projectId, projectLabel, setScenario: demo?.setScenario, demoNotice: demo?.recoveryNotice }
}
export function useApiContext(): ApiContext {
  const context = inject(apiContextKey)
  if (!context) throw new Error('API context is missing')
  return context
}
