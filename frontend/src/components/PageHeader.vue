<script setup lang="ts">
defineProps<{ eyebrow?: string; title: string; description?: string }>()
</script>

<template>
  <header class="page-header">
    <div>
      <span v-if="eyebrow" class="page-eyebrow">{{ eyebrow }}</span>
      <h1>{{ title }}</h1>
      <p v-if="description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="page-header__actions"><slot name="actions" /></div>
  </header>
</template>
