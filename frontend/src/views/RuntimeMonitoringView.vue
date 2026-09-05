<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Activity, AlertCircle, Bot, BookOpen, Cpu, Database, FileCode2, MemoryStick, RefreshCw, Server, ShieldCheck, Sparkles, Workflow, Zap } from 'lucide-vue-next'
import ResourceGauge from '@/components/ResourceGauge.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { systemApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { useUiStore } from '@/stores/ui'
import type { ResourceMetric, RuntimeResourceMetric, ServiceHealth, SystemMetrics } from '@/types'
import { formatBytes, formatDateTime, formatUptime } from '@/utils/format'

const ui = useUiStore()
const metrics = ref<SystemMetrics | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const metricsError = ref('')

const services = computed<ServiceHealth[]>(() => {
  if (!metrics.value) return []
  const values: ServiceHealth[] = [
    { name: 'API 服务', status: metrics.value.api.status, message: metrics.value.api.error },
    { name: 'MySQL 数据库', status: metrics.value.database.status, latency_ms: metrics.value.database.latency_ms, message: metrics.value.database.error },
    { name: 'Redis 缓存', status: metrics.value.redis.status, latency_ms: metrics.value.redis.latency_ms, message: metrics.value.redis.error },
    { name: '轮询 Worker', status: metrics.value.worker.status, message: metrics.value.worker.error },
  ]
  if (metrics.value.ai_worker) values.push({ name: 'AI Worker', status: metrics.value.ai_worker.status, message: metrics.value.ai_worker.error })
  if (metrics.value.qq_worker) values.push({ name: 'QQ Worker', status: metrics.value.qq_worker.status, message: metrics.value.qq_worker.error })
  if (metrics.value.xhs_worker) values.push({ name: '小红书 Worker', status: metrics.value.xhs_worker.status, message: metrics.value.xhs_worker.error })
  return values
})

const serviceResources = computed(() => {
  const current = metrics.value
  if (!current) return []
  return [
    { name: 'API 服务', description: '主进程 RSS 内存', icon: FileCode2, metric: current.api },
    { name: 'MySQL 数据库', description: 'InnoDB 缓冲池', icon: Database, metric: current.database },
    { name: 'Redis 缓存', description: 'Redis 已分配内存', icon: Zap, metric: current.redis },
    { name: '轮询 Worker', description: '进程 RSS 内存', icon: Workflow, metric: current.worker },
    ...(current.ai_worker ? [{ name: 'AI Worker', description: '进程 RSS 内存', icon: Sparkles, metric: current.ai_worker }] : []),
    ...(current.qq_worker ? [{ name: 'QQ Worker', description: '进程 RSS 内存', icon: Bot, metric: current.qq_worker }] : []),
    ...(current.xhs_worker ? [{ name: '小红书 Worker', description: `浏览器任务进程 · 队列 ${Number(current.xhs_worker.queue_depth || 0)}`, icon: BookOpen, metric: current.xhs_worker }] : []),
  ]
})

const resourceCards = computed<Array<{ label: string; metric: ResourceMetric; color: 'purple' | 'cyan' | 'green'; detail: string }>>(() => {
  const current = metrics.value
  return [
    { label: 'CPU', metric: { percent: current?.cpu_percent || 0 }, color: 'purple', detail: current?.load_average?.length ? `负载 ${current.load_average.map((value) => value.toFixed(2)).join(' / ')}` : '系统使用率' },
    { label: '内存', metric: { percent: current?.memory.percent || 0, used: current?.memory.used_bytes, total: current?.memory.total_bytes }, color: 'cyan', detail: current ? `${formatBytes(current.memory.used_bytes)} / ${formatBytes(current.memory.total_bytes)}` : '等待数据' },
    { label: '磁盘', metric: { percent: current?.disk.percent || 0, used: current?.disk.used_bytes, total: current?.disk.total_bytes }, color: 'green', detail: current ? `${formatBytes(current.disk.used_bytes)} / ${formatBytes(current.disk.total_bytes)}` : '等待数据' },
  ]
})

const healthyServiceCount = computed(() => services.value.filter((service) => ['healthy', 'ok', 'online', 'active'].includes(service.status)).length)

function cpuText(metric: RuntimeResourceMetric) {
  return typeof metric.cpu_percent === 'number' ? `${metric.cpu_percent.toFixed(1)}%` : '等待采样'
}

function memoryText(metric: RuntimeResourceMetric) {
  const used = metric.memory_used_bytes ?? metric.rss_bytes
  if (used == null) return '暂无数据'
  return metric.memory_total_bytes ? `${formatBytes(used)} / ${formatBytes(metric.memory_total_bytes)}` : formatBytes(used)
}

function resourcePercent(value?: number | null) {
  return `${Math.min(100, Math.max(0, Number(value || 0)))}%`
}

async function refreshMetrics(showToast = true) {
  refreshing.value = true
  metricsError.value = ''
  try {
    metrics.value = await systemApi.metrics()
  } catch (requestError) {
    metricsError.value = getErrorMessage(requestError, '无法刷新服务器指标')
    if (showToast) ui.toast('指标刷新失败', 'error', metricsError.value)
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

let timer: number | undefined
onMounted(() => {
  refreshMetrics(false)
  timer = window.setInterval(() => refreshMetrics(false), 60_000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div class="runtime-monitoring-view page-stack">
    <section class="monitoring-command-bar" aria-labelledby="monitoring-heading">
      <div class="monitoring-command-bar__intro">
        <span class="monitoring-command-bar__icon"><Server :size="21" /></span>
        <div>
          <span class="monitoring-eyebrow">SYSTEM HEALTH</span>
          <h2 id="monitoring-heading">应用服务器指标</h2>
          <p>集中查看 API、依赖服务与后台 Worker 的运行状态。</p>
        </div>
      </div>
      <div class="monitoring-command-bar__actions">
        <div class="monitoring-summary" aria-live="polite"><strong>{{ healthyServiceCount }}/{{ services.length || 0 }}</strong><span>服务正常</span></div>
        <span class="live-copy"><span class="live-dot" />每分钟更新</span>
        <el-button class="monitoring-refresh" :loading="refreshing" @click="refreshMetrics()"><RefreshCw v-if="!refreshing" :size="16" />刷新指标</el-button>
      </div>
    </section>

    <div v-if="metricsError && !metrics" class="error-panel monitoring-error" role="alert"><AlertCircle :size="21" /><div><strong>服务器指标不可用</strong><span>{{ metricsError }}</span></div><el-button @click="refreshMetrics()">重试</el-button></div>

    <section class="server-hero monitoring-resource-panel" aria-label="服务器资源概览">
      <header class="monitoring-section-header">
        <div><span class="monitoring-section-kicker">RESOURCE OVERVIEW</span><h2>资源概览</h2><p>系统级容量与 API 进程运行时间</p></div>
        <span class="monitoring-status-key"><i class="is-healthy" />健康阈值 <b>&lt; 70%</b></span>
      </header>
      <div v-if="loading && !metrics" class="resource-grid"><div v-for="index in 4" :key="index" class="resource-card skeleton-card"><span /><span /><span /></div></div>
      <div v-else class="resource-grid">
        <article v-for="card in resourceCards" :key="card.label" class="resource-card"><ResourceGauge :label="card.label" :value="card.metric.percent" :detail="card.detail" :color="card.color" /></article>
        <article class="resource-card uptime-card"><span class="resource-card__icon"><Activity :size="21" /></span><div><span>API 进程运行时间</span><strong>{{ formatUptime(metrics?.uptime_seconds) }}</strong><small>采集于 {{ formatDateTime(metrics?.generated_at) }}</small></div></article>
      </div>
    </section>

    <section class="service-resources" aria-labelledby="service-resources-heading">
      <header class="service-resources__header"><div><h2 id="service-resources-heading">服务资源</h2><p>数据库、缓存与后台 Worker 的 CPU 和内存占用</p></div><MemoryStick :size="19" /></header>
      <div v-if="serviceResources.length" class="service-resource-grid">
        <article v-for="resource in serviceResources" :key="resource.name" class="service-resource-card">
          <header><span><component :is="resource.icon" :size="18" /></span><div><strong>{{ resource.name }}</strong><small>{{ resource.description }}</small></div><StatusBadge :status="resource.metric.status" /></header>
          <div class="service-resource-card__metrics">
            <div class="resource-metric-row"><div class="resource-metric-row__label"><Cpu :size="14" />CPU</div><strong>{{ cpuText(resource.metric) }}</strong><i><b :style="{ width: resourcePercent(resource.metric.cpu_percent) }" /></i></div>
            <div class="resource-metric-row"><div class="resource-metric-row__label"><MemoryStick :size="14" />内存</div><strong>{{ memoryText(resource.metric) }}</strong><i><b :style="{ width: resourcePercent(resource.metric.memory_percent) }" /></i></div>
          </div>
          <p v-if="resource.metric.resource_error" :title="String(resource.metric.resource_error)">资源指标不可用，连接健康检查仍正常</p>
        </article>
      </div>
      <div v-else class="inline-empty">等待服务资源数据</div>
    </section>

    <section class="monitoring-detail-grid">
      <article class="panel">
        <header class="panel__header"><div><h2>核心服务</h2><p>数据库、缓存与调度组件连接状态</p></div><ShieldCheck :size="19" /></header>
        <div v-if="services.length" class="service-health-grid">
          <div v-for="service in services" :key="service.name" class="service-health-card"><span class="service-health-card__icon"><component :is="service.name.includes('Redis') ? Zap : service.name.includes('数据库') ? Database : service.name.includes('API') ? FileCode2 : service.name.includes('小红书') ? BookOpen : Workflow" :size="19" /></span><div><strong>{{ service.name }}</strong><span>{{ service.message || (service.latency_ms != null ? `响应 ${service.latency_ms} ms` : '服务状态已上报') }}</span></div><StatusBadge :status="service.status" /></div>
        </div>
        <div v-else class="inline-empty">暂无服务健康数据</div>
      </article>

      <article class="panel">
        <header class="panel__header"><div><h2>运行进程</h2><p>API 资源与 Worker 心跳状态</p></div><Cpu :size="19" /></header>
        <div class="process-grid">
          <div class="process-stat"><span class="service-health-card__icon"><FileCode2 :size="18" /></span><div><small>进程 PID</small><strong>{{ metrics?.process.pid ?? '-' }}</strong></div></div>
          <div class="process-stat"><span class="service-health-card__icon"><Cpu :size="18" /></span><div><small>进程 CPU</small><strong>{{ Number(metrics?.process.cpu_percent || 0).toFixed(1) }}%</strong></div></div>
          <div class="process-stat"><span class="service-health-card__icon"><MemoryStick :size="18" /></span><div><small>进程内存</small><strong>{{ formatBytes(metrics?.process.rss_bytes) }}</strong></div></div>
          <div class="process-stat"><span class="service-health-card__icon"><Workflow :size="18" /></span><div><small>线程 / 文件</small><strong>{{ metrics?.process.threads ?? 0 }} / {{ metrics?.process.open_files ?? 0 }}</strong></div></div>
        </div>
        <div class="worker-heartbeat-list">
          <div class="worker-heartbeat"><span><Workflow :size="17" /></span><div><strong>轮询 Worker</strong><small>最后心跳 {{ formatDateTime(metrics?.worker.last_heartbeat || metrics?.worker.timestamp) }}<template v-if="metrics?.worker.ttl_seconds != null">，TTL {{ metrics.worker.ttl_seconds }} 秒</template></small></div><StatusBadge :status="metrics?.worker.status || 'unknown'" /></div>
          <div v-if="metrics?.ai_worker" class="worker-heartbeat"><span><Sparkles :size="17" /></span><div><strong>AI Worker</strong><small>最后心跳 {{ formatDateTime(metrics.ai_worker.last_heartbeat || metrics.ai_worker.timestamp) }}<template v-if="metrics.ai_worker.ttl_seconds != null">，TTL {{ metrics.ai_worker.ttl_seconds }} 秒</template><template v-if="metrics.ai_worker.active_tasks != null">，活跃任务 {{ metrics.ai_worker.active_tasks }}</template></small></div><StatusBadge :status="metrics.ai_worker.status || 'unknown'" /></div>
          <div v-if="metrics?.qq_worker" class="worker-heartbeat"><span><Bot :size="17" /></span><div><strong>QQ Worker</strong><small>最后心跳 {{ formatDateTime(metrics.qq_worker.last_heartbeat || metrics.qq_worker.timestamp) }}<template v-if="metrics.qq_worker.ttl_seconds != null">，TTL {{ metrics.qq_worker.ttl_seconds }} 秒</template><template v-if="metrics.qq_worker.active_tasks != null">，活跃任务 {{ metrics.qq_worker.active_tasks }}</template></small></div><StatusBadge :status="metrics.qq_worker.status || 'unknown'" /></div>
        </div>
      </article>
    </section>
  </div>
</template>
