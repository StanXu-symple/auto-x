<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { usePagedTable } from '@/composables/usePagedTable'
import { aiApi, monitoredUsersApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type {
  AiSkill,
  AiSkillPayload,
  AiUserProfile,
  AiUserSkillBinding,
  EntityId,
  MonitoredUser,
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
      ? await aiApi.updateSkill(editingSkill.value.id, skillForm)
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
      description="管理可复用的创作指令、账号绑定与作者画像"
    >
      <template #actions>
        <a-button :loading="skillsLoading" @click="loadSkills"><ReloadOutlined /> 刷新</a-button>
      </template>
    </PageHeader>
    <a-card :bordered="false">
      <div class="toolbar">
        <div class="toolbar__controls">
          <a-button type="primary" @click="editSkill()"><PlusOutlined /> 新建 Skill</a-button>
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
        :scroll="{ x: 720 }"
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
        <a-table-column title="状态" :width="120">
          <template #default="{ record }"
            ><StatusPill :value="record.is_active" :label="record.is_active ? '启用' : '停用'"
          /></template>
        </a-table-column>
        <a-table-column title="操作" :width="120">
          <template #default="{ record }">
            <a-space>
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
              :options="skills.map((skill) => ({ label: skill.name, value: skill.id }))"
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
      ><a-form layout="vertical"
        ><a-form-item label="名称"><a-input v-model:value="skillForm.name" /></a-form-item
        ><a-form-item label="说明"><a-input v-model:value="skillForm.description" /></a-form-item
        ><a-form-item label="指令"
          ><a-textarea v-model:value="skillForm.instructions" :rows="10" /></a-form-item
        ><a-form-item label="启用"
          ><a-switch v-model:checked="skillForm.is_active" /></a-form-item></a-form
    ></a-modal>
  </div>
</template>
