<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { aiApi, aiDataSourceApi, aiListenTasksApi, monitoredUsersApi } from '@/services/api'
import { usePagedTable } from '@/composables/usePagedTable'
import { getErrorMessage } from '@/services/http'
import { formatDateTime, formatDuration } from '@/utils/format'
import StatusPill from '@/components/StatusPill.vue'
import type {
  AiDataSourceStatus,
  AiJob,
  AiListenMode,
  AiListenTask,
  AiListenTaskBackfill,
  AiListenTaskPayload,
  AiListenTaskPreview,
  AiListenTaskStats,
  AiSettings,
  AiSkill,
  EntityId,
  MonitoredUser,
} from '@/types'

const emit = defineEmits<{
  editDraft: [job: AiJob]
  changed: []
  navigate: [tab: 'source' | 'settings']
}>()
const router = useRouter()
const users = ref<MonitoredUser[]>([])
const skills = ref<AiSkill[]>([])
const settings = ref<AiSettings | null>(null)
const source = ref<AiDataSourceStatus | null>(null)
const filters = reactive({
  search: '',
  desired_state: 'all',
  monitored_user_id: 'all' as EntityId | 'all',
})
const stateOptions = [
  { label: '全部状态', value: 'all' },
  { label: '已启用', value: 'enabled' },
  { label: '已暂停', value: 'paused' },
  { label: '已归档', value: 'archived' },
]
const modeOptions = [
  { label: '原创', value: 'original' },
  { label: '全部', value: 'all' },
  { label: '回复', value: 'reply' },
  { label: '转推（含引用）', value: 'retweet' },
]
const modeLabel = (value: string) =>
  modeOptions.find((item) => item.value === value)?.label || value
const summary = ref<AiListenTaskStats | null>(null)
const {
  rows: tasks,
  total,
  loading,
  pagination,
  load,
  reset,
  change,
} = usePagedTable(async (query) => {
  const result = await aiListenTasksApi.list({
    ...query,
    q: filters.search.trim() || undefined,
    state:
      filters.desired_state === 'all'
        ? undefined
        : (filters.desired_state as AiListenTask['desired_state']),
    monitored_user_id: filters.monitored_user_id === 'all' ? undefined : filters.monitored_user_id,
    include_archived: filters.desired_state === 'archived',
  })
  summary.value = result.summary
  return result
}, '无法加载监听任务')

const editorOpen = ref(false)
const editing = ref<AiListenTask | null>(null)
const saving = ref(false)
const previewing = ref(false)
const preview = ref<AiListenTaskPreview | null>(null)
let taskPreviewRequest = 0
const form = reactive<AiListenTaskPayload>({
  name: '',
  desired_state: 'enabled',
  all_monitored_users: false,
  monitored_user_ids: [],
  listen_mode: 'original',
  skill_ids: [],
  initial_sync_days: 0,
  max_attempts_override: null,
  language_override: null,
  tone_override: null,
  max_output_tokens_override: null,
})
const historyPreset = ref(0)
const historyOptions = [
  { label: '从启用时刻开始', value: 0 },
  { label: '最近 1 天', value: 1 },
  { label: '最近 3 天', value: 3 },
  { label: '最近 7 天', value: 7 },
  { label: '自定义天数', value: -1 },
]
function selectHistoryPreset(value: number) {
  historyPreset.value = value
  if (value !== -1) form.initial_sync_days = value
  else if ([0, 1, 3, 7].includes(form.initial_sync_days)) form.initial_sync_days = 14
}
watch(
  form,
  () => {
    taskPreviewRequest++
    preview.value = null
  },
  { deep: true },
)
const scopeSummary = computed(() =>
  form.all_monitored_users ? '全部监听账号' : `${form.monitored_user_ids.length} 个账号`,
)
const configSummary = computed(
  () =>
    `监听${scopeSummary.value}的${modeLabel(form.listen_mode)}内容，按顺序使用 ${form.skill_ids.length} 个 Skills，每条内容生成一篇文章草稿${form.initial_sync_days > 0 && !editing.value ? `；首次处理最近 ${form.initial_sync_days} 天已采集内容` : ''}。`,
)
const selectedAccounts = computed(() =>
  form.all_monitored_users
    ? users.value
    : users.value.filter((user) =>
        form.monitored_user_ids.some((id) => String(id) === String(user.id)),
      ),
)
const limitedAccounts = computed(() =>
  selectedAccounts.value.filter(
    (user) =>
      !user.is_active ||
      (form.listen_mode === 'reply' && !user.include_replies) ||
      (form.listen_mode === 'retweet' && !user.include_retweets),
  ),
)
function unavailableAccountLabel(item: AiListenTaskPreview['unavailable_accounts'][number]) {
  return typeof item === 'string'
    ? item
    : `@${item.username || item.id || '未知账号'}：${item.reason || '暂不可采集'}`
}

const detailOpen = ref(false)
const detail = ref<AiListenTask | null>(null)
const detailLoading = ref(false)
const detailTab = ref('records')
const recordStatus = ref('all')
const {
  rows: records,
  total: recordsTotal,
  loading: recordsLoading,
  pagination: recordsPagination,
  reset: resetRecords,
  change: changeRecords,
} = usePagedTable(
  (query) =>
    aiApi.jobs({
      ...query,
      listen_task_id: detail.value?.id,
      status: recordStatus.value === 'all' ? undefined : recordStatus.value,
    }),
  '无法加载生成记录',
)
const {
  rows: events,
  loading: eventsLoading,
  pagination: eventsPagination,
  reset: resetEvents,
  change: changeEvents,
} = usePagedTable((query) => aiListenTasksApi.events(detail.value!.id, query), '无法加载任务日志')
const {
  rows: backfills,
  loading: backfillsLoading,
  pagination: backfillsPagination,
  reset: resetBackfills,
  change: changeBackfills,
} = usePagedTable(
  (query) => aiListenTasksApi.backfills(detail.value!.id, query),
  '无法加载历史回溯进度',
)
const attemptsOpen = ref(false)
const attemptsJob = ref<AiJob | null>(null)
const {
  rows: attempts,
  loading: attemptsLoading,
  pagination: attemptsPagination,
  reset: resetAttempts,
  change: changeAttempts,
} = usePagedTable((query) => aiApi.jobAttempts(attemptsJob.value!.id, query), '无法加载执行尝试')
const backfillOpen = ref(false)
const backfillPreview = ref<AiListenTaskPreview | null>(null)
const backfillLoading = ref(false)
const backfillForm = reactive({ from_at: '', to_at: '' })
let backfillPreviewRequest = 0
watch(backfillForm, () => {
  backfillPreviewRequest++
  backfillPreview.value = null
})

async function loadOptions() {
  try {
    const [config, dataSource] = await Promise.all([aiApi.settings(), aiDataSourceApi.status()])
    settings.value = config
    source.value = dataSource
    const accountRows: MonitoredUser[] = []
    const skillRows: AiSkill[] = []
    for (let page = 1; page <= 100; page++) {
      const result = await monitoredUsersApi.list({ page, page_size: 100 })
      accountRows.push(...result.items)
      if (accountRows.length >= result.total || !result.items.length) break
    }
    for (let page = 1; page <= 100; page++) {
      const result = await aiApi.skillPage({ page, page_size: 100 })
      skillRows.push(...result.items)
      if (skillRows.length >= result.total || !result.items.length) break
    }
    users.value = accountRows
    skills.value = skillRows
  } catch (error) {
    message.error(getErrorMessage(error, '无法加载账号、Skills 或 AI 配置'))
  }
}
async function refreshTasks() {
  await Promise.all([load(), loadOptions()])
}
defineExpose({ refresh: refreshTasks })

function setForm(task?: AiListenTask) {
  taskPreviewRequest++
  Object.assign(form, {
    name: task?.name || '',
    desired_state: task?.desired_state === 'paused' ? 'paused' : 'enabled',
    all_monitored_users: task?.all_monitored_users || false,
    monitored_user_ids: [...(task?.monitored_user_ids || [])],
    listen_mode: (task?.listen_mode || 'original') as AiListenMode,
    skill_ids: [...(task?.skill_ids || [])],
    initial_sync_days: task?.initial_sync_days || 0,
    max_attempts_override: task?.max_attempts_override ?? null,
    language_override: task?.language_override ?? null,
    tone_override: task?.tone_override ?? null,
    max_output_tokens_override: task?.max_output_tokens_override ?? null,
  })
  preview.value = null
  historyPreset.value = [0, 1, 3, 7].includes(task?.initial_sync_days || 0)
    ? task?.initial_sync_days || 0
    : -1
  editing.value = task || null
  editorOpen.value = true
}

async function edit(task: AiListenTask) {
  try {
    setForm(await aiListenTasksApi.detail(task.id))
  } catch (error) {
    message.error(getErrorMessage(error, '无法读取任务配置'))
  }
}

function duplicate(task: AiListenTask) {
  setForm(task)
  form.name = `${task.name}（副本）`
  form.desired_state = 'paused'
  editing.value = null
}

function payload(): AiListenTaskPayload {
  return {
    name: form.name.trim(),
    desired_state: form.desired_state,
    all_monitored_users: form.all_monitored_users,
    monitored_user_ids: form.all_monitored_users ? [] : [...form.monitored_user_ids],
    listen_mode: form.listen_mode,
    skill_ids: [...form.skill_ids],
    initial_sync_days: form.initial_sync_days,
    max_attempts_override: form.max_attempts_override || null,
    language_override: form.language_override?.trim() || null,
    tone_override: form.tone_override?.trim() || null,
    max_output_tokens_override: form.max_output_tokens_override || null,
  }
}

function validate() {
  if (!form.name.trim()) return '请填写任务名称'
  if (!form.all_monitored_users && !form.monitored_user_ids.length) return '请选择至少一个监听账号'
  if (!form.skill_ids.length || form.skill_ids.length > 20) return '请选择 1–20 个 Skills'
  if (form.initial_sync_days == null || form.initial_sync_days < 0 || form.initial_sync_days > 365)
    return '首次历史范围需为 0–365 天'
  const inactive = form.skill_ids.filter(
    (id) => !skills.value.find((skill) => String(skill.id) === String(id) && skill.is_active),
  )
  if (inactive.length) return '所选 Skill 已停用或删除，请重新选择'
  return ''
}

async function previewTask() {
  const error = validate()
  if (error) return message.warning(error)
  previewing.value = true
  const request = ++taskPreviewRequest
  try {
    const estimate = await aiListenTasksApi.preview({
      ...payload(),
      task_id: editing.value?.id,
      exclude_legacy_generated: true,
    })
    if (request === taskPreviewRequest) preview.value = estimate
  } catch (err) {
    message.error(getErrorMessage(err, '无法预估匹配内容'))
  } finally {
    previewing.value = false
  }
}

async function save(state?: 'enabled' | 'paused') {
  const error = validate()
  if (error) return message.warning(error)
  if (!editing.value && state === 'enabled' && form.initial_sync_days > 0 && !preview.value)
    return message.warning('先预估历史范围，再保存并启用')
  saving.value = true
  try {
    const data = payload()
    if (!editing.value && state) data.desired_state = state
    if (
      !editing.value &&
      data.desired_state === 'enabled' &&
      settings.value?.auto_trigger_mode === 'legacy_all'
    ) {
      if (!(await confirmLegacySwitch())) return
      data.switch_from_legacy = true
    }
    if (editing.value)
      await aiListenTasksApi.update(editing.value.id, {
        name: data.name,
        all_monitored_users: data.all_monitored_users,
        monitored_user_ids: data.monitored_user_ids,
        listen_mode: data.listen_mode,
        skill_ids: data.skill_ids,
        max_attempts_override: data.max_attempts_override,
        language_override: data.language_override,
        tone_override: data.tone_override,
        max_output_tokens_override: data.max_output_tokens_override,
        config_version: editing.value.config_version,
      })
    else await aiListenTasksApi.create(data)
    editorOpen.value = false
    await load()
    emit('changed')
    await loadOptions()
    if (detail.value && editing.value?.id === detail.value.id) await refreshDetail()
    message.success(
      editing.value
        ? '任务修改已保存'
        : state === 'paused'
          ? '任务已保存为暂停'
          : '任务已创建并启用',
    )
  } catch (err) {
    message.error(getErrorMessage(err, '保存监听任务失败'))
  } finally {
    saving.value = false
  }
}

async function refreshDetail() {
  if (!detail.value) return
  detailLoading.value = true
  try {
    detail.value = await aiListenTasksApi.detail(detail.value.id)
  } catch (error) {
    message.error(getErrorMessage(error, '无法刷新任务详情'))
  } finally {
    detailLoading.value = false
  }
  await Promise.all([resetRecords(), resetEvents(), resetBackfills()])
}

async function openDetail(task: AiListenTask) {
  detail.value = task
  detailOpen.value = true
  detailTab.value = 'records'
  recordStatus.value = 'all'
  await refreshDetail()
}

async function changeState(task: AiListenTask, action: 'pause' | 'resume' | 'archive') {
  try {
    if (action === 'pause') await aiListenTasksApi.pause(task.id)
    else if (action === 'resume') {
      const switchFromLegacy = settings.value?.auto_trigger_mode === 'legacy_all'
      if (!task.activated_at && task.initial_sync_days > 0) {
        const estimate = await aiListenTasksApi.preview({
          ...payloadFromTask(task),
          task_id: task.id,
          exclude_legacy_generated: true,
        })
        if (!(await confirmFirstActivation(estimate))) return
      }
      if (switchFromLegacy && !(await confirmLegacySwitch())) return
      await aiListenTasksApi.resume(task.id, switchFromLegacy)
    } else await aiListenTasksApi.archive(task.id)
    await load()
    emit('changed')
    if (action === 'resume') await loadOptions()
    if (detail.value?.id === task.id) await refreshDetail()
    message.success(
      action === 'pause' ? '任务已暂停' : action === 'resume' ? '任务已恢复' : '任务已归档',
    )
  } catch (error) {
    message.error(getErrorMessage(error, '操作失败'))
  }
}

function confirmLegacySwitch(): Promise<boolean> {
  return new Promise((resolve) => {
    Modal.confirm({
      title: '切换为监听任务自动生成？',
      content:
        '切换后，新采集内容只按已启用的监听任务入队；旧自动生成不再为所有内容无条件建队列。已有待处理记录仍按原快照执行，历史回溯默认排除旧流程已生成的内容。',
      okText: '确认切换并启用',
      cancelText: '返回修改',
      onOk: () => resolve(true),
      onCancel: () => resolve(false),
    })
  })
}

function confirmFirstActivation(estimate: AiListenTaskPreview): Promise<boolean> {
  return new Promise((resolve) => {
    Modal.confirm({
      title: '确认首次历史回溯',
      content: `已采集内容中匹配 ${estimate.matched} 条，排除重复与旧流程记录后预计待生成 ${estimate.pending} 条。启用后将异步入队。`,
      okText: '确认并启用',
      cancelText: '取消',
      onOk: () => resolve(true),
      onCancel: () => resolve(false),
    })
  })
}

function confirmArchive(task: AiListenTask) {
  Modal.confirm({
    title: `归档「${task.name}」？`,
    content: '归档会停止新内容匹配，并取消尚未领取的记录。已有历史会保留。',
    okText: '归档',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => changeState(task, 'archive'),
  })
}

async function decideQueue(decision: 'continue_old_snapshot' | 'cancel_old_queue') {
  if (!detail.value) return
  try {
    await aiListenTasksApi.queueDecision(detail.value.id, decision)
    await refreshDetail()
    await load()
    message.success(decision === 'continue_old_snapshot' ? '旧队列已恢复' : '旧队列已取消')
  } catch (error) {
    message.error(getErrorMessage(error, '处理旧队列失败'))
  }
}

async function showAttempts(job: AiJob) {
  attemptsJob.value = job
  attemptsOpen.value = true
  await resetAttempts()
}

async function retry(job: AiJob) {
  try {
    await aiApi.retryJob(job.id)
    await resetRecords()
    await refreshDetail()
    message.success('记录已重新排队')
  } catch (error) {
    message.error(getErrorMessage(error, '重试失败'))
  }
}
function editRecordDraft(job: AiJob) {
  detailOpen.value = false
  emit('editDraft', job)
}
function openArticle(job: AiJob) {
  if (!job.draft) return
  detailOpen.value = false
  void router.push('/articles')
}
function navigateToSource() {
  editorOpen.value = false
  emit('navigate', 'source')
}

function openBackfill() {
  backfillPreviewRequest++
  backfillForm.from_at = ''
  backfillForm.to_at = ''
  backfillPreview.value = null
  backfillOpen.value = true
}

function backfillRange() {
  const from = new Date(backfillForm.from_at)
  const to = new Date(backfillForm.to_at)
  if (
    !backfillForm.from_at ||
    !backfillForm.to_at ||
    Number.isNaN(from.getTime()) ||
    Number.isNaN(to.getTime()) ||
    from >= to
  ) {
    message.warning('请选择有效的历史起止时间')
    return null
  }
  return { from_at: from.toISOString(), to_at: to.toISOString() }
}

async function previewBackfill() {
  if (!detail.value) return
  const range = backfillRange()
  if (!range) return
  backfillLoading.value = true
  const request = ++backfillPreviewRequest
  try {
    const estimate = await aiListenTasksApi.preview({
      ...payloadFromTask(detail.value),
      task_id: detail.value.id,
      ...range,
      exclude_legacy_generated: true,
    })
    if (request === backfillPreviewRequest) backfillPreview.value = estimate
  } catch (error) {
    message.error(getErrorMessage(error, '无法预估历史内容'))
  } finally {
    backfillLoading.value = false
  }
}

function payloadFromTask(task: AiListenTask): AiListenTaskPayload {
  return {
    name: task.name,
    desired_state: task.desired_state === 'paused' ? 'paused' : 'enabled',
    all_monitored_users: task.all_monitored_users,
    monitored_user_ids: [...task.monitored_user_ids],
    listen_mode: task.listen_mode,
    skill_ids: [...task.skill_ids],
    initial_sync_days: task.initial_sync_days,
    max_attempts_override: task.max_attempts_override,
    language_override: task.language_override,
    tone_override: task.tone_override,
    max_output_tokens_override: task.max_output_tokens_override,
  }
}

async function submitBackfill() {
  if (!detail.value || !backfillPreview.value) return
  const range = backfillRange()
  if (!range) return
  backfillLoading.value = true
  try {
    await aiListenTasksApi.backfill(detail.value.id, { ...range, exclude_legacy_generated: true })
    backfillOpen.value = false
    await refreshDetail()
    message.success('历史补生成请求已提交')
  } catch (error) {
    message.error(getErrorMessage(error, '补生成提交失败'))
  } finally {
    backfillLoading.value = false
  }
}

function taskAccounts(task: AiListenTask) {
  if (task.all_monitored_users) return '全部监听账号'
  const names =
    task.accounts?.map((account) => `@${account.username}`) ||
    task.monitored_user_ids.map(
      (id) => `@${users.value.find((user) => String(user.id) === String(id))?.username || id}`,
    )
  return names.join('、') || '暂无账号'
}
function taskSkills(task: AiListenTask) {
  return (
    (
      task.skills?.map((skill) => skill.name) ||
      task.skill_ids.map(
        (id) => skills.value.find((skill) => String(skill.id) === String(id))?.name || `#${id}`,
      )
    ).join(' → ') || '暂无'
  )
}
function stateLabel(task: AiListenTask) {
  return task.desired_state === 'enabled'
    ? '已启用'
    : task.desired_state === 'paused'
      ? '已暂停'
      : '已归档'
}
function lastAccountPoll(task: AiListenTask) {
  const dates = (task.accounts || [])
    .map((account) => account.last_polled_at)
    .filter((value): value is string => Boolean(value))
    .sort()
  return dates.length ? dates[dates.length - 1] : null
}
function backfillStatus(backfill: AiListenTaskBackfill) {
  return (
    (
      {
        pending: '等待扫描',
        running: '扫描中',
        completed: '已完成',
        failed: '失败',
        cancelled: '已取消',
      } as Record<string, string>
    )[backfill.status] || backfill.status
  )
}

onMounted(async () => {
  await refreshTasks()
})
</script>

<template>
  <div class="listen-workspace">
    <a-card :bordered="false">
      <div class="toolbar">
        <div class="toolbar__controls">
          <a-button type="primary" @click="setForm()">新增任务</a-button>
          <a-button :loading="loading" @click="refreshTasks">刷新</a-button>
          <a-input-search
            v-model:value="filters.search"
            placeholder="搜索任务名称"
            style="width: 200px"
            @search="reset"
          />
          <a-select
            v-model:value="filters.desired_state"
            :options="stateOptions"
            style="width: 140px"
            @change="reset"
          />
          <a-select
            v-model:value="filters.monitored_user_id"
            style="width: 180px"
            :options="[
              { label: '全部账号', value: 'all' },
              ...users.map((user) => ({ label: `@${user.username}`, value: user.id })),
            ]"
            @change="reset"
          />
        </div>
        <span class="toolbar__hint">共 {{ total }} 条监听任务</span>
      </div>
      <a-space wrap style="margin: 4px 0 18px">
        <StatusPill
          :value="settings?.enabled && settings?.auto_generate"
          :label="
            settings?.enabled && settings?.auto_generate ? 'AI 自动生成已开启' : 'AI 自动生成已关闭'
          "
        />
        <StatusPill
          :value="source?.configured"
          :label="source?.configured ? `数据源：${source.name} / ${source.model}` : '数据源未配置'"
        />
        <StatusPill
          :value="settings?.worker_status === 'online' || settings?.worker_status === 'healthy'"
          :label="`AI Worker：${settings?.worker_status || '未知'}`"
        />
        <StatusPill
          v-if="settings?.auto_trigger_mode === 'legacy_all'"
          value="warning"
          label="旧自动生成兼容模式"
        />
      </a-space>
      <div v-if="summary" class="summary-strip">
        当前筛选全部任务：匹配 {{ summary.matched }} · 排队 {{ summary.queued }} · 执行中
        {{ summary.running }} · 等待重试 {{ summary.retry_wait }} · 成功 {{ summary.succeeded }} ·
        最终失败 {{ summary.failed }} · 已取消 {{ summary.cancelled }} · 累计尝试
        {{ summary.lifetime_attempts }}
      </div>
      <a-table
        :data-source="tasks"
        :loading="loading"
        row-key="id"
        :pagination="pagination"
        :scroll="{ x: 1180 }"
        @change="change"
      >
        <a-table-column title="任务名称" :width="200">
          <template #default="{ record }"
            ><a-button type="link" style="padding-left: 0" @click="openDetail(record)">{{
              record.name
            }}</a-button>
            <div class="muted">
              最近命中：{{ formatDateTime(record.last_matched_at) }}
            </div></template
          >
        </a-table-column>
        <a-table-column title="监听范围" :width="190"
          ><template #default="{ record }"
            ><a-tooltip :title="taskAccounts(record)"
              ><span class="one-line">{{ taskAccounts(record) }}</span></a-tooltip
            >
            <div class="muted">{{ modeLabel(record.listen_mode) }}</div></template
          ></a-table-column
        >
        <a-table-column title="Skills" :width="170"
          ><template #default="{ record }"
            ><a-tooltip :title="taskSkills(record)"
              ><span class="one-line">{{ taskSkills(record) }}</span></a-tooltip
            ></template
          ></a-table-column
        >
        <a-table-column title="状态" :width="230"
          ><template #default="{ record }"
            ><StatusPill
              :value="record.desired_state === 'enabled' ? 'active' : record.desired_state"
              :label="stateLabel(record)"
            />
            <div class="muted">
              {{
                [
                  ...(record.health?.reasons || []),
                  ...(record.dependency?.reasons || []),
                  record.queue_hold_reason,
                ]
                  .filter(Boolean)
                  .join('；') ||
                (record.desired_state === 'enabled' &&
                !(record.stats?.queued || record.stats?.running || record.stats?.retry_wait)
                  ? '等待新内容'
                  : '—')
              }}
            </div></template
          ></a-table-column
        >
        <a-table-column title="执行情况" :width="160"
          ><template #default="{ record }"
            >匹配 {{ record.stats?.matched || 0 }}
            <div class="muted">
              排队 {{ record.stats?.queued || 0 }} · 执行 {{ record.stats?.running || 0 }} · 重试
              {{ record.stats?.retry_wait || 0 }}
            </div></template
          ></a-table-column
        >
        <a-table-column title="结果" :width="160"
          ><template #default="{ record }"
            >成功 {{ record.stats?.succeeded || 0 }} · 失败 {{ record.stats?.failed || 0 }}
            <div class="muted">
              取消 {{ record.stats?.cancelled || 0 }} · 累计尝试
              {{ record.stats?.lifetime_attempts || 0 }}
            </div></template
          ></a-table-column
        >
        <a-table-column title="操作" :width="220"
          ><template #default="{ record }"
            ><a-space wrap
              ><a-button type="link" @click="openDetail(record)">详情</a-button
              ><a-button
                v-if="record.desired_state !== 'archived'"
                type="link"
                @click="edit(record)"
                >编辑</a-button
              ><a-button
                v-if="record.desired_state === 'enabled'"
                type="link"
                @click="changeState(record, 'pause')"
                >暂停</a-button
              ><a-button
                v-if="record.desired_state === 'paused'"
                type="link"
                @click="changeState(record, 'resume')"
                >恢复</a-button
              ><a-button type="link" @click="duplicate(record)">复制</a-button
              ><a-button
                v-if="record.desired_state !== 'archived'"
                type="link"
                danger
                @click="confirmArchive(record)"
                >归档</a-button
              ></a-space
            ></template
          ></a-table-column
        >
        <template #emptyText
          ><a-empty
            description="暂无监听任务。选择账号、配置 AI 数据源和 Skills 后，创建第一条任务。"
        /></template>
      </a-table>
    </a-card>

    <a-drawer
      v-model:open="editorOpen"
      :title="editing ? `编辑监听任务 · ${editing.name}` : '新增监听任务'"
      width="min(640px, 100vw)"
      :body-style="{ paddingBottom: '96px' }"
      destroy-on-close
    >
      <a-form layout="vertical">
        <a-form-item label="任务名称" required
          ><a-input v-model:value="form.name" :maxlength="100" placeholder="例如：AI 产品动态分析"
        /></a-form-item>
        <a-form-item label="监听范围" required
          ><a-radio-group v-model:value="form.all_monitored_users"
            ><a-radio :value="false">指定账号</a-radio
            ><a-radio :value="true">全部监听账号</a-radio></a-radio-group
          ><a-select
            v-if="!form.all_monitored_users"
            v-model:value="form.monitored_user_ids"
            mode="multiple"
            style="width: 100%; margin-top: 10px"
            placeholder="选择监听账号"
            :options="
              users.map((user) => ({
                label: `@${user.username}${user.is_active ? '' : '（已暂停采集）'}`,
                value: user.id,
              }))
            "
          />
          <div class="muted">
            任务只使用账号已采集的内容；全部账号会覆盖以后新增的监听账号。
          </div></a-form-item
        >
        <a-form-item label="监听模式"
          ><a-select v-model:value="form.listen_mode" :options="modeOptions" />
          <div class="muted">回复包括串文，转推包括引用；采集范围由账号设置决定。</div></a-form-item
        >
        <a-alert
          v-if="limitedAccounts.length"
          type="warning"
          show-icon
          style="margin-bottom: 18px"
          :message="`${limitedAccounts.length} 个账号当前未采集所选类型内容，任务不会修改账号采集选项。`"
          ><template #description
            ><router-link to="/accounts">前往账号设置</router-link></template
          ></a-alert
        >
        <a-form-item label="生成 Skills" required
          ><a-select
            v-model:value="form.skill_ids"
            mode="multiple"
            placeholder="请选择 1–20 个已启用 Skills"
            :options="
              skills.map((skill) => ({
                label: `${skill.name}${skill.is_active ? '' : '（已停用）'}`,
                value: skill.id,
                disabled: !skill.is_active,
              }))
            "
          />
          <div v-if="form.skill_ids.length" class="skill-order">
            <div v-for="(id, index) in form.skill_ids" :key="id">
              <span
                >{{ index + 1 }}.
                {{
                  skills.find((skill) => String(skill.id) === String(id))?.name || `#${id}`
                }}</span
              ><a-button
                type="link"
                size="small"
                :disabled="index === 0"
                @click="
                  [form.skill_ids[index - 1], form.skill_ids[index]] = [
                    form.skill_ids[index]!,
                    form.skill_ids[index - 1]!,
                  ]
                "
                >上移</a-button
              ><a-button
                type="link"
                size="small"
                :disabled="index === form.skill_ids.length - 1"
                @click="
                  [form.skill_ids[index + 1], form.skill_ids[index]] = [
                    form.skill_ids[index]!,
                    form.skill_ids[index + 1]!,
                  ]
                "
                >下移</a-button
              >
            </div>
          </div>
          <div class="muted">按上方顺序应用，已有记录保留创建时的 Skill 快照。</div></a-form-item
        >
        <a-alert
          type="info"
          show-icon
          :message="
            source?.configured
              ? `当前 AI 数据源：${source.name} · ${source.model}`
              : '尚未配置 AI 数据源；任务可以先保存为暂停。'
          "
        />
        <a-button type="link" style="padding-left: 0" @click="navigateToSource"
          >查看 AI 数据源设置</a-button
        >
        <a-form-item v-if="!editing" label="首次历史范围" style="margin-top: 20px"
          ><a-select
            :value="historyPreset"
            :options="historyOptions"
            @change="selectHistoryPreset"
          />
          <a-input-number
            v-if="historyPreset === -1"
            v-model:value="form.initial_sync_days"
            :min="1"
            :max="365"
            style="margin-top: 10px; width: 160px"
            addon-after="天"
          />
          <div class="muted">历史范围只扫描已采集内容；保存并启用后才会入队。</div></a-form-item
        >
        <a-collapse ghost
          ><a-collapse-panel key="advanced" header="高级配置"
            ><a-form-item label="最大尝试次数"
              ><a-input-number
                v-model:value="form.max_attempts_override"
                :min="1"
                :max="10"
                placeholder="继承创作设置" /></a-form-item
            ><a-form-item label="语言"
              ><a-input
                v-model:value="form.language_override"
                placeholder="继承创作设置" /></a-form-item
            ><a-form-item label="语气"
              ><a-input
                v-model:value="form.tone_override"
                placeholder="继承创作设置" /></a-form-item
            ><a-form-item label="输出 Token 上限"
              ><a-input-number
                v-model:value="form.max_output_tokens_override"
                :min="128"
                :max="100000"
                placeholder="继承创作设置" /></a-form-item></a-collapse-panel
        ></a-collapse>
        <a-alert type="info" show-icon :message="configSummary" style="margin: 18px 0" />
        <a-space
          ><a-button :loading="previewing" @click="previewTask">预估匹配内容</a-button
          ><span v-if="preview"
            >匹配 {{ preview.matched }} · 重复 {{ preview.duplicates }} · 旧流程已生成
            {{ preview.legacy_generated }} · 待生成 {{ preview.pending }}</span
          ></a-space
        >
        <a-alert
          v-if="preview?.unavailable_accounts?.length"
          type="warning"
          show-icon
          style="margin-top: 10px"
          :message="`有 ${preview.unavailable_accounts.length} 个账号当前不可采集`"
          :description="preview.unavailable_accounts.map(unavailableAccountLabel).join('；')"
        />
      </a-form>
      <template #footer
        ><a-space
          ><a-button @click="editorOpen = false">取消</a-button
          ><a-button v-if="!editing" :loading="saving" @click="save('paused')">保存为暂停</a-button
          ><a-button
            type="primary"
            :loading="saving"
            @click="save(editing ? undefined : 'enabled')"
            >{{ editing ? '保存修改' : '保存并启用' }}</a-button
          ></a-space
        ></template
      >
    </a-drawer>

    <a-drawer
      v-model:open="detailOpen"
      :title="detail?.name || '监听任务详情'"
      width="min(900px, 100vw)"
      destroy-on-close
    >
      <template v-if="detail">
        <a-space wrap style="margin-bottom: 16px"
          ><StatusPill
            :value="detail.desired_state === 'enabled' ? 'active' : detail.desired_state"
            :label="stateLabel(detail)"
          /><a-button :loading="detailLoading" @click="refreshDetail">刷新</a-button
          ><a-button v-if="detail.desired_state !== 'archived'" @click="edit(detail)">编辑</a-button
          ><a-button v-if="detail.desired_state === 'enabled'" @click="changeState(detail, 'pause')"
            >暂停</a-button
          ><a-button v-if="detail.desired_state === 'paused'" @click="changeState(detail, 'resume')"
            >恢复</a-button
          ><a-button v-if="detail.desired_state !== 'archived'" @click="openBackfill"
            >补生成历史</a-button
          ></a-space
        >
        <a-alert
          v-if="detail.health?.reasons?.length || detail.dependency?.reasons?.length"
          type="warning"
          show-icon
          :message="
            [...(detail.health?.reasons || []), ...(detail.dependency?.reasons || [])].join('；')
          "
          style="margin-bottom: 14px"
        />
        <a-alert
          v-if="detail.queue_hold_reason"
          type="warning"
          show-icon
          message="所选 Skill 曾失效，旧队列已挂起。请决定如何处理入队时的旧配置快照。"
          style="margin-bottom: 14px"
          ><template #description
            ><a-space
              ><a-button size="small" @click="decideQueue('continue_old_snapshot')"
                >按旧快照继续</a-button
              ><a-button size="small" danger @click="decideQueue('cancel_old_queue')"
                >取消旧队列</a-button
              ></a-space
            ></template
          ></a-alert
        >
        <a-descriptions bordered size="small" :column="2"
          ><a-descriptions-item label="监听范围">{{ taskAccounts(detail) }}</a-descriptions-item
          ><a-descriptions-item label="模式">{{
            modeLabel(detail.listen_mode)
          }}</a-descriptions-item
          ><a-descriptions-item label="Skills" :span="2">{{
            taskSkills(detail)
          }}</a-descriptions-item
          ><a-descriptions-item label="配置版本">{{ detail.config_version }}</a-descriptions-item
          ><a-descriptions-item label="首次启用">{{
            formatDateTime(detail.activated_at)
          }}</a-descriptions-item
          ><a-descriptions-item label="数据源" :span="2"
            >{{
              detail.data_source_name
                ? `${detail.data_source_name} · ${detail.data_source_model || '未指定模型'}`
                : '未配置'
            }}<span v-if="detail.data_source_verified_at" class="muted">
              · 上次连接测试 {{ formatDateTime(detail.data_source_verified_at) }}（{{
                detail.data_source_verification_status || '未知'
              }}）</span
            ></a-descriptions-item
          ></a-descriptions
        >
        <div class="detail-stats">
          <div>
            已匹配<strong>{{ detail.stats?.matched || 0 }}</strong>
          </div>
          <div>
            成功<strong>{{ detail.stats?.succeeded || 0 }}</strong>
          </div>
          <div>
            最终失败<strong>{{ detail.stats?.failed || 0 }}</strong>
          </div>
          <div>
            已取消<strong>{{ detail.stats?.cancelled || 0 }}</strong>
          </div>
          <div>
            排队<strong>{{ detail.stats?.queued || 0 }}</strong>
          </div>
          <div>
            执行中<strong>{{ detail.stats?.running || 0 }}</strong>
          </div>
          <div>
            等待重试<strong>{{ detail.stats?.retry_wait || 0 }}</strong>
          </div>
          <div>
            累计尝试<strong>{{ detail.stats?.lifetime_attempts || 0 }}</strong>
          </div>
        </div>
        <a-descriptions size="small" :column="3"
          ><a-descriptions-item label="最近命中">{{
            formatDateTime(detail.last_matched_at)
          }}</a-descriptions-item
          ><a-descriptions-item label="最近成功">{{
            formatDateTime(detail.last_success_at)
          }}</a-descriptions-item
          ><a-descriptions-item label="最近失败">{{
            formatDateTime(detail.last_failure_at)
          }}</a-descriptions-item
          ><a-descriptions-item label="AI 最近执行">{{
            formatDateTime(detail.last_ai_started_at)
          }}</a-descriptions-item
          ><a-descriptions-item label="账号最后采集">{{
            formatDateTime(lastAccountPoll(detail))
          }}</a-descriptions-item></a-descriptions
        >
        <a-tabs v-model:activeKey="detailTab"
          ><a-tab-pane key="records" tab="生成记录"
            ><div class="toolbar">
              <a-select
                v-model:value="recordStatus"
                style="width: 150px"
                :options="[
                  { label: '全部状态', value: 'all' },
                  { label: '排队中', value: 'queued' },
                  { label: '执行中', value: 'running' },
                  { label: '等待重试', value: 'retry_wait' },
                  { label: '成功', value: 'succeeded' },
                  { label: '失败', value: 'failed' },
                  { label: '已取消', value: 'cancelled' },
                ]"
                @change="resetRecords"
              /><span class="muted">共 {{ recordsTotal }} 条</span>
            </div>
            <a-table
              :data-source="records"
              :loading="recordsLoading"
              row-key="id"
              :pagination="recordsPagination"
              :scroll="{ x: 700 }"
              @change="changeRecords"
              ><a-table-column title="源内容" :width="300"
                ><template #default="{ record }"
                  ><strong>@{{ record.source_username || '未知账号' }}</strong>
                  <div class="one-line">{{ record.source_text || '—' }}</div></template
                ></a-table-column
              ><a-table-column title="状态" :width="100"
                ><template #default="{ record }"
                  ><StatusPill :value="record.status" /></template></a-table-column
              ><a-table-column title="尝试" :width="110"
                ><template #default="{ record }"
                  >{{ record.attempts }}/{{ record.max_attempts }}
                  <div class="muted">
                    累计 {{ record.lifetime_attempts ?? record.attempts }}
                  </div></template
                ></a-table-column
              ><a-table-column title="结果" :width="180"
                ><template #default="{ record }"
                  ><div class="one-line">
                    {{ record.draft?.title || record.last_error || record.error_message || '—' }}
                  </div></template
                ></a-table-column
              ><a-table-column title="操作" :width="220"
                ><template #default="{ record }"
                  ><a-space
                    ><a-button type="link" @click="showAttempts(record)">尝试明细</a-button
                    ><a-button v-if="record.draft" type="link" @click="editRecordDraft(record)"
                      >编辑草稿</a-button
                    ><a-button v-if="record.draft" type="link" @click="openArticle(record)"
                      >文章管理</a-button
                    ><a-button v-if="record.status === 'failed'" type="link" @click="retry(record)"
                      >重试</a-button
                    ></a-space
                  ></template
                ></a-table-column
              ></a-table
            ></a-tab-pane
          ><a-tab-pane key="backfills" tab="历史回溯"
            ><a-table
              :data-source="backfills"
              :loading="backfillsLoading"
              row-key="id"
              :pagination="backfillsPagination"
              :scroll="{ x: 680 }"
              @change="changeBackfills"
            >
              <a-table-column title="时间范围" :width="250"
                ><template #default="{ record }"
                  >{{ formatDateTime(record.from_at) }} 至
                  {{ formatDateTime(record.to_at) }}</template
                ></a-table-column
              >
              <a-table-column title="状态" :width="100"
                ><template #default="{ record }">{{
                  backfillStatus(record)
                }}</template></a-table-column
              >
              <a-table-column title="进度" :width="150"
                ><template #default="{ record }"
                  >已扫描 {{ record.scanned_count }} · 已入队 {{ record.enqueued_count }}</template
                ></a-table-column
              >
              <a-table-column title="提交时间" :width="130"
                ><template #default="{ record }">{{
                  formatDateTime(record.created_at)
                }}</template></a-table-column
              >
              <a-table-column title="错误" data-index="last_error" /> </a-table></a-tab-pane
          ><a-tab-pane key="events" tab="运行日志"
            ><a-table
              :data-source="events"
              :loading="eventsLoading"
              row-key="id"
              :pagination="eventsPagination"
              @change="changeEvents"
              ><a-table-column title="时间"
                ><template #default="{ record }">{{
                  formatDateTime(record.created_at)
                }}</template></a-table-column
              ><a-table-column title="事件" data-index="event_type" /><a-table-column
                title="详情"
                data-index="summary" /></a-table></a-tab-pane
        ></a-tabs>
      </template>
    </a-drawer>

    <a-modal
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
    >

    <a-modal
      v-model:open="backfillOpen"
      title="补生成历史"
      :ok-text="'提交补生成'"
      :ok-button-props="{ disabled: !backfillPreview }"
      :confirm-loading="backfillLoading"
      @ok="submitBackfill"
      ><a-alert
        type="info"
        show-icon
        message="只扫描所选时间范围内已采集的内容，默认排除旧自动流程已生成的内容。"
        style="margin-bottom: 18px"
      /><a-form layout="vertical"
        ><a-form-item label="开始时间"
          ><a-input
            v-model:value="backfillForm.from_at"
            type="datetime-local"
            @change="backfillPreview = null" /></a-form-item
        ><a-form-item label="结束时间"
          ><a-input
            v-model:value="backfillForm.to_at"
            type="datetime-local"
            @change="backfillPreview = null" /></a-form-item></a-form
      ><a-button :loading="backfillLoading" @click="previewBackfill">预估匹配内容</a-button>
      <div v-if="backfillPreview" style="margin-top: 12px">
        匹配 {{ backfillPreview.matched }} · 重复 {{ backfillPreview.duplicates }} · 旧流程已生成
        {{ backfillPreview.legacy_generated }} · 待生成 {{ backfillPreview.pending }}
      </div></a-modal
    >
  </div>
</template>

<style scoped>
.one-line {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 260px;
}
.skill-order {
  margin-top: 8px;
  display: grid;
  gap: 2px;
}
.skill-order > div {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
}
.skill-order span {
  min-width: 140px;
}
.detail-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 20px 0;
}
.detail-stats > div {
  border: 1px solid var(--color-border, #e5e7e5);
  border-radius: 8px;
  padding: 10px;
  color: var(--color-text-secondary, #72756c);
  font-size: 12px;
}
.detail-stats strong {
  display: block;
  color: var(--color-text, #292c29);
  font-size: 24px;
  font-weight: 500;
}
.summary-strip {
  margin: 0 0 16px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--color-fill-secondary, #f5f6f2);
  color: var(--color-text-secondary, #72756c);
  font-size: 12px;
}
@media (max-width: 640px) {
  .detail-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
