import { inject, type InjectionKey } from 'vue'
import type { AgentApi, MockScenario } from './contracts'

export interface ApiContext {
  api: AgentApi; mock: boolean; projectId: string; projectLabel: string
  setScenario?: (scenario: MockScenario) => void
}
export const apiContextKey: InjectionKey<ApiContext> = Symbol('agent-api')
export function useApiContext(): ApiContext {
  const context = inject(apiContextKey)
  if (!context) throw new Error('API context is missing')
  return context
}
