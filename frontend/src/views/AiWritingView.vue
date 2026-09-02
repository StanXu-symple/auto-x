<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import {
  AlertCircle,
  Bot,
  Check,
  Clipboard,
  Clock3,
  Copy,
  Edit3,
  FileText,
  KeyRound,
  ListRestart,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserRound,
  WandSparkles,
} from 'lucide-vue-next'
import { aiApi, monitoredUsersApi, tweetsApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { useUiStore } from '@/stores/ui'
import type {
  AiDraft,
  AiDraftStatus,
  AiFeature,
  AiJob,
  AiSettings,
  AiSkill,
  AiSkillPayload,
  AiUserProfile,
  AiUserSkillBinding,
  EntityId,
  MonitoredUser,
  Tweet,
  UpdateAiSettingsPayload,
} from '@/types'
import { formatDateTime, formatRelative } from '@/utils/format'
import EmptyState from '@/components/EmptyState.vue'
import PaginationBar from '@/components/PaginationBar.vue'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()

const activeTab = ref(String(route.query.tab || 'jobs'))
const settings = ref<AiSettings | null>(null)
const skills = ref<AiSkill[]>([])
const jobs = ref<AiJob[]>([])
const sourceTweets = ref<Tweet[]>([])
const monitoredUsers = ref<MonitoredUser[]>([])
const features = ref<AiFeature[]>([])
const selectedUserId = ref<EntityId | ''>('')
const selectedFeatureCode = ref('article_generation')
const userBinding = ref<AiUserSkillBinding | null>(null)
const userProfile = ref<AiUserProfile | null>(null)
const bindingSkillIds = ref<EntityId[]>([])
const bindingSaving = ref(false)
const jobsTotal = ref(0)
const loading = reactive({ initial: true, settings: false, skills: false, jobs: false, context: false, generate: false, draft: false })
const errors = reactive({ settings: '', skills: '', jobs: '' })
const jobFilters = reactive({ page: 1, page_size: 15, status: 'all' })

const settingsForm = reactive<UpdateAiSettingsPayload>({
  enabled: false,
  auto_generate: true,
  prompt_template: '',
  language: 'zh-CN',
  tone: '专业自然',
  require_review: true,
  reasoning_effort: 'medium',
  default_skill_ids: [],
  max_attempts: 3,
  max_output_tokens: 2500,
  request_timeout_seconds: 60,
})

const skillDialogOpen = ref(false)
const editingSkill = ref<AiSkill | null>(null)
const skillSaving = ref(false)
const skillFormError = ref('')
const skillForm = reactive<AiSkillPayload>({ name: '', description: '', instructions: '', is_active: true })

const generateDialogOpen = ref(false)
const generateFormError = ref('')
const generateForm = reactive({ source_x_tweet_id: '', feature_code: 'article_generation', override_skills: false, skill_ids: [] as EntityId[] })

const draftDialogOpen = ref(false)
const editingJob = ref<AiJob | null>(null)
const draftFormError = ref('')
const draftForm = reactive({ id: '' as EntityId, title: '', content: '', excerpt: '', status: 'draft' as AiDraftStatus | string, revision: 1 })

const configured = computed(() => settings.value?.provider_ready === true)
const highlightedJobId = computed(() => String(route.query.job || ''))
const runningJobs = computed(() => jobs.value.filter((job) => ['queued', 'running', 'retry_wait'].includes(job.status)).length)
const draftJobs = computed(() => jobs.value.filter((job) => Boolean(job.draft)).length)

function applySettings(value: AiSettings) {
  settings.value = value
  settingsForm.enabled = value.enabled
  settingsForm.auto_generate = value.auto_generate
  settingsForm.prompt_template = value.prompt_template || ''
  settingsForm.language = value.language || 'zh-CN'
  settingsForm.tone = value.tone || '专业自然'
  settingsForm.require_review = value.require_review
  settingsForm.reasoning_effort = value.reasoning_effort || 'medium'
  settingsForm.default_skill_ids = [...(value.default_skill_ids || [])]
  settingsForm.max_attempts = value.max_attempts
  settingsForm.max_output_tokens = value.max_output_tokens
  settingsForm.request_timeout_seconds = value.request_timeout_seconds
}

async function loadSettings() {
  loading.settings = true
  errors.settings = ''
  try {
    applySettings(await aiApi.settings())
  } catch (requestError) {
    errors.settings = getErrorMessage(requestError, '无法加载 AI 自动生成配置')
  } finally {
    loading.settings = false
  }
}

async function loadSkills() {
  loading.skills = true
  errors.skills = ''
  try {
    skills.value = await aiApi.skills()
  } catch (requestError) {
    errors.skills = getErrorMessage(requestError, '无法加载 Skills')
  } finally {
    loading.skills = false
  }
}

async function loadJobs(silent = false) {
  if (!silent) loading.jobs = true
  errors.jobs = ''
  try {
    const result = await aiApi.jobs({
      page: jobFilters.page,
      page_size: jobFilters.page_size,
      status: jobFilters.status === 'all' ? undefined : jobFilters.status,
    })
    jobs.value = result.items
    jobsTotal.value = result.total
  } catch (requestError) {
    errors.jobs = getErrorMessage(requestError, '无法加载 AI 生成任务')
  } finally {
    loading.jobs = false
  }
}

async function loadSourceTweets() {
  try {
    sourceTweets.value = (await tweetsApi.list({ page: 1, page_size: 100 })).items
  } catch {
    sourceTweets.value = []
  }
}

async function loadContextOptions() {
  try {
    const [usersResult, featureResult] = await Promise.all([
      monitoredUsersApi.list({ page: 1, page_size: 100 }),
      aiApi.features(),
    ])
    monitoredUsers.value = usersResult.items
    features.value = featureResult
    if (!features.value.some((feature) => feature.code === selectedFeatureCode.value)) {
      selectedFeatureCode.value = features.value[0]?.code || 'article_generation'
    }
    if (!selectedUserId.value && monitoredUsers.value[0]) selectedUserId.value = monitoredUsers.value[0].id
    await loadUserContext()
  } catch (requestError) {
    ui.toast('加载用户 AI 上下文失败', 'error', getErrorMessage(requestError))
  }
}

async function loadUserContext() {
  if (!selectedUserId.value || !selectedFeatureCode.value) {
    userBinding.value = null
    userProfile.value = null
    return
  }
  loading.context = true
  try {
    const [binding, profile] = await Promise.all([
      aiApi.userSkillBinding(selectedUserId.value, selectedFeatureCode.value),
      aiApi.userProfile(selectedUserId.value),
    ])
    userBinding.value = binding
    userProfile.value = profile
    bindingSkillIds.value = [...binding.skill_ids]
  } catch (requestError) {
    ui.toast('读取用户 Skill 策略失败', 'error', getErrorMessage(requestError))
  } finally {
    loading.context = false
  }
}

async function saveUserBinding() {
  if (!selectedUserId.value || !selectedFeatureCode.value) return
  bindingSaving.value = true
  try {
    userBinding.value = await aiApi.saveUserSkillBinding(
      selectedUserId.value,
      selectedFeatureCode.value,
      [...bindingSkillIds.value],
    )
    bindingSkillIds.value = [...userBinding.value.skill_ids]
    ui.toast('用户 Skill 策略已保存', 'success', '后续会话会按用户和 AI 功能点动态加载')
  } catch (requestError) {
    ui.toast('保存用户 Skill 策略失败', 'error', getErrorMessage(requestError))
  } finally {
    bindingSaving.value = false
  }
}

async function loadAll() {
  loading.initial = true
  await Promise.all([loadSettings(), loadSkills(), loadJobs(), loadSourceTweets()])
  await loadContextOptions()
  loading.initial = false
}

function validateSettings() {
  if (settingsForm.max_attempts < 1 || settingsForm.max_attempts > 10) return '最大重试次数应为 1–10'
  if (settingsForm.max_output_tokens < 128 || settingsForm.max_output_tokens > 100000) return '最大输出 Token 应为 128–100000'
  return ''
}

async function saveSettings() {
  const validationError = validateSettings()
  if (validationError) {
    ui.toast('配置未保存', 'warning', validationError)
    return
  }
  loading.settings = true
  try {
    const payload: UpdateAiSettingsPayload = {
      ...settingsForm,
      prompt_template: settingsForm.prompt_template.trim(),
      tone: settingsForm.tone.trim(),
      default_skill_ids: [...settingsForm.default_skill_ids],
    }
    applySettings(await aiApi.updateSettings(payload))
    ui.toast('AI 配置已保存', 'success', '新任务会使用最新模型与创作策略')
  } catch (requestError) {
    ui.toast('保存 AI 配置失败', 'error', getErrorMessage(requestError))
  } finally {
    loading.settings = false
  }
}

function resetSettings() {
  if (settings.value) applySettings(settings.value)
}

function openCreateSkill() {
  editingSkill.value = null
  skillForm.name = ''
  skillForm.description = ''
  skillForm.instructions = ''
  skillForm.is_active = true
  skillFormError.value = ''
  skillDialogOpen.value = true
}

function openEditSkill(skill: AiSkill) {
  editingSkill.value = skill
  skillForm.name = skill.name
  skillForm.description = skill.description || ''
  skillForm.instructions = skill.instructions
  skillForm.is_active = skill.is_active
  skillFormError.value = ''
  skillDialogOpen.value = true
}

async function saveSkill() {
  skillFormError.value = ''
  if (!skillForm.name.trim()) skillFormError.value = '请填写 Skill 名称'
  else if (!skillForm.instructions.trim()) skillFormError.value = '请填写 Skill 指令'
  if (skillFormError.value) return
  skillSaving.value = true
  try {
    const payload: AiSkillPayload = {
      name: skillForm.name.trim(),
      description: skillForm.description?.trim(),
      instructions: skillForm.instructions.trim(),
      is_active: skillForm.is_active,
    }
    if (editingSkill.value) await aiApi.updateSkill(editingSkill.value.id, payload)
    else await aiApi.createSkill(payload)
    skillDialogOpen.value = false
    await loadSkills()
    ui.toast(editingSkill.value ? 'Skill 已更新' : 'Skill 已创建', 'success')
  } catch (requestError) {
    skillFormError.value = getErrorMessage(requestError, '保存 Skill 失败')
  } finally {
    skillSaving.value = false
  }
}

async function removeSkill(skill: AiSkill) {
  try {
    await ElMessageBox.confirm(`删除 Skill「${skill.name}」？历史任务不会被删除。`, '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await aiApi.removeSkill(skill.id)
    settingsForm.default_skill_ids = settingsForm.default_skill_ids.filter((id) => String(id) !== String(skill.id))
    await loadSkills()
    ui.toast('Skill 已删除', 'success')
  } catch (requestError) {
    ui.toast('删除 Skill 失败', 'error', getErrorMessage(requestError))
  }
}

function openGenerateDialog(tweet?: Tweet) {
  generateForm.source_x_tweet_id = tweet ? tweet.tweet_id : ''
  generateForm.feature_code = selectedFeatureCode.value || 'article_generation'
  generateForm.override_skills = false
  generateForm.skill_ids = []
  generateFormError.value = ''
  generateDialogOpen.value = true
}

async function submitGeneration() {
  generateFormError.value = ''
  if (!generateForm.source_x_tweet_id.trim()) {
    generateFormError.value = '请选择推文或输入 X Tweet ID'
    return
  }
  loading.generate = true
  try {
    const result = await aiApi.generateFromTweet(generateForm.source_x_tweet_id.trim(), {
      feature_code: generateForm.feature_code,
      skill_ids: generateForm.override_skills ? [...generateForm.skill_ids] : undefined,
      idempotency_key: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${generateForm.source_x_tweet_id}`,
    })
    generateDialogOpen.value = false
    activeTab.value = 'jobs'
    await loadJobs()
    const resultId = result.job_id ?? result.id
    if (resultId != null) await router.replace({ query: { ...route.query, tab: 'jobs', job: String(resultId) } })
    ui.toast('生成任务已加入队列', 'success', 'AI Worker 会在后台生成草稿')
  } catch (requestError) {
    generateFormError.value = getErrorMessage(requestError, '创建生成任务失败')
  } finally {
    loading.generate = false
  }
}

function jobDraft(job: AiJob): AiDraft | null {
  return job.draft || null
}

function jobSourceText(job: AiJob) {
  return job.source_tweet?.text || job.source_text || ''
}

function jobSourceUsername(job: AiJob) {
  return job.source_tweet?.username || job.source_username || ''
}

function skillNames(job: AiJob) {
  if (job.skills?.length) return job.skills.map((skill) => skill.name)
  const ids = job.skill_ids?.length ? job.skill_ids : job.skill_id != null ? [job.skill_id] : []
  return ids.map((id) => skills.value.find((skill) => String(skill.id) === String(id))?.name || `Skill #${id}`)
}

function jobStatusMeta(status: string) {
  const values: Record<string, { label: string; type: 'success' | 'warning' | 'danger' | 'info' | '' }> = {
    queued: { label: '排队中', type: 'info' },
    running: { label: '生成中', type: 'warning' },
    retry_wait: { label: '等待重试', type: 'warning' },
    succeeded: { label: '已生成', type: 'success' },
    failed: { label: '失败', type: 'danger' },
    cancelled: { label: '已取消', type: '' },
  }
  return values[status] || { label: status, type: '' as const }
}

function openDraft(job: AiJob) {
  const draft = jobDraft(job)
  if (!draft) return
  editingJob.value = job
  draftForm.id = draft.id
  draftForm.title = draft.title
  draftForm.content = draft.content
  draftForm.excerpt = draft.excerpt || ''
  draftForm.status = draft.status
  draftForm.revision = draft.revision
  draftFormError.value = ''
  draftDialogOpen.value = true
}

async function saveDraft(status?: AiDraftStatus) {
  if (!draftForm.content.trim()) {
    draftFormError.value = '草稿正文不能为空'
    return
  }
  loading.draft = true
  draftFormError.value = ''
  try {
    await aiApi.updateDraft(draftForm.id, {
      title: draftForm.title.trim(),
      content: draftForm.content.trim(),
      excerpt: draftForm.excerpt.trim(),
      status: status || draftForm.status,
      revision: draftForm.revision,
    })
    draftDialogOpen.value = false
    await loadJobs()
    ui.toast(status === 'approved' ? '草稿已审核通过' : status === 'rejected' ? '草稿已退回' : '草稿已保存', 'success')
  } catch (requestError) {
    draftFormError.value = getErrorMessage(requestError, '保存草稿失败；如果草稿已被他人更新，请刷新后重试')
  } finally {
    loading.draft = false
  }
}

async function copyDraft(job: AiJob) {
  const draft = jobDraft(job)
  if (!draft) return
  const text = [draft.title, draft.content].filter(Boolean).join('\n\n')
  try {
    await navigator.clipboard.writeText(text)
    ui.toast('草稿已复制', 'success')
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    textarea.remove()
    ui.toast('草稿已复制', 'success')
  }
}

async function retryJob(job: AiJob) {
  try {
    await aiApi.retryJob(job.id)
    ui.toast('任务已重新排队', 'success')
    await loadJobs()
  } catch (requestError) {
    ui.toast('任务重试失败', 'error', getErrorMessage(requestError))
  }
}

function changeJobsPage(page: number) {
  jobFilters.page = page
  loadJobs()
}

function jobRowClass({ row }: { row: AiJob }) {
  return highlightedJobId.value && String(row.id) === highlightedJobId.value ? 'ai-job-row--highlighted' : ''
}

watch(
  () => jobFilters.status,
  () => {
    jobFilters.page = 1
    loadJobs()
  },
)

watch(activeTab, (tab) => {
  router.replace({ query: { ...route.query, tab } })
})

watch([selectedUserId, selectedFeatureCode], () => loadUserContext())

let refreshTimer: number | undefined
onMounted(() => {
  loadAll()
  refreshTimer = window.setInterval(() => {
    if (activeTab.value === 'jobs' && !loading.jobs) loadJobs(true)
  }, 30_000)
})
onBeforeUnmount(() => window.clearInterval(refreshTimer))
</script>

<template>
  <div class="ai-writing-view page-stack">
    <section class="ai-hero">
      <div class="ai-hero__copy">
        <span class="ai-hero__icon"><WandSparkles :size="24" /></span>
        <div><span class="eyebrow">AI CONTENT WORKFLOW</span><h2>从监听素材到可发布草稿</h2><p>自动提炼推文价值，组合 Skills 生成内容，并把每一篇草稿交给人工审核。</p></div>
      </div>
      <div class="ai-hero__actions">
        <div class="provider-readiness" :class="{ 'is-ready': configured && settings?.enabled }"><span /><div><small>Provider readiness</small><strong>{{ !settings?.enabled ? 'AI 未启用' : configured ? '已就绪' : '等待配置' }}</strong></div></div>
        <el-button type="primary" @click="openGenerateDialog()"><Sparkles :size="16" />新建生成任务</el-button>
        <el-tooltip content="刷新配置、Skills 与任务"><el-button circle :loading="loading.initial" @click="loadAll"><RefreshCw v-if="!loading.initial" :size="16" /></el-button></el-tooltip>
      </div>
    </section>

    <section class="ai-summary-grid">
      <article><span class="summary-icon is-purple"><Bot :size="18" /></span><div><small>当前 Provider</small><strong>{{ settings?.provider === 'codex_bridge' ? 'Codex Bridge' : 'OpenAI Responses' }}</strong><p>{{ settings?.model || '尚未设置模型' }}</p></div></article>
      <article><span class="summary-icon is-blue"><ListRestart :size="18" /></span><div><small>当前页运行任务</small><strong>{{ runningJobs }}</strong><p>排队、执行与等待重试</p></div></article>
      <article><span class="summary-icon is-green"><FileText :size="18" /></span><div><small>当前页草稿</small><strong>{{ draftJobs }}</strong><p>{{ settings?.require_review ? '启用人工审核' : '生成后自动通过' }}</p></div></article>
      <article><span class="summary-icon is-orange"><WandSparkles :size="18" /></span><div><small>可用 Skills</small><strong>{{ skills.filter((skill) => skill.is_active).length }}</strong><p>已选择 {{ settings?.default_skill_ids?.length || 0 }} 个默认 Skill</p></div></article>
    </section>

    <section class="panel ai-workbench">
      <el-tabs v-model="activeTab" class="ai-tabs">
        <el-tab-pane name="jobs"><template #label><span class="ai-tab-label"><Clipboard :size="15" />任务与草稿</span></template>
          <div class="ai-pane">
            <header class="ai-pane__toolbar">
              <div><h3>生成任务</h3><p>查看队列、失败重试和已生成草稿</p></div>
              <div class="ai-pane__actions"><el-select v-model="jobFilters.status" class="status-filter"><el-option label="全部状态" value="all" /><el-option label="排队中" value="queued" /><el-option label="生成中" value="running" /><el-option label="等待重试" value="retry_wait" /><el-option label="已生成" value="succeeded" /><el-option label="失败" value="failed" /><el-option label="已取消" value="cancelled" /></el-select><el-button :loading="loading.jobs" @click="loadJobs()"><RefreshCw v-if="!loading.jobs" :size="15" />手动刷新</el-button></div>
            </header>
            <el-alert v-if="errors.jobs" :title="errors.jobs" type="error" :closable="false" show-icon />
            <el-table v-if="jobs.length || loading.jobs" v-loading="loading.jobs" :data="jobs" row-key="id" class="sentinel-table ai-job-table" :row-class-name="jobRowClass">
              <el-table-column label="来源内容" min-width="260"><template #default="{ row: job }"><div class="job-source"><span class="source-mark">#{{ job.source_tweet_id }}</span><div><strong>{{ jobSourceUsername(job) ? `@${jobSourceUsername(job)}` : `源推文 ${job.source_tweet_id}` }}</strong><p>{{ jobSourceText(job) || (job.source_x_tweet_id ? `X Post ID ${job.source_x_tweet_id}` : '源内容详情未随任务返回') }}</p></div></div></template></el-table-column>
              <el-table-column label="Skills" min-width="150"><template #default="{ row: job }"><div class="skill-tags"><el-tag v-for="name in skillNames(job)" :key="name" size="small" effect="plain">{{ name }}</el-tag><span v-if="!skillNames(job).length">默认策略</span></div></template></el-table-column>
              <el-table-column label="状态" width="105"><template #default="{ row: job }"><el-tag :type="jobStatusMeta(job.status).type" effect="dark" round>{{ jobStatusMeta(job.status).label }}</el-tag></template></el-table-column>
              <el-table-column label="尝试" width="85"><template #default="{ row: job }"><span class="attempt-copy">{{ job.attempts || 0 }} / {{ job.max_attempts || '—' }}</span></template></el-table-column>
              <el-table-column label="创建时间" min-width="145"><template #default="{ row: job }"><span class="date-cell"><strong>{{ formatRelative(job.created_at) }}</strong><small>{{ formatDateTime(job.created_at) }}</small></span></template></el-table-column>
              <el-table-column label="草稿 / 操作" min-width="235" fixed="right"><template #default="{ row: job }"><div class="job-actions"><template v-if="jobDraft(job)"><el-button size="small" @click="openDraft(job)"><Edit3 :size="14" />编辑</el-button><el-button size="small" @click="copyDraft(job)"><Copy :size="14" />复制</el-button></template><el-button v-if="['failed', 'retry_wait', 'cancelled'].includes(job.status)" size="small" type="warning" plain @click="retryJob(job)"><RotateCcw :size="14" />重试</el-button><el-tooltip v-if="job.last_error || job.error_message" :content="job.last_error || job.error_message" placement="top"><AlertCircle class="job-error-icon" :size="16" /></el-tooltip><span v-if="!jobDraft(job) && !['failed', 'retry_wait', 'cancelled'].includes(job.status)" class="waiting-copy"><Clock3 :size="14" />等待草稿</span></div></template></el-table-column>
            </el-table>
            <EmptyState v-else-if="!loading.jobs" compact title="还没有 AI 生成任务" description="从内容流选择推文，或在这里输入源推文 ID 创建第一条任务"><template #icon><Sparkles :size="26" /></template><el-button type="primary" @click="openGenerateDialog()">新建生成任务</el-button></EmptyState>
            <PaginationBar v-if="jobsTotal > jobFilters.page_size" :page="jobFilters.page" :page-size="jobFilters.page_size" :total="jobsTotal" @change="changeJobsPage" />
          </div>
        </el-tab-pane>

        <el-tab-pane name="settings"><template #label><span class="ai-tab-label"><Settings2 :size="15" />自动生成配置</span></template>
          <div v-loading="loading.settings" class="ai-pane settings-pane">
            <el-alert v-if="errors.settings" :title="errors.settings" type="error" :closable="false" show-icon />
            <div class="settings-sections">
              <section class="settings-block">
                <header><span><Bot :size="18" /></span><div><h3>统一 AI 数据源</h3><p>模型、服务地址和 API Key 由独立数据源菜单统一管理</p></div><el-tag :type="configured ? 'success' : 'warning'" effect="plain">{{ configured ? '数据源可用' : '等待配置' }}</el-tag></header>
                <div class="credential-status"><span :class="{ 'is-ready': configured }"><KeyRound :size="15" /></span><div><strong>{{ settings?.model || '尚未配置模型' }}</strong><small>{{ settings?.base_url || '请先配置 OpenAI 兼容 Base URL 与 API Key' }}</small></div><el-button type="primary" plain @click="router.push('/ai-data-source')">管理 AI 数据源</el-button></div>
                <div class="settings-form-grid compact-provider-settings">
                  <el-form-item label="推理强度"><el-select v-model="settingsForm.reasoning_effort"><el-option label="无" value="none" /><el-option label="低" value="low" /><el-option label="中" value="medium" /><el-option label="高" value="high" /><el-option label="超高" value="xhigh" /><el-option label="最大" value="max" /></el-select></el-form-item>
                  <el-form-item label="请求超时"><el-input-number v-model="settingsForm.request_timeout_seconds" :min="5" :max="600" /><span class="field-unit">秒</span></el-form-item>
                </div>
              </section>

              <section class="settings-block">
                <header><span><WandSparkles :size="18" /></span><div><h3>创作策略</h3><p>定义提示词、语气、语言和默认 Skill 组合</p></div></header>
                <el-form-item label="提示词模板"><el-input v-model="settingsForm.prompt_template" type="textarea" :rows="6" maxlength="20000" show-word-limit placeholder="说明如何理解源推文、提炼观点和组织最终内容。" /></el-form-item>
                <div class="settings-form-grid">
                  <el-form-item label="输出语气"><el-select v-model="settingsForm.tone" filterable allow-create><el-option v-for="tone in ['专业自然', '简洁有力', '轻松口语', '深度分析', '犀利评论']" :key="tone" :label="tone" :value="tone" /></el-select></el-form-item>
                  <el-form-item label="输出语言"><el-select v-model="settingsForm.language"><el-option label="简体中文" value="zh-CN" /><el-option label="繁体中文" value="zh-TW" /><el-option label="English" value="en" /><el-option label="日本語" value="ja" /></el-select></el-form-item>
                  <el-form-item label="默认 Skills" class="span-2"><el-select v-model="settingsForm.default_skill_ids" multiple collapse-tags collapse-tags-tooltip placeholder="选择一个或多个 Skill"><el-option v-for="skill in skills.filter((item) => item.is_active)" :key="skill.id" :label="skill.name" :value="skill.id" /></el-select></el-form-item>
                  <el-form-item label="最大重试次数"><el-input-number v-model="settingsForm.max_attempts" :min="1" :max="10" /></el-form-item>
                  <el-form-item label="最大输出 Token"><el-input-number v-model="settingsForm.max_output_tokens" :min="128" :max="100000" :step="128" /></el-form-item>
                </div>
              </section>

              <section class="settings-block automation-block">
                <header><span><ShieldCheck :size="18" /></span><div><h3>自动化与审核</h3><p>控制新推文何时进入生成队列，以及草稿是否需要人工确认</p></div></header>
                <div class="switch-list"><div><span><strong>启用 AI 创作</strong><small>关闭后不会创建或执行新的生成任务</small></span><el-switch v-model="settingsForm.enabled" /></div><div><span><strong>采集后自动生成</strong><small>新推文入库后自动按默认 Skills 创建任务</small></span><el-switch v-model="settingsForm.auto_generate" :disabled="!settingsForm.enabled" /></div><div><span><strong>必须人工审核</strong><small>生成结果先进入草稿状态，审核通过后再用于发布</small></span><el-switch v-model="settingsForm.require_review" /></div></div>
              </section>
            </div>
            <footer class="settings-footer"><span>最后更新：{{ formatDateTime(settings?.updated_at) }}</span><el-button @click="resetSettings"><RotateCcw :size="15" />重置</el-button><el-button type="primary" :loading="loading.settings" @click="saveSettings"><Save v-if="!loading.settings" :size="15" />保存配置</el-button></footer>
          </div>
        </el-tab-pane>

        <el-tab-pane name="user-context"><template #label><span class="ai-tab-label"><UserRound :size="15" />用户策略与画像</span></template>
          <div v-loading="loading.context" class="ai-pane user-context-pane">
            <header class="ai-pane__toolbar">
              <div><h3>按用户动态装配 AI 上下文</h3><p>每个监听用户可针对不同 AI 功能点选择 Skill；画像会随成功生成持续更新。</p></div>
              <el-button :loading="loading.context" @click="loadContextOptions"><RefreshCw v-if="!loading.context" :size="15" />刷新</el-button>
            </header>
            <div class="context-selector">
              <el-form-item label="监听用户"><el-select v-model="selectedUserId" filterable placeholder="选择监听用户"><el-option v-for="user in monitoredUsers" :key="user.id" :label="`${user.display_name || user.username} · @${user.username}`" :value="user.id" /></el-select></el-form-item>
              <el-form-item label="AI 功能点"><el-select v-model="selectedFeatureCode" placeholder="选择功能点"><el-option v-for="feature in features" :key="feature.code" :label="feature.name" :value="feature.code" /></el-select></el-form-item>
            </div>
            <EmptyState v-if="!monitoredUsers.length" compact title="还没有监听用户" description="先添加需要监听的 X 用户，再为他配置专属 Skill"><template #icon><UserRound :size="26" /></template><el-button type="primary" @click="router.push('/accounts')">添加监听用户</el-button></EmptyState>
            <div v-else class="context-grid">
              <section class="settings-block binding-card">
                <header><span><WandSparkles :size="18" /></span><div><h3>专属 Skill 组合</h3><p>{{ userBinding?.feature.description || '选择该功能调用时需要注入的提示指令' }}</p></div><el-tag effect="plain" :type="userBinding?.resolution_source === 'user_feature_binding' ? 'success' : 'info'">{{ userBinding?.resolution_source === 'user_feature_binding' ? '用户专属' : '继承全局默认' }}</el-tag></header>
                <el-alert title="留空并保存表示移除专属绑定，系统将自动回退到全局默认 Skills。" type="info" :closable="false" show-icon />
                <el-form-item label="Skills（顺序即优先级）"><el-select v-model="bindingSkillIds" multiple filterable collapse-tags collapse-tags-tooltip placeholder="选择一个或多个 Skill"><el-option v-for="skill in skills.filter((item) => item.is_active)" :key="skill.id" :label="`${skill.name} · v${skill.version || 1}`" :value="skill.id" /></el-select></el-form-item>
                <div class="feature-prompt"><small>功能点基础提示词</small><p>{{ userBinding?.feature.base_prompt || features.find((item) => item.code === selectedFeatureCode)?.base_prompt }}</p></div>
                <footer><span>解析顺序：手动覆盖 → 用户功能绑定 → 全局默认</span><el-button type="primary" :loading="bindingSaving" @click="saveUserBinding"><Save v-if="!bindingSaving" :size="15" />保存策略</el-button></footer>
              </section>
              <section class="settings-block profile-card">
                <header><span><Bot :size="18" /></span><div><h3>作者长期画像</h3><p>基于历史画像、近期动态和当前帖子进行保守迭代</p></div><el-tag effect="plain">v{{ userProfile?.version || 0 }} · 置信度 {{ Math.round((userProfile?.confidence || 0) * 100) }}%</el-tag></header>
                <dl class="profile-details"><div><dt>他是谁</dt><dd>{{ userProfile?.identity_summary || '尚未生成；首次成功创作后自动形成画像。' }}</dd></div><div><dt>近期关注</dt><dd>{{ userProfile?.focus_summary || '暂无近期关注总结' }}</dd></div><div><dt>动态关联与思想脉络</dt><dd>{{ userProfile?.relationship_summary || '暂无关联分析' }}</dd></div></dl>
                <div class="profile-topics"><small>长期主题</small><div><el-tag v-for="topic in userProfile?.recurring_topics || []" :key="topic" effect="plain">{{ topic }}</el-tag><span v-if="!userProfile?.recurring_topics?.length">暂无</span></div></div>
                <footer><span>最近更新：{{ formatDateTime(userProfile?.updated_at) }}</span><span v-if="userProfile?.last_source_tweet_id">来源推文 #{{ userProfile.last_source_tweet_id }}</span></footer>
              </section>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane name="skills"><template #label><span class="ai-tab-label"><WandSparkles :size="15" />Skills</span></template>
          <div class="ai-pane">
            <header class="ai-pane__toolbar"><div><h3>创作 Skills</h3><p>把写作方法拆成可复用、可组合的提示指令</p></div><div class="ai-pane__actions"><el-button :loading="loading.skills" @click="loadSkills"><RefreshCw v-if="!loading.skills" :size="15" />刷新</el-button><el-button type="primary" @click="openCreateSkill"><Plus :size="15" />新建 Skill</el-button></div></header>
            <el-alert v-if="errors.skills" :title="errors.skills" type="error" :closable="false" show-icon />
            <div v-loading="loading.skills" class="skill-grid">
              <article v-for="skill in skills" :key="skill.id" class="skill-card" :class="{ 'is-inactive': !skill.is_active }"><header><span class="skill-card__icon"><Sparkles :size="17" /></span><div><strong>{{ skill.name }}</strong><small>v{{ skill.version || 1 }} · {{ skill.is_active ? '启用' : '停用' }}</small></div><el-tag :type="skill.is_active ? 'success' : 'info'" size="small" effect="plain">{{ skill.is_active ? '可用' : '停用' }}</el-tag></header><p>{{ skill.description || '暂无说明' }}</p><pre>{{ skill.instructions }}</pre><footer><span>更新于 {{ formatDateTime(skill.updated_at || skill.created_at) }}</span><div><el-button circle size="small" @click="openEditSkill(skill)"><Edit3 :size="14" /></el-button><el-button circle size="small" type="danger" plain @click="removeSkill(skill)"><Trash2 :size="14" /></el-button></div></footer></article>
            </div>
            <EmptyState v-if="!loading.skills && !skills.length" compact title="还没有 Skill" description="创建一个 Skill，沉淀你的标题、长文或评论写作方法"><template #icon><WandSparkles :size="26" /></template><el-button type="primary" @click="openCreateSkill">新建 Skill</el-button></EmptyState>
          </div>
        </el-tab-pane>
      </el-tabs>
    </section>

    <el-dialog v-model="generateDialogOpen" title="新建 AI 生成任务" width="min(580px, 94vw)" destroy-on-close>
      <el-alert v-if="generateFormError" :title="generateFormError" type="error" :closable="false" show-icon />
      <el-form label-position="top" class="dialog-form">
        <el-form-item label="源推文" required><el-select v-model="generateForm.source_x_tweet_id" filterable allow-create default-first-option placeholder="选择最近推文，或直接输入 X Tweet ID"><el-option v-for="tweet in sourceTweets" :key="tweet.id" :value="tweet.tweet_id" :label="`X ${tweet.tweet_id} · @${tweet.username} · ${tweet.text.slice(0, 60)}`"><div class="tweet-option"><strong>X {{ tweet.tweet_id }} · @{{ tweet.username }}</strong><span>{{ tweet.text }}</span></div></el-option></el-select></el-form-item>
        <el-form-item label="AI 功能点"><el-select v-model="generateForm.feature_code"><el-option v-for="feature in features" :key="feature.code" :label="feature.name" :value="feature.code" /></el-select></el-form-item>
        <el-form-item><el-switch v-model="generateForm.override_skills" active-text="本次手动覆盖用户 Skill 策略" inactive-text="按用户与功能点动态加载 Skills" /></el-form-item>
        <el-form-item v-if="generateForm.override_skills" label="本次使用的 Skills"><el-select v-model="generateForm.skill_ids" multiple collapse-tags collapse-tags-tooltip placeholder="选择本次覆盖组合"><el-option v-for="skill in skills.filter((item) => item.is_active)" :key="skill.id" :label="skill.name" :value="skill.id" /></el-select></el-form-item>
      </el-form>
      <template #footer><el-button @click="generateDialogOpen = false">取消</el-button><el-button type="primary" :loading="loading.generate" @click="submitGeneration"><Sparkles v-if="!loading.generate" :size="15" />加入生成队列</el-button></template>
    </el-dialog>

    <el-dialog v-model="skillDialogOpen" :title="editingSkill ? '编辑 Skill' : '新建 Skill'" width="min(650px, 94vw)" destroy-on-close>
      <el-alert v-if="skillFormError" :title="skillFormError" type="error" :closable="false" show-icon />
      <el-form label-position="top" class="dialog-form"><div class="dialog-form-grid"><el-form-item label="名称" required><el-input v-model="skillForm.name" maxlength="100" placeholder="例如：公众号深度长文" /></el-form-item><el-form-item label="状态"><el-switch v-model="skillForm.is_active" active-text="启用" inactive-text="停用" /></el-form-item></div><el-form-item label="说明"><el-input v-model="skillForm.description" maxlength="4000" placeholder="这个 Skill 适合什么场景" /></el-form-item><el-form-item label="Skill 指令" required><el-input v-model="skillForm.instructions" type="textarea" :rows="10" maxlength="20000" show-word-limit placeholder="描述角色、写作步骤、结构要求、禁忌和输出格式" /></el-form-item></el-form>
      <template #footer><el-button @click="skillDialogOpen = false">取消</el-button><el-button type="primary" :loading="skillSaving" @click="saveSkill"><Save v-if="!skillSaving" :size="15" />保存 Skill</el-button></template>
    </el-dialog>

    <el-dialog v-model="draftDialogOpen" title="编辑与审核草稿" width="min(820px, 96vw)" destroy-on-close>
      <el-alert v-if="draftFormError" :title="draftFormError" type="error" :closable="false" show-icon />
      <el-form label-position="top" class="dialog-form"><el-form-item label="标题"><el-input v-model="draftForm.title" maxlength="300" show-word-limit /></el-form-item><el-form-item label="摘要"><el-input v-model="draftForm.excerpt" type="textarea" :rows="2" maxlength="1000" /></el-form-item><el-form-item label="正文" required><el-input v-model="draftForm.content" type="textarea" :rows="16" /></el-form-item></el-form>
      <div class="draft-dialog-meta"><span>Revision {{ draftForm.revision }}</span><span v-if="editingJob">任务 #{{ editingJob.id }}</span></div>
      <template #footer><el-button type="danger" plain :disabled="loading.draft" @click="saveDraft('rejected')">退回</el-button><span class="dialog-footer-spacer" /><el-button :disabled="loading.draft" @click="draftDialogOpen = false">取消</el-button><el-button :loading="loading.draft" @click="saveDraft('draft')"><Save v-if="!loading.draft" :size="15" />保存草稿</el-button><el-button type="primary" :loading="loading.draft" @click="saveDraft('approved')"><Check v-if="!loading.draft" :size="15" />审核通过</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.ai-writing-view { gap: 16px; }
.ai-hero { display: flex; align-items: center; justify-content: space-between; min-height: 118px; padding: 22px 24px; border: 1px solid rgba(124, 98, 255, .3); border-radius: 14px; background: radial-gradient(circle at 12% 10%, rgba(124, 98, 255, .18), transparent 32%), linear-gradient(135deg, #121522, #0d1018); box-shadow: var(--shadow); gap: 24px; }
.ai-hero__copy, .ai-hero__actions, .ai-summary-grid article, .settings-block header, .skill-card header, .job-source, .job-actions, .ai-tab-label { display: flex; align-items: center; }
.ai-hero__copy { gap: 16px; }
.ai-hero__icon { display: inline-flex; flex: 0 0 auto; align-items: center; justify-content: center; width: 54px; height: 54px; border: 1px solid rgba(163, 145, 255, .45); border-radius: 16px; color: #fff; background: linear-gradient(145deg, #9a83ff, #6445ed); box-shadow: 0 14px 30px rgba(104, 72, 237, .3); }
.eyebrow { color: var(--primary-bright); font-size: 8px; font-weight: 800; letter-spacing: .15em; }
.ai-hero h2 { margin: 4px 0 5px; font-size: 19px; }
.ai-hero p, .ai-pane__toolbar p, .settings-block header p { margin: 0; color: var(--muted); font-size: 10px; }
.ai-hero__actions { justify-content: flex-end; gap: 8px; }
.provider-readiness { display: flex; align-items: center; margin-right: 5px; padding: 8px 11px; border: 1px solid var(--border); border-radius: 9px; background: rgba(0, 0, 0, .16); gap: 8px; }
.provider-readiness > span { width: 7px; height: 7px; border-radius: 50%; background: var(--orange); box-shadow: 0 0 9px rgba(245, 167, 66, .65); }
.provider-readiness.is-ready > span { background: var(--green); box-shadow: 0 0 9px rgba(50, 212, 154, .65); }
.provider-readiness div { display: flex; flex-direction: column; }
.provider-readiness small { color: var(--muted); font-size: 7px; text-transform: uppercase; }
.provider-readiness strong { margin-top: 2px; font-size: 9px; }
.ai-summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 11px; }
.ai-summary-grid article { min-width: 0; padding: 14px 16px; border: 1px solid var(--border); border-radius: 11px; background: var(--surface); gap: 11px; }
.summary-icon { display: inline-flex; flex: 0 0 auto; align-items: center; justify-content: center; width: 35px; height: 35px; border-radius: 9px; }
.summary-icon.is-purple { color: var(--primary-bright); background: var(--primary-soft); }.summary-icon.is-blue { color: var(--blue); background: rgba(86,149,255,.12); }.summary-icon.is-green { color: var(--green); background: rgba(50,212,154,.11); }.summary-icon.is-orange { color: var(--orange); background: rgba(245,167,66,.11); }
.ai-summary-grid article > div { min-width: 0; }.ai-summary-grid small { display: block; color: var(--muted); font-size: 8px; }.ai-summary-grid strong { display: block; overflow: hidden; margin-top: 2px; font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }.ai-summary-grid p { overflow: hidden; margin: 2px 0 0; color: var(--muted); font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.ai-workbench { overflow: visible; }.ai-tabs :deep(.el-tabs__header) { margin: 0; padding: 0 18px; border-bottom: 1px solid var(--border); }.ai-tabs :deep(.el-tabs__nav-wrap::after) { display: none; }.ai-tabs :deep(.el-tabs__item) { height: 52px; color: var(--muted); font-size: 11px; }.ai-tabs :deep(.el-tabs__item.is-active) { color: var(--text); }.ai-tab-label { gap: 7px; }
.ai-pane { min-height: 390px; padding: 18px; }.ai-pane__toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 15px; gap: 16px; }.ai-pane__toolbar h3, .settings-block h3 { margin: 0 0 3px; font-size: 13px; }.ai-pane__actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }.ai-pane__actions .el-input { width: 210px; }.status-filter { width: 125px; }
.job-source { min-width: 0; gap: 9px; }.source-mark { flex: 0 0 auto; padding: 4px 6px; border-radius: 5px; color: var(--primary-bright); background: var(--primary-soft); font-family: ui-monospace, monospace; font-size: 8px; }.job-source > div { min-width: 0; }.job-source strong { font-size: 10px; }.job-source p { overflow: hidden; max-width: 330px; margin: 3px 0 0; color: var(--muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }.skill-tags { display: flex; flex-wrap: wrap; gap: 4px; }.skill-tags > span { color: var(--muted); font-size: 9px; }.attempt-copy, .waiting-copy { color: var(--muted); font-size: 9px; }.job-actions { min-height: 30px; gap: 5px; }.waiting-copy { display: inline-flex; align-items: center; gap: 5px; }.job-error-icon { margin-left: 4px; color: var(--red); }.ai-job-table :deep(.ai-job-row--highlighted td.el-table__cell) { background: rgba(124, 98, 255, .13) !important; }
.settings-pane { padding: 18px 20px 0; }.settings-sections { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 13px; }.settings-block { padding: 17px; border: 1px solid var(--border); border-radius: 11px; background: rgba(9, 11, 17, .38); }.settings-block header { margin-bottom: 17px; gap: 10px; }.settings-block header > span { display: inline-flex; align-items: center; justify-content: center; width: 34px; height: 34px; border-radius: 9px; color: var(--primary-bright); background: var(--primary-soft); }.settings-block header > div { flex: 1; }.settings-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; }.span-2 { grid-column: 1 / -1; }.field-unit { margin-left: 7px; color: var(--muted); font-size: 9px; }.provider-option { display: flex; flex-direction: column; padding: 4px 0; }.provider-option small { color: var(--muted); font-size: 8px; }.automation-block { grid-column: 1 / -1; }.switch-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }.switch-list > div { display: flex; align-items: center; justify-content: space-between; padding: 12px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface-2); gap: 10px; }.switch-list span { display: flex; flex-direction: column; }.switch-list strong { font-size: 10px; }.switch-list small { margin-top: 3px; color: var(--muted); font-size: 8px; line-height: 1.45; }.settings-footer { display: flex; align-items: center; justify-content: flex-end; margin: 18px -20px 0; padding: 13px 20px; border-top: 1px solid var(--border); gap: 8px; }.settings-footer > span { margin-right: auto; color: var(--muted); font-size: 8px; }
.credential-status { display: flex; width: 100%; align-items: center; padding: 10px 12px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface-2); gap: 10px; }.credential-status > span { display: inline-flex; flex: 0 0 auto; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: 8px; color: var(--orange); background: rgba(245,167,66,.1); }.credential-status > span.is-ready { color: var(--green); background: rgba(50,212,154,.1); }.credential-status > div { display: flex; flex: 1; min-width: 0; flex-direction: column; }.credential-status strong { font-size: 9px; }.credential-status small { margin-top: 3px; overflow: hidden; color: var(--muted); font-size: 8px; line-height: 1.45; text-overflow: ellipsis; }.compact-provider-settings { margin-top: 14px; }
.context-selector { display: grid; grid-template-columns: repeat(2, minmax(0, 280px)); margin-bottom: 14px; gap: 12px; }.context-selector :deep(.el-form-item) { margin-bottom: 0; }.context-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 13px; }.binding-card, .profile-card { display: flex; min-height: 390px; flex-direction: column; }.binding-card :deep(.el-alert) { margin-bottom: 14px; }.binding-card :deep(.el-select) { width: 100%; }.binding-card > footer, .profile-card > footer { display: flex; align-items: center; justify-content: space-between; margin-top: auto; padding-top: 15px; color: var(--muted); font-size: 8px; gap: 10px; }.feature-prompt { padding: 12px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface-2); }.feature-prompt small, .profile-topics small { color: var(--primary-bright); font-size: 8px; font-weight: 700; }.feature-prompt p { margin: 7px 0 0; color: var(--text-soft); font-size: 9px; line-height: 1.7; white-space: pre-wrap; }.profile-details { display: grid; margin: 0; gap: 9px; }.profile-details > div { padding: 11px 12px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface-2); }.profile-details dt { color: var(--primary-bright); font-size: 8px; font-weight: 700; }.profile-details dd { margin: 6px 0 0; color: var(--text-soft); font-size: 9px; line-height: 1.65; }.profile-topics { margin-top: 12px; }.profile-topics > div { display: flex; flex-wrap: wrap; margin-top: 7px; color: var(--muted); font-size: 9px; gap: 5px; }
.skill-grid { display: grid; min-height: 120px; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 11px; }.skill-card { display: flex; min-width: 0; min-height: 220px; flex-direction: column; padding: 15px; border: 1px solid var(--border); border-radius: 11px; background: linear-gradient(145deg, var(--surface-2), var(--surface)); }.skill-card.is-inactive { opacity: .62; }.skill-card header { gap: 9px; }.skill-card__icon { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 8px; color: var(--primary-bright); background: var(--primary-soft); }.skill-card header > div { min-width: 0; flex: 1; }.skill-card header strong, .skill-card header small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.skill-card header strong { font-size: 11px; }.skill-card header small { margin-top: 2px; color: var(--muted); font-size: 8px; }.skill-card > p { overflow: hidden; min-height: 28px; margin: 12px 0 8px; color: var(--text-soft); font-size: 9px; line-height: 1.55; }.skill-card pre { display: -webkit-box; overflow: hidden; flex: 1; margin: 0; padding: 10px; border: 1px solid var(--border); border-radius: 8px; color: var(--muted); background: rgba(0,0,0,.18); font-family: inherit; font-size: 8px; line-height: 1.6; white-space: pre-wrap; -webkit-box-orient: vertical; -webkit-line-clamp: 5; }.skill-card footer { display: flex; align-items: center; justify-content: space-between; margin-top: 11px; color: var(--muted); font-size: 8px; }.skill-card footer > div { display: flex; gap: 4px; }
.dialog-form { margin-top: 14px; }.dialog-form-grid { display: grid; grid-template-columns: minmax(0, 1fr) 150px; gap: 12px; }.tweet-option { display: flex; flex-direction: column; max-width: 470px; }.tweet-option span { overflow: hidden; color: var(--muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }.draft-dialog-meta { display: flex; justify-content: space-between; color: var(--muted); font-size: 8px; }.dialog-footer-spacer { display: inline-block; min-width: 80px; flex: 1; }
:deep(.el-dialog__footer > div) { display: flex; align-items: center; width: 100%; }
@media (max-width: 1180px) { .ai-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.skill-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.settings-sections, .context-grid { grid-template-columns: minmax(0, 1fr); }.automation-block { grid-column: auto; }.switch-list { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 800px) { .ai-hero { align-items: flex-start; flex-direction: column; }.ai-hero__actions { width: 100%; justify-content: flex-start; flex-wrap: wrap; }.provider-readiness { margin-right: auto; }.ai-pane__toolbar { align-items: flex-start; flex-direction: column; }.ai-pane__actions { width: 100%; justify-content: flex-start; flex-wrap: wrap; }.ai-pane__actions .el-input { width: min(100%, 280px); }.skill-grid, .context-selector { grid-template-columns: minmax(0, 1fr); }.settings-form-grid { grid-template-columns: minmax(0, 1fr); }.span-2 { grid-column: auto; } }
@media (max-width: 560px) { .ai-hero { padding: 18px; }.ai-hero__copy { align-items: flex-start; }.ai-hero__icon { width: 42px; height: 42px; }.ai-summary-grid { grid-template-columns: minmax(0, 1fr); }.ai-pane { padding: 13px; }.dialog-form-grid { grid-template-columns: minmax(0, 1fr); } }
.ai-hero{border-color:var(--border);border-radius:16px;background:#fff;box-shadow:0 1px 2px rgba(18,24,40,.025),0 8px 28px rgba(18,24,40,.035)}
.ai-hero__icon{color:var(--primary);border-color:#dcd9ff;background:var(--primary-soft);box-shadow:none}.provider-readiness{background:var(--surface-2)}.settings-block{background:#fafbfc}.skill-card{background:#fff}.skill-card pre{background:#fafbfc}
</style>
