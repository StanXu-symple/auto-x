<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  agentWorkflowsApi,
  allPages,
  nodeTypeLabel,
  runActive,
  runStatusLabel,
} from '@/services/agentWorkflows'
import type { AgentWorkflow, WorkflowRunDetail, WorkflowRunInput } from '@/services/agentWorkflows'
import type { EntityId } from '@/types'
import { getErrorMessage } from '@/services/http'
import { formatDateTime } from '@/utils/format'

const props = defineProps<{
  open: boolean
  workflowId?: number
  runId?: string
  article?: { id: EntityId; revision: number; title: string } | null
  tweet?: { tweet_id: string; text: string } | null
}>()
const emit = defineEmits<{ 'update:open': [value: boolean]; changed: [] }>()
const flows = ref<AgentWorkflow[]>([])
const selectedId = ref<number>()
const input = ref('')
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const run = ref<WorkflowRunDetail | null>(null)
const selected = computed(() => flows.value.find((flow) => flow.id === selectedId.value))
const hasArticle = computed(() =>
  run.value?.steps.some((step) => step.node_type === 'article' && step.output?.content),
)
const decisions: Record<string, string> = { pass: '通过', revise: '建议修改', reject: '不通过' }
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
let submission: WorkflowRunInput | null = null

function requestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const n = Math.floor(Math.random() * 16)
    return (c === 'x' ? n : (n & 3) | 8).toString(16)
  })
}
function stop() {
  ++generation
  clearTimeout(timer)
}
async function initialize() {
  stop()
  const token = generation
  run.value = null
  error.value = ''
  submission = null
  selectedId.value = props.workflowId
  input.value = props.article
    ? '请按流程审核或修订这篇文章，并说明判断依据。'
    : props.tweet
      ? '请根据这条内容，按流程完成分析、创作和审核。'
      : ''
  loading.value = true
  try {
    if (props.runId) await refresh(props.runId, token)
    else {
      const result = await allPages((query) => agentWorkflowsApi.flows({ ...query, active: true }))
      if (token !== generation) return
      flows.value = result
    }
  } catch (e) {
    if (token === generation) error.value = getErrorMessage(e)
  } finally {
    if (token === generation) loading.value = false
  }
}
async function refresh(id: string, token = generation) {
  clearTimeout(timer)
  try {
    const result = await agentWorkflowsApi.detail(id)
    if (token !== generation || !props.open) return
    run.value = result
    error.value = ''
    if (runActive(result.status)) timer = setTimeout(() => void refresh(id, token), 2000)
    else emit('changed')
  } catch (e) {
    if (token === generation) error.value = getErrorMessage(e, '进度读取失败，点击刷新重试')
  }
}
async function start() {
  if (busy.value || !selected.value || !input.value.trim()) return
  const candidate: WorkflowRunInput = {
    workflow_id: selected.value.id,
    workflow_version: selected.value.version,
    request_id: submission?.request_id || requestId(),
    input_text: input.value.trim(),
    ...(props.article
      ? { article_id: props.article.id, article_revision: props.article.revision }
      : {}),
    ...(props.tweet ? { tweet_id: props.tweet.tweet_id } : {}),
  }
  if (submission && JSON.stringify(candidate) !== JSON.stringify(submission))
    candidate.request_id = requestId()
  submission = candidate
  busy.value = true
  error.value = ''
  const token = generation
  try {
    const created = await agentWorkflowsApi.run(candidate)
    await refresh(created.id, token)
    emit('changed')
  } catch (e) {
    if (token === generation) error.value = getErrorMessage(e, '流程启动失败')
  } finally {
    busy.value = false
  }
}
async function action(kind: 'retry' | 'cancel' | 'save') {
  if (!run.value || busy.value) return
  busy.value = true
  const id = run.value.id
  const token = generation
  try {
    if (kind === 'save') {
      await agentWorkflowsApi.saveArticle(id)
      message.success('已保存新草稿到文章管理')
    } else if (kind === 'cancel') await agentWorkflowsApi.cancel(id)
    else await agentWorkflowsApi.retry(id)
    await refresh(id, token)
    emit('changed')
  } catch (e) {
    error.value = getErrorMessage(e)
  } finally {
    busy.value = false
  }
}
function duration(start: string | null, end: string | null) {
  return start && end
    ? `${Math.max(0, (Date.parse(end) - Date.parse(start)) / 1000).toFixed(1)} 秒`
    : ''
}
watch(
  () => [props.open, props.runId, props.workflowId],
  () => {
    if (props.open) void initialize()
    else stop()
  },
  { immediate: true },
)
onUnmounted(stop)
</script>
<template>
  <a-modal
    :open="open"
    title="运行 Agent 流程"
    :width="900"
    :footer="null"
    :mask-closable="!busy"
    :closable="!busy"
    :keyboard="!busy"
    @cancel="emit('update:open', false)"
  >
    <a-spin :spinning="loading">
      <a-alert v-if="error" type="error" show-icon :message="error" style="margin-bottom: 16px">
        <template #action
          ><a-button v-if="run" @click="refresh(run.id)">刷新</a-button
          ><a-button v-else-if="!flows.length" @click="initialize">重试</a-button></template
        >
      </a-alert>
      <template v-if="!run">
        <a-form layout="vertical" :disabled="busy">
          <a-form-item label="Agent 流程" required
            ><a-select
              v-model:value="selectedId"
              show-search
              option-filter-prop="label"
              :options="
                flows.map((f) => ({
                  value: f.id,
                  label: `${f.name} · v${f.version}`,
                  disabled: f.nodes.some((n) => !n.is_active),
                }))
              "
              placeholder="选择已启用的流程"
          /></a-form-item>
          <div v-if="selected" class="chain">
            <template v-for="(node, index) in selected.nodes" :key="index"
              ><a-tag>{{ index + 1 }}. {{ node.name }}</a-tag
              ><span v-if="index < selected.nodes.length - 1">→</span></template
            >
          </div>
          <a-alert
            v-if="article"
            type="info"
            :message="`文章来源：${article.title}（v${article.revision}）`"
            style="margin-bottom: 16px"
          />
          <p v-if="tweet" class="source-text">{{ tweet.text }}</p>
          <a-form-item label="本次任务要求 / 输入素材" required
            ><a-textarea
              v-model:value="input"
              :rows="7"
              :maxlength="30000"
              show-count
              placeholder="例如：根据以下素材生成文章，再从读者价值和准确性方面进行审核……"
          /></a-form-item>
          <p class="muted">
            每一步接收本次要求、来源内容和前序结果。运行调用已配置的 AI
            数据源；结果保留在运行记录中。
          </p>
          <a-button
            type="primary"
            :loading="busy"
            :disabled="!selected || !input.trim() || loading"
            @click="start"
            >开始运行</a-button
          >
        </a-form>
      </template>
      <template v-else>
        <a-space wrap style="margin-bottom: 12px"
          ><strong>{{ run.workflow_name }} · v{{ run.workflow_version }}</strong
          ><a-tag
            :color="run.status === 'failed' ? 'red' : run.status === 'succeeded' ? 'green' : 'blue'"
            >{{ runStatusLabel(run.status) }}</a-tag
          ><span class="muted">{{ run.config_snapshot.model }}</span
          ><a-button size="small" @click="refresh(run.id)">刷新</a-button></a-space
        >
        <a-alert v-if="run.error" type="warning" :message="run.error" style="margin-bottom: 12px" />
        <a-alert
          v-if="run.source_changed"
          type="warning"
          show-icon
          message="来源文章已修改或删除，这份结果仅针对运行时的文章版本。"
          style="margin-bottom: 12px"
        />
        <a-collapse style="margin-bottom: 20px"
          ><a-collapse-panel key="input" header="查看本次输入与来源快照">
            <pre>{{ run.input_text }}</pre>
            <pre v-if="run.source_snapshot">{{ JSON.stringify(run.source_snapshot, null, 2) }}</pre>
          </a-collapse-panel></a-collapse
        >
        <a-timeline>
          <a-timeline-item
            v-for="step in run.steps"
            :key="step.position"
            :color="
              step.status === 'succeeded' ? 'green' : step.status === 'failed' ? 'red' : 'blue'
            "
          >
            <strong>{{ step.position }}. {{ step.node_name }}</strong> ·
            {{ runStatusLabel(step.status) }}
            <div class="muted">
              {{ nodeTypeLabel(step.node_type) }} · 已尝试 {{ step.attempts }} 次
              {{ duration(step.started_at, step.completed_at) }} ·
              {{ formatDateTime(step.started_at) }}
            </div>
            <div v-if="step.error" class="error-text">{{ step.error }}</div>
            <div v-if="step.output" class="result">
              <template v-if="step.node_type === 'review'"
                ><a-space
                  ><strong>{{ step.output.score }} / 100</strong
                  ><a-tag :color="step.output.decision === 'pass' ? 'green' : 'orange'">{{
                    decisions[step.output.decision || '']
                  }}</a-tag></a-space
                >
                <p>{{ step.output.summary }}</p>
                <ul v-if="step.output.issues?.length">
                  <li v-for="(item, index) in step.output.issues" :key="index">{{ item }}</li>
                </ul>
                <strong v-if="step.output.suggestions?.length">修改建议</strong>
                <ul>
                  <li v-for="(item, index) in step.output.suggestions" :key="index">{{ item }}</li>
                </ul></template
              >
              <template v-else-if="step.node_type === 'article'"
                ><h3>{{ step.output.title }}</h3>
                <p v-if="step.output.excerpt" class="muted">{{ step.output.excerpt }}</p>
                <pre>{{ step.output.content }}</pre>
              </template>
              <pre v-else>{{ step.output.text }}</pre>
            </div>
            <a-collapse style="margin-top: 8px"
              ><a-collapse-panel key="references" header="查看运行时的节点提示词"
                ><div
                  v-for="skill in run.workflow_snapshot.steps[step.position - 1]?.skills"
                  :key="String(skill.id)"
                >
                  <strong>{{ skill.name }} · v{{ skill.version }}</strong>
                  <pre>{{ skill.instructions }}</pre>
                </div></a-collapse-panel
              ></a-collapse
            >
          </a-timeline-item>
        </a-timeline>
        <a-space wrap
          ><a-button
            v-if="run.status === 'succeeded' && hasArticle && !run.article_id"
            type="primary"
            :loading="busy"
            @click="action('save')"
            >保存新草稿到文章管理</a-button
          ><router-link v-if="run.article_id" to="/articles" @click="emit('update:open', false)"
            >前往文章管理 · #{{ run.article_id }}</router-link
          ><a-button v-if="run.status === 'failed'" :loading="busy" @click="action('retry')"
            >从失败节点重试</a-button
          ><a-button v-if="runActive(run.status)" danger :loading="busy" @click="action('cancel')"
            >取消运行</a-button
          ></a-space
        >
        <p class="muted" style="margin-top: 12px">
          流程完成代表节点执行完毕；是否通过审核请查看审核结论。发布操作在文章管理中完成。
        </p>
      </template>
    </a-spin>
  </a-modal>
</template>
<style scoped>
.chain {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 18px;
}
pre,
.source-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: inherit;
  max-height: 420px;
  overflow-y: auto;
}
.source-text {
  max-height: 150px;
}
.result {
  padding: 16px;
  margin-top: 10px;
  background: var(--surface-muted, #f6f7f9);
  border-radius: 8px;
}
.result p {
  white-space: pre-wrap;
}
.error-text {
  color: #b91c1c;
  margin: 8px 0;
}
</style>
