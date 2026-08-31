<script setup lang="ts">
import { Inbox } from 'lucide-vue-next'

withDefaults(defineProps<{ title?: string; description?: string; compact?: boolean }>(), {
  title: '暂无数据',
  description: '当前没有可显示的内容',
  compact: false,
})
</script>

<template>
  <div class="empty-state" :class="{ 'empty-state--compact': compact }">
    <div class="empty-state__icon"><slot name="icon"><Inbox :size="24" /></slot></div>
    <strong>{{ title }}</strong>
    <p>{{ description }}</p>
    <div v-if="$slots.default" class="empty-state__action"><slot /></div>
  </div>
</template>
