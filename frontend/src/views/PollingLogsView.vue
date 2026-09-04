<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { AlertCircle, CalendarDays, ChevronDown, Clock3, FileClock, RefreshCw, TerminalSquare } from 'lucide-vue-next'
import { monitoredUsersApi, pollingLogsApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type { MonitoredUser, PollingRun } from '@/types'
import { formatDateTime, formatDuration } from '@/utils/format'
import EmptyState from '@/components/EmptyState.vue'
import PaginationBar from '@/components/PaginationBar.vue'
import StatusBadge from '@/components/StatusBadge.vue'

const runs = ref<PollingRun[]>([])
const accounts = ref<MonitoredUser[]>([])
const total = ref(0)
const loading = ref(true)
const error = ref('')
const expanded = ref<string | null>(null)
const filters = reactive({ page: 1, page_size: 20, monitored_user_id: '', status: 'all', trigger: 'all', date_from: '', date_to: '' })
const successCount = computed(() => runs.value.filter((run) => run.status === 'success').length)
const issueCount = computed(() => runs.value.filter((run) => ['failed', 'error', 'rate_limited', 'partial'].includes(run.status)).length)
const hasFilters = computed(() => Boolean(
  filters.monitored_user_id || filters.status !== 'all' || filters.trigger !== 'all' || filters.date_from || filters.date_to,
))

function clearFilters() {
  Object.assign(filters, {
    monitored_user_id: '', status: 'all', trigger: 'all', date_from: '', date_to: '',
  })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await pollingLogsApi.list({
      page: filters.page,
      page_size: filters.page_size,
      monitored_user_id: filters.monitored_user_id || undefined,
      status: filters.status === 'all' ? undefined : filters.status,
      trigger: filters.trigger === 'all' ? undefined : filters.trigger,
      started_after: filters.date_from ? `${filters.date_from}T00:00:00Z` : undefined,
      started_before: filters.date_to ? `${filters.date_to}T23:59:59Z` : undefined,
    })
    runs.value = result.items
    total.value = result.total
  } catch (requestError) {
    error.value = getErrorMessage(requestError, '无法加载轮询记录')
  } finally {
    loading.value = false
  }
}

watch(
  () => [filters.monitored_user_id, filters.status, filters.trigger, filters.date_from, filters.date_to],
  () => {
    filters.page = 1
    load()
  },
)

function changePage(page: number) {
  filters.page = page
  load()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

onMounted(async () => {
  load()
  try {
    accounts.value = (await monitoredUsersApi.list({ page: 1, page_size: 100 })).items
  } catch {
    accounts.value = []
  }
})
</script>

<template>
  <div class="logs-view page-stack">
    <section class="summary-strip">
      <div class="summary-strip__metric"><span class="summary-strip__icon"><FileClock :size="18" /></span><span><small>记录总数</small><strong>{{ total }}</strong></span></div>
      <i />
      <div class="summary-strip__metric"><span class="summary-strip__icon is-success"><TerminalSquare :size="18" /></span><span><small>当前页成功</small><strong>{{ successCount }}</strong></span></div>
      <i />
      <div class="summary-strip__metric"><span class="summary-strip__icon is-danger"><AlertCircle :size="18" /></span><span><small>当前页异常</small><strong>{{ issueCount }}</strong></span></div>
    </section>

    <section class="panel data-panel">
      <header class="data-toolbar data-toolbar--logs">
        <div class="toolbar-heading"><strong>执行记录</strong><span>按账号、状态和时间定位每次轮询结果</span></div>
        <el-tooltip content="刷新记录"><el-button class="logs-refresh" circle :loading="loading" aria-label="刷新记录" @click="load"><RefreshCw v-if="!loading" :size="16" /></el-button></el-tooltip>
      </header>

      <div class="log-filter-grid">
        <label class="field field--compact"><span class="field__label">监听账号</span><el-select v-model="filters.monitored_user_id" filterable clearable placeholder="全部账号"><el-option v-for="account in accounts" :key="account.id" :label="`@${account.username}`" :value="String(account.id)" /></el-select></label>
        <label class="field field--compact"><span class="field__label">执行状态</span>
        <el-select v-model="filters.status" class="element-filter-select"><el-option label="全部状态" value="all" /><el-option label="成功" value="success" /><el-option label="失败" value="error" /><el-option label="限流" value="rate_limited" /><el-option label="执行中" value="running" /><el-option label="已跳过" value="skipped" /></el-select>
        </label>
        <label class="field field--compact"><span class="field__label">触发方式</span>
        <el-select v-model="filters.trigger" class="element-filter-select"><el-option label="全部方式" value="all" /><el-option label="定时调度" value="scheduled" /><el-option label="手动触发" value="manual" /></el-select>
        </label>
        <label class="field field--compact"><span class="field__label">起始日期</span><el-date-picker v-model="filters.date_from" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" :prefix-icon="CalendarDays" /></label>
        <label class="field field--compact"><span class="field__label">结束日期</span><el-date-picker v-model="filters.date_to" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" :prefix-icon="CalendarDays" /></label>
        <el-button class="log-filter-grid__clear" :disabled="!hasFilters" @click="clearFilters">清空筛选</el-button>
      </div>

      <div class="content-result-bar" aria-live="polite"><span class="content-result-bar__count">共 <strong>{{ total }}</strong> 次执行记录</span><span class="content-result-bar__hint">时间按浏览器本地时区显示</span></div>

      <div v-if="error && !runs.length" class="error-panel error-panel--embedded">
        <AlertCircle :size="21" /><div><strong>记录加载失败</strong><span>{{ error }}</span></div><button class="button button--secondary" @click="load">重试</button>
      </div>
      <div v-if="loading && !runs.length" class="table-skeleton"><span v-for="index in 8" :key="index" /></div>

      <div v-else-if="runs.length" class="table-wrap">
        <table class="data-table logs-table">
          <colgroup><col class="logs-table__col-account" /><col class="logs-table__col-trigger" /><col class="logs-table__col-start" /><col class="logs-table__col-duration" /><col class="logs-table__col-content" /><col class="logs-table__col-status" /><col class="logs-table__col-action" /></colgroup>
          <thead><tr><th class="logs-table__head-account">任务 / 账号</th><th>触发方式</th><th>开始时间</th><th>耗时</th><th>发现内容</th><th>状态</th><th aria-label="操作" /></tr></thead>
          <tbody>
            <template v-for="run in runs" :key="run.id">
              <tr :class="{ 'is-expanded': expanded === String(run.id) }">
                <td class="logs-table__account" data-label="任务 / 账号"><div class="run-id-cell"><span><TerminalSquare :size="16" /></span><div><strong>@{{ run.username || '未知账号' }}</strong><small>#{{ run.id }}</small><small v-if="run.worker_id" class="worker-id">{{ run.worker_id }}</small></div></div></td>
                <td data-label="触发方式"><span class="trigger-label"><component :is="run.trigger === 'manual' ? RefreshCw : Clock3" :size="14" />{{ run.trigger === 'manual' ? '手动触发' : '定时调度' }}</span></td>
                <td data-label="开始时间"><span class="date-cell"><strong>{{ formatDateTime(run.started_at) }}</strong><small v-if="run.finished_at">结束于 {{ formatDateTime(run.finished_at) }}</small></span></td>
                <td data-label="耗时">{{ formatDuration(run.duration_ms) }}</td>
                <td class="logs-table__content" data-label="发现内容"><strong class="new-content-count">{{ run.tweets_inserted }}</strong><small class="fetched-count">获取 {{ run.tweets_fetched }}</small></td>
                <td data-label="状态"><StatusBadge :status="run.status" /></td>
                <td class="logs-table__action"><button v-if="run.error_message" class="icon-button" aria-label="查看错误" @click="expanded = expanded === String(run.id) ? null : String(run.id)"><ChevronDown :size="16" :class="{ 'rotate-180': expanded === String(run.id) }" /></button></td>
              </tr>
              <tr v-if="expanded === String(run.id)" class="log-detail-row"><td colspan="7"><div class="log-error-detail"><AlertCircle :size="17" /><div><strong>错误详情</strong><code>{{ run.error_message }}</code></div></div></td></tr>
            </template>
          </tbody>
        </table>
      </div>

      <EmptyState v-else-if="!loading" title="暂无轮询记录" description="调度器开始执行监听任务后，结果会记录在这里">
        <template #icon><FileClock :size="26" /></template>
      </EmptyState>
      <PaginationBar v-if="total > filters.page_size" :page="filters.page" :page-size="filters.page_size" :total="total" @change="changePage" />
    </section>
  </div>
</template>
