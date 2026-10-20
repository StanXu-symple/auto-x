<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  DeleteOutlined,
  EditOutlined,
  GithubOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { usePagedTable } from '@/composables/usePagedTable'
import { aiApi, monitoredUsersApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { skillOptionLabel, skillSourceLabel } from '@/utils/skills'
import type {
  AiSkill,
  AiSkillPayload,
  AiUserProfile,
  AiUserSkillBinding,
  EntityId,
  MonitoredUser,
  GithubSkillImportResult,
} from '@/types'

const skills = ref<AiSkill[]>([])
const users = ref<MonitoredUser[]>([])
const selectedUser = ref<EntityId>()
const feature = ref('article_generation')
const profile = ref<AiUserProfile | null>(null)
const binding = ref<AiUserSkillBinding | null>(null)
const bindingIds = ref<EntityId[]>([])
const bindingLoading = ref(false)
const skillOpen = ref(false)
const skillSaving = ref(false)
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
const {
  rows: skillRows,
  total: skillsTotal,
  loading: skillsLoading,
  pagination: skillsPagination,
  load: loadSkills,
  change: changeSkillPage,
} = usePagedTable(aiApi.skillPage, '无法加载 Skills')

async function loadUsers() {
  try {
    users.value = (await monitoredUsersApi.list({ page: 1, page_size: 100 })).items
  } catch (e) {
    message.error(getErrorMessage(e, '无法加载监听账号'))
  }
}
function editSkill(skill?: AiSkill) {
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
async function saveSkill() {
  if (skillSaving.value) return
  if (!skillForm.name.trim() || !skillForm.instructions.trim())
    return message.warning('请填写名称和 Skill 指令')
  skillSaving.value = true
  try {
    editingSkill.value
      ? await aiApi.updateSkill(
          editingSkill.value.id,
          editingSkill.value.source_type === 'github'
            ? { is_active: skillForm.is_active }
            : skillForm,
        )
      : await aiApi.createSkill(skillForm)
    skillOpen.value = false
    await loadSkills()
    if (selectedUser.value) await loadSkillOptions()
    message.success('Skill 已保存')
  } catch (e) {
    message.error(getErrorMessage(e, '保存 Skill 失败'))
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
    if (selectedUser.value) await loadSkillOptions()
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
  if (importing.value || skill.source_type !== 'github') return
  updatingSkill.value = skill
  importResult.value = null
  importError.value = ''
  importOpen.value = true
  await importGithub()
}
function removeSkill(skill: AiSkill) {
  Modal.confirm({
    title: `删除 Skill「${skill.name}」？`,
    okType: 'danger',
    okText: '删除',
    cancelText: '取消',
    onOk: async () => {
      try {
        await aiApi.removeSkill(skill.id)
        await loadSkills()
        if (selectedUser.value) await loadSkillOptions()
        message.success('Skill 已删除')
      } catch (e) {
        message.error(getErrorMessage(e, '删除 Skill 失败'))
        throw e
      }
    },
  })
}
async function loadSkillOptions() {
  const all: AiSkill[] = []
  let page = 1
  while (true) {
    const result = await aiApi.skillPage({ page, page_size: 100 })
    all.push(...result.items)
    if (all.length >= result.total || !result.items.length) break
    page++
  }
  skills.value = all
}
async function loadContext() {
  if (!selectedUser.value) return
  bindingLoading.value = true
  try {
    await loadSkillOptions()
    ;[profile.value, binding.value] = await Promise.all([
      aiApi.userProfile(selectedUser.value),
      aiApi.userSkillBinding(selectedUser.value, feature.value),
    ])
    bindingIds.value = [...(binding.value?.skill_ids || [])]
  } catch (e) {
    message.error(getErrorMessage(e, '读取用户上下文失败'))
  } finally {
    bindingLoading.value = false
  }
}
async function saveBinding() {
  if (!selectedUser.value || bindingLoading.value) return
  bindingLoading.value = true
  try {
    binding.value = await aiApi.saveUserSkillBinding(
      selectedUser.value,
      feature.value,
      bindingIds.value,
    )
    message.success('Skill 绑定已保存')
  } catch (e) {
    message.error(getErrorMessage(e, '保存 Skill 绑定失败'))
  } finally {
    bindingLoading.value = false
  }
}
onMounted(async () => {
  await Promise.all([loadSkills(), loadUsers()])
})
</script>

<template>
  <div class="page-stack">
    <PageHeader
      eyebrow="CONTENT / 02"
      title="Skills管理"
      description="管理自定义写作指令与从 GitHub 导入的 Skills"
    >
      <template #actions>
        <a-button :loading="skillsLoading" @click="loadSkills"><ReloadOutlined /> 刷新</a-button>
      </template>
    </PageHeader>
    <a-card :bordered="false">
      <div class="toolbar">
        <div class="toolbar__controls">
          <a-button type="primary" @click="editSkill()"><PlusOutlined /> 新建 Skill</a-button>
          <a-button :disabled="importing" @click="openImport()"
            ><GithubOutlined /> 从 GitHub 导入</a-button
          >
          <a-button :loading="skillsLoading" @click="loadSkills"><ReloadOutlined /> 刷新</a-button>
          <span class="toolbar__hint">用可复用的提示指令控制输出结构</span>
        </div>
        <span class="toolbar__hint">共 {{ skillsTotal }} 条 Skills</span>
      </div>
      <a-table
        :data-source="skillRows"
        :loading="skillsLoading"
        row-key="id"
        :pagination="skillsPagination"
        :scroll="{ x: 900 }"
        @change="changeSkillPage"
      >
        <a-table-column title="名称" data-index="name" :width="220" :ellipsis="true">
          <template #default="{ record }"
            ><strong>{{ record.name }}</strong></template
          >
        </a-table-column>
        <a-table-column title="说明" data-index="description" :ellipsis="true">
          <template #default="{ record }"
            ><span class="muted">{{ record.description || '暂无说明' }}</span></template
          >
        </a-table-column>
        <a-table-column title="来源" :width="160">
          <template #default="{ record }">
            <a-tag>{{ skillSourceLabel(record) }}</a-tag>
            <div v-if="record.source_metadata" class="muted">
              {{ record.source_metadata.repository }}
              <br />{{ record.source_metadata.commit.slice(0, 7) }}
            </div>
          </template>
        </a-table-column>
        <a-table-column title="状态" :width="100">
          <template #default="{ record }"
            ><StatusPill :value="record.is_active" :label="record.is_active ? '启用' : '停用'"
          /></template>
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
              <a-tooltip title="编辑"
                ><a-button type="link" aria-label="编辑 Skill" @click="editSkill(record)"
                  ><EditOutlined /></a-button
              ></a-tooltip>
              <a-tooltip title="删除"
                ><a-button type="link" danger aria-label="删除 Skill" @click="removeSkill(record)"
                  ><DeleteOutlined /></a-button
              ></a-tooltip>
            </a-space>
          </template>
        </a-table-column>
      </a-table>
      <a-collapse style="margin-top: 20px">
        <a-collapse-panel key="legacy" header="旧策略 · 账号与功能点 Skill 绑定">
          <a-alert
            type="info"
            show-icon
            message="此处保留旧策略和手动生成所需的账号 Skill 绑定；监听任务使用任务中明确选择的 Skills。"
            style="margin-bottom: 16px"
          />
          <a-form layout="inline">
            <a-form-item label="监听账号"
              ><a-select
                v-model:value="selectedUser"
                style="width: 220px"
                :options="users.map((user) => ({ label: `@${user.username}`, value: user.id }))"
                @change="loadContext"
            /></a-form-item>
            <a-form-item label="功能点"
              ><a-select
                v-model:value="feature"
                style="width: 220px"
                :options="[
                  { label: '文章生成', value: 'article_generation' },
                  { label: '内容摘要', value: 'content_summary' },
                  { label: '标题优化', value: 'title_optimization' },
                ]"
                @change="loadContext"
            /></a-form-item>
          </a-form>
          <a-descriptions v-if="profile" bordered :column="1" style="margin-top: 18px"
            ><a-descriptions-item label="作者">@{{ profile.username }}</a-descriptions-item
            ><a-descriptions-item label="身份">{{ profile.identity_summary }}</a-descriptions-item
            ><a-descriptions-item label="近期关注">{{ profile.focus_summary }}</a-descriptions-item
            ><a-descriptions-item label="长期主题">{{
              profile.recurring_topics.join('、') || '暂无'
            }}</a-descriptions-item></a-descriptions
          >
          <a-space v-if="binding" direction="vertical" style="width: 100%; margin-top: 18px"
            ><span class="muted">绑定 Skills</span
            ><a-checkbox-group
              v-model:value="bindingIds"
              :options="
                skills.map((skill) => ({
                  label: skillOptionLabel(skill),
                  value: skill.id,
                  disabled: !skill.is_active,
                }))
              "
            /><a-button type="primary" :loading="bindingLoading" @click="saveBinding"
              >保存绑定</a-button
            ></a-space
          >
          <a-empty v-else description="选择账号后查看画像和绑定" />
        </a-collapse-panel>
      </a-collapse>
    </a-card>
    <a-modal
      v-model:open="skillOpen"
      :title="editingSkill ? '编辑 Skill' : '新建 Skill'"
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
        v-if="editingSkill?.source_type === 'github'"
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
            :readonly="editingSkill?.source_type === 'github'" /></a-form-item
        ><a-form-item label="说明"
          ><a-input
            v-model:value="skillForm.description"
            :readonly="editingSkill?.source_type === 'github'" /></a-form-item
        ><a-form-item label="指令"
          ><a-textarea
            v-model:value="skillForm.instructions"
            :rows="10"
            :readonly="editingSkill?.source_type === 'github'" /></a-form-item
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
  </div>
</template>
