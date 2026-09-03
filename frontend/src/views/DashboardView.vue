<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  AlertCircle,
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  Cpu,
  Database,
  MessageSquareText,
  RefreshCw,
  Server,
  UsersRound,
  Zap,
} from 'lucide-vue-next'
import { dashboardApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type { DashboardSummary, PollingTrendPoint, ServiceHealth } from '@/types'
import { formatDateTime, formatDuration, formatNumber, formatRelative, tweetTime } from '@/utils/format'
import EmptyState from '@/components/EmptyState.vue'
import PollingTrendChart from '@/components/PollingTrendChart.vue'
import StatusBadge from '@/components/StatusBadge.vue'

const summary = ref<DashboardSummary | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const error = ref('')

const counts = computed(() => summary.value?.counts)
const trend = computed<PollingTrendPoint[]>(() => {
  if (!summary.value) return []
  const buckets = new Map<string, PollingTrendPoint>()
  for (const run of [...summary.value.recent_runs].reverse()) {
    const date = new Date(run.started_at)
    const label = Number.isNaN(date.getTime()) ? '最近' : date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    const point = buckets.get(label) || { label, success: 0, failed: 0 }
    if (run.status === 'success') point.success += 1
    else if (['error', 'failed', 'rate_limited'].includes(run.status)) point.failed += 1
    buckets.set(label, point)
  }
  if (buckets.size) return [...buckets.values()]
  return [{ label: '过去 24h', success: summary.value.polling.successful_runs_last_24h, failed: summary.value.polling.failed_runs_last_24h }]
})
const services = computed<ServiceHealth[]>(() => {
  const server = summary.value?.server
  if (server) {
    const values = [
      { name: 'MySQL 数据库', status: server.database.status, latency_ms: server.database.latency_ms, message: server.database.error },
      { name: 'Redis 缓存', status: server.redis.status, latency_ms: server.redis.latency_ms, message: server.redis.error },
      { name: '轮询 Worker', status: server.worker.status, message: server.worker.error },
    ]
    if (server.qq_worker) values.push({ name: 'QQ Worker', status: server.qq_worker.status, message: server.qq_worker.error })
    return values
  }
  return [
    { name: 'API 服务', status: summary.value ? 'healthy' : 'unknown' },
    { name: '任务调度器', status: summary.value ? 'healthy' : 'unknown' },
  ]
})

const cards = computed(() => [
  {
    label: '监听账号',
    value: counts.value?.monitored_users || 0,
    detail: `${counts.value?.active_users || 0} 个正在运行`,
    icon: UsersRound,
    tone: 'purple',
  },
  {
    label: '今日新推文',
    value: counts.value?.tweets_last_24h || 0,
    detail: `累计 ${formatNumber(counts.value?.tweets)}`,
    icon: MessageSquareText,
    tone: 'blue',
  },
  {
    label: '今日轮询',
    value: summary.value?.polling.runs_last_24h || 0,
    detail: `${summary.value?.polling.failed_runs_last_24h || 0} 次失败`,
    icon: RefreshCw,
    tone: 'cyan',
  },
  {
    label: '成功率',
    value: `${Number(summary.value?.polling.success_rate || 0).toFixed(1)}%`,
    detail: '过去 24 小时',
    icon: CheckCircle2,
    tone: 'green',
  },
])

async function load(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true
  error.value = ''
  try {
    summary.value = await dashboardApi.summary()
  } catch (requestError) {
    error.value = getErrorMessage(requestError, '无法加载仪表盘')
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

let timer: number | undefined
onMounted(() => {
  load()
  timer = window.setInterval(() => load(true), 60_000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div class="dashboard page-stack">
    <div class="page-actions-row">
      <div class="live-copy"><span class="live-dot" />数据每 60 秒自动刷新</div>
      <button class="button button--secondary" :disabled="refreshing" @click="load(true)">
        <RefreshCw :size="16" :class="{ 'spin': refreshing }" />{{ refreshing ? '刷新中' : '刷新数据' }}
      </button>
    </div>

    <div v-if="error && !summary" class="error-panel">
      <AlertCircle :size="22" />
      <div><strong>仪表盘加载失败</strong><span>{{ error }}</span></div>
      <button class="button button--secondary" @click="load()">重新加载</button>
    </div>

    <template v-if="loading">
      <div class="metric-grid"><div v-for="index in 4" :key="index" class="metric-card skeleton-card"><span /><span /><span /></div></div>
      <div class="dashboard-grid"><div class="panel skeleton-panel" /><div class="panel skeleton-panel" /></div>
    </template>

    <template v-else-if="summary">
      <section class="metric-grid">
        <article v-for="card in cards" :key="card.label" class="metric-card">
          <div class="metric-card__head">
            <span>{{ card.label }}</span>
            <span class="metric-card__icon" :class="`metric-card__icon--${card.tone}`"><component :is="card.icon" :size="19" /></span>
          </div>
          <strong>{{ card.value }}</strong>
          <small>{{ card.detail }}</small>
        </article>
      </section>

      <section class="dashboard-grid dashboard-grid--top">
        <article class="panel panel--wide">
          <header class="panel__header">
            <div><h2>轮询趋势</h2><p>最近 24 小时任务执行情况</p></div>
            <div class="chart-legend"><span><i class="is-success" />成功</span><span><i class="is-failed" />失败</span></div>
          </header>
          <PollingTrendChart :points="trend" />
        </article>

        <article class="panel">
          <header class="panel__header"><div><h2>系统状态</h2><p>核心服务运行情况</p></div><Server :size="19" /></header>
          <div class="service-list">
            <div v-for="service in services" :key="service.name" class="service-row">
              <span class="service-row__icon"><component :is="service.name.toLowerCase().includes('redis') ? Zap : service.name.toLowerCase().includes('data') || service.name.includes('数据库') ? Database : Cpu" :size="17" /></span>
              <div><strong>{{ service.name }}</strong><small>{{ service.latency_ms != null ? `${service.latency_ms} ms` : service.message || '运行正常' }}</small></div>
              <StatusBadge :status="service.status" />
            </div>
          </div>
          <RouterLink class="panel-link" to="/settings">查看服务器详情 <ArrowUpRight :size="15" /></RouterLink>
        </article>
      </section>

      <section class="dashboard-grid">
        <article class="panel panel--wide">
          <header class="panel__header"><div><h2>最新采集内容</h2><p>最近监听到的推文</p></div><RouterLink to="/tweets">查看全部 <ArrowUpRight :size="15" /></RouterLink></header>
          <div v-if="summary.recent_tweets?.length" class="recent-feed">
            <div v-for="tweet in summary.recent_tweets.slice(0, 5)" :key="tweet.id" class="recent-tweet">
              <span class="avatar avatar--soft">{{ (tweet.username || 'X').slice(0, 1).toUpperCase() }}</span>
              <div class="recent-tweet__content">
                <div><strong>@{{ tweet.username || '未知用户' }}</strong><span>{{ tweet.lang || '—' }}</span><small>{{ formatRelative(tweetTime(tweet)) }}</small></div>
                <p>{{ tweet.text }}</p>
              </div>
            </div>
          </div>
          <EmptyState v-else compact title="还没有采集到推文" description="添加监听账号后，新内容会出现在这里" />
        </article>

        <article class="panel">
          <header class="panel__header"><div><h2>最近任务</h2><p>调度器执行记录</p></div><Clock3 :size="19" /></header>
          <div v-if="summary.recent_runs?.length" class="run-list">
            <div v-for="run in summary.recent_runs.slice(0, 6)" :key="run.id" class="run-row">
              <span class="run-row__line" :class="`is-${run.status}`" />
              <div><strong>@{{ run.username || '未知账号' }}</strong><small>{{ formatDateTime(run.started_at) }} · {{ formatDuration(run.duration_ms) }}</small></div>
              <StatusBadge :status="run.status" />
            </div>
          </div>
          <EmptyState v-else compact title="暂无任务记录" description="调度器运行后会显示任务状态" />
          <RouterLink class="panel-link" to="/polling-logs">查看完整记录 <ArrowUpRight :size="15" /></RouterLink>
        </article>
      </section>
    </template>
  </div>
</template>
