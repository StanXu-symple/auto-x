<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  PlusOutlined,
  SendOutlined,
  RobotOutlined,
  FileTextOutlined,
  CloseOutlined,
  DeleteOutlined,
} from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import PageHeader from '@/components/PageHeader.vue'
import { aiApi } from '@/services/api'
import { agentChatApi } from '@/services/agentChat'
import type {
  AgentConversation,
  AgentConversationDetail,
  AgentReference,
  AgentTurn,
  AgentMessagePayload,
  AgentStreamEvent,
} from '@/services/agentChat'
import { getErrorMessage } from '@/services/http'
import {
  createAgentRequestId,
  extractAgentOutputPreview,
  isActiveTurn,
  mentionAt,
  removeMention,
} from '@/utils/agentChat'
import type { AiSkill } from '@/types'

const route = useRoute()
const router = useRouter()
const conversations = ref<AgentConversation[]>([])
const total = ref(0)
const listPage = ref(1)
const listLoading = ref(false)
const listError = ref('')
const detail = ref<AgentConversationDetail | null>(null)
const loading = ref(false)
const sending = ref(false)
const action = ref('')
const deletingId = ref('')
const error = ref('')
const text = ref('')
const selected = ref<AgentReference[]>([])
const skills = ref<AiSkill[]>([])
const skillsLoading = ref(false)
const skillsError = ref('')
const composer = ref<HTMLTextAreaElement>()
const transcript = ref<HTMLElement>()
const mention = ref<ReturnType<typeof mentionAt>>(null)
const optionIndex = ref(0)
const composing = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | undefined
let elapsedTimer: ReturnType<typeof setInterval> | undefined
let epoch = 0
let listEpoch = 0
let disposed = false
let pending: { key: string; payload: AgentMessagePayload } | null = null
const clock = ref(Date.now())
const streamActive = ref<Record<string, boolean>>({})
const streamControllers = new Map<string, AbortController>()
const streamStarted = new Set<string>()
interface AgentToolTrace {
  name: string
  callId?: string
  phase: string
  argumentsText: string
}
interface AgentStreamTrace {
  phase: string
  label: string
  startedAt: number
  elapsedMs: number
  reasoning: string
  tools: AgentToolTrace[]
  outputChars: number
  outputRaw: string
  outputPreview: ReturnType<typeof extractAgentOutputPreview>
}
const streamTraces = ref<Record<string, AgentStreamTrace>>({})
const currentId = computed(() =>
  typeof route.query.conversation === 'string' ? route.query.conversation : '',
)
const active = computed(() => detail.value?.turns.find(isActiveTurn))
const canSend = computed(
  () =>
    text.value.trim() &&
    !active.value &&
    !sending.value &&
    !action.value &&
    !deletingId.value &&
    !loading.value &&
    (!currentId.value || detail.value) &&
    (detail.value?.turns.length || 0) < 100,
)
const candidates = computed(() =>
  skills.value
    .filter(
      (skill) =>
        !selected.value.some((item) => String(item.id) === String(skill.id)) &&
        `${skill.name} ${skill.description || ''}`
          .toLowerCase()
          .includes(mention.value?.query || ''),
    )
    .slice(0, 30),
)
const statusLabels = {
  queued: '等待生成',
  running: '正在创作',
  failed: '生成失败',
  cancelled: '已取消',
  succeeded: '已完成',
}

async function loadList(more = false) {
  if (more && listLoading.value) return
  // A failed refresh after deletion invalidates offset-based pagination.
  if (listError.value) more = false
  const ticket = ++listEpoch
  listLoading.value = true
  listError.value = ''
  try {
    const page = more ? listPage.value + 1 : 1
    const result = await agentChatApi.list(page)
    if (disposed || ticket !== listEpoch) return
    conversations.value = more
      ? [...conversations.value, ...result.items].filter(
          (item, index, items) => items.findIndex((other) => other.id === item.id) === index,
        )
      : result.items
    total.value = result.total
    listPage.value = page
  } catch (e) {
    if (!disposed && ticket === listEpoch) listError.value = getErrorMessage(e, '加载对话列表失败')
  } finally {
    if (ticket === listEpoch) listLoading.value = false
  }
}

async function loadSkills() {
  if (skillsLoading.value) return
  skillsLoading.value = true
  skillsError.value = ''
  try {
    const all: AiSkill[] = []
    for (let page = 1; !disposed; page++) {
      const result = await aiApi.skillPage({ active: true, page, page_size: 100 })
      all.push(...result.items)
      if (all.length >= result.total || !result.items.length) break
    }
    if (!disposed) skills.value = all
  } catch (e) {
    skillsError.value = getErrorMessage(e, '提示词服务暂不可用')
  } finally {
    skillsLoading.value = false
  }
}

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = undefined
}
function schedulePoll() {
  stopPolling()
  if (!disposed && !deletingId.value && active.value && !streamControllers.size)
    pollTimer = setTimeout(() => refresh(), 2000)
}

function traceFor(turnId: string): AgentStreamTrace | undefined {
  return streamTraces.value[turnId]
}
function streamLabel(turn: AgentTurn): string {
  return traceFor(turn.id)?.label || statusLabels[turn.status]
}
function formatElapsed(turn: AgentTurn): string {
  const trace = traceFor(turn.id)
  const elapsed = trace
    ? Math.max(trace.elapsedMs, trace.startedAt ? clock.value - trace.startedAt : 0)
    : 0
  const seconds = Math.floor(elapsed / 1000)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}
function stopStream(turnId: string) {
  streamControllers.get(turnId)?.abort()
  streamControllers.delete(turnId)
  streamActive.value = { ...streamActive.value, [turnId]: false }
}
function stopAllStreams() {
  for (const turnId of streamControllers.keys()) stopStream(turnId)
}
function updateTrace(turnId: string, event: AgentStreamEvent) {
  const previous = streamTraces.value[turnId] || {
    phase: 'queued',
    label: '等待生成',
    startedAt: Date.now(),
    elapsedMs: 0,
    reasoning: '',
    tools: [],
    outputChars: 0,
    outputRaw: '',
    outputPreview: { reply: '', title: '', excerpt: '', content: '' },
  }
  const trace: AgentStreamTrace = { ...previous, tools: [...previous.tools] }
  if (event.type === 'status') {
    trace.phase = event.phase
    trace.label = event.label
  } else if (event.type === 'reasoning') {
    trace.phase = 'thinking'
    trace.label = '模型思考摘要'
    trace.reasoning = (trace.reasoning + event.delta).slice(-30000)
  } else if (event.type === 'output') {
    trace.phase = 'output'
    trace.label = '正在生成回复'
    trace.outputChars += event.delta.length
    trace.outputRaw = (trace.outputRaw + event.delta).slice(0, 120000)
    trace.outputPreview = extractAgentOutputPreview(trace.outputRaw)
  } else if (event.type === 'tool') {
    trace.phase = 'tool'
    trace.label = '调用工具'
    const index = event.call_id
      ? trace.tools.findIndex((item) => item.callId === event.call_id)
      : trace.tools.length - 1
    if (index >= 0 && event.delta) trace.tools[index]!.argumentsText += event.delta
    else
      trace.tools.push({
        name: event.name || '工具调用',
        callId: event.call_id,
        phase: event.phase,
        argumentsText: event.delta || '',
      })
  } else if (event.type === 'error') {
    trace.phase = 'failed'
    trace.label = '生成失败'
  } else if (event.type === 'done') {
    trace.phase = event.status
    trace.label = event.status === 'succeeded' ? '生成完成' : statusLabels[event.status]
    trace.elapsedMs = Math.max(trace.elapsedMs, clock.value - trace.startedAt)
  }
  streamTraces.value = { ...streamTraces.value, [turnId]: trace }
}
function handleStreamEvent(turn: AgentTurn, event: AgentStreamEvent) {
  if (event.type === 'deleted') {
    void forgetConversation(currentId.value)
    message.info('对话已被删除或已到期清理')
    return
  }
  updateTrace(turn.id, event)
  if (
    event.type === 'status' &&
    event.status &&
    ['queued', 'running', 'succeeded', 'failed', 'cancelled'].includes(event.status)
  )
    turn.status = event.status as AgentTurn['status']
  if (event.type === 'error') {
    turn.status = 'failed'
    turn.error = event.message
  }
  if (event.type === 'done') {
    turn.status = event.status
    stopStream(turn.id)
    void refresh()
  }
}
async function startStream(turn: AgentTurn) {
  if (!currentId.value || streamStarted.has(turn.id) || !isActiveTurn(turn)) return
  const conversationId = currentId.value
  streamStarted.add(turn.id)
  const controller = new AbortController()
  streamControllers.set(turn.id, controller)
  streamActive.value = { ...streamActive.value, [turn.id]: true }
  updateTrace(turn.id, {
    type: 'status',
    phase: 'queued',
    label: '等待生成',
    status: turn.status,
  })
  try {
    await agentChatApi.stream(conversationId, turn.id, controller.signal, (event) => {
      if (!disposed && !controller.signal.aborted && conversationId === currentId.value)
        handleStreamEvent(turn, event)
    })
  } catch (e) {
    if (!controller.signal.aborted && !disposed) {
      streamStarted.delete(turn.id)
      error.value = getErrorMessage(e, '对话流暂时中断，将继续等待生成结果')
      schedulePoll()
    }
  } finally {
    streamControllers.delete(turn.id)
    streamActive.value = { ...streamActive.value, [turn.id]: false }
    if (!disposed && isActiveTurn(turn)) schedulePoll()
  }
}
async function refresh(initial = false) {
  const id = currentId.value
  if (!id || disposed) return
  const ticket = ++epoch
  if (initial) loading.value = true
  try {
    const result = await agentChatApi.detail(id)
    if (disposed || ticket !== epoch || id !== currentId.value) return
    const changed = JSON.stringify(detail.value?.turns) !== JSON.stringify(result.turns)
    detail.value = result
    error.value = ''
    const running = result.turns.find(isActiveTurn)
    if (running) void startStream(running)
    if (initial)
      selected.value =
        result.turns[result.turns.length - 1]?.skill_snapshot.map((item) => ({ ...item })) || []
    if (changed) {
      await scrollBottom()
      if (!active.value) void loadList()
    }
  } catch (e) {
    if (ticket === epoch && !disposed) {
      if (isMissingConversation(e)) {
        await forgetConversation(id)
        message.info('对话已被删除或已到期清理')
      } else error.value = getErrorMessage(e, '加载消息失败，可稍后刷新')
    }
  } finally {
    if (ticket === epoch && !disposed) {
      loading.value = false
      schedulePoll()
    }
  }
}

async function scrollBottom() {
  await nextTick()
  transcript.value?.scrollTo({ top: transcript.value.scrollHeight, behavior: 'auto' })
}
async function navigate(id = '') {
  if (sending.value || action.value || deletingId.value) return
  if (!id && !currentId.value) {
    text.value = ''
    selected.value = []
    error.value = ''
    return
  }
  const query = { ...route.query }
  if (id) query.conversation = id
  else delete query.conversation
  await router.replace({ query })
}

function isMissingConversation(e: unknown) {
  return (e as { response?: { status?: number } })?.response?.status === 404
}
async function forgetConversation(id: string) {
  if (disposed) return
  ++listEpoch
  listLoading.value = false
  if (conversations.value.some((item) => item.id === id)) total.value = Math.max(0, total.value - 1)
  conversations.value = conversations.value.filter((item) => item.id !== id)
  if (currentId.value === id) {
    ++epoch
    stopPolling()
    stopAllStreams()
    streamStarted.clear()
    streamTraces.value = {}
    detail.value = null
    text.value = ''
    selected.value = []
    mention.value = null
    pending = null
    error.value = ''
    loading.value = false
    const query = { ...route.query }
    delete query.conversation
    await router.replace({ query })
  }
  void loadList()
}
async function deleteConversation(item: AgentConversation) {
  if (deletingId.value || sending.value || action.value) return
  deletingId.value = item.id
  try {
    try {
      await agentChatApi.remove(item.id)
    } catch (e) {
      if (!isMissingConversation(e)) throw e
    }
    await forgetConversation(item.id)
    message.success('对话已删除')
  } catch (e) {
    message.error(getErrorMessage(e, '删除对话失败，请稍后重试'))
    throw e
  } finally {
    deletingId.value = ''
    schedulePoll()
  }
}

function removeConversation(item: AgentConversation) {
  if (deletingId.value || sending.value || action.value) return
  Modal.confirm({
    title: '删除这条对话？',
    content: '将永久删除这条对话及全部消息，进行中的回复也会丢弃。已保存到文章管理的文章仍会保留。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => deleteConversation(item),
  })
}
watch(
  currentId,
  () => {
    stopAllStreams()
    streamStarted.clear()
    stopPolling()
    ++epoch
    if (currentId.value && detail.value?.id === currentId.value) return
    detail.value = null
    text.value = ''
    selected.value = []
    mention.value = null
    pending = null
    error.value = ''
    loading.value = false
    void nextTick(() => transcript.value?.scrollTo({ top: 0, behavior: 'auto' }))
    if (currentId.value) void refresh(true)
  },
  { immediate: true },
)

function updateMention() {
  if (composing.value) return
  mention.value = mentionAt(text.value, composer.value?.selectionStart ?? text.value.length)
  optionIndex.value = 0
}
function useExample() {
  text.value = '帮我写一篇面向职场新人的文章，主题是如何把 AI 用在日常工作中。先和我确认文章大纲。'
  composer.value?.focus()
}
function compositionEnd() {
  composing.value = false
  updateMention()
}
async function openMentions() {
  const input = composer.value
  const start = input?.selectionStart ?? text.value.length
  const end = input?.selectionEnd ?? start
  const prefix = start && !/\s/.test(text.value[start - 1]!) ? ' @' : '@'
  text.value = text.value.slice(0, start) + prefix + text.value.slice(end)
  await nextTick()
  input?.focus()
  input?.setSelectionRange(start + prefix.length, start + prefix.length)
  updateMention()
}
async function selectSkill(skill: AiSkill) {
  if (selected.value.length >= 20) {
    message.warning('最多关联 20 个提示词或 Skills')
    return
  }
  if (selected.value.some((item) => String(item.id) === String(skill.id))) return
  selected.value.push({
    id: skill.id,
    name: skill.name,
    version: skill.version || 1,
    content_type: skill.content_type || (skill.source_type === 'github' ? 'skill' : 'prompt'),
  })
  const position = mention.value?.start ?? text.value.length
  if (mention.value) text.value = removeMention(text.value, mention.value)
  mention.value = null
  await nextTick()
  composer.value?.focus()
  composer.value?.setSelectionRange(position, position)
}
function keydown(event: KeyboardEvent) {
  if (event.isComposing || composing.value) return
  if (mention.value) {
    if (event.key === 'Escape') {
      event.preventDefault()
      mention.value = null
      return
    }
    if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault()
      optionIndex.value = Math.max(
        0,
        Math.min(
          candidates.value.length - 1,
          optionIndex.value + (event.key === 'ArrowDown' ? 1 : -1),
        ),
      )
      document
        .getElementById(`agent-option-${optionIndex.value}`)
        ?.scrollIntoView({ block: 'nearest' })
      return
    }
    if (
      event.key === 'Enter' &&
      !event.ctrlKey &&
      !event.metaKey &&
      candidates.value[optionIndex.value]
    ) {
      event.preventDefault()
      void selectSkill(candidates.value[optionIndex.value]!)
      return
    }
  }
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault()
    void send()
  }
}

async function send() {
  if (!canSend.value) return
  if (mention.value) {
    message.info('请先选择 @ 候选项，或按 Esc 关闭候选列表')
    return
  }
  sending.value = true
  error.value = ''
  stopPolling()
  const sendEpoch = ++epoch
  try {
    const content = text.value.trim()
    const skillIds = selected.value.map((item) => item.id)
    const key = JSON.stringify([currentId.value, content, skillIds])
    if (!pending || pending.key !== key)
      pending = {
        key,
        payload: { request_id: createAgentRequestId(), content, skill_ids: skillIds },
      }
    if (!detail.value) {
      const created = await agentChatApi.create()
      if (disposed || sendEpoch !== epoch) return
      detail.value = { ...created, turns: [] }
      await router.replace({ query: { ...route.query, conversation: created.id } })
      if (disposed || currentId.value !== created.id || !pending) return
      pending.key = JSON.stringify([created.id, content, skillIds])
    }
    const id = detail.value.id
    const turn = await agentChatApi.send(id, pending.payload)
    if (disposed || currentId.value !== id) return
    if (!detail.value.turns.some((item) => item.id === turn.id)) detail.value.turns.push(turn)
    void startStream(turn)
    text.value = ''
    pending = null
    void loadList()
    await scrollBottom()
  } catch (e) {
    error.value = getErrorMessage(e, '发送失败，请重试；消息内容已保留')
  } finally {
    sending.value = false
    schedulePoll()
  }
}

async function turnAction(turn: AgentTurn, kind: 'cancel' | 'retry' | 'save') {
  if (action.value || deletingId.value) return
  const id = currentId.value
  action.value = `${kind}:${turn.id}`
  stopPolling()
  ++epoch
  try {
    if (kind === 'save') {
      const result = await agentChatApi.save(id, turn.id)
      if (!disposed && id === currentId.value) turn.article_id = result.article_id
      message.success('文章已保存，可在文章管理中编辑和发布')
    } else {
      const result = await agentChatApi[kind](id, turn.id)
      if (!disposed && id === currentId.value && detail.value) {
        detail.value.turns = detail.value.turns.map((item) =>
          item.id === result.id ? result : item,
        )
        error.value = ''
        if (isActiveTurn(result)) {
          streamStarted.delete(result.id)
          void startStream(result)
        } else stopStream(result.id)
      }
    }
  } catch (e) {
    message.error(getErrorMessage(e, '操作失败，请稍后重试'))
  } finally {
    action.value = ''
    schedulePoll()
  }
}

onMounted(() => {
  void loadList()
  void loadSkills()
  elapsedTimer = setInterval(() => {
    clock.value = Date.now()
  }, 250)
})
onBeforeUnmount(() => {
  disposed = true
  ++epoch
  stopPolling()
  stopAllStreams()
  if (elapsedTimer) clearInterval(elapsedTimer)
})
</script>

<template>
  <div class="page-stack agent-page">
    <PageHeader title="智能体对话" description="从一个想法开始，和智能体一起把文章写好。">
      <template #actions><RouterLink to="/skills">管理提示词与 Skills</RouterLink></template>
    </PageHeader>
    <div class="agent-workspace">
      <aside class="conversation-sidebar" aria-label="历史对话">
        <a-button block :disabled="sending || !!action || !!deletingId" @click="navigate()"
          ><PlusOutlined /> 新建对话</a-button
        >
        <div class="sidebar-label">最近的对话</div>
        <p v-if="listError" class="inline-error">
          {{ listError }} <button class="text-button" @click="loadList()">重试</button>
        </p>
        <p v-else-if="!conversations.length" class="muted">
          {{ listLoading ? '正在加载…' : '你的创作对话将保存在这里' }}
        </p>
        <nav class="conversation-list" aria-label="选择对话">
          <div
            v-for="item in conversations"
            :key="item.id"
            :class="['conversation-item', { selected: item.id === currentId }]"
          >
            <button
              class="conversation-open"
              :aria-current="item.id === currentId ? 'page' : undefined"
              :disabled="sending || !!action || !!deletingId"
              @click="navigate(item.id)"
            >
              <FileTextOutlined /><span :title="item.title">{{ item.title }}</span>
            </button>
            <a-button
              class="conversation-delete"
              type="text"
              danger
              :loading="deletingId === item.id"
              :disabled="sending || !!action || !!deletingId"
              :aria-label="`删除对话：${item.title}`"
              title="删除对话"
              @click="removeConversation(item)"
              ><DeleteOutlined
            /></a-button>
          </div>
        </nav>
        <a-button
          v-if="conversations.length < total"
          type="text"
          :loading="listLoading"
          @click="loadList(true)"
          >加载更多</a-button
        >
        <div class="sidebar-note">文章创作助手<br /><span>生成 · 讨论 · 修改</span></div>
      </aside>
      <section class="conversation-main" aria-label="文章创作对话">
        <header class="conversation-header">
          <div>
            <RobotOutlined /><strong>{{ detail?.title || '开启一篇新文章' }}</strong>
          </div>
          <span class="capability-label">文章创作</span>
        </header>
        <div ref="transcript" class="transcript" :aria-busy="loading">
          <p v-if="loading" class="muted">正在加载对话…</p>
          <div v-else-if="!detail?.turns.length" class="chat-empty">
            <div class="empty-icon"><FileTextOutlined /></div>
            <h2>今天，想写点什么？</h2>
            <p>
              告诉我主题、受众或你已有的素材。<br />输入 <kbd>@</kbd> 关联提示词和
              Skills，设定文章的写法。
            </p>
            <button class="example-prompt" @click="useExample">
              试试：和我一起构思一篇 AI 工作指南 <span>↗</span>
            </button>
          </div>
          <div v-for="(turn, index) in detail?.turns || []" :key="turn.id" class="chat-turn">
            <div class="user-message">
              <span class="speaker">你</span>
              <div class="message-text">{{ turn.content }}</div>
              <div v-if="turn.skill_snapshot.length" class="reference-list">
                <span
                  v-for="reference in turn.skill_snapshot"
                  :key="String(reference.id)"
                  class="reference-chip"
                  >{{ reference.content_type === 'skill' ? 'Skill' : '提示词' }} ·
                  {{ reference.name }} <small>v{{ reference.version }}</small></span
                >
              </div>
            </div>
            <div class="assistant-message">
              <span class="speaker"><RobotOutlined /> 创作助手</span>
              <div v-if="isActiveTurn(turn)" class="generation-status" role="status">
                <a-spin size="small" /> {{ streamLabel(turn) }} · {{ formatElapsed(turn) }}
                <a-button
                  size="small"
                  type="text"
                  :loading="action === `cancel:${turn.id}`"
                  :disabled="!!action"
                  @click="turnAction(turn, 'cancel')"
                  >取消生成</a-button
                >
              </div>
              <div v-if="isActiveTurn(turn) && traceFor(turn.id)" class="stream-trace">
                <div class="stream-trace__meta">
                  <span v-if="traceFor(turn.id)?.phase === 'thinking'" class="stream-badge thinking"
                    >思考</span
                  ><span v-else-if="traceFor(turn.id)?.phase === 'tool'" class="stream-badge tool"
                    >工具</span
                  ><span v-else class="stream-badge status">状态</span>
                  <span v-if="traceFor(turn.id)?.outputChars"
                    >已接收 {{ traceFor(turn.id)?.outputChars }} 字符</span
                  >
                </div>
                <details v-if="traceFor(turn.id)?.reasoning" open class="stream-trace__detail">
                  <summary>模型思考摘要</summary>
                  <p>{{ traceFor(turn.id)?.reasoning }}</p>
                </details>
                <div v-if="traceFor(turn.id)?.tools.length" class="stream-trace__tools">
                  <div v-for="tool in traceFor(turn.id)?.tools" :key="tool.name + tool.phase">
                    工具调用：{{ tool.name }}（{{
                      tool.phase === 'arguments' ? '参数生成中' : '已开始'
                    }}）
                  </div>
                </div>
                <div
                  v-if="
                    traceFor(turn.id)?.outputPreview.reply ||
                    traceFor(turn.id)?.outputPreview.title ||
                    traceFor(turn.id)?.outputPreview.content
                  "
                  class="stream-preview"
                >
                  <p v-if="traceFor(turn.id)?.outputPreview.reply">
                    {{ traceFor(turn.id)?.outputPreview.reply }}
                  </p>
                  <h3 v-if="traceFor(turn.id)?.outputPreview.title">
                    {{ traceFor(turn.id)?.outputPreview.title }}
                  </h3>
                  <p
                    v-if="traceFor(turn.id)?.outputPreview.excerpt"
                    class="stream-preview__excerpt"
                  >
                    {{ traceFor(turn.id)?.outputPreview.excerpt }}
                  </p>
                  <div
                    v-if="traceFor(turn.id)?.outputPreview.content"
                    class="stream-preview__content"
                  >
                    {{ traceFor(turn.id)?.outputPreview.content }}
                  </div>
                </div>
              </div>
              <template v-else-if="turn.status === 'succeeded'">
                <p class="message-text">{{ turn.reply }}</p>
                <div
                  v-if="traceFor(turn.id)?.reasoning || traceFor(turn.id)?.tools.length"
                  class="stream-trace stream-trace--done"
                >
                  <details open class="stream-trace__detail">
                    <summary>生成过程</summary>
                    <p v-if="traceFor(turn.id)?.reasoning">
                      模型思考摘要：{{ traceFor(turn.id)?.reasoning }}
                    </p>
                    <p v-for="tool in traceFor(turn.id)?.tools" :key="tool.name + tool.phase">
                      工具调用：{{ tool.name }}
                    </p>
                  </details>
                </div>
                <article v-if="turn.article" class="article-result">
                  <div class="article-heading">
                    <span>文章草稿</span><span v-if="turn.article_id">已保存</span>
                  </div>
                  <h2>{{ turn.article.title }}</h2>
                  <p v-if="turn.article.excerpt" class="article-excerpt">
                    {{ turn.article.excerpt }}
                  </p>
                  <div class="article-content">{{ turn.article.content }}</div>
                  <footer>
                    <a-button
                      v-if="!turn.article_id"
                      :loading="action === `save:${turn.id}`"
                      :disabled="!!action"
                      @click="turnAction(turn, 'save')"
                      >保存到文章管理</a-button
                    ><RouterLink v-else to="/articles">前往文章管理</RouterLink
                    ><span>继续对话即可修改文章</span>
                  </footer>
                </article>
              </template>
              <div v-else class="turn-error">
                <p>{{ turn.error || statusLabels[turn.status] }}</p>
                <a-button
                  v-if="index === (detail?.turns.length || 0) - 1"
                  size="small"
                  :loading="action === `retry:${turn.id}`"
                  :disabled="!!action"
                  @click="turnAction(turn, 'retry')"
                  >重新生成</a-button
                >
              </div>
            </div>
          </div>
        </div>
        <div class="composer-area">
          <a-alert v-if="error" :message="error" type="error" show-icon class="chat-alert"
            ><template #action
              ><a-button v-if="currentId" size="small" @click="refresh(!detail)"
                >刷新</a-button
              ></template
            ></a-alert
          >
          <div class="composer-box">
            <div v-if="selected.length" class="reference-list selected-references">
              <span v-for="reference in selected" :key="String(reference.id)" class="reference-chip"
                >{{ reference.content_type === 'skill' ? 'Skill' : '提示词' }} · {{ reference.name
                }}<button
                  :aria-label="`移除 ${reference.name}`"
                  :disabled="sending"
                  @click="selected = selected.filter((item) => item.id !== reference.id)"
                >
                  <CloseOutlined /></button
              ></span>
            </div>
            <label for="agent-composer" class="sr-only">消息，输入 @ 选择提示词和 Skills</label>
            <textarea
              id="agent-composer"
              ref="composer"
              v-model="text"
              rows="3"
              maxlength="20000"
              placeholder="描述你想写的内容，输入 @ 关联提示词或 Skills…"
              :disabled="sending || loading"
              :aria-expanded="!!mention"
              :aria-controls="mention ? 'agent-mentions' : undefined"
              :aria-activedescendant="
                mention && candidates.length ? `agent-option-${optionIndex}` : undefined
              "
              aria-autocomplete="list"
              @input="updateMention"
              @click="updateMention"
              @keyup.left="updateMention"
              @keyup.right="updateMention"
              @keydown="keydown"
              @compositionstart="composing = true"
              @compositionend="compositionEnd"
            />
            <div
              v-if="mention"
              id="agent-mentions"
              class="mention-menu"
              role="listbox"
              aria-label="关联提示词或 Skills"
            >
              <div class="mention-heading">
                选择提示词或 Skill
                <button class="text-button" aria-label="关闭候选列表" @click="mention = null">
                  <CloseOutlined />
                </button>
              </div>
              <p v-if="skillsLoading" class="muted">正在加载…</p>
              <p v-else-if="skillsError" class="inline-error">
                {{ skillsError }} <button class="text-button" @click="loadSkills">重试</button>
              </p>
              <p v-else-if="!candidates.length" class="muted">没有匹配项，可先到提示词管理添加。</p>
              <button
                v-for="(skill, index) in candidates"
                :id="`agent-option-${index}`"
                :key="String(skill.id)"
                role="option"
                :aria-selected="optionIndex === index"
                :class="['mention-option', { highlighted: index === optionIndex }]"
                @pointerdown.prevent
                @click="selectSkill(skill)"
              >
                <span class="reference-type">{{
                  skill.content_type === 'skill' || skill.source_type === 'github'
                    ? 'Skill'
                    : '提示词'
                }}</span
                ><span
                  ><strong>{{ skill.name }}</strong
                  ><small>{{ skill.description || '使用这份写作指导' }}</small></span
                >
              </button>
            </div>
            <div class="composer-toolbar">
              <button
                class="mention-trigger"
                :disabled="sending || loading"
                aria-label="关联提示词或 Skills"
                @click="openMentions"
              >
                @ <span>提示词 / Skills</span></button
              ><a-button type="primary" :disabled="!canSend" :loading="sending" @click="send"
                ><SendOutlined /> 发送</a-button
              >
            </div>
          </div>
          <div class="composer-hint">
            <span>{{
              active ? '回复完成后可继续提出修改要求' : '关联内容会沿用到后续消息，可随时移除'
            }}</span
            ><span>Ctrl / ⌘ + Enter 发送</span>
          </div>
          <p v-if="(detail?.turns.length || 0) >= 100" class="inline-error">
            本对话已达 100 条消息，请新建对话。
          </p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.agent-page {
  gap: 20px;
}
.agent-page :deep(.page-header) {
  gap: 12px;
  padding-bottom: 0;
}
.agent-page :deep(.page-header h1) {
  font-size: 30px;
  margin: 0 0 8px;
}
.agent-workspace {
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
  border: 1px solid var(--line, #e1e3d9);
  border-radius: 16px;
  overflow: hidden;
  background: #fff;
  height: min(900px, calc(100dvh - 225px));
  min-height: 440px;
}
.conversation-sidebar {
  padding: 20px 14px;
  border-right: 1px solid #e1e3d9;
  background: #fafaf6;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
}
.sidebar-label,
.speaker,
.article-heading {
  font-size: 12px;
  font-weight: 600;
  color: #72756c;
}
.sidebar-label {
  padding: 8px 8px 0;
}
.conversation-list {
  overflow-y: auto;
  display: grid;
  gap: 4px;
}
.conversation-item {
  border: 0;
  background: transparent;
  text-align: left;
  display: flex;
  gap: 9px;
  padding: 5px 6px 5px 10px;
  border-radius: 8px;
  color: #54574e;
  cursor: pointer;
  min-width: 0;
  align-items: center;
}
.conversation-open {
  border: 0;
  background: transparent;
  text-align: left;
  display: flex;
  gap: 9px;
  padding: 0;
  color: inherit;
  cursor: pointer;
  min-width: 0;
  align-items: center;
  flex: 1;
}
.conversation-open span:last-child {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.conversation-item:hover,
.conversation-item.selected {
  background: #f3e9e2;
  color: #92442e;
}
.conversation-delete {
  flex-shrink: 0;
}
.sidebar-note {
  margin-top: auto;
  padding: 14px 8px 0;
  font-size: 13px;
  line-height: 1.8;
  color: #72756c;
}
.sidebar-note span {
  font-size: 12px;
}
.conversation-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.conversation-header {
  padding: 18px 26px;
  display: flex;
  align-items: center;
  gap: 16px;
  justify-content: space-between;
  border-bottom: 1px solid #eeefe7;
}
.conversation-header > div {
  display: flex;
  gap: 10px;
  min-width: 0;
  align-items: center;
}
.conversation-header strong {
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.capability-label {
  flex-shrink: 0;
  font-size: 11px;
  color: #3d705a;
  background: #edf3ed;
  border-radius: 5px;
  padding: 4px 7px;
}
.transcript {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 28px clamp(16px, 4vw, 56px);
  scroll-behavior: auto;
}
.chat-empty {
  min-height: 300px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 18px 0;
}
.empty-icon {
  background: #f3e9e2;
  color: #a64e35;
  width: 48px;
  height: 48px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  font-size: 24px;
  margin-bottom: 18px;
}
.chat-empty h2 {
  font-size: 24px;
  font-weight: 600;
  margin-bottom: 12px;
}
.chat-empty p {
  color: #72756c;
  font-size: 14px;
  line-height: 1.9;
}
.example-prompt {
  padding: 14px 18px;
  border: 1px solid #e1e3d9;
  background: #fafaf6;
  border-radius: 10px;
  color: #54574e;
  margin-top: 16px;
  cursor: pointer;
  text-align: left;
  font-size: 13px;
}
.example-prompt span {
  margin-left: 10px;
  color: #a64e35;
}
.chat-turn + .chat-turn {
  margin-top: 32px;
}
.user-message {
  background: #f7f7f2;
  padding: 16px 20px;
  border-radius: 12px;
  margin-left: 24px;
}
.speaker {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 10px;
}
.message-text,
.article-content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 14px;
  line-height: 1.9;
  margin: 0;
}
.assistant-message {
  margin-top: 24px;
}
.assistant-message > .speaker {
  color: #a64e35;
}
.reference-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}
.reference-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #e1e3d9;
  background: #fff;
  color: #616458;
  padding: 3px 8px;
  border-radius: 5px;
  font-size: 11px;
  max-width: 100%;
  overflow-wrap: anywhere;
}
.reference-chip small {
  color: #8a8d81;
}
.reference-chip button {
  border: 0;
  background: transparent;
  color: #72756c;
  cursor: pointer;
  padding: 3px;
}
.article-result {
  border: 1px solid #e1e3d9;
  border-radius: 10px;
  margin-top: 18px;
  padding: 20px;
}
.article-heading {
  display: flex;
  justify-content: space-between;
  color: #3d705a;
}
.article-result h2 {
  font-size: 20px;
  line-height: 1.5;
  margin: 16px 0;
  overflow-wrap: anywhere;
}
.article-excerpt {
  border-left: 2px solid #d6c3b4;
  padding-left: 12px;
  color: #72756c;
  font-size: 13px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.article-result footer {
  margin-top: 24px;
  padding-top: 14px;
  border-top: 1px solid #eeefe7;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.article-result footer > span {
  color: #72756c;
  font-size: 12px;
}
.generation-status {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: #72756c;
}
.stream-trace {
  margin: 12px 0 0 24px;
  padding: 12px 14px;
  border: 1px solid #e8e9e1;
  border-radius: 9px;
  background: #fbfbf8;
  color: #72756c;
  font-size: 12px;
  line-height: 1.7;
}
.stream-trace--done {
  margin-left: 0;
}
.stream-trace__meta {
  display: flex;
  align-items: center;
  gap: 8px;
}
.stream-badge {
  border-radius: 999px;
  padding: 2px 7px;
  font-size: 11px;
}
.stream-badge.thinking {
  background: #eee9f8;
  color: #6c4ca0;
}
.stream-badge.tool {
  background: #edf3ed;
  color: #3d705a;
}
.stream-badge.status {
  background: #f3e9e2;
  color: #92442e;
}
.stream-trace__detail {
  margin-top: 8px;
}
.stream-trace__detail summary {
  cursor: pointer;
  color: #54574e;
}
.stream-trace__detail p {
  margin: 6px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.stream-trace__tools {
  margin-top: 8px;
  color: #3d705a;
}
.stream-preview {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #e8e9e1;
  color: #383b35;
  font-size: 14px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.stream-preview p {
  margin: 0 0 10px;
}
.stream-preview h3 {
  margin: 12px 0 6px;
  color: #222820;
  font-size: 17px;
}
.stream-preview__excerpt {
  color: #72756c;
}
.stream-preview__content {
  line-height: 1.85;
}
.turn-error {
  border-left: 2px solid #d2a28b;
  padding-left: 14px;
  color: #91472f;
  font-size: 13px;
}
.composer-area {
  padding: 16px clamp(16px, 4vw, 40px) 18px;
  border-top: 1px solid #eeefe7;
}
.composer-box {
  border: 1px solid #d6d9ce;
  border-radius: 12px;
  padding: 12px 14px;
  position: relative;
  background: #fff;
}
.composer-box:focus-within {
  border-color: #a64e35;
  box-shadow: 0 0 0 2px #a64e3510;
}
.selected-references {
  margin-top: 0;
  margin-bottom: 10px;
  max-height: 90px;
  overflow-y: auto;
}
textarea {
  resize: vertical;
  border: 0;
  width: 100%;
  min-height: 70px;
  max-height: 180px;
  outline: none;
  font: inherit;
  font-size: 14px;
  line-height: 1.65;
  background: transparent;
  color: #292c29;
}
textarea::placeholder {
  color: #8a8d81;
}
.composer-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}
.mention-trigger {
  border: 0;
  background: #f7f7f2;
  color: #616458;
  padding: 6px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 600;
}
.mention-trigger span {
  font-size: 12px;
  margin-left: 4px;
  font-weight: 400;
}
.composer-hint {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 11px;
  color: #72756c;
  margin-top: 10px;
}
.mention-menu {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  width: min(460px, 100%);
  max-height: 280px;
  overflow-y: auto;
  border: 1px solid #e1e3d9;
  background: white;
  border-radius: 10px;
  box-shadow: 0 8px 28px #292c2915;
  z-index: 20;
  padding: 6px;
}
.mention-heading {
  font-size: 12px;
  color: #72756c;
  padding: 6px 8px;
  display: flex;
  justify-content: space-between;
}
.mention-option {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px;
  border: 0;
  background: transparent;
  border-radius: 6px;
  text-align: left;
  cursor: pointer;
}
.mention-option.highlighted,
.mention-option:hover {
  background: #f3e9e2;
}
.mention-option > span:last-child {
  min-width: 0;
}
.mention-option strong {
  display: block;
  font-size: 13px;
  color: #292c29;
  overflow-wrap: anywhere;
}
.mention-option small {
  display: block;
  color: #72756c;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 11px;
  max-width: 300px;
}
.reference-type {
  flex-shrink: 0;
  font-size: 10px;
  border: 1px solid #d6d9ce;
  border-radius: 4px;
  color: #616458;
  padding: 2px 4px;
  min-width: 40px;
  text-align: center;
}
.muted {
  color: #72756c;
  font-size: 12px;
  line-height: 1.8;
  padding: 0 8px;
}
.inline-error {
  color: #91472f;
  font-size: 12px;
}
.text-button {
  border: 0;
  background: none;
  color: #a64e35;
  cursor: pointer;
}
.chat-alert {
  margin-bottom: 10px;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
button:focus-visible,
a:focus-visible {
  outline: 2px solid #a64e35;
  outline-offset: 2px;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
@media (max-width: 1000px) {
  .agent-workspace {
    grid-template-columns: 190px minmax(0, 1fr);
  }
  .composer-hint span:last-child {
    display: none;
  }
}
@media (max-width: 700px) {
  .agent-workspace {
    display: flex;
    flex-direction: column;
    height: calc(100dvh - 265px);
    min-height: 440px;
  }
  .conversation-sidebar {
    border-right: 0;
    border-bottom: 1px solid #e1e3d9;
    padding: 10px;
    gap: 8px;
    flex-direction: row;
    align-items: center;
    flex-shrink: 0;
  }
  .conversation-sidebar > .ant-btn {
    width: auto;
    flex-shrink: 0;
  }
  .sidebar-label,
  .sidebar-note {
    display: none;
  }
  .conversation-list {
    display: flex;
    flex: 1;
    overflow-x: auto;
  }
  .conversation-item {
    max-width: 160px;
    flex-shrink: 0;
  }
  .conversation-main {
    flex: 1;
  }
  .conversation-header {
    padding: 14px 16px;
  }
  .chat-empty h2 {
    font-size: 21px;
  }
  .chat-empty {
    min-height: 250px;
  }
  .user-message {
    margin-left: 0;
  }
  .article-result {
    padding: 15px;
  }
  .composer-area {
    padding: 12px;
  }
}
</style>
