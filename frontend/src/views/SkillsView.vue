<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  DeleteOutlined,
  EditOutlined,
  GithubOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
} from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { aiApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { groupSkills, skillContentType, skillSourceSummary, skillTypeLabel } from '@/utils/skills'
import type { AiContentType, AiSkill, AiSkillPayload, GithubSkillImportResult } from '@/types'

const skills = ref<AiSkill[]>([])
const activeTab = ref<AiContentType>('skill')
const resourceTabs = [
  { key: 'skill', label: 'Skills' },
  { key: 'prompt', label: '提示词' },
]
const visibleSkills = computed(() =>
  skills.value.filter((skill) => skillContentType(skill) === activeTab.value),
)
const resourceLabel = computed(() => (activeTab.value === 'skill' ? 'Skills' : '提示词'))
const skillOpen = ref(false)
const skillSaving = ref(false)
const promptGenerating = ref(false)
const promptGenerateOpen = ref(false)
const promptRequirement = ref('')
const promptGeneration = ref<Awaited<ReturnType<typeof aiApi.promptGeneration>> | null>(null)
const promptGenerationError = ref('')
const promptCancelling = ref(false)
let promptGenerationTimer: ReturnType<typeof setTimeout> | null = null
let promptGenerationEpoch = 0
let promptWaitStarted = 0
let promptSubmission: { request_id: string; requirement: string } | null = null
let disposed = false
const importOpen = ref(false)
const importing = ref(false)
const updatingSkill = ref<AiSkill | null>(null)
const importForm = reactive({ url: '', ref: '', path: '' })
const importResult = ref<GithubSkillImportResult | null>(null)
const importError = ref('')
const editingSkill = ref<AiSkill | null>(null)
const skillForm = reactive<AiSkillPayload>({
  name: '',
  description: '',
  instructions: '',
  is_active: true,
})
const skillsLoading = ref(false)
const skillRows = computed(() => groupSkills(visibleSkills.value))
const expandedRowKeys = ref<string[]>([])
const skillPage = reactive({ current: 1, pageSize: 15 })
const skillsPagination = computed(() => ({
  ...skillPage,
  total: skillRows.value.length,
  showSizeChanger: true,
  pageSizeOptions: ['15', '30', '50', '100'],
  showQuickJumper: true,
  showTotal: (count: number) =>
    activeTab.value === 'skill' ? `共 ${count} 项（GitHub 按来源分组）` : `共 ${count} 条提示词`,
}))
watch(activeTab, () => {
  skillPage.current = 1
})
let skillsRequest = 0

function changeSkillPage(page: { current?: number; pageSize?: number }) {
  const size = page.pageSize || skillPage.pageSize
  skillPage.current = size !== skillPage.pageSize ? 1 : page.current || 1
  skillPage.pageSize = size
}

function toggleGroup(key: string) {
  expandedRowKeys.value = expandedRowKeys.value.includes(key)
    ? expandedRowKeys.value.filter((item) => item !== key)
    : [...expandedRowKeys.value, key]
}

async function loadSkills() {
  const request = ++skillsRequest
  skillsLoading.value = true
  try {
    await loadAllSkills(request)
  } catch (e) {
    if (request === skillsRequest) message.error(getErrorMessage(e, '无法加载 Skills 与提示词'))
  } finally {
    if (request === skillsRequest) skillsLoading.value = false
  }
}

function editSkill(skill?: AiSkill) {
  if (!skill && activeTab.value !== 'prompt') return
  editingSkill.value = skill || null
  Object.assign(
    skillForm,
    skill
      ? {
          name: skill.name,
          description: skill.description || '',
          instructions: skill.instructions,
          is_active: skill.is_active,
        }
      : { name: '', description: '', instructions: '', is_active: true },
  )
  skillOpen.value = true
}

function requestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  const bytes = globalThis.crypto?.getRandomValues
    ? globalThis.crypto.getRandomValues(new Uint8Array(16))
    : Uint8Array.from({ length: 16 }, () => Math.floor(Math.random() * 256))
  bytes[6] = (bytes[6]! & 0x0f) | 0x40
  bytes[8] = (bytes[8]! & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function openPromptGenerator() {
  if (activeTab.value !== 'prompt' || promptGenerating.value) return
  promptGenerateOpen.value = true
}

function stopPromptPolling() {
  if (promptGenerationTimer !== null) {
    clearTimeout(promptGenerationTimer)
    promptGenerationTimer = null
  }
}

async function pollPromptGeneration(id: string, epoch = promptGenerationEpoch) {
  try {
    const current = await aiApi.promptGeneration(id)
    if (disposed || epoch !== promptGenerationEpoch) return
    promptGeneration.value = current
    if (current.status === 'queued' || current.status === 'running') {
      if (Date.now() - promptWaitStarted > 20 * 60_000) {
        throw new Error('等待时间较长，请检查 AI Worker 状态，或点击“继续获取结果”')
      }
      promptGenerationTimer = setTimeout(() => void pollPromptGeneration(id, epoch), 1500)
    } else {
      promptGenerating.value = false
      promptSubmission = null
      if (current.status === 'failed')
        promptGenerationError.value = current.error || '提示词生成失败'
    }
  } catch (e) {
    if (disposed || epoch !== promptGenerationEpoch) return
    promptGenerating.value = false
    promptGenerationError.value = getErrorMessage(e, '无法查询生成状态，请继续获取结果')
  }
}

async function generatePrompt() {
  if (promptGenerating.value || promptCancelling.value) return
  const requirement = promptRequirement.value.trim()
  if (!requirement) return message.warning('请描述你需要的提示词')
  if (requirement.length > 8000) return message.warning('需求最多填写 8000 字')
  stopPromptPolling()
  const epoch = ++promptGenerationEpoch
  promptWaitStarted = Date.now()
  promptGenerating.value = true
  promptGenerationError.value = ''
  if (promptGeneration.value && ['queued', 'running'].includes(promptGeneration.value.status)) {
    await pollPromptGeneration(promptGeneration.value.id, epoch)
    return
  }
  if (!promptSubmission || promptSubmission.requirement !== requirement)
    promptSubmission = { request_id: requestId(), requirement }
  promptGeneration.value = null
  try {
    const created = await aiApi.createPromptGeneration(promptSubmission)
    if (disposed || epoch !== promptGenerationEpoch) return
    promptGeneration.value = created
    await pollPromptGeneration(created.id, epoch)
  } catch (e) {
    if (disposed || epoch !== promptGenerationEpoch) return
    promptGenerating.value = false
    promptGenerationError.value = getErrorMessage(e, '无法提交提示词生成任务，请重试')
  }
}

async function cancelPromptGeneration() {
  if (promptCancelling.value || (promptGenerating.value && !promptGeneration.value)) return
  const current = promptGeneration.value
  ++promptGenerationEpoch
  stopPromptPolling()
  promptGenerating.value = false
  if (!current || !['queued', 'running'].includes(current.status)) {
    promptGenerateOpen.value = false
    return
  }
  promptCancelling.value = true
  try {
    const cancelled = await aiApi.cancelPromptGeneration(current.id)
    if (disposed) return
    promptGeneration.value = cancelled
    promptSubmission = null
    promptGenerateOpen.value = false
  } catch (e) {
    if (!disposed) promptGenerationError.value = getErrorMessage(e, '取消失败，请重试')
  } finally {
    promptCancelling.value = false
  }
}

function useGeneratedPrompt() {
  const result = promptGeneration.value?.result
  if (!result) return
  Object.assign(skillForm, {
    name: result.name,
    description: result.description || '',
    instructions: result.instructions,
    is_active: true,
  })
  editingSkill.value = null
  promptGenerateOpen.value = false
  skillOpen.value = true
  promptGeneration.value = null
  promptSubmission = null
  promptRequirement.value = ''
  promptGenerationError.value = ''
}
async function saveSkill() {
  if (skillSaving.value) return
  if (!skillForm.name.trim() || !skillForm.instructions.trim())
    return message.warning('请填写名称和提示词内容')
  skillSaving.value = true
  try {
    editingSkill.value
      ? await aiApi.updateSkill(
          editingSkill.value.id,
          skillContentType(editingSkill.value) === 'skill'
            ? { is_active: skillForm.is_active }
            : skillForm,
        )
      : await aiApi.createSkill({ ...skillForm, content_type: 'prompt' })
    skillOpen.value = false
    await loadSkills()
    message.success('已保存')
  } catch (e) {
    message.error(getErrorMessage(e, '保存失败'))
  } finally {
    skillSaving.value = false
  }
}
function openImport() {
  if (importing.value) return
  updatingSkill.value = null
  Object.assign(importForm, { url: '', ref: '', path: '' })
  importResult.value = null
  importError.value = ''
  importOpen.value = true
}
async function importGithub() {
  if (importing.value) return
  if (!updatingSkill.value && !importForm.url.trim())
    return message.warning('请输入 GitHub 仓库或目录地址')
  importing.value = true
  importError.value = ''
  importResult.value = null
  try {
    importResult.value = updatingSkill.value
      ? await aiApi.updateGithubSkill(updatingSkill.value.id)
      : await aiApi.importGithubSkills({
          url: importForm.url.trim(),
          ref: importForm.ref.trim() || undefined,
          path: importForm.path.trim() || undefined,
        })
    await loadSkills()
    message.success(
      updatingSkill.value
        ? importResult.value.updated || importResult.value.created
          ? 'GitHub Skills 已更新'
          : '已是最新版本'
        : 'GitHub Skills 已保存',
    )
  } catch (e) {
    importError.value = getErrorMessage(
      e,
      updatingSkill.value ? '更新失败，请重试' : '导入失败，请重试',
    )
  } finally {
    importing.value = false
  }
}
async function updateGithub(skill: AiSkill) {
  if (importing.value || skill.source_type !== 'github' || skillContentType(skill) !== 'skill')
    return
  updatingSkill.value = skill
  importResult.value = null
  importError.value = ''
  importOpen.value = true
  await importGithub()
}
function removeSkill(skill: AiSkill) {
  Modal.confirm({
    title: `删除${skillTypeLabel(skill)}「${skill.name}」？`,
    content: '删除后将停用该项，使用它的监听任务会暂停队列，历史生成记录保留。',
    okType: 'danger',
    okText: '删除',
    cancelText: '取消',
    onOk: async () => {
      try {
        await aiApi.removeSkill(skill.id)
        await loadSkills()
        message.success('已删除')
      } catch (e) {
        message.error(getErrorMessage(e, '删除失败'))
        throw e
      }
    },
  })
}
async function loadAllSkills(request: number) {
  const all: AiSkill[] = []
  let page = 1
  while (true) {
    const result = await aiApi.skillPage({ page, page_size: 100 })
    if (request !== skillsRequest) return
    all.push(...result.items)
    if (all.length >= result.total || !result.items.length) break
    page++
  }
  skills.value = all
  const groupKeys = new Set(
    skillRows.value.filter((row) => row.kind === 'group').map((row) => row.key),
  )
  expandedRowKeys.value = expandedRowKeys.value.filter((key) => groupKeys.has(key))
  skillPage.current = Math.min(
    skillPage.current,
    Math.max(1, Math.ceil(skillRows.value.length / skillPage.pageSize)),
  )
}
onMounted(loadSkills)
onBeforeUnmount(() => {
  disposed = true
  ++promptGenerationEpoch
  ++skillsRequest
  stopPromptPolling()
})
</script>

<template>
  <div class="page-stack">
    <PageHeader
      eyebrow="CONTENT / 02"
      title="提示词管理"
      description="管理从 GitHub 导入的 Skills，以及手动编写或 AI 辅助生成的提示词"
    >
      <template #actions>
        <a-button :loading="skillsLoading" @click="loadSkills"><ReloadOutlined /> 刷新</a-button>
      </template>
    </PageHeader>
    <a-card :bordered="false">
      <a-tabs v-model:active-key="activeTab" :destroy-inactive-tab-pane="true">
        <a-tab-pane v-for="tab in resourceTabs" :key="tab.key" :tab="tab.label">
          <div class="toolbar">
            <div class="toolbar__controls">
              <a-button v-if="activeTab === 'prompt'" type="primary" @click="editSkill()"
                ><PlusOutlined /> 新建提示词</a-button
              >
              <a-button v-if="activeTab === 'prompt'" @click="openPromptGenerator">
                <RobotOutlined /> AI 生成
              </a-button>
              <a-button
                v-if="activeTab === 'skill'"
                type="primary"
                :disabled="importing"
                @click="openImport()"
                ><GithubOutlined /> 从 GitHub 导入</a-button
              >
              <a-button :loading="skillsLoading" @click="loadSkills"
                ><ReloadOutlined /> 刷新</a-button
              >
              <span class="toolbar__hint">{{
                activeTab === 'skill'
                  ? 'GitHub Skills 按导入来源分组，点击名称或箭头展开'
                  : '手动编写或用 AI 生成提示词，在创作与 Agent 节点中按需使用'
              }}</span>
            </div>
            <span class="toolbar__hint">共 {{ visibleSkills.length }} 条 {{ resourceLabel }}</span>
          </div>
          <a-table
            :data-source="skillRows"
            :loading="skillsLoading"
            v-model:expanded-row-keys="expandedRowKeys"
            row-key="key"
            size="middle"
            table-layout="fixed"
            :pagination="skillsPagination"
            :scroll="{ x: activeTab === 'skill' ? 1150 : 760 }"
            @change="changeSkillPage"
          >
            <a-table-column
              title="名称"
              data-index="name"
              :width="activeTab === 'skill' ? 280 : 220"
              :ellipsis="true"
            >
              <template #default="{ record }">
                <button
                  v-if="record.kind === 'group'"
                  type="button"
                  class="skill-group-toggle"
                  :aria-expanded="expandedRowKeys.includes(record.key)"
                  @click="toggleGroup(record.key)"
                >
                  <GithubOutlined />
                  <strong class="skill-name" :title="record.name">{{ record.name }}</strong>
                  <span class="skill-group-count">{{ record.children.length }}</span>
                </button>
                <strong v-else class="skill-name skill-leaf-name" :title="record.name">{{
                  record.name
                }}</strong>
              </template>
            </a-table-column>
            <a-table-column title="说明" data-index="description" :ellipsis="true">
              <template #default="{ record }"
                ><span class="muted">{{ record.description || '暂无说明' }}</span></template
              >
            </a-table-column>
            <a-table-column
              title="来源"
              :width="activeTab === 'skill' ? 240 : 100"
              :ellipsis="{ showTitle: false }"
            >
              <template #default="{ record }">
                <a-tooltip
                  placement="topRight"
                  :trigger="['hover', 'focus']"
                  :overlay-style="{ maxWidth: '480px' }"
                >
                  <template #title>
                    <div v-if="record.source_metadata" class="skill-source-detail">
                      <div>仓库：{{ record.source_metadata.repository }}</div>
                      <div>分支 / 版本：{{ record.source_metadata.ref }}</div>
                      <div>
                        导入目录：{{
                          (record.source_metadata.import_path ?? record.source_metadata.path) || '/'
                        }}
                      </div>
                      <div v-if="record.kind === 'skill'">
                        Skill 目录：{{ record.source_metadata.path || '/' }}
                      </div>
                      <div v-if="record.kind === 'skill'">
                        Commit：{{ record.source_metadata.commit }}
                      </div>
                      <div>原始地址：{{ record.source_metadata.url }}</div>
                    </div>
                    <span v-else>自定义 · 保存在数据库中的写作指令</span>
                  </template>
                  <span class="skill-source-text muted" tabindex="0">{{
                    skillSourceSummary(record)
                  }}</span>
                </a-tooltip>
              </template>
            </a-table-column>
            <a-table-column title="状态" :width="120">
              <template #default="{ record }">
                <span v-if="record.kind === 'group'" class="muted"
                  >{{ record.activeCount }}/{{ record.children.length }} 启用</span
                >
                <StatusPill
                  v-else
                  :value="record.is_active"
                  :label="record.is_active ? '启用' : '停用'"
                />
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="210">
              <template #default="{ record }">
                <a-space>
                  <a-tooltip v-if="record.source_type === 'github'" title="从 GitHub 更新">
                    <a-button
                      type="link"
                      aria-label="更新 GitHub Skill"
                      :loading="importing && updatingSkill?.id === record.id"
                      :disabled="importing"
                      @click="updateGithub(record)"
                      ><ReloadOutlined /> 更新</a-button
                    >
                  </a-tooltip>
                  <a-tooltip v-if="record.kind === 'skill'" title="编辑"
                    ><a-button
                      type="link"
                      :aria-label="`编辑${skillTypeLabel(record)}`"
                      @click="editSkill(record)"
                      ><EditOutlined /></a-button
                  ></a-tooltip>
                  <a-tooltip v-if="record.kind === 'skill'" title="删除"
                    ><a-button
                      type="link"
                      danger
                      :aria-label="`删除${skillTypeLabel(record)}`"
                      @click="removeSkill(record)"
                      ><DeleteOutlined /></a-button
                  ></a-tooltip>
                </a-space>
              </template>
            </a-table-column>
          </a-table>
        </a-tab-pane>
      </a-tabs>
    </a-card>
    <a-modal
      v-model:open="skillOpen"
      :title="editingSkill ? `编辑${skillTypeLabel(editingSkill)}` : '新建提示词'"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="skillSaving"
      :cancel-button-props="{ disabled: skillSaving }"
      :closable="!skillSaving"
      :mask-closable="!skillSaving"
      :keyboard="!skillSaving"
      @ok="saveSkill"
    >
      <a-alert
        v-if="editingSkill && skillContentType(editingSkill) === 'skill'"
        type="info"
        show-icon
        message="GitHub Skill 内容可通过列表中的更新按钮同步，可在这里切换启用状态。"
        style="margin-bottom: 16px" />
      <a-descriptions v-if="editingSkill?.source_metadata" :column="1" size="small">
        <a-descriptions-item label="仓库">{{
          editingSkill.source_metadata.repository
        }}</a-descriptions-item>
        <a-descriptions-item label="目录">{{
          editingSkill.source_metadata.path || '/'
        }}</a-descriptions-item>
        <a-descriptions-item label="版本">{{
          editingSkill.source_metadata.commit.slice(0, 12)
        }}</a-descriptions-item>
        <a-descriptions-item label="资源"
          >{{ editingSkill.source_metadata.file_count }} 个文件已保存；{{
            editingSkill.source_metadata.reference_files.length
          }}
          份参考文本参与写作</a-descriptions-item
        >
      </a-descriptions>
      <a-alert
        v-for="warning in editingSkill?.source_metadata?.warnings || []"
        :key="warning"
        type="warning"
        :message="warning"
        style="margin-bottom: 8px" />
      <a-form layout="vertical"
        ><a-form-item label="名称"
          ><a-input
            v-model:value="skillForm.name"
            :readonly="!!editingSkill && skillContentType(editingSkill) === 'skill'" /></a-form-item
        ><a-form-item label="说明"
          ><a-input
            v-model:value="skillForm.description"
            :readonly="!!editingSkill && skillContentType(editingSkill) === 'skill'" /></a-form-item
        ><a-form-item
          :label="
            editingSkill && skillContentType(editingSkill) === 'skill' ? 'Skill 指令' : '提示词内容'
          "
          ><a-textarea
            v-model:value="skillForm.instructions"
            :rows="10"
            :readonly="!!editingSkill && skillContentType(editingSkill) === 'skill'" /></a-form-item
        ><a-form-item label="启用"
          ><a-switch v-model:checked="skillForm.is_active" /></a-form-item></a-form
    ></a-modal>
    <a-modal
      v-model:open="importOpen"
      :title="updatingSkill ? `更新 Skill「${updatingSkill.name}」` : '从 GitHub 导入 Skills'"
      :width="680"
      :ok-text="updatingSkill ? '再次检查更新' : '导入'"
      cancel-text="关闭"
      :confirm-loading="importing"
      :closable="!importing"
      :mask-closable="!importing"
      :keyboard="!importing"
      :cancel-button-props="{ disabled: importing }"
      @ok="importGithub"
    >
      <p v-if="!updatingSkill" class="muted">
        粘贴公开仓库或目录地址，自动识别其中的
        SKILL.md。完整保存目录文件，将写作指令和可读取的参考文本用于文章生成。配套脚本和生图工具暂不执行。
      </p>
      <a-alert
        v-else
        type="info"
        show-icon
        message="按原仓库、分支或版本和导入目录检查更新，同目录的 Skills 会一起更新。"
        style="margin-bottom: 16px"
      >
        <template #description>
          {{ updatingSkill.source_metadata?.repository }} ·
          {{ updatingSkill.source_metadata?.ref }} ·
          {{ updatingSkill.source_metadata?.import_path ?? updatingSkill.source_metadata?.path }}
        </template>
      </a-alert>
      <a-form v-if="!updatingSkill" layout="vertical">
        <a-form-item label="GitHub 地址" required>
          <a-input
            v-model:value="importForm.url"
            :disabled="importing"
            placeholder="https://github.com/SpaceZephyr/creator-buddy/tree/main/xhs-Skills"
          />
          <div class="github-url-help">
            <strong>以下三种地址都支持，.git 后缀可有可无：</strong>
            <ul>
              <li>
                <span>仓库首页</span><code>https://github.com/SpaceZephyr/creator-buddy</code>
              </li>
              <li>
                <span>Git 克隆地址</span
                ><code>https://github.com/SpaceZephyr/creator-buddy.git</code>
              </li>
              <li>
                <span>指定目录（推荐，只导入需要的 Skills）</span
                ><code>https://github.com/SpaceZephyr/creator-buddy/tree/main/xhs-Skills</code>
              </li>
            </ul>
            <p>前两种默认扫描仓库全部 Skills；目录地址只扫描该目录，分支和子目录通常可留空。</p>
            <p>
              仅支持公开 GitHub 仓库的 HTTPS 地址，不支持 SSH 地址或 /blob/ 单文件链接。分支名包含 /
              时，请填仓库首页，并在下方分别填写分支和子目录。
            </p>
          </div>
        </a-form-item>
        <a-form-item label="分支 / 标签 / Commit（可选）"
          ><a-input
            v-model:value="importForm.ref"
            :disabled="importing"
            placeholder="留空使用地址中的分支或仓库默认分支"
        /></a-form-item>
        <a-form-item label="子目录（可选）"
          ><a-input
            v-model:value="importForm.path"
            :disabled="importing"
            placeholder="留空使用地址中的目录，如 xhs-Skills"
        /></a-form-item>
      </a-form>
      <a-alert v-if="importError" type="error" show-icon :message="importError" />
      <a-alert
        v-if="importResult"
        type="success"
        show-icon
        :message="`新增 ${importResult.created} 个，更新 ${importResult.updated} 个，版本相同 ${importResult.unchanged} 个`"
      >
        <template #description>
          <div>{{ importResult.repository }} · {{ importResult.commit.slice(0, 12) }}</div>
          <div>{{ importResult.skills.map((skill) => skill.name).join('、') }}</div>
        </template>
      </a-alert>
      <a-alert
        v-for="warning in importResult?.warnings || []"
        :key="warning"
        type="warning"
        :message="warning"
        style="margin-top: 8px"
      />
    </a-modal>
    <a-modal
      :open="promptGenerateOpen"
      title="AI 生成提示词"
      :width="720"
      :ok-text="
        promptGeneration?.result
          ? '编辑并保存'
          : promptGeneration && ['queued', 'running'].includes(promptGeneration.status)
            ? '继续获取结果'
            : '确认生成'
      "
      :cancel-text="
        promptGeneration && ['queued', 'running'].includes(promptGeneration.status)
          ? '取消生成'
          : '关闭'
      "
      :confirm-loading="promptGenerating"
      :ok-button-props="{ disabled: promptCancelling }"
      :cancel-button-props="{
        disabled: promptGenerating && !promptGeneration,
        loading: promptCancelling,
      }"
      :closable="!promptGenerating && !promptCancelling"
      :mask-closable="false"
      :keyboard="!promptGenerating && !promptCancelling"
      @ok="promptGeneration?.result ? useGeneratedPrompt() : generatePrompt()"
      @cancel="cancelPromptGeneration"
    >
      <a-alert
        v-if="promptGenerationError"
        type="error"
        show-icon
        :message="promptGenerationError"
        style="margin-bottom: 16px"
      />
      <a-alert
        v-else-if="promptGenerating"
        type="info"
        show-icon
        :message="
          promptGeneration?.status === 'queued' ? '等待 AI 开始生成…' : 'AI 正在整理提示词，请稍候…'
        "
        description="生成完成后可以先检查和编辑，再保存到提示词管理。"
        style="margin-bottom: 16px"
      />
      <template v-if="!promptGeneration?.result">
        <a-form layout="vertical">
          <a-form-item label="你需要什么提示词？" required>
            <a-textarea
              v-model:value="promptRequirement"
              :rows="8"
              :disabled="
                promptGenerating ||
                promptCancelling ||
                (!!promptGeneration && ['queued', 'running'].includes(promptGeneration.status))
              "
              maxlength="8000"
              show-count
              placeholder="例如：生成一个小红书爆款标题提示词，要求面向科技从业者，输出 10 个标题并逐个评分。"
            />
          </a-form-item>
        </a-form>
        <p class="muted">AI 会生成提示词名称、用途说明和可直接使用的完整提示词，不会自动保存。</p>
      </template>
      <template v-else>
        <a-alert
          type="success"
          show-icon
          message="提示词已生成，请确认后使用"
          style="margin-bottom: 16px"
        />
        <a-descriptions :column="1" bordered size="small">
          <a-descriptions-item label="名称">{{ promptGeneration.result.name }}</a-descriptions-item>
          <a-descriptions-item label="说明">{{
            promptGeneration.result.description || '暂无说明'
          }}</a-descriptions-item>
        </a-descriptions>
        <a-form layout="vertical" style="margin-top: 16px">
          <a-form-item label="生成的提示词">
            <a-textarea :value="promptGeneration.result.instructions" :rows="14" readonly />
          </a-form-item>
        </a-form>
      </template>
    </a-modal>
  </div>
</template>

<style scoped>
.skill-group-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: calc(100% - 32px);
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
  vertical-align: middle;
}
.skill-group-toggle:hover,
.skill-group-toggle:focus-visible {
  color: var(--accent);
}
.skill-name,
.skill-source-text {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}
.skill-name {
  min-width: 0;
}
.skill-leaf-name {
  max-width: calc(100% - 48px);
}
.skill-group-count {
  flex-shrink: 0;
  padding: 0 7px;
  border-radius: 4px;
  background: var(--paper-soft);
  font-size: 12px;
}
.skill-source-detail {
  overflow-wrap: anywhere;
}
.github-url-help {
  margin-top: 10px;
  font-size: 12px;
  color: var(--muted);
}
.github-url-help ul {
  margin: 8px 0;
  padding-left: 18px;
}
.github-url-help li + li {
  margin-top: 6px;
}
.github-url-help code {
  display: block;
  overflow-wrap: anywhere;
  color: var(--ink);
}
.github-url-help p {
  margin: 6px 0 0;
}
</style>
