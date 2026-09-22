<script setup lang="ts">
defineProps<{ label: string; value: string | number; detail?: string; accent?: 'terracotta' | 'sage' | 'slate' | 'ochre' }>()
</script>

<template>
  <a-card class="metric-card" :class="`metric-card--${accent || 'terracotta'}`" :bordered="false">
    <span class="metric-card__label">{{ label }}</span>
    <strong>{{ value }}</strong>
    <small>{{ detail }}</small>
  </a-card>
</template>
