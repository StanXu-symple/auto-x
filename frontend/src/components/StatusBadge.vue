<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status?: string | boolean | null; label?: string }>()

const normalized = computed(() => {
  if (props.status === true) return 'healthy'
  if (props.status === false) return 'paused'
  return String(props.status || 'unknown').toLowerCase()
})

const tone = computed(() => {
  if (['active', 'idle', 'healthy', 'success', 'online', 'connected', 'ok'].includes(normalized.value)) return 'success'
  if (['paused', 'skipped', 'pending', 'queued', 'unknown', 'offline'].includes(normalized.value)) return 'neutral'
  if (['polling', 'running'].includes(normalized.value)) return 'info'
  if (['partial', 'degraded', 'warning'].includes(normalized.value)) return 'warning'
  return 'danger'
})

const labels: Record<string, string> = {
  active: '运行中',
  idle: '正常',
  healthy: '健康',
  success: '成功',
  online: '在线',
  connected: '已连接',
  ok: '正常',
  paused: '已暂停',
  skipped: '已跳过',
  pending: '等待中',
  queued: '队列中',
  unknown: '未知',
  offline: '离线',
  polling: '轮询中',
  running: '执行中',
  partial: '部分成功',
  degraded: '异常',
  warning: '警告',
  error: '错误',
  unhealthy: '异常',
  rate_limited: '已限流',
  failed: '失败',
  down: '不可用',
}
</script>

<template>
  <span class="status-badge" :class="`status-badge--${tone}`">
    <span class="status-badge__dot" />
    {{ label || labels[normalized] || normalized }}
  </span>
</template>
