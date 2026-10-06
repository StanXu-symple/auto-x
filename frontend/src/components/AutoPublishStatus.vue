<script setup lang="ts">
import StatusPill from '@/components/StatusPill.vue'

defineProps<{
  dispatches?: Array<{
    channel: 'xhs' | 'qq'
    status: string
    last_error?: string | null
  }>
}>()

const channelNames = { xhs: '小红书', qq: 'QQ' }
const statusNames: Record<string, string> = {
  pending: '待推送',
  retry_wait: '等待图片',
  dispatching: '推送中',
  accepted: '投递中',
  published: '已推送',
  failed: '推送失败',
  uncertain: '结果待核对',
}
</script>

<template>
  <a-space v-if="dispatches?.length" size="small" wrap>
    <a-tooltip v-for="dispatch in dispatches" :key="dispatch.channel">
      <template #title>{{ dispatch.last_error || '生成后自动推送' }}</template>
      <StatusPill
        :value="dispatch.status"
        :label="`${channelNames[dispatch.channel]}：${statusNames[dispatch.status] || dispatch.status}`"
      />
    </a-tooltip>
  </a-space>
  <span v-else class="muted">—</span>
</template>
