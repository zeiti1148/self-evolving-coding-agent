<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 })
const paths: Record<string, string> = {
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5',
  moon: 'M20.5 13.5A9 9 0 0 1 10.5 3a9 9 0 1 0 10 10.5Z',
  terminal: 'm5 5 6 7-6 7m9 0h6', plus: 'M12 5v14M5 12h14',
  book: 'M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H4V4Zm16 0h-4a3 3 0 0 0-3 3m7-3v15h-3a4 4 0 0 0-4 2',
  play: 'm8 5 11 7-11 7V5Z', code: 'm8 6-6 6 6 6m8-12 6 6-6 6m-3-14-2 16',
  check: 'm5 12 4 4L19 6', clock: 'M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  arrow: 'M5 12h14m-5-5 5 5-5 5', file: 'M14 2H5v20h14V7l-5-5Zm0 0v5h5M8 12h8m-8 4h6',
  chevron: 'm9 5 7 7-7 7', shield: 'm12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Zm-4 9 3 3 5-6',
  refresh: 'M20 7v5h-5m5 0a8 8 0 1 0-2 6M20 12l-3-5', warning: 'm12 3 10 18H2L12 3Zm0 6v5m0 3v1',
}
</script>
<template><svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] ?? paths.file" /></svg></template>
