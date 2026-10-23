import { readonly, ref } from 'vue'

type Theme = 'light' | 'dark'
const KEY = 'coding-agent-theme'
export function useTheme() {
  let initial: Theme = 'light'
  try { if (window.matchMedia('(prefers-color-scheme: dark)').matches) initial = 'dark' } catch { /* Older browsers use light. */ }
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === 'light' || saved === 'dark') initial = saved
  } catch { /* Theme selection remains available without storage. */ }
  const theme = ref<Theme>(initial)
  document.documentElement.dataset.theme = initial
  function setTheme(value: Theme) {
    theme.value = value
    document.documentElement.dataset.theme = value
    try { localStorage.setItem(KEY, value) } catch { /* Keep the choice for this page. */ }
  }
  return { theme: readonly(theme), setTheme }
}
