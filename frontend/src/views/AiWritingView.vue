<script setup lang="ts">
import { usePagedTable } from '@/composables/usePagedTable'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  DeleteOutlined,
  ReloadOutlined,
  SaveOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import { aiApi, aiDataSourceApi, aiListenTasksApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { skillOptionLabel } from '@/utils/skills'
import type {
  AiDataSourceStatus,
  AiJob,
  AiListenTask,
  AiSettings,
  AiSkill,
  EntityId,
  UpdateAiSettingsPayload,
} from '@/types'
import MetricCard from '@/components/MetricCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import AutoPublishStatus from '@/components/AutoPublishStatus.vue'
import AiListenTasks from '@/components/AiListenTasks.vue'
import { formatDateTime, formatDuration } from '@/utils/format'
const route = useRoute()
const normalizeTab = (value: unknown) => {
  if (value === 'data-source') return 'source'
  return typeof value === 'string' && ['tasks', 'jobs', 'settings', 'source'].includes(value)
    ? value
    : 'tasks'
}
const tab = ref(normalizeTab(route.query.tab))
const listenTasksPanel = ref<InstanceType<typeof AiListenTasks> | null>(null)
watch(
  () => route.query.tab,
  (value) => {
    tab.value = normalizeTab(value)
  },
)
const draftOpen = ref(false)
const draftSaving = ref(false)
const draftForm = reactive({ id: 0 as EntityId, title: '', content: '', excerpt: '', revision: 0 })
const loading = reactive({ settings: false, source: false })
const settings = ref<AiSettings | null>(null)
const defaultSkills = ref<AiSkill[]>([])
const defaultSkillsLoading = ref(false)
const source = ref<AiDataSourceStatus | null>(null)
const filters = reactive({
  status: 'all',
  trigger_type: 'all',
  listen_task_id: 'all' as EntityId | 'all',
})
const listenTaskOptions = ref<AiListenTask[]>([])
const attemptsOpen = ref(false)
const attemptsJob = ref<AiJob | null>(null)
const {
  rows: attempts,
  loading: attemptsLoading,
  pagination: attemptsPagination,
  reset: resetAttempts,
  change: changeAttempts,
} = usePagedTable((query) => aiApi.jobAttempts(attemptsJob.value!.id, query), '无法加载执行尝试')
const jobStatusOptions = [
  { label: '全部状态', value: 'all' },
  { label: '队列中', value: 'queued' },
  { label: '执行中', value: 'running' },
  { label: '已完成', value: 'succeeded' },
  { label: '失败', value: 'failed' },
  { label: '等待重试', value: 'retry_wait' },
  { label: '已取消', value: 'cancelled' },
]
const jobSourceOptions = [
  { label: '全部来源', value: 'all' },
  { label: '监听任务', value: 'listen_task' },
  { label: '手动生成', value: 'manual' },
  { label: '旧自动生成', value: 'legacy_auto' },
]
function jobSource(job: AiJob) {
  return job.trigger_type === 'listen_task' || job.listen_task_id
    ? `监听任务 · ${job.listen_task_name || listenTaskOptions.value.find((task) => String(task.id) === String(job.listen_task_id))?.name || `#${job.listen_task_id || '未知'}`}`
    : job.trigger_type === 'manual' || job.manual
      ? '手动生成'
      : '旧自动生成'
}
const sourceModels = ref<string[]>([])
const settingsForm = reactive<UpdateAiSettingsPayload>({
  enabled: false,
  auto_generate: true,
  prompt_template: '',
  language: 'zh-CN',
  tone: '专业自然',
  reasoning_effort: 'medium',
  default_skill_ids: [],
  max_attempts: 3,
  max_output_tokens: 2500,
  request_timeout_seconds: 60,
})
const sourceForm = reactive({
  name: 'OpenAI',
  base_url: 'https://api.openai.com/v1',
  model: 'gpt-5.6-terra',
  api_key: '',
})
const running = computed(
  () => jobs.value.filter((job) => ['queued', 'running', 'retry_wait'].includes(job.status)).length,
)
function applySettings(value: AiSettings) {
  settings.value = value
  Object.assign(settingsForm, {
    enabled: value.enabled,
    auto_generate: value.auto_generate,
    prompt_template: value.prompt_template || '',
    language: value.language,
    tone: value.tone,
    reasoning_effort: value.reasoning_effort,
    default_skill_ids: [...(value.default_skill_ids || [])],
    max_attempts: value.max_attempts,
    max_output_tokens: value.max_output_tokens,
    request_timeout_seconds: value.request_timeout_seconds,
  })
}
const {
  rows: jobs,
  total: jobsTotal,
  loading: jobsLoading,
  pagination: jobsPagination,
  load: loadJobs,
  reset: resetJobs,
  change: changeJobPage,
} = usePagedTable(
  (query) =>
    aiApi.jobs({
      ...query,
      status: filters.status === 'all' ? undefined : filters.status,
      trigger_type: filters.trigger_type === 'all' ? undefined : filters.trigger_type,
      listen_task_id: filters.listen_task_id === 'all' ? undefined : filters.listen_task_id,
    }),
  '无法加载 AI 任务',
)
async function loadSettings() {
  loading.settings = true
  try {
    applySettings(await aiApi.settings())
  } catch (e) {
    message.error(getErrorMessage(e, '无法加载 AI 设置'))
  } finally {
    loading.settings = false
  }
}
async function loadDefaultSkills() {
  defaultSkillsLoading.value = true
  try {
    const all: AiSkill[] = []
    let page = 1
    while (true) {
      const result = await aiApi.skillPage({ page, page_size: 100 })
      all.push(...result.items)
      if (all.length >= result.total || !result.items.length) break
      page++
    }
    defaultSkills.value = all
  } catch (e) {
    message.error(getErrorMessage(e, '无法加载默认 Skills'))
  } finally {
    defaultSkillsLoading.value = false
  }
}
watch(
  tab,
  (value) => {
    if (value === 'settings') void loadDefaultSkills()
  },
  { immediate: true },
)

async function loadSource() {
  loading.source = true
  try {
    source.value = await aiDataSourceApi.status()
    if (source.value.configured) {
      sourceForm.name = source.value.name || 'OpenAI'
      sourceForm.base_url = source.value.base_url || ''
      sourceForm.model = source.value.model || ''
    }
  } catch (e) {
    message.error(getErrorMessage(e, '无法加载 AI 数据源'))
  } finally {
    loading.source = false
  }
}
async function saveSource() {
  if (!sourceForm.name || !sourceForm.base_url || !sourceForm.model)
    return message.warning('请填写名称、Base URL 和模型')
  const apiKey = sourceForm.api_key.trim()
  if (!source.value?.configured && !apiKey) return message.warning('首次配置请填写 API Key')
  try {
    source.value = await aiDataSourceApi.save({
      ...sourceForm,
      api_key: apiKey || undefined,
      protocol: 'openai_responses',
    })
    sourceForm.api_key = ''
    message.success('AI 数据源已加密保存')
  } catch (e) {
    message.error(getErrorMessage(e, '保存失败'))
  }
}
async function testSource() {
  try {
    const result = await aiDataSourceApi.test()
    sourceModels.value = result.models
    result.valid ? message.success(result.message) : message.warning(result.message)
    await loadSource()
  } catch (e) {
    message.error(getErrorMessage(e, '测试失败'))
  }
}
async function saveSettings() {
  loading.settings = true
  try {
    applySettings(
      await aiApi.updateSettings({
        ...settingsForm,
        default_skill_ids: [...settingsForm.default_skill_ids],
      }),
    )
    message.success('AI 配置已保存')
  } catch (e) {
    message.error(getErrorMessage(e, '保存失败'))
  } finally {
    loading.settings = false
  }
}
async function retry(job: AiJob) {
  try {
    await aiApi.retryJob(job.id)
    await loadJobs()
    message.success('任务已重新排队')
  } catch (e) {
    message.error(getErrorMessage(e, '重试失败'))
  }
}
async function showAttempts(job: AiJob) {
  attemptsJob.value = job
  attemptsOpen.value = true
  await resetAttempts()
}
async function refreshJobContext() {
  await loadJobs()
  try {
    listenTaskOptions.value = (
      await aiListenTasksApi.list({ page: 1, page_size: 100, include_archived: true })
    ).items
  } catch {}
}
function refreshCurrentTab() {
  if (tab.value === 'tasks') void listenTasksPanel.value?.refresh()
  else if (tab.value === 'jobs') void loadJobs()
  else if (tab.value === 'settings') void loadSettings()
  else if (tab.value === 'source') void loadSource()
}
async function removeJob(job: AiJob) {
  Modal.confirm({
    title: '删除这条 AI 任务？',
    okType: 'danger',
    okText: '删除',
    cancelText: '取消',
    onOk: async () => {
      await aiApi.removeJob(job.id)
      await loadJobs()
      message.success('任务已删除')
    },
  })
}
function editDraft(job: AiJob) {
  if (!job.draft) return
  Object.assign(draftForm, {
    id: job.draft.id,
    title: job.draft.title,
    content: job.draft.content,
    excerpt: job.draft.excerpt || '',
    revision: job.draft.revision,
  })
  draftOpen.value = true
}
async function saveDraft() {
  draftSaving.value = true
  try {
    await aiApi.updateDraft(draftForm.id, {
      title: draftForm.title.trim(),
      content: draftForm.content.trim(),
      excerpt: draftForm.excerpt.trim(),
      revision: draftForm.revision,
    })
    draftOpen.value = false
    await loadJobs()
    message.success('草稿已保存')
  } catch (e) {
    message.error(getErrorMessage(e, '草稿保存失败'))
  } finally {
    draftSaving.value = false
  }
}
onMounted(async () => {
  await Promise.all([loadJobs(), loadSettings(), loadSource()])
  try {
    listenTaskOptions.value = (
      await aiListenTasksApi.list({ page: 1, page_size: 100, include_archived: true })
    ).items
  } catch {}
})
</script>

<template>
  <div class="page-stack">
    <PageHeader
      eyebrow="CONTENT / 01"
      title="AI 创作"
      description="把监听内容转化为可编辑、可发布的创作草稿"
      ><template #actions
        ><a-button @click="refreshCurrentTab"><ReloadOutlined /> 刷新</a-button></template
      ></PageHeader
    ><a-tabs v-model:activeKey="tab" class="workspace-tabs"
      ><a-tab-pane key="tasks" tab="监听任务"
        ><AiListenTasks
          ref="listenTasksPanel"
          @edit-draft="editDraft"
          @changed="refreshJobContext"
          @navigate="(value) => (tab = value)" /></a-tab-pane
      ><a-tab-pane key="jobs" tab="生成记录"
        ><div class="metric-grid">
          <MetricCard label="运行中" :value="running" detail="当前页队列 / 执行中" /><MetricCard
            label="草稿数"
            :value="jobs.filter((job) => job.draft).length"
            detail="当前页可继续编辑"
            accent="sage"
          />
        </div>
        <a-card :bordered="false" style="margin-top: 18px"
          ><div class="toolbar">
            <div class="toolbar__controls">
              <a-select
                v-model:value="filters.status"
                style="width: 150px"
                :options="jobStatusOptions"
                @change="resetJobs"
              /><a-select
                v-model:value="filters.trigger_type"
                style="width: 150px"
                :options="jobSourceOptions"
                @change="resetJobs"
              /><a-select
                v-model:value="filters.listen_task_id"
                style="width: 180px"
                :options="[
                  { label: '全部监听任务', value: 'all' },
                  ...listenTaskOptions.map((task) => ({ label: task.name, value: task.id })),
                ]"
                @change="resetJobs"
              /><a-button :loading="jobsLoading" @click="loadJobs"><ReloadOutlined /> 刷新</a-button
              ><a-button type="primary" @click="tab = 'settings'"
                ><ThunderboltOutlined /> 配置 AI</a-button
              >
            </div>
            <span class="toolbar__hint">共 {{ jobsTotal }} 条生成记录</span>
          </div>
          <a-table
            :data-source="jobs"
            :loading="jobsLoading"
            row-key="id"
            :pagination="jobsPagination"
            @change="changeJobPage"
            ><a-table-column title="源内容"
              ><template #default="{ record }"
                ><strong>@{{ record.source_username || '未知账号' }}</strong>
                <p
                  class="muted"
                  style="
                    max-width: 430px;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                  "
                >
                  {{ record.source_text || '—' }}
                </p></template
              ></a-table-column
            ><a-table-column title="任务来源"
              ><template #default="{ record }">{{ jobSource(record) }}</template></a-table-column
            ><a-table-column title="状态"
              ><template #default="{ record }"
                ><StatusPill :value="record.status" /></template></a-table-column
            ><a-table-column title="自动推送"
              ><template #default="{ record }"
                ><AutoPublishStatus
                  :dispatches="record.auto_publish_dispatches" /></template></a-table-column
            ><a-table-column title="草稿"
              ><template #default="{ record }">{{
                record.draft?.title || '尚未生成'
              }}</template></a-table-column
            ><a-table-column title="尝试"
              ><template #default="{ record }"
                >{{ record.attempts }}/{{ record.max_attempts }}
                <div class="muted">
                  累计 {{ record.lifetime_attempts ?? record.attempts }}
                </div></template
              ></a-table-column
            ><a-table-column title="操作"
              ><template #default="{ record }"
                ><a-space
                  ><a-button v-if="record.draft" type="link" @click="editDraft(record)"
                    >编辑草稿</a-button
                  ><a-button type="link" @click="showAttempts(record)">尝试明细</a-button
                  ><a-button v-if="record.status === 'failed'" type="link" @click="retry(record)"
                    >重试</a-button
                  ><a-button
                    v-if="!record.listen_task_id"
                    type="link"
                    danger
                    @click="removeJob(record)"
                    ><DeleteOutlined /></a-button></a-space></template></a-table-column></a-table></a-card></a-tab-pane
      ><a-tab-pane key="settings" tab="创作设置"
        ><a-card :bordered="false"
          ><a-alert
            v-if="settings?.auto_trigger_mode === 'legacy_all'"
            type="warning"
            show-icon
            message="当前仍使用旧自动生成兼容模式。首次启用监听任务时会提示切换；切换后自动触发范围由监听任务决定。"
            style="margin-bottom: 18px"
          /><a-form layout="vertical" style="max-width: 760px"
            ><a-form-item label="创作开关"
              ><a-switch v-model:checked="settingsForm.enabled" /><span
                class="muted"
                style="margin-left: 10px"
                >启用后允许创建生成任务</span
              ></a-form-item
            ><a-form-item label="自动触发总开关"
              ><a-switch
                v-model:checked="settingsForm.auto_generate"
                :disabled="!settingsForm.enabled"
              />
              <div class="muted">
                启用监听任务后，仅由任务配置决定哪些内容自动生成。
              </div></a-form-item
            ><a-form-item label="语言与语气"
              ><a-space
                ><a-input v-model:value="settingsForm.language" placeholder="语言" /><a-input
                  v-model:value="settingsForm.tone"
                  placeholder="语气" /></a-space></a-form-item
            ><a-form-item label="默认 Skills">
              <a-select
                v-model:value="settingsForm.default_skill_ids"
                mode="multiple"
                allow-clear
                option-filter-prop="label"
                :loading="defaultSkillsLoading"
                placeholder="选择自定义或 GitHub Skills"
                :options="
                  defaultSkills.map((skill) => ({
                    label: skillOptionLabel(skill),
                    value: skill.id,
                    disabled: !skill.is_active,
                  }))
                "
              />
              <div class="muted">
                未明确选择 Skills 且账号没有单独绑定时使用；监听任务使用任务中选择的 Skills。
              </div> </a-form-item
            ><a-form-item label="默认提示词"
              ><a-textarea v-model:value="settingsForm.prompt_template" :rows="5" /></a-form-item
            ><a-form-item label="推理强度"
              ><a-select
                v-model:value="settingsForm.reasoning_effort"
                :options="
                  ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'].map((value) => ({
                    label: value,
                    value,
                  }))
                "
                style="width: 220px" /></a-form-item
            ><a-form-item label="运行参数"
              ><a-space
                ><a-input-number
                  v-model:value="settingsForm.max_attempts"
                  addon-before="重试"
                  :min="1"
                  :max="10" /><a-input-number
                  v-model:value="settingsForm.max_output_tokens"
                  addon-before="Token"
                  :min="128"
                  :max="100000" /><a-input-number
                  v-model:value="settingsForm.request_timeout_seconds"
                  addon-before="超时"
                  :min="10"
                  :max="600" /></a-space></a-form-item
            ><a-button type="primary" :loading="loading.settings" @click="saveSettings"
              ><SaveOutlined /> 保存配置</a-button
            ></a-form
          ></a-card
        ></a-tab-pane
      ><a-tab-pane key="source" tab="AI 数据源"
        ><a-card :bordered="false"
          ><a-alert
            type="info"
            show-icon
            message="API Key 仅用于后端调用，保存后以密码占位符表示已配置，不显示真实密钥。"
          /><a-form layout="vertical" style="max-width: 700px; margin-top: 18px"
            ><a-form-item label="名称"><a-input v-model:value="sourceForm.name" /></a-form-item
            ><a-form-item label="Base URL"
              ><a-input v-model:value="sourceForm.base_url" /></a-form-item
            ><a-form-item label="模型"
              ><a-input v-model:value="sourceForm.model" />
              <div v-if="sourceModels.length" class="muted">
                可用模型：{{ sourceModels.join('、') }}
              </div></a-form-item
            ><a-form-item
              label="API Key"
              :extra="
                source?.configured
                  ? 'API Key 已保存，留空保留原密钥，填写新值后保存可替换。'
                  : '首次配置请填写 API Key。'
              "
              ><a-input-password
                v-model:value="sourceForm.api_key"
                :placeholder="
                  source?.configured ? '••••••••（已保存，输入新值可替换）' : '首次配置必填'
                " /></a-form-item
            ><a-space
              ><a-button type="primary" :loading="loading.source" @click="saveSource"
                >保存数据源</a-button
              ><a-button :loading="loading.source" @click="testSource">测试连接</a-button></a-space
            ></a-form
          ></a-card
        ></a-tab-pane
      ></a-tabs
    ><a-modal
      v-model:open="attemptsOpen"
      :title="`执行尝试 · 记录 #${attemptsJob?.id || ''}`"
      :footer="null"
      width="760px"
      ><a-table
        :data-source="attempts"
        :loading="attemptsLoading"
        row-key="id"
        :pagination="attemptsPagination"
        :scroll="{ x: 650 }"
        @change="changeAttempts"
        ><a-table-column title="开始时间"
          ><template #default="{ record }">{{
            formatDateTime(record.started_at)
          }}</template></a-table-column
        ><a-table-column title="轮次/序号"
          ><template #default="{ record }"
            >{{ record.round_number }} / {{ record.attempt_number }}</template
          ></a-table-column
        ><a-table-column title="结果" data-index="status" /><a-table-column title="耗时"
          ><template #default="{ record }">{{
            formatDuration(record.duration_ms)
          }}</template></a-table-column
        ><a-table-column title="模型" data-index="model_name" /><a-table-column title="错误"
          ><template #default="{ record }">{{
            record.error_summary || record.error_type || '—'
          }}</template></a-table-column
        ></a-table
      ></a-modal
    ><a-modal
      v-model:open="draftOpen"
      title="编辑生成草稿"
      ok-text="保存草稿"
      cancel-text="取消"
      width="760px"
      :confirm-loading="draftSaving"
      @ok="saveDraft"
      ><a-form layout="vertical"
        ><a-form-item label="标题"><a-input v-model:value="draftForm.title" /></a-form-item
        ><a-form-item label="摘要"
          ><a-textarea v-model:value="draftForm.excerpt" :rows="3" /></a-form-item
        ><a-form-item label="正文"
          ><a-textarea v-model:value="draftForm.content" :rows="14" /></a-form-item></a-form
    ></a-modal>
  </div>
</template>
