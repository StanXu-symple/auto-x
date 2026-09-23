<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { systemApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { formatBytes, formatDateTime } from '@/utils/format'
import type { SystemMetrics } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import MetricCard from '@/components/MetricCard.vue'

const metrics = ref<SystemMetrics | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const error = ref('')
const nodes = computed(() => Object.entries(metrics.value?.nodes || {})
  .sort(([a], [b]) => a.localeCompare(b))
  .map(([id, value]) => ({ id, ...value })))
const onlineNodes = computed(() => nodes.value.filter(n => n.status === 'healthy').length)
const selectedNode = ref('all')
const visibleNodes = computed(() => nodes.value.filter(n => selectedNode.value === 'all' || n.id === selectedNode.value))
const services = computed(() => {
  const s = metrics.value
  if (!s) return []
  return [
    { name: 'API 服务', value: s.api },
    { name: 'PostgreSQL 数据库', value: s.database },
    { name: 'Redis 缓存', value: s.redis },
    { name: '轮询 Worker', value: s.worker },
    ...(s.ai_worker ? [{ name: 'AI Worker', value: s.ai_worker }] : []),
    ...(s.qq_worker ? [{ name: 'QQ Worker', value: s.qq_worker }] : []),
    ...(s.xhs_worker ? [{ name: '小红书 Worker', value: s.xhs_worker }] : []),
  ]
})
const instances = computed(() => (metrics.value?.monitoring?.instances || [])
  .filter(i => selectedNode.value === 'all' || i.node === selectedNode.value))
function percent(value?: number | null) { return value == null ? '—' : `${value.toFixed(1)}%` }
function uptime(seconds?: number) {
  if (seconds == null) return '—'
  const hours = Math.floor(seconds / 3600)
  return hours >= 24 ? `${Math.floor(hours / 24)}天 ${hours % 24}小时` : `${hours}小时 ${Math.floor(seconds % 3600 / 60)}分`
}
async function refresh(show = false) {
  if (refreshing.value) return
  refreshing.value = true
  try {
    metrics.value = await systemApi.metrics()
    error.value = ''
    if (selectedNode.value !== 'all' && !metrics.value.nodes?.[selectedNode.value]) selectedNode.value = 'all'
  } catch (e) {
    error.value = getErrorMessage(e, '无法读取服务器指标')
    if (show) message.error(error.value)
  } finally { loading.value = false; refreshing.value = false }
}
let timer: number | undefined
onMounted(() => { void refresh(); timer = window.setInterval(() => void refresh(), 10_000) })
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div class="page-stack">
    <PageHeader eyebrow="SYSTEM / 01" title="运行监控" description="按服务器查看资源使用情况，追踪服务健康与 Worker 心跳">
      <template #actions><a-button :loading="refreshing" @click="refresh(true)"><ReloadOutlined /> 立即刷新</a-button></template>
    </PageHeader>
    <a-alert v-if="error" type="warning" :message="error" show-icon />
    <a-alert v-if="metrics?.monitoring?.error" type="warning" :message="metrics.monitoring.error" show-icon />
    <a-spin :spinning="loading">
      <template v-if="metrics">
        <div class="node-toolbar">
          <div><h2>服务器资源</h2><span class="muted">{{ nodes.length }} 台服务器 · {{ onlineNodes }} 台采集正常 · 每 10 秒刷新</span></div>
          <a-select v-model:value="selectedNode" aria-label="选择服务器" class="node-select">
            <a-select-option value="all">全部服务器</a-select-option>
            <a-select-option v-for="node in nodes" :key="node.id" :value="node.id">{{ node.id }}</a-select-option>
          </a-select>
        </div>
        <a-empty v-if="!nodes.length" description="暂无服务器资源数据，请检查监控 Agent 和节点拓扑配置" />
        <section v-for="node in visibleNodes" :key="node.id" class="server-section">
          <div class="server-heading">
            <div><strong>{{ node.id }}</strong> <StatusPill :value="node.status" /></div>
            <small class="muted">{{ node.sampled_at ? `采集于 ${formatDateTime(node.sampled_at)}` : '尚无有效采样' }}</small>
          </div>
          <a-alert v-if="node.error" type="warning" :message="node.error" show-icon class="node-warning" />
          <div class="metric-grid">
            <MetricCard label="CPU 使用率" :value="percent(node.cpu_percent)" :detail="node.load_average ? `负载 ${node.load_average.map(n => n.toFixed(2)).join(' / ')}` : '等待宿主机采样'" />
            <MetricCard label="内存" :value="percent(node.memory?.percent)" :detail="node.memory ? `${formatBytes(node.memory.used_bytes)} / ${formatBytes(node.memory.total_bytes)}` : '暂无数据'" accent="slate" />
            <MetricCard label="磁盘 · 根分区" :value="percent(node.disk?.percent)" :detail="node.disk ? `${formatBytes(node.disk.used_bytes)} / ${formatBytes(node.disk.total_bytes)}` : '暂无数据'" accent="ochre" />
            <MetricCard label="服务器运行时间" :value="uptime(node.uptime_seconds)" detail="自宿主机启动以来" accent="sage" />
          </div>
        </section>
        <a-card title="业务服务健康" :bordered="false" class="health-card">
          <a-table :data-source="services" :pagination="false" row-key="name" :scroll="{ x: 680 }">
            <a-table-column title="服务" data-index="name" />
            <a-table-column title="状态"><template #default="{ record }"><StatusPill :value="record.value.status" /></template></a-table-column>
            <a-table-column title="CPU / 内存"><template #default="{ record }">{{ percent(record.value.cpu_percent) }} / {{ record.value.memory_used_bytes != null ? formatBytes(record.value.memory_used_bytes) : '—' }}</template></a-table-column>
            <a-table-column title="说明"><template #default="{ record }"><span class="muted">{{ record.value.error || record.value.resource_note || (record.value.status === 'offline' ? '未收到 Worker 心跳' : record.value.latency_ms != null ? `${record.value.latency_ms} ms` : '运行正常') }}</span></template></a-table-column>
          </a-table>
        </a-card>
        <a-card title="服务实例 · 按服务器" :bordered="false" class="health-card">
          <a-table :data-source="instances" :pagination="{ pageSize: 12, hideOnSinglePage: true }" :row-key="(record: any) => `${record.node}:${record.instance_id}`" :scroll="{ x: 680 }">
            <a-table-column title="服务器" data-index="node" />
            <a-table-column title="服务" data-index="name" />
            <a-table-column title="容器状态"><template #default="{ record }"><StatusPill :value="record.status" /></template></a-table-column>
            <a-table-column title="CPU / 内存"><template #default="{ record }">{{ percent(record.cpu_percent) }} / {{ record.memory_used_bytes != null ? formatBytes(record.memory_used_bytes) : '—' }}</template></a-table-column>
            <a-table-column title="采集说明"><template #default="{ record }">{{ record.resource_note || '采集正常' }}</template></a-table-column>
          </a-table>
        </a-card>
      </template>
    </a-spin>
  </div>
</template>

<style scoped>
.node-toolbar, .server-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
.node-toolbar { margin-bottom: 24px; }
.node-toolbar h2 { margin: 0 0 4px; font-size: 20px; }
.node-select { min-width: 180px; }
.server-section { margin-bottom: 28px; }
.server-heading { padding-bottom: 12px; border-bottom: 1px solid var(--color-border, #e4e4de); }
.server-heading strong { font-size: 18px; margin-right: 12px; }
.node-warning { margin-top: 12px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.health-card { margin-top: 24px; }
@media (max-width: 1000px) { .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .metric-grid { grid-template-columns: 1fr; } .node-select { width: 100%; } }
</style>
