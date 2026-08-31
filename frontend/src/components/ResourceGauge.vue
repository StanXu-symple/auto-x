<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  label: string
  value?: number
  detail?: string
  color?: 'purple' | 'cyan' | 'green' | 'orange'
}>()

const percent = computed(() => Math.min(100, Math.max(0, Number(props.value || 0))))
const circumference = 2 * Math.PI * 41
const dashOffset = computed(() => circumference * (1 - percent.value / 100))
</script>

<template>
  <div class="resource-gauge">
    <div class="resource-gauge__visual" :class="`is-${color || 'purple'}`">
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle cx="50" cy="50" r="41" class="resource-gauge__track" />
        <circle cx="50" cy="50" r="41" class="resource-gauge__value" :stroke-dasharray="circumference" :stroke-dashoffset="dashOffset" />
      </svg>
      <strong>{{ percent.toFixed(0) }}<small>%</small></strong>
    </div>
    <div><strong>{{ label }}</strong><span>{{ detail || '当前使用率' }}</span></div>
  </div>
</template>
