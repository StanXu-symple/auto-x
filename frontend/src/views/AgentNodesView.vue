<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import SkillDetailsModal from '@/components/SkillDetailsModal.vue'
import { usePagedTable } from '@/composables/usePagedTable'
import { aiApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import {
  agentWorkflowsApi,
  allPages,
  nodeTypeLabel,
  nodeTypeOptions,
} from '@/services/agentWorkflows'
import type { AgentNode, AgentNodeInput } from '@/services/agentWorkflows'
import type { AiSkill } from '@/types'
import { formatDateTime } from '@/utils/format'

const { rows, loading, pagination, load, change } = usePagedTable(
  agentWorkflowsApi.nodes,
  '无法加载节点',
)
const open = ref(false)
const editing = ref<AgentNode | null>(null)
const saving = ref(false)
const skills = ref<AiSkill[]>([])
const referenceError = ref('')
const referenceLoading = ref(false)
const detail = ref<AiSkill | null>(null)
const detailOpen = ref(false)
const form = reactive<AgentNodeInput>({
  name: '',
  description: '',
  node_type: 'custom',
  skill_ids: [],
  is_active: true,
})
const options = computed(() =>
  skills.value.map((skill) => ({
    value: skill.id,
    disabled: !skill.is_active,
    label: `${skill.content_type === 'skill' ? 'Skill' : '提示词'} · ${skill.name}${skill.is_active ? '' : '（停用）'}`,
  })),
)
const selected = computed(() =>
  form.skill_ids.map((id) => ({
    id,
    skill: skills.value.find((s) => String(s.id) === String(id)),
  })),
)
async function loadReferences() {
  referenceLoading.value = true
  referenceError.value = ''
  try {
    skills.value = await allPages(aiApi.skillPage)
  } catch (e) {
    referenceError.value = getErrorMessage(e, '关联内容加载失败')
  } finally {
    referenceLoading.value = false
  }
}
function edit(node?: AgentNode) {
  editing.value = node || null
  Object.assign(
    form,
    node
      ? {
          name: node.name,
          description: node.description,
          node_type: node.node_type,
          skill_ids: [...node.skill_ids],
          is_active: node.is_active,
        }
      : { name: '', description: '', node_type: 'custom', skill_ids: [], is_active: true },
  )
  open.value = true
  void loadReferences()
}
function move(index: number, offset: number) {
  const target = index + offset
  if (target < 0 || target >= form.skill_ids.length) return
  const copy = [...form.skill_ids]
  const [item] = copy.splice(index, 1)
  copy.splice(target, 0, item!)
  form.skill_ids = copy
}
function showDetails(skill?: AiSkill) {
  if (!skill) return
  detail.value = skill
  detailOpen.value = true
}
async function save() {
  if (!form.name.trim() || !form.skill_ids.length)
    return message.warning('请填写名称并关联至少一个提示词或 Skill')
  if (saving.value || referenceLoading.value || referenceError.value) return
  saving.value = true
  try {
    await agentWorkflowsApi.saveNode({ ...form, skill_ids: [...form.skill_ids] }, editing.value)
    open.value = false
    message.success('节点已保存')
    await load()
  } catch (e) {
    message.error(getErrorMessage(e))
  } finally {
    saving.value = false
  }
}
function disable(node: AgentNode) {
  Modal.confirm({
    title: `停用「${node.name}」？`,
    content: '关联流程将无法发起新的运行；已创建的运行继续使用快照。',
    onOk: async () => {
      await agentWorkflowsApi.disableNode(node.id)
      await load()
    },
  })
}
onMounted(load)
</script>
<template>
  <div class="page-stack">
    <PageHeader
      title="Agent 节点管理"
      description="将已有提示词与 Skills 组成可复用节点，再在流程中按顺序调用"
    >
      <template #actions
        ><a-button @click="load"><ReloadOutlined />刷新</a-button
        ><a-button type="primary" @click="edit()"><PlusOutlined />新建节点</a-button></template
      >
    </PageHeader>
    <a-card :bordered="false">
      <a-table
        :data-source="rows"
        :loading="loading"
        :pagination="pagination"
        row-key="id"
        :scroll="{ x: 850 }"
        @change="change"
      >
        <a-table-column title="节点名称"
          ><template #default="{ record }"
            ><strong>{{ record.name }}</strong>
            <p class="muted description">{{ record.description || '—' }}</p></template
          ></a-table-column
        >
        <a-table-column title="输出类型" :width="180"
          ><template #default="{ record }">{{
            nodeTypeLabel(record.node_type)
          }}</template></a-table-column
        >
        <a-table-column title="关联内容" :width="100"
          ><template #default="{ record }"
            >{{ record.skill_ids.length }} 项</template
          ></a-table-column
        >
        <a-table-column title="状态 / 版本" :width="150"
          ><template #default="{ record }"
            ><a-tag :color="record.is_active ? 'green' : 'default'">{{
              record.is_active ? '启用' : '停用'
            }}</a-tag
            >v{{ record.version }}</template
          ></a-table-column
        >
        <a-table-column title="更新时间" :width="180"
          ><template #default="{ record }">{{
            formatDateTime(record.updated_at)
          }}</template></a-table-column
        >
        <a-table-column title="操作" :width="150"
          ><template #default="{ record }"
            ><a-button type="link" @click="edit(record)">编辑</a-button
            ><a-button v-if="record.is_active" type="link" danger @click="disable(record)"
              >停用</a-button
            ></template
          ></a-table-column
        >
      </a-table>
    </a-card>
    <a-modal
      v-model:open="open"
      :title="editing ? '编辑 Agent 节点' : '新建 Agent 节点'"
      :width="760"
      ok-text="保存节点"
      cancel-text="取消"
      :confirm-loading="saving"
      :mask-closable="!saving"
      :ok-button-props="{ disabled: referenceLoading || Boolean(referenceError) }"
      @ok="save"
    >
      <a-form layout="vertical">
        <a-form-item label="节点名称" required
          ><a-input v-model:value="form.name" :maxlength="100" placeholder="例如：文章审核节点"
        /></a-form-item>
        <a-form-item label="说明"
          ><a-textarea
            v-model:value="form.description"
            :maxlength="2000"
            :rows="2"
            placeholder="描述这个节点的用途"
        /></a-form-item>
        <a-form-item label="输出类型" required
          ><a-select v-model:value="form.node_type" :options="nodeTypeOptions" />
          <div class="muted">
            {{ nodeTypeOptions.find((item) => item.value === form.node_type)?.description }}
          </div></a-form-item
        >
        <a-form-item label="关联提示词 / Skills" required>
          <a-select
            v-model:value="form.skill_ids"
            mode="multiple"
            show-search
            option-filter-prop="label"
            :options="options"
            :loading="referenceLoading"
            :max-tag-count="4"
            placeholder="选择已有提示词或 Skills，最多 20 项"
          />
          <div class="muted">
            内容在 <router-link to="/skills">提示词管理</router-link> 中维护，节点按下方顺序应用。
          </div>
        </a-form-item>
        <a-alert v-if="referenceError" type="error" :message="referenceError"
          ><template #action><a-button @click="loadReferences">重试</a-button></template></a-alert
        >
        <div v-for="(item, index) in selected" :key="String(item.id)" class="reference-row">
          <span>{{ index + 1 }}. {{ item.skill?.name || `已删除的关联项 #${item.id}` }}</span>
          <a-space>
            <a-button
              type="link"
              :disabled="!item.skill"
              @click="showDetails(item.skill)"
              >详情</a-button
            >
            <a-button size="small" :disabled="index === 0" @click="move(index, -1)">上移</a-button>
            <a-button size="small" :disabled="index === selected.length - 1" @click="move(index, 1)"
              >下移</a-button
            >
            <a-button type="link" danger @click="form.skill_ids.splice(index, 1)">移除</a-button>
          </a-space>
        </div>
        <a-form-item label="启用节点"><a-switch v-model:checked="form.is_active" /></a-form-item>
      </a-form>
    </a-modal>
    <SkillDetailsModal v-model:open="detailOpen" :skill="detail" />
  </div>
</template>
<style scoped>
.description {
  max-width: 320px;
  margin: 4px 0 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.reference-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-color, #eee);
}
.reference-row > span {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
