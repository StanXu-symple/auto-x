<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Activity,
  AlertCircle,
  Clock3,
  Cpu,
  Database,
  Bot,
  FileCode2,
  HardDrive,
  MemoryStick,
  RefreshCw,
  RotateCcw,
  Save,
  Server,
  Settings2,
  ShieldCheck,
  Sparkles,
  Workflow,
  Zap,
} from 'lucide-vue-next'
import { systemApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { useUiStore } from '@/stores/ui'
import type { ResourceMetric, RuntimeResourceMetric, ServiceHealth, SystemMetrics, SystemSettings } from '@/types'
import { formatBytes, formatDateTime, formatUptime } from '@/utils/format'
import ResourceGauge from '@/components/ResourceGauge.vue'
import StatusBadge from '@/components/StatusBadge.vue'

const ui = useUiStore()
const metrics = ref<SystemMetrics | null>(null)
const settings = ref<SystemSettings | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const saving = ref(false)
const metricsError = ref('')
const settingsError = ref('')
const formError = ref('')
const form = reactive<SystemSettings>({ global_poll_interval_seconds: 300, max_concurrency: 5 })

const dirty = computed(() =>
  Boolean(
    settings.value &&
      (form.global_poll_interval_seconds !== settings.value.global_poll_interval_seconds ||
        form.max_concurrency !== settings.value.max_concurrency),
  ),
)

const services = computed<ServiceHealth[]>(() => {
  if (!metrics.value) return []
  const values: ServiceHealth[] = [
    { name: 'MySQL 数据库', status: metrics.value.database.status, latency_ms: metrics.value.database.latency_ms, message: metrics.value.database.error },
    { name: 'Redis 缓存', status: metrics.value.redis.status, latency_ms: metrics.value.redis.latency_ms, message: metrics.value.redis.error },
    { name: '轮询 Worker', status: metrics.value.worker.status, message: metrics.value.worker.error },
  ]
  if (metrics.value.ai_worker) values.push({ name: 'AI Worker', status: metrics.value.ai_worker.status, message: metrics.value.ai_worker.error })
  if (metrics.value.xhs_worker) values.push({ name: '小红书 Worker', status: metrics.value.xhs_worker.status, message: metrics.value.xhs_worker.error })
  if (metrics.value.qq_worker) values.push({ name: 'QQ Worker', status: metrics.value.qq_worker.status, message: metrics.value.qq_worker.error })
  return values
})

const serviceResources = computed(() => {
  const current = metrics.value
  if (!current) return []
  return [
    { name: 'MySQL 数据库', description: 'InnoDB 缓冲池', icon: Database, metric: current.database },
    { name: 'Redis 缓存', description: 'Redis 已分配内存', icon: Zap, metric: current.redis },
    { name: '轮询 Worker', description: '进程 RSS 内存', icon: Workflow, metric: current.worker },
    ...(current.ai_worker ? [{ name: 'AI Worker', description: '进程 RSS 内存', icon: Sparkles, metric: current.ai_worker }] : []),
    ...(current.xhs_worker ? [{ name: '小红书 Worker', description: '进程 RSS 内存', icon: Activity, metric: current.xhs_worker }] : []),
    ...(current.qq_worker ? [{ name: 'QQ Worker', description: '进程 RSS 内存', icon: Bot, metric: current.qq_worker }] : []),
  ]
})

function cpuText(metric: RuntimeResourceMetric) {
  return typeof metric.cpu_percent === 'number' ? `${metric.cpu_percent.toFixed(1)}%` : '等待采样'
}

function memoryUsed(metric: RuntimeResourceMetric) {
  return metric.memory_used_bytes ?? metric.rss_bytes
}

function memoryText(metric: RuntimeResourceMetric) {
  const used = memoryUsed(metric)
  if (used == null) return '暂无数据'
  return metric.memory_total_bytes
    ? `${formatBytes(used)} / ${formatBytes(metric.memory_total_bytes)}`
    : formatBytes(used)
}

function resourcePercent(value?: number | null) {
  return `${Math.min(100, Math.max(0, Number(value || 0)))}%`
}

const resourceCards = computed<Array<{ label: string; metric: ResourceMetric; color: 'purple' | 'cyan' | 'green'; detail: string }>>(() => {
  const current = metrics.value
  return [
    {
      label: 'CPU',
      metric: { percent: current?.cpu_percent || 0 },
      color: 'purple',
      detail: current?.load_average?.length ? `负载 ${current.load_average.map((value) => value.toFixed(2)).join(' / ')}` : '系统使用率',
    },
    {
      label: '内存',
      metric: { percent: current?.memory.percent || 0, used: current?.memory.used_bytes, total: current?.memory.total_bytes },
      color: 'cyan',
      detail: current ? `${formatBytes(current.memory.used_bytes)} / ${formatBytes(current.memory.total_bytes)}` : '等待数据',
    },
    {
      label: '磁盘',
      metric: { percent: current?.disk.percent || 0, used: current?.disk.used_bytes, total: current?.disk.total_bytes },
      color: 'green',
      detail: current ? `${formatBytes(current.disk.used_bytes)} / ${formatBytes(current.disk.total_bytes)}` : '等待数据',
    },
  ]
})

function applySettings(value: SystemSettings) {
  form.global_poll_interval_seconds = value.global_poll_interval_seconds
  form.max_concurrency = value.max_concurrency
}

async function load() {
  loading.value = true
  metricsError.value = ''
  settingsError.value = ''
  const [metricsResult, settingsResult] = await Promise.allSettled([systemApi.metrics(), systemApi.settings()])
  if (metricsResult.status === 'fulfilled') metrics.value = metricsResult.value
  else metricsError.value = getErrorMessage(metricsResult.reason, '无法获取服务器指标')
  if (settingsResult.status === 'fulfilled') {
    settings.value = settingsResult.value
    applySettings(settingsResult.value)
  } else settingsError.value = getErrorMessage(settingsResult.reason, '无法加载系统配置')
  loading.value = false
}

async function refreshMetrics() {
  refreshing.value = true
  metricsError.value = ''
  try {
    metrics.value = await systemApi.metrics()
  } catch (requestError) {
    metricsError.value = getErrorMessage(requestError, '无法刷新服务器指标')
    ui.toast('指标刷新失败', 'error', metricsError.value)
  } finally {
    refreshing.value = false
  }
}

function validate() {
  if (!Number.isInteger(form.global_poll_interval_seconds) || form.global_poll_interval_seconds < 15 || form.global_poll_interval_seconds > 86_400) {
    return '默认轮询间隔必须是 15–86400 秒之间的整数'
  }
  if (!Number.isInteger(form.max_concurrency) || form.max_concurrency < 1 || form.max_concurrency > 100) {
    return '最大并发任务数必须是 1–100 之间的整数'
  }
  return ''
}

async function save() {
  formError.value = validate()
  if (formError.value) return
  saving.value = true
  try {
    const updated = await systemApi.updateSettings({
      global_poll_interval_seconds: form.global_poll_interval_seconds,
      max_concurrency: form.max_concurrency,
    })
    settings.value = updated
    applySettings(updated)
    ui.toast('系统设置已保存', 'success', '新的调度配置将在下一轮任务中生效')
  } catch (requestError) {
    formError.value = getErrorMessage(requestError, '保存设置失败')
  } finally {
    saving.value = false
  }
}

function reset() {
  if (settings.value) applySettings(settings.value)
  formError.value = ''
}

let timer: number | undefined
onMounted(() => {
  load()
  timer = window.setInterval(refreshMetrics, 60_000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div class="settings-view page-stack">
    <section class="server-hero">
      <div class="server-hero__head">
        <div><span class="server-hero__icon"><Server :size="22" /></span><div><h2>应用服务器</h2><p>API 进程与依赖服务实时指标</p></div></div>
        <div class="server-hero__actions">
          <span class="live-copy"><span class="live-dot" />1 分钟自动刷新</span>
          <el-button :loading="refreshing" @click="refreshMetrics"><RefreshCw v-if="!refreshing" :size="16" />刷新指标</el-button>
        </div>
      </div>

      <div v-if="metricsError && !metrics" class="error-panel error-panel--embedded"><AlertCircle :size="21" /><div><strong>服务器指标不可用</strong><span>{{ metricsError }}</span></div><button class="button button--secondary" @click="refreshMetrics">重试</button></div>
      <div v-if="loading && !metrics" class="resource-grid"><div v-for="index in 4" :key="index" class="resource-card skeleton-card"><span /><span /><span /></div></div>
      <div v-else class="resource-grid">
        <article v-for="card in resourceCards" :key="card.label" class="resource-card">
          <ResourceGauge :label="card.label" :value="card.metric.percent" :detail="card.detail" :color="card.color" />
        </article>
        <article class="resource-card uptime-card">
          <span class="resource-card__icon"><Activity :size="21" /></span>
          <div><span>API 进程运行时间</span><strong>{{ formatUptime(metrics?.uptime_seconds) }}</strong><small>采集于 {{ formatDateTime(metrics?.generated_at) }}</small></div>
        </article>
      </div>
    </section>

    <section class="service-resources">
      <header class="service-resources__header">
        <div><h2>服务资源</h2><p>数据库、缓存与后台 Worker 的 CPU 和内存占用</p></div>
        <MemoryStick :size="19" />
      </header>
      <div v-if="serviceResources.length" class="service-resource-grid">
        <article v-for="resource in serviceResources" :key="resource.name" class="service-resource-card">
          <header>
            <span><component :is="resource.icon" :size="18" /></span>
            <div><strong>{{ resource.name }}</strong><small>{{ resource.description }}</small></div>
            <StatusBadge :status="resource.metric.status" />
          </header>
          <div class="service-resource-card__metrics">
            <div>
              <span><Cpu :size="14" />CPU</span>
              <strong>{{ cpuText(resource.metric) }}</strong>
              <i><b :style="{ width: resourcePercent(resource.metric.cpu_percent) }" /></i>
            </div>
            <div>
              <span><MemoryStick :size="14" />内存</span>
              <strong>{{ memoryText(resource.metric) }}</strong>
              <i><b :style="{ width: resourcePercent(resource.metric.memory_percent) }" /></i>
            </div>
          </div>
          <p v-if="resource.metric.resource_error" :title="String(resource.metric.resource_error)">资源指标不可用，连接健康检查仍正常</p>
        </article>
      </div>
      <div v-else class="inline-empty">等待服务资源数据</div>
    </section>

    <section class="settings-grid">
      <div class="settings-grid__main page-stack">
        <article class="panel">
          <header class="panel__header"><div><h2>核心服务</h2><p>数据库、缓存与调度组件连接状态</p></div><ShieldCheck :size="19" /></header>
          <div v-if="services.length" class="service-health-grid">
            <div v-for="service in services" :key="service.name" class="service-health-card">
              <span class="service-health-card__icon"><component :is="service.name.includes('Redis') ? Zap : service.name.includes('数据库') ? Database : Workflow" :size="19" /></span>
              <div><strong>{{ service.name }}</strong><span>{{ service.message || (service.latency_ms != null ? `响应 ${service.latency_ms} ms` : '服务状态已上报') }}</span></div>
              <StatusBadge :status="service.status" />
            </div>
          </div>
          <div v-else class="inline-empty">暂无服务健康数据</div>
        </article>

        <article class="panel">
          <header class="panel__header"><div><h2>运行进程</h2><p>API 进程资源与 Worker 心跳</p></div><Cpu :size="19" /></header>
          <div class="process-grid">
            <div class="process-stat"><span class="service-health-card__icon"><FileCode2 :size="18" /></span><div><small>进程 PID</small><strong>{{ metrics?.process.pid ?? '—' }}</strong></div></div>
            <div class="process-stat"><span class="service-health-card__icon"><Cpu :size="18" /></span><div><small>进程 CPU</small><strong>{{ Number(metrics?.process.cpu_percent || 0).toFixed(1) }}%</strong></div></div>
            <div class="process-stat"><span class="service-health-card__icon"><MemoryStick :size="18" /></span><div><small>进程内存</small><strong>{{ formatBytes(metrics?.process.rss_bytes) }}</strong></div></div>
            <div class="process-stat"><span class="service-health-card__icon"><Workflow :size="18" /></span><div><small>线程 / 文件</small><strong>{{ metrics?.process.threads ?? 0 }} / {{ metrics?.process.open_files ?? 0 }}</strong></div></div>
          </div>
          <div class="worker-heartbeat">
            <span><Workflow :size="17" /></span>
            <div><strong>轮询 Worker</strong><small>最后心跳 {{ formatDateTime(metrics?.worker.last_heartbeat || metrics?.worker.timestamp) }}<template v-if="metrics?.worker.ttl_seconds != null"> · TTL {{ metrics.worker.ttl_seconds }} 秒</template></small></div>
            <StatusBadge :status="metrics?.worker.status || 'unknown'" />
          </div>
          <div v-if="metrics?.ai_worker" class="worker-heartbeat">
            <span><Sparkles :size="17" /></span>
            <div><strong>AI Worker</strong><small>最后心跳 {{ formatDateTime(metrics.ai_worker.last_heartbeat || metrics.ai_worker.timestamp) }}<template v-if="metrics.ai_worker.ttl_seconds != null"> · TTL {{ metrics.ai_worker.ttl_seconds }} 秒</template><template v-if="metrics.ai_worker.active_tasks != null"> · 活跃任务 {{ metrics.ai_worker.active_tasks }}</template></small></div>
            <StatusBadge :status="metrics.ai_worker.status || 'unknown'" />
          </div>
          <div v-if="metrics?.xhs_worker" class="worker-heartbeat">
            <span><Activity :size="17" /></span>
            <div><strong>小红书 Worker</strong><small>最后心跳 {{ formatDateTime(metrics.xhs_worker.last_heartbeat || metrics.xhs_worker.timestamp) }}<template v-if="metrics.xhs_worker.ttl_seconds != null"> · TTL {{ metrics.xhs_worker.ttl_seconds }} 秒</template><template v-if="metrics.xhs_worker.active_tasks != null"> · 活跃任务 {{ metrics.xhs_worker.active_tasks }}</template></small></div>
            <StatusBadge :status="metrics.xhs_worker.status || 'unknown'" />
          </div>
          <div v-if="metrics?.qq_worker" class="worker-heartbeat">
            <span><Bot :size="17" /></span>
            <div><strong>QQ Worker</strong><small>最后心跳 {{ formatDateTime(metrics.qq_worker.last_heartbeat || metrics.qq_worker.timestamp) }}<template v-if="metrics.qq_worker.ttl_seconds != null"> · TTL {{ metrics.qq_worker.ttl_seconds }} 秒</template><template v-if="metrics.qq_worker.active_tasks != null"> · 活跃任务 {{ metrics.qq_worker.active_tasks }}</template></small></div>
            <StatusBadge :status="metrics.qq_worker.status || 'unknown'" />
          </div>
        </article>
      </div>

      <article class="panel settings-form-panel">
        <header class="panel__header"><div><h2>全局轮询设置</h2><p>使用系统默认策略的账号会实时继承这些值</p></div><Settings2 :size="19" /></header>
        <div v-if="settingsError && !settings" class="form-alert form-alert--error settings-load-error">{{ settingsError }}</div>
        <el-form class="form-stack sentinel-form" label-position="top" @submit.prevent="save">
          <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon />

          <el-form-item label="默认轮询间隔">
            <div class="element-number-row"><Clock3 :size="17" /><el-input-number v-model="form.global_poll_interval_seconds" :min="15" :max="86400" :step="15" controls-position="right" /><span>秒</span></div>
            <small class="field__hint">允许范围 15 秒至 24 小时；账号可单独覆盖此值</small>
            <div class="interval-presets">
              <el-button v-for="option in [60, 300, 600, 1800, 3600]" :key="option" size="small" :type="form.global_poll_interval_seconds === option ? 'primary' : ''" plain @click="form.global_poll_interval_seconds = option">{{ option < 3600 ? `${option / 60}分钟` : '1小时' }}</el-button>
            </div>
          </el-form-item>

          <el-form-item label="最大并发任务">
            <div class="element-number-row"><Workflow :size="17" /><el-input-number v-model="form.max_concurrency" :min="1" :max="100" controls-position="right" /><span>个</span></div>
            <small class="field__hint">同时运行的轮询任务上限。提高此值会增加 CPU、网络和 X API 压力</small>
          </el-form-item>

          <div class="settings-impact-note"><HardDrive :size="17" /><p><strong>变更影响</strong><span>缩短默认间隔时，未设置独立频率的活跃账号会提前进入下一次调度。</span></p></div>

          <div class="settings-form-panel__footer">
            <span v-if="dirty" class="unsaved-indicator"><span />有未保存的更改</span><span v-else />
            <el-button v-if="dirty" :disabled="saving" @click="reset"><RotateCcw :size="16" />撤销</el-button>
            <el-button type="primary" :loading="saving" :disabled="!dirty || !settings" @click="save"><Save v-if="!saving" :size="16" />保存设置</el-button>
          </div>
        </el-form>
      </article>
    </section>
  </div>
</template>
