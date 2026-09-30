<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ value?: string | boolean | null; label?: string }>()
const normalized = computed(() =>
  props.value === true
    ? 'active'
    : props.value === false
      ? 'paused'
      : String(props.value || 'unknown').toLowerCase(),
)
const tone = computed(() =>
  [
    'active',
    'healthy',
    'success',
    'online',
    'connected',
    'valid',
    'succeeded',
    'published',
    'sent',
    'ok',
  ].includes(normalized.value)
    ? 'success'
    : ['running', 'polling', 'queued', 'connecting', 'backfilling', 'pending'].includes(
          normalized.value,
        )
      ? 'info'
      : ['warning', 'partial', 'degraded', 'retry_wait', 'rate_limited'].includes(normalized.value)
        ? 'warning'
        : ['failed', 'error', 'invalid', 'down', 'unhealthy', 'identity_conflict'].includes(
              normalized.value,
            )
          ? 'danger'
          : 'neutral',
)
const labels: Record<string, string> = {
  active: '运行中',
  paused: '已暂停',
  idle: '等待下次轮询',
  backfilling: '历史同步中',
  rate_limited: '请求限流',
  identity_conflict: '账号身份冲突',
  pending: '待处理',
  healthy: '健康',
  success: '成功',
  valid: '校验通过',
  invalid: '凭据无效',
  running: '执行中',
  queued: '队列中',
  polling: '轮询中',
  succeeded: '已完成',
  unpublished: '未发布',
  published: '已发布',
  sent: '已发送',
  failed: '失败',
  error: '异常',
  unknown: '未知',
  online: '在线',
  offline: '离线',
  connecting: '连接中',
  degraded: '降级',
  partial: '部分成功',
  retry_wait: '等待重试',
}
</script>

<template>
  <a-tag class="status-pill" :class="`status-pill--${tone}`"
    ><span />{{ label || labels[normalized] || normalized }}</a-tag
  >
</template>
