/** Pause background-tab polling; keep one scheduled refresh per component. */
export function createPagePolling() {
  let timer: ReturnType<typeof setTimeout> | undefined
  let pending: (() => void) | undefined
  let disposed = false
  const clearTimer = () => { if (timer !== undefined) clearTimeout(timer); timer = undefined }
  const run = () => {
    clearTimer()
    if (disposed || document.visibilityState === 'hidden') return
    const callback = pending; pending = undefined; callback?.()
  }
  const visibilityChanged = () => {
    if (document.visibilityState === 'hidden') clearTimer()
    else if (pending) run()
  }
  document.addEventListener('visibilitychange', visibilityChanged)
  return {
    cancel() { clearTimer(); pending = undefined },
    schedule(callback: () => void, delay: number) {
      clearTimer()
      if (disposed) return
      pending = callback
      if (document.visibilityState !== 'hidden') timer = setTimeout(run, delay)
    },
    dispose() {
      disposed = true; clearTimer(); pending = undefined
      document.removeEventListener('visibilitychange', visibilityChanged)
    },
  }
}
