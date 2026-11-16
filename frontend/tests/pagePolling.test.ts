import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPagePolling } from '../src/utils/pagePolling'

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('page visibility polling', () => {
  it('pauses hidden tabs, refreshes once on return, and disposes listeners', async () => {
    vi.useFakeTimers()
    let visibility: DocumentVisibilityState = 'visible'
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
    const polling = createPagePolling(), refresh = vi.fn()
    try {
      polling.schedule(refresh, 5000)
      visibility = 'hidden'; document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(60000)
      expect(refresh).not.toHaveBeenCalled()
      visibility = 'visible'; document.dispatchEvent(new Event('visibilitychange'))
      expect(refresh).toHaveBeenCalledTimes(1)
      document.dispatchEvent(new Event('visibilitychange'))
      expect(refresh).toHaveBeenCalledTimes(1)
      polling.schedule(refresh, 5000)
      polling.dispose()
      await vi.advanceTimersByTimeAsync(10000)
      document.dispatchEvent(new Event('visibilitychange'))
      expect(refresh).toHaveBeenCalledTimes(1)
    } finally { polling.dispose() }
  })

  it('replaces scheduled refreshes and waits until an initially hidden page is visible', async () => {
    vi.useFakeTimers()
    let visibility: DocumentVisibilityState = 'hidden'
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
    const polling = createPagePolling(), old = vi.fn(), current = vi.fn()
    try {
      polling.schedule(old, 5000); polling.schedule(current, 30000)
      await vi.advanceTimersByTimeAsync(60000)
      expect(current).not.toHaveBeenCalled()
      visibility = 'visible'; document.dispatchEvent(new Event('visibilitychange'))
      expect(current).toHaveBeenCalledTimes(1); expect(old).not.toHaveBeenCalled()
      polling.schedule(current, 5000); polling.cancel()
      await vi.advanceTimersByTimeAsync(10000)
      expect(current).toHaveBeenCalledTimes(1)
    } finally { polling.dispose() }
  })
})
