<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { excerpt } from '../utils/experiencePresentation'
const props = withDefaults(defineProps<{ text: string; limit?: number }>(), { limit: 130 })
const expanded = ref(false), id = useId()
const preview = computed(() => excerpt(props.text, props.limit))
const shortened = computed(() => Array.from(props.text.replace(/\s+/g, ' ').trim()).length > props.limit)
watch(() => props.text, () => { expanded.value = false })
</script>
<template>
  <div class="experience-text">
    <p :id="id" :class="{ expanded }">{{ expanded ? text : preview }}</p>
    <button v-if="shortened" type="button" class="text-button" :aria-controls="id" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? '收起原文' : '展开原文' }}</button>
  </div>
</template>
<style scoped>
.experience-text{min-width:0}.experience-text p{margin:0;line-height:1.75;overflow-wrap:anywhere;font-size:13px}.experience-text p.expanded{white-space:pre-wrap}.experience-text button{font-size:12px;margin-top:6px}
</style>
