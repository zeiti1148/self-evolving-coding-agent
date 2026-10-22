import { afterEach, expect, it, vi } from 'vitest'
import { createApiContext } from '../src/api/context'

afterEach(() => vi.unstubAllGlobals())

it.each(['', undefined])('uses the current origin for real API calls when no base URL is supplied', async base => {
  const fetcher = vi.fn().mockResolvedValue(new Response('[]'))
  vi.stubGlobal('fetch', fetcher)
  const context = createApiContext({ VITE_USE_MOCK: 'false', VITE_API_BASE_URL: base })
  await expect(context.api.listExperiences()).resolves.toEqual([])
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/experiences?limit=20&offset=0')
  expect(context.mock).toBe(false)
  expect(context.setScenario).toBeUndefined()
})

it('keeps explicit API origins and never falls back to mock data on errors', async () => {
  const fetcher = vi.fn().mockRejectedValue(new TypeError('offline'))
  vi.stubGlobal('fetch', fetcher)
  const context = createApiContext({ VITE_USE_MOCK: 'false', VITE_API_BASE_URL: 'https://api.example.test/' })
  await expect(context.api.listExperiences()).rejects.toMatchObject({ status: 0 })
  expect(fetcher.mock.calls[0]?.[0]).toBe('https://api.example.test/api/experiences?limit=20&offset=0')
})
