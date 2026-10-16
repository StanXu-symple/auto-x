<script setup lang="ts">
import StatusPill from '@/components/StatusPill.vue'
import { reconciliationLabels, safeXhsPublishedUrl } from '@/utils/publishReconciliation'
import { formatDateTime } from '@/utils/format'
import type { ArticlePublishReconcileDecision } from '@/types'

defineProps<{
  dispatches?: Array<{
    channel: 'xhs' | 'qq'
    status: string
    last_error?: string | null
    reconciliation_result?: ArticlePublishReconcileDecision | null
    reconciliation_note?: string | null
    reconciled_at?: string | null
    published_url?: string | null
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

function dispatchStatus(dispatch: { status: string; reconciliation_result?: string | null }) {
  return dispatch.reconciliation_result === 'not_published' && dispatch.status === 'failed'
    ? '已核对未发布'
    : statusNames[dispatch.status] || dispatch.status
}
</script>

<template>
  <a-space v-if="dispatches?.length" size="small" wrap>
    <span v-for="dispatch in dispatches" :key="dispatch.channel">
      <a-tooltip>
        <template #title>
          <div>{{ dispatch.last_error || '生成后自动推送' }}</div>
          <template v-if="dispatch.reconciliation_result">
            <div>{{ reconciliationLabels[dispatch.reconciliation_result] }}</div>
            <div v-if="dispatch.reconciliation_note">
              核对说明：{{ dispatch.reconciliation_note }}
            </div>
            <div v-if="dispatch.reconciled_at">
              核对时间：{{ formatDateTime(dispatch.reconciled_at) }}
            </div>
          </template>
        </template>
        <StatusPill
          :value="dispatch.status"
          :label="`${channelNames[dispatch.channel]}：${dispatchStatus(dispatch)}`"
        />
      </a-tooltip>
      <a
        v-if="safeXhsPublishedUrl(dispatch.published_url)"
        :href="safeXhsPublishedUrl(dispatch.published_url)"
        target="_blank"
        rel="noopener noreferrer"
        style="white-space: nowrap"
        >查看笔记</a
      >
    </span>
  </a-space>
  <span v-else class="muted">—</span>
</template>
