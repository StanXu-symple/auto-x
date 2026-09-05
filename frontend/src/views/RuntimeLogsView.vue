<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Eraser, Logs, Pause, Play, Search, TerminalSquare } from 'lucide-vue-next'
import { runtimeLogsApi, type RuntimeLogEvent, type RuntimeLogSystem } from '@/services/api'
import { getErrorMessage } from '@/services/http'

const fallbackSystems: RuntimeLogSystem[] = [
  { value: 'backend', label: 'Backend' },
  { value: 'worker', label: '轮询 Worker' },
  { value: 'ai-worker', label: 'AI Worker' },
  { value: 'qq-worker', label: 'QQ Worker' },
  { value: 'xhs-worker', label: '小红书 Worker' },
]
const systems = ref<RuntimeLogSystem[]>(fallbackSystems)
const selectedSystem = ref('xhs-worker')
const activeSystem = ref('')
const lines = ref<string[]>([])
const state = ref<'idle' | 'connecting' | 'live' | 'reconnecting' | 'paused' | 'error'>('idle')
const autoScroll = ref(true)
const terminal = ref<HTMLElement | null>(null)
let controller: AbortController | null = null
let retryTimer: number | undefined
let runId = 0

const activeLabel = computed(
  () => systems.value.find((item) => item.value === activeSystem.value)?.label || '未选择',
)
const stateLabel = computed(() => ({
  idle: '等待查询', connecting: '正在连接', live: '实时接收', reconnecting: '正在重连',
  paused: '已暂停', error: '连接异常',
}[state.value]))

function normalizeLine(line: string) {
  try {
    const value = JSON.parse(line)
    const details = Object.fromEntries(
      Object.entries(value).filter(([key]) => !['timestamp', 'level', 'logger', 'message', 'service', 'exception'].includes(key)),
    )
    const suffix = [
      value.exception ? String(value.exception) : '',
      Object.keys(details).length ? JSON.stringify(details) : '',
    ].filter(Boolean).join('\n')
    return {
      raw: line,
      timestamp: value.timestamp ? new Date(value.timestamp).toLocaleTimeString('zh-CN', { hour12: false }) : '--:--:--',
      level: String(value.level || 'INFO').toLowerCase(),
      message: `${String(value.message || line)}${suffix ? `\n${suffix}` : ''}`,
      logger: String(value.logger || ''),
    }
  } catch {
    return { raw: line, timestamp: '--:--:--', level: 'info', message: line, logger: '' }
  }
}

const visibleLines = computed(() => lines.value.map(normalizeLine))

async function scrollToBottom() {
  if (!autoScroll.value) return
  await nextTick()
  if (terminal.value) terminal.value.scrollTop = terminal.value.scrollHeight
}

function acceptEvent(event: RuntimeLogEvent) {
  if (event.event === 'ready') {
    lines.value = event.data.lines || []
    state.value = 'live'
  } else if (event.data.line) {
    lines.value.push(event.data.line)
    if (lines.value.length > 2000) lines.value.splice(0, lines.value.length - 2000)
  }
  void scrollToBottom()
}

async function connect(id: number) {
  controller?.abort()
  const currentController = new AbortController()
  controller = currentController
  try {
    await runtimeLogsApi.stream(activeSystem.value, currentController.signal, acceptEvent)
    if (id === runId && state.value !== 'paused') scheduleReconnect(id)
  } catch (error) {
    if (currentController.signal.aborted || id !== runId) return
    const firstFailure = state.value !== 'reconnecting'
    state.value = 'error'
    if (firstFailure) ElMessage.error(getErrorMessage(error, '日志流连接失败'))
    scheduleReconnect(id)
  }
}

function scheduleReconnect(id: number) {
  if (id !== runId || state.value === 'paused') return
  state.value = 'reconnecting'
  window.clearTimeout(retryTimer)
  retryTimer = window.setTimeout(() => void connect(id), 2000)
}

function query() {
  runId += 1
  activeSystem.value = selectedSystem.value
  lines.value = []
  state.value = 'connecting'
  void connect(runId)
}

function pause() {
  runId += 1
  controller?.abort()
  window.clearTimeout(retryTimer)
  state.value = 'paused'
}

function resume() {
  if (!activeSystem.value) return query()
  runId += 1
  state.value = 'connecting'
  void connect(runId)
}

onMounted(async () => {
  try { systems.value = await runtimeLogsApi.systems() } catch { /* fallback remains available */ }
})
onBeforeUnmount(() => {
  runId += 1
  controller?.abort()
  window.clearTimeout(retryTimer)
})
</script>

<template>
  <div class="runtime-logs-view page-stack">
    <section class="log-query-panel">
      <div class="log-query-panel__title">
        <span class="log-query-panel__icon"><Logs :size="21" /></span>
        <div><span>LOG EXPLORER</span><h2>生产日志查询</h2><p>选择运行进程，建立实时日志流。</p></div>
      </div>
      <div class="log-query-form">
        <label><span>所属系统</span><el-select v-model="selectedSystem" aria-label="所属系统"><el-option v-for="system in systems" :key="system.value" :label="system.label" :value="system.value" /></el-select></label>
        <el-button type="primary" :loading="state === 'connecting'" @click="query"><Search :size="16" />查询</el-button>
      </div>
    </section>

    <section class="log-stream-panel">
      <header class="log-stream-panel__head">
        <div><span class="terminal-mark"><TerminalSquare :size="18" /></span><div><strong>{{ activeLabel }}</strong><span>{{ lines.length.toLocaleString() }} 行</span></div></div>
        <div class="log-stream-actions">
          <span class="stream-state" :class="`is-${state}`"><i />{{ stateLabel }}</span>
          <el-checkbox v-model="autoScroll">自动滚动</el-checkbox>
          <el-tooltip content="清空当前显示"><el-button circle aria-label="清空当前显示" @click="lines = []"><Eraser :size="16" /></el-button></el-tooltip>
          <el-tooltip :content="state === 'paused' ? '继续接收' : '暂停接收'"><el-button circle :aria-label="state === 'paused' ? '继续接收' : '暂停接收'" :disabled="state === 'idle'" @click="state === 'paused' ? resume() : pause()"><Play v-if="state === 'paused'" :size="16" /><Pause v-else :size="16" /></el-button></el-tooltip>
        </div>
      </header>
      <div ref="terminal" class="log-terminal" aria-live="polite">
        <div v-if="!visibleLines.length" class="log-terminal__empty"><TerminalSquare :size="28" /><strong>{{ state === 'connecting' ? '正在连接日志流' : '选择系统并点击查询' }}</strong><span>最新日志将在这里持续输出</span></div>
        <div v-for="(line, index) in visibleLines" v-else :key="`${index}-${line.raw}`" class="log-line" :class="`is-${line.level}`" :title="line.raw"><span class="log-line__number">{{ index + 1 }}</span><time>{{ line.timestamp }}</time><b>{{ line.level.toUpperCase() }}</b><span class="log-line__message">{{ line.message }}</span><small v-if="line.logger">{{ line.logger }}</small></div>
      </div>
    </section>
  </div>
</template>
