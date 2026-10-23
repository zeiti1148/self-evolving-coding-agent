import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useTheme } from '../src/features/theme'

beforeEach(() => { localStorage.clear(); delete document.documentElement.dataset.theme })
afterEach(() => vi.unstubAllGlobals())
function system(dark: boolean) { vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: dark }))) }

it('uses system preference initially and applies it to the document', () => {
  system(true)
  expect(useTheme().theme.value).toBe('dark')
  expect(document.documentElement.dataset.theme).toBe('dark')
})
it('persists explicit choices and restores them instead of system preference', () => {
  system(true)
  const state = useTheme()
  state.setTheme('light')
  expect(state.theme.value).toBe('light')
  expect(document.documentElement.dataset.theme).toBe('light')
  expect(useTheme().theme.value).toBe('light')
  state.setTheme('dark')
  expect(useTheme().theme.value).toBe('dark')
})
it('ignores invalid saved values and falls back to the system', () => {
  system(false)
  localStorage.setItem('coding-agent-theme', 'broken')
  expect(useTheme().theme.value).toBe('light')
})
it('allows switching even when browser storage is unavailable', () => {
  system(false)
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  const state = useTheme()
  expect(() => state.setTheme('dark')).not.toThrow()
  expect(document.documentElement.dataset.theme).toBe('dark')
})
