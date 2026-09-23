<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ClearOutlined, PauseCircleOutlined, PlayCircleOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { runtimeLogsApi, type RuntimeLogEvent, type RuntimeLogSystem } from '@/services/api'; import { getErrorMessage } from '@/services/http'; import PageHeader from '@/components/PageHeader.vue'; import StatusPill from '@/components/StatusPill.vue'
const fallback: RuntimeLogSystem[] = [{ value:'backend',label:'Backend' },{ value:'worker',label:'轮询 Worker' },{ value:'ai-worker',label:'AI Worker' },{ value:'qq-worker',label:'QQ Worker' },{ value:'xhs-worker',label:'小红书 Worker' }]
const systems = ref(fallback); const selected = ref('worker'); const active = ref(''); const lines = ref<string[]>([]); const state = ref<'idle'|'connecting'|'live'|'paused'|'error'|'reconnecting'>('idle'); const autoScroll = ref(true); const terminal = ref<HTMLElement | null>(null); let controller: AbortController | null = null; let retryTimer: number | undefined; let runId = 0
const stateLabel = computed(() => ({ idle:'等待查询', connecting:'连接中', live:'实时接收', paused:'已暂停', error:'连接异常', reconnecting:'正在重连' }[state.value]))
function formatUtc8Timestamp(value: unknown) {
  const parsed = new Date(String(value))
  if (Number.isNaN(parsed.getTime())) return null
  const shifted = new Date(parsed.getTime() + 8 * 60 * 60 * 1000)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}T${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}:${pad(shifted.getUTCSeconds())}+08:00`
}
function normalizeLine(line: string) {
  try {
    const value = JSON.parse(line) as Record<string, unknown>
    if (typeof value.timestamp === 'string') {
      const formatted = formatUtc8Timestamp(value.timestamp)
      if (formatted) value.timestamp = formatted
      return JSON.stringify(value)
    }
  } catch {
    // Preserve non-JSON log lines exactly as received.
  }
  return line
}
const visibleLines = computed(() => lines.value.map(normalizeLine))
function accept(event: RuntimeLogEvent) { if (event.event === 'ready') lines.value = event.data.lines || []; else if (event.data.line) lines.value.push(event.data.line); if (lines.value.length > 2000) lines.value.splice(0, lines.value.length - 2000); state.value = 'live'; void nextTick(() => { if (autoScroll.value && terminal.value) terminal.value.scrollTop = terminal.value.scrollHeight }) }
async function connect(id: number) { controller?.abort(); controller = new AbortController(); try { await runtimeLogsApi.stream(active.value, controller.signal, accept); if (id === runId && state.value !== 'paused') reconnect(id) } catch (e) { if (id !== runId || controller.signal.aborted) return; state.value = 'error'; message.error(getErrorMessage(e, '日志流连接失败')); reconnect(id) } }
function reconnect(id: number) { state.value = 'reconnecting'; window.clearTimeout(retryTimer); retryTimer = window.setTimeout(() => void connect(id), 2000) }
function query() { runId++; active.value = selected.value; lines.value = []; state.value = 'connecting'; void connect(runId) }
function pause() { runId++; controller?.abort(); window.clearTimeout(retryTimer); state.value = 'paused' }
function clear() { lines.value = [] }
onMounted(async () => { try { systems.value = await runtimeLogsApi.systems() } catch { systems.value = fallback } }); onBeforeUnmount(() => { runId++; controller?.abort(); window.clearTimeout(retryTimer) })
</script>

<template><div class="page-stack"><PageHeader eyebrow="SYSTEM / 02" title="日志监控" description="通过实时流查看各个服务的运行日志"><template #actions><StatusPill :value="state" :label="stateLabel" /></template></PageHeader><a-card :bordered="false"><div class="toolbar"><div class="toolbar__controls"><a-select v-model:value="selected" style="width:200px" :options="systems.map((item) => ({ label:item.label, value:item.value }))" /><a-button type="primary" @click="query"><PlayCircleOutlined /> 连接日志</a-button><a-button @click="pause"><PauseCircleOutlined /> 暂停</a-button><a-button @click="clear"><ClearOutlined /> 清空</a-button></div><a-checkbox v-model:checked="autoScroll">自动滚动</a-checkbox></div><pre ref="terminal" class="log-terminal">{{ visibleLines.join('\n') || '选择服务并连接，实时日志会显示在这里。' }}</pre></a-card></div></template>
