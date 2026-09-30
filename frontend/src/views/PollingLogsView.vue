<script setup lang="ts">
import { usePagedTable } from '@/composables/usePagedTable'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { DeleteOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import { monitoredUsersApi, pollingLogsApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type { MonitoredUser, PollingRun } from '@/types'
import { formatDateTime, formatDuration } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
const accounts = ref<MonitoredUser[]>([])
const filters = reactive({
  monitored_user_id: '',
  status: 'all',
  trigger: 'all',
  started_after: '',
  started_before: '',
})
const {
  rows,
  loading,
  pagination: basePagination,
  query,
  load,
  reset,
  change,
} = usePagedTable(
  (params) =>
    pollingLogsApi.list({
      ...params,
      monitored_user_id: filters.monitored_user_id || undefined,
      status: filters.status === 'all' ? undefined : filters.status,
      trigger: filters.trigger === 'all' ? undefined : filters.trigger,
      started_after: filters.started_after || undefined,
      started_before: filters.started_before || undefined,
    }),
  '无法加载轮询记录',
)
query.page_size = 20
const pagination = computed(() => ({
  ...basePagination.value,
  pageSizeOptions: ['20', '30', '50', '100'],
}))
const deleting = ref<Set<PollingRun['id']>>(new Set())
const clearing = ref(false)
function remove(row: PollingRun) {
  Modal.confirm({
    title: '删除这条轮询记录？',
    content: '删除后不可恢复。仅删除执行记录，已采集内容和监听账号会保留；正在执行的采集继续运行。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      if (clearing.value || deleting.value.has(row.id)) return
      deleting.value.add(row.id)
      try {
        await pollingLogsApi.remove(row.id)
        await load()
        message.success('轮询记录已删除')
      } catch (error) {
        message.error(getErrorMessage(error, '删除轮询记录失败'))
        throw error
      } finally {
        deleting.value.delete(row.id)
      }
    },
  })
}
function clearAll() {
  Modal.confirm({
    title: '清空全部轮询记录？',
    content:
      '将永久删除所有账号、所有状态的轮询记录，不受当前筛选和分页限制。已采集内容和监听账号会保留，后续采集仍会产生新记录。',
    okText: '清空全部',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      if (clearing.value || deleting.value.size) return
      clearing.value = true
      try {
        await pollingLogsApi.clear()
        await reset()
        message.success('全部轮询记录已清空')
      } catch (error) {
        message.error(getErrorMessage(error, '清空轮询记录失败'))
        throw error
      } finally {
        clearing.value = false
      }
    },
  })
}
onMounted(async () => {
  void load()
  try {
    accounts.value = (await monitoredUsersApi.list({ page: 1, page_size: 100 })).items
  } catch {}
})
watch(
  () => [
    filters.monitored_user_id,
    filters.status,
    filters.trigger,
    filters.started_after,
    filters.started_before,
  ],
  () => {
    void reset()
  },
)
</script>

<template>
  <div class="page-stack">
    <PageHeader eyebrow="MONITOR / 03" title="轮询记录" description="追踪每次采集任务的执行结果"
      ><template #actions
        ><a-button @click="load"><ReloadOutlined /> 刷新</a-button></template
      ></PageHeader
    ><a-card :bordered="false"
      ><div class="toolbar">
        <div class="toolbar__controls">
          <a-select
            v-model:value="filters.monitored_user_id"
            allow-clear
            placeholder="全部账号"
            style="width: 160px"
            :options="
              accounts.map((item) => ({ label: `@${item.username}`, value: String(item.id) }))
            "
          /><a-select
            v-model:value="filters.status"
            style="width: 140px"
            :options="
              ['all', 'running', 'success', 'partial', 'failed', 'skipped'].map((value) => ({
                label: value === 'all' ? '全部状态' : value,
                value,
              }))
            "
          /><a-select
            v-model:value="filters.trigger"
            style="width: 140px"
            :options="[
              { label: '全部来源', value: 'all' },
              { label: '定时', value: 'scheduled' },
              { label: '手动', value: 'manual' },
            ]"
          /><a-input v-model:value="filters.started_after" type="date" /><a-input
            v-model:value="filters.started_before"
            type="date"
          />
        </div>
        <a-button danger :loading="clearing" :disabled="deleting.size > 0" @click="clearAll"
          ><DeleteOutlined /> 清空全部</a-button
        >
      </div>
      <a-table
        :data-source="rows"
        :loading="loading"
        row-key="id"
        :pagination="pagination"
        @change="change"
        ><a-table-column title="账号"
          ><template #default="{ record }"
            ><strong>@{{ record.username || '未知账号' }}</strong>
            <div class="muted">{{ record.trigger || 'scheduled' }}</div></template
          ></a-table-column
        ><a-table-column title="状态"
          ><template #default="{ record }"
            ><StatusPill :value="record.status" /></template></a-table-column
        ><a-table-column title="执行时间"
          ><template #default="{ record }">{{
            formatDateTime(record.started_at)
          }}</template></a-table-column
        ><a-table-column title="耗时"
          ><template #default="{ record }">{{
            formatDuration(record.duration_ms)
          }}</template></a-table-column
        ><a-table-column title="采集 / 新增"
          ><template #default="{ record }"
            ><span class="mono"
              >{{ record.tweets_fetched }} / {{ record.tweets_inserted }}</span
            ></template
          ></a-table-column
        ><a-table-column title="错误"
          ><template #default="{ record }"
            ><span class="muted">{{ record.error_message || '—' }}</span></template
          ></a-table-column
        ><a-table-column title="操作" :width="80"
          ><template #default="{ record }"
            ><a-tooltip title="删除轮询记录"
              ><a-button
                type="text"
                danger
                aria-label="删除轮询记录"
                :loading="deleting.has(record.id)"
                :disabled="clearing || deleting.has(record.id)"
                @click="remove(record)"
                ><DeleteOutlined /></a-button></a-tooltip></template></a-table-column></a-table
    ></a-card>
  </div>
</template>
