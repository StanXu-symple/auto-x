<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import AgentWorkflowRunner from '@/components/AgentWorkflowRunner.vue'
import SkillDetailsModal from '@/components/SkillDetailsModal.vue'
import { usePagedTable } from '@/composables/usePagedTable'
import { aiApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import {
  agentWorkflowsApi,
  allPages,
  nodeTypeLabel,
  runStatusLabel,
} from '@/services/agentWorkflows'
import type { AgentNode, AgentWorkflow, AgentWorkflowInput } from '@/services/agentWorkflows'
import type { AiSkill } from '@/types'
import { formatDateTime } from '@/utils/format'

const route = useRoute()
const tab = ref(route.query.article_id ? 'runs' : 'flows')
const {
  rows: flows,
  loading,
  pagination,
  load,
  change,
} = usePagedTable(agentWorkflowsApi.flows, '无法加载流程')
const historyArticle = computed(() =>
  typeof route.query.article_id === 'string' ? route.query.article_id : undefined,
)
const history = reactive(
  usePagedTable(
    (query) => agentWorkflowsApi.runs({ ...query, article_id: historyArticle.value }),
    '无法加载运行记录',
  ),
)
const nodes = ref<AgentNode[]>([])
const references = ref<AiSkill[]>([])
const reference = ref<AiSkill | null>(null)
const referenceOpen = ref(false)
const resourcesLoading = ref(false)
const resourcesError = ref('')
const open = ref(false)
const editing = ref<AgentWorkflow | null>(null)
const saving = ref(false)
const addId = ref<number>()
const form = reactive<AgentWorkflowInput>({
  name: '',
  description: '',
  node_ids: [],
  is_active: true,
})
const runOpen = ref(false)
const runFlowId = ref<number>()
const runId = ref<string>()
const chain = computed(() => form.node_ids.map((id) => nodes.value.find((n) => n.id === id)))
async function resources() {
  resourcesLoading.value = true
  resourcesError.value = ''
  try {
    const [loadedNodes, skills] = await Promise.all([
      allPages(agentWorkflowsApi.nodes),
      allPages(aiApi.skillPage),
    ])
    nodes.value = loadedNodes
    references.value = skills
  } catch (e) {
    resourcesError.value = getErrorMessage(e, '无法加载节点或关联内容')
  } finally {
    resourcesLoading.value = false
  }
}
function edit(flow?: AgentWorkflow) {
  editing.value = flow || null
  Object.assign(
    form,
    flow
      ? {
          name: flow.name,
          description: flow.description,
          node_ids: [...flow.node_ids],
          is_active: flow.is_active,
        }
      : { name: '', description: '', node_ids: [], is_active: true },
  )
  addId.value = undefined
  open.value = true
  void resources()
}
function add() {
  if (addId.value && form.node_ids.length < 10) form.node_ids.push(addId.value)
}
function move(index: number, offset: number) {
  const target = index + offset
  if (target < 0 || target >= form.node_ids.length) return
  const [item] = form.node_ids.splice(index, 1)
  form.node_ids.splice(target, 0, item!)
}
async function save() {
  if (saving.value || resourcesLoading.value || resourcesError.value) return
  if (!form.name.trim() || !form.node_ids.length) return message.warning('请填写名称并添加节点')
  saving.value = true
  try {
    await agentWorkflowsApi.saveFlow({ ...form, node_ids: [...form.node_ids] }, editing.value)
    open.value = false
    message.success('流程已保存')
    await load()
  } catch (e) {
    message.error(getErrorMessage(e))
  } finally {
    saving.value = false
  }
}
function disable(flow: AgentWorkflow) {
  Modal.confirm({
    title: `停用「${flow.name}」？`,
    content: '已排队和运行中的流程继续使用创建时的快照。',
    onOk: async () => {
      await agentWorkflowsApi.disableFlow(flow.id)
      await load()
    },
  })
}
function openRun(flowId?: number, id?: string) {
  runFlowId.value = flowId
  runId.value = id
  runOpen.value = true
}
function showReference(id: number | string) {
  reference.value = references.value.find((s) => String(s.id) === String(id)) || null
  referenceOpen.value = true
}
watch(tab, (value) => {
  if (value === 'runs') void history.load()
})
watch(historyArticle, () => {
  tab.value = 'runs'
  void history.reset()
})
onMounted(() => {
  void load()
  if (tab.value === 'runs') void history.load()
})
</script>
<template>
  <div class="page-stack">
    <PageHeader
      title="Agent 流程编排"
      description="将节点串成一条链：原始输入和每一步结果自动传给后续节点"
    >
      <template #actions
        ><a-button @click="tab === 'runs' ? history.load() : load()"
          ><ReloadOutlined />刷新</a-button
        ><a-button type="primary" @click="edit()"><PlusOutlined />新建流程</a-button></template
      >
    </PageHeader>
    <a-card :bordered="false">
      <a-tabs v-model:active-key="tab">
        <a-tab-pane key="flows" tab="流程模板">
          <a-table
            :data-source="flows"
            :loading="loading"
            :pagination="pagination"
            row-key="id"
            :scroll="{ x: 1000 }"
            @change="change"
          >
            <a-table-column title="流程" :width="230"
              ><template #default="{ record }"
                ><strong>{{ record.name }}</strong>
                <p class="description muted" :title="record.description">
                  {{ record.description || '—' }}
                </p></template
              ></a-table-column
            >
            <a-table-column title="节点链条" :width="400"
              ><template #default="{ record }"
                ><div class="chain">
                  <template v-for="(node, index) in record.nodes" :key="index"
                    ><a-tag :color="node.is_active ? 'blue' : 'red'"
                      >{{ index + 1 }}. {{ node.name
                      }}{{ node.is_active ? '' : '（已停用）' }}</a-tag
                    ><span v-if="index < record.nodes.length - 1">→</span></template
                  >
                </div></template
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
            <a-table-column title="操作" :width="220"
              ><template #default="{ record }"
                ><a-space
                  ><a-button
                    type="link"
                    :disabled="
                      !record.is_active || record.nodes.some((n: AgentNode) => !n.is_active)
                    "
                    @click="openRun(record.id)"
                    >运行</a-button
                  ><a-button type="link" @click="edit(record)">编辑</a-button
                  ><a-button v-if="record.is_active" type="link" danger @click="disable(record)"
                    >停用</a-button
                  ></a-space
                ></template
              ></a-table-column
            >
          </a-table>
        </a-tab-pane>
        <a-tab-pane key="runs" tab="运行记录">
          <p v-if="historyArticle" class="muted">
            文章 #{{ historyArticle }} 的相关运行 ·
            <router-link to="/agent-workflows">查看全部流程</router-link>
          </p>
          <p class="muted">显示当前账号发起的运行，关闭页面后仍由 AI Worker 继续执行。</p>
          <a-table
            :data-source="history.rows"
            :loading="history.loading"
            :pagination="history.pagination"
            row-key="id"
            :scroll="{ x: 760 }"
            @change="history.change"
          >
            <a-table-column title="流程"
              ><template #default="{ record }"
                >{{ record.workflow_name }} · v{{ record.workflow_version }}</template
              ></a-table-column
            >
            <a-table-column title="运行状态"
              ><template #default="{ record }"
                >{{ runStatusLabel(record.status) }}
                <div v-if="record.error" class="muted">{{ record.error }}</div></template
              ></a-table-column
            >
            <a-table-column title="当前节点"
              ><template #default="{ record }">{{
                record.current_position || '等待开始'
              }}</template></a-table-column
            >
            <a-table-column title="创建时间"
              ><template #default="{ record }">{{
                formatDateTime(record.created_at)
              }}</template></a-table-column
            >
            <a-table-column title="操作"
              ><template #default="{ record }"
                ><a-button type="link" @click="openRun(undefined, record.id)"
                  >查看过程与结果</a-button
                ></template
              ></a-table-column
            >
          </a-table>
        </a-tab-pane>
      </a-tabs>
    </a-card>
    <a-modal
      v-model:open="open"
      :title="editing ? '编辑 Agent 流程' : '新建 Agent 流程'"
      :width="820"
      ok-text="保存流程"
      cancel-text="取消"
      :confirm-loading="saving"
      :mask-closable="!saving"
      :ok-button-props="{ disabled: resourcesLoading || Boolean(resourcesError) }"
      @ok="save"
    >
      <a-form layout="vertical" :disabled="saving">
        <a-form-item label="流程名称" required
          ><a-input v-model:value="form.name" :maxlength="100" placeholder="例如：文章发布流程"
        /></a-form-item>
        <a-form-item label="说明"
          ><a-textarea v-model:value="form.description" :rows="2" :maxlength="2000"
        /></a-form-item>
        <a-form-item label="添加节点" required
          ><a-space style="width: 100%"
            ><a-select
              v-model:value="addId"
              style="width: 320px; max-width: 65vw"
              show-search
              option-filter-prop="label"
              :loading="resourcesLoading"
              :options="
                nodes.map((n) => ({
                  value: n.id,
                  label: `${n.name} · ${nodeTypeLabel(n.node_type)}`,
                  disabled: !n.is_active,
                }))
              "
              placeholder="选择节点"
            /><a-button :disabled="!addId || form.node_ids.length >= 10" @click="add"
              >添加到链条</a-button
            ></a-space
          >
          <div class="muted">
            最多 10 步，可重复使用同一个节点。按从上到下的顺序执行。
          </div></a-form-item
        >
        <a-alert v-if="resourcesError" type="error" :message="resourcesError"
          ><template #action><a-button @click="resources">重试</a-button></template></a-alert
        >
        <a-empty v-if="!form.node_ids.length" description="添加第一个节点，开始编排流程" />
        <div v-for="(node, index) in chain" :key="index" class="node-card">
          <div class="node-heading">
            <div>
              <strong
                >{{ index + 1 }}. {{ node?.name || `未找到节点 #${form.node_ids[index]}` }}</strong
              ><a-tag v-if="node">{{ nodeTypeLabel(node.node_type) }}</a-tag
              ><a-tag v-if="node && !node.is_active" color="red">已停用</a-tag>
            </div>
            <a-space
              ><a-button size="small" :disabled="index === 0" @click="move(index, -1)"
                >上移</a-button
              ><a-button size="small" :disabled="index === chain.length - 1" @click="move(index, 1)"
                >下移</a-button
              ><a-button type="link" danger @click="form.node_ids.splice(index, 1)"
                >移除</a-button
              ></a-space
            >
          </div>
          <p class="muted">
            {{
              index ? `接收原始输入，以及第 1～${index} 步的全部结果` : '接收本次任务要求与原始素材'
            }}
          </p>
          <a-space v-if="node" wrap
            ><a-button
              v-for="id in node.skill_ids"
              :key="String(id)"
              type="link"
              size="small"
              @click="showReference(id)"
              >{{ references.find((s) => String(s.id) === String(id))?.name || `关联项 #${id}` }} ·
              详情</a-button
            ></a-space
          >
        </div>
        <a-form-item label="启用流程"><a-switch v-model:checked="form.is_active" /></a-form-item>
        <p class="muted">
          运行时固定流程、节点和提示词版本。审核输出作为后续节点的上下文；文章发布在文章管理中操作。
        </p>
      </a-form>
    </a-modal>
    <SkillDetailsModal v-model:open="referenceOpen" :skill="reference" />
    <AgentWorkflowRunner
      v-model:open="runOpen"
      :workflow-id="runFlowId"
      :run-id="runId"
      @changed="history.load"
    />
  </div>
</template>
<style scoped>
.description {
  margin: 4px 0;
  max-width: 220px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.chain {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
.node-card {
  padding: 16px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 10px;
  margin-bottom: 12px;
}
.node-heading {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.node-heading strong {
  margin-right: 8px;
}
</style>
