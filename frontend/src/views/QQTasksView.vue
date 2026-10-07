<script setup lang="ts">
import { usePagedTable } from '@/composables/usePagedTable'
import { onMounted, reactive, ref } from 'vue'
import {
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
} from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import { qqApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type { EntityId, QQBotAccount, QQJoinedGroup, QQScheduledTask } from '@/types'
import { formatDateTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import MetricCard from '@/components/MetricCard.vue'
import StatusPill from '@/components/StatusPill.vue'
type TaskForm = Omit<
  QQScheduledTask,
  'id' | 'last_run_at' | 'next_run_at' | 'created_at' | 'updated_at'
>
const bots = ref<QQBotAccount[]>([])
const open = ref(false)
const historyOpen = ref(false)
const editing = ref<QQScheduledTask | null>(null)
const pushing = ref<Set<EntityId>>(new Set())
const form = reactive<TaskForm>({
  name: '',
  message: '',
  frequency: 'daily',
  interval_value: 1,
  run_time: '09:00:00',
  weekdays: [],
  month_day: 1,
  is_enabled: true,
  send_immediately: false,
  bot_ids: [],
  groups: [],
})
const joinedGroups = reactive<Record<number, QQJoinedGroup[]>>({})
const groupLoading = reactive<Record<number, boolean>>({})
const groupErrors = reactive<Record<number, string>>({})
const groupRequests = new Map<number, number>()
let groupRequestSequence = 0
async function loadGroups(botId: number, refresh = false) {
  if (!botId || (!refresh && (groupLoading[botId] || joinedGroups[botId]))) return
  const request = ++groupRequestSequence
  groupRequests.set(botId, request)
  groupLoading[botId] = true
  groupErrors[botId] = ''
  try {
    const groups = await qqApi.joinedGroups(botId)
    if (groupRequests.get(botId) === request) joinedGroups[botId] = groups
  } catch (e) {
    if (groupRequests.get(botId) === request)
      groupErrors[botId] = getErrorMessage(e, '读取群列表失败，可刷新重试或手动填写')
  } finally {
    if (groupRequests.get(botId) === request) groupLoading[botId] = false
  }
}
function groupOptions(botId: number) {
  return (joinedGroups[botId] || []).map((group) => ({
    value: group.group_openid,
    label: group.name ? `${group.name} · ${group.group_openid}` : group.group_openid,
  }))
}
function filterGroup(input: string, option: { label?: string; value?: string }) {
  return `${option.label || ''} ${option.value || ''}`.toLowerCase().includes(input.toLowerCase())
}
function changeGroupBot(group: TaskForm['groups'][number]) {
  group.group_openid = ''
  void loadGroups(group.bot_id)
}
function changeTaskBots() {
  for (const group of form.groups) {
    if (!form.bot_ids.includes(group.bot_id)) {
      group.bot_id = 0
      group.group_openid = ''
    }
  }
  for (const botId of form.bot_ids) void loadGroups(botId)
}
const frequencyOptions = [
  { label: '每秒', value: 'secondly' },
  { label: '每分钟', value: 'minutely' },
  { label: '每小时', value: 'hourly' },
  { label: '每天', value: 'daily' },
  { label: '每周', value: 'weekly' },
  { label: '每月', value: 'monthly' },
]
const weekdayOptions = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'].map(
  (label, value) => ({ label, value }),
)
const enabled = ref(0)
const {
  rows: tasks,
  total,
  loading,
  pagination,
  load: loadTasks,
  change: changePage,
} = usePagedTable(async (query) => {
  const result = await qqApi.taskPage(query)
  enabled.value = result.enabled_total
  return result
}, '无法读取 QQ 任务')
const historyTask = ref<EntityId | null>(null)
const {
  rows: history,
  loading: historyLoading,
  pagination: historyPagination,
  reset: resetHistory,
  change: changeHistoryPage,
} = usePagedTable(
  (query) => qqApi.deliveries({ ...query, task_id: historyTask.value! }),
  '读取历史失败',
)
async function load() {
  await loadTasks()
}

function blank() {
  Object.assign(form, {
    name: '',
    message: '',
    frequency: 'daily',
    interval_value: 1,
    run_time: '09:00:00',
    weekdays: [],
    month_day: 1,
    is_enabled: true,
    send_immediately: false,
    bot_ids: [],
    groups: [],
  })
}
async function edit(task?: QQScheduledTask) {
  try {
    bots.value = await qqApi.bots()
  } catch (e) {
    message.error(getErrorMessage(e, '无法读取机器人'))
    return
  }
  editing.value = task || null
  if (task)
    Object.assign(form, {
      ...task,
      bot_ids: [...task.bot_ids],
      weekdays: [...task.weekdays],
      groups: task.groups.map((group) => ({ ...group })),
    })
  else blank()
  open.value = true
  groupRequests.clear()
  for (const key of Object.keys(joinedGroups)) delete joinedGroups[Number(key)]
  for (const key of Object.keys(groupLoading)) delete groupLoading[Number(key)]
  for (const key of Object.keys(groupErrors)) delete groupErrors[Number(key)]
  for (const group of form.groups) void loadGroups(group.bot_id)
}
function addGroup() {
  const botId = form.bot_ids[0] || 0
  form.groups.push({ bot_id: botId, group_openid: '' })
  void loadGroups(botId)
}
function removeGroup(index: number) {
  form.groups.splice(index, 1)
}
async function save() {
  if (
    !form.name.trim() ||
    !form.message.trim() ||
    !form.bot_ids.length ||
    !form.groups.length ||
    form.groups.some(
      (group) =>
        !group.bot_id || !form.bot_ids.includes(group.bot_id) || !group.group_openid.trim(),
    )
  )
    return message.warning('请填写任务内容、至少一个机器人和有效群 OpenID')
  try {
    const payload = {
      ...form,
      name: form.name.trim(),
      message: form.message.trim(),
      groups: form.groups.map((group) => ({
        bot_id: Number(group.bot_id),
        group_openid: group.group_openid.trim(),
      })),
    }
    editing.value
      ? await qqApi.updateTask(editing.value.id, payload)
      : await qqApi.createTask(payload)
    open.value = false
    await load()
    message.success('任务已保存')
  } catch (e) {
    message.error(getErrorMessage(e, '保存任务失败'))
  }
}
async function toggle(task: QQScheduledTask) {
  try {
    await qqApi.updateTask(task.id, {
      ...task,
      is_enabled: !task.is_enabled,
      send_immediately: false,
    })
    await load()
  } catch (e) {
    message.error(getErrorMessage(e, '更新任务失败'))
  }
}
async function pushNow(task: QQScheduledTask) {
  if (pushing.value.has(task.id)) return
  pushing.value.add(task.id)
  try {
    const result = await qqApi.pushTask(task.id)
    message.success(result.message)
  } catch (e) {
    message.error(getErrorMessage(e, '立即推送失败'))
  } finally {
    pushing.value.delete(task.id)
  }
}
function remove(task: QQScheduledTask) {
  Modal.confirm({
    title: `删除任务「${task.name}」？`,
    okType: 'danger',
    okText: '删除',
    cancelText: '取消',
    onOk: async () => {
      await qqApi.removeTask(task.id)
      await load()
      message.success('任务已删除')
    },
  })
}
async function showHistory(task: QQScheduledTask) {
  historyTask.value = task.id
  history.value = []
  historyOpen.value = true
  await resetHistory()
}
onMounted(load)
</script>
<template>
  <div class="page-stack">
    <PageHeader eyebrow="CHANNEL / 03" title="QQ 任务" description="配置定时消息任务和投递范围"
      ><template #actions
        ><a-space
          ><a-button :loading="loading" @click="load"><ReloadOutlined /> 刷新</a-button
          ><a-button type="primary" @click="edit()"><PlusOutlined /> 新建任务</a-button></a-space
        ></template
      ></PageHeader
    >
    <div class="metric-grid">
      <MetricCard label="任务总数" :value="total" detail="全部计划" /><MetricCard
        label="运行中"
        :value="enabled"
        detail="已启用计划"
        accent="sage"
      />
    </div>
    <a-card :bordered="false"
      ><a-table
        :data-source="tasks"
        :loading="loading"
        row-key="id"
        :pagination="pagination"
        @change="changePage"
        ><a-table-column title="任务"
          ><template #default="{ record }"
            ><strong>{{ record.name }}</strong>
            <div class="muted">{{ record.message }}</div></template
          ></a-table-column
        ><a-table-column title="计划"
          ><template #default="{ record }"
            >{{ record.frequency }} · {{ record.run_time }}</template
          ></a-table-column
        ><a-table-column title="状态"
          ><template #default="{ record }"
            ><StatusPill :value="record.is_enabled" /></template></a-table-column
        ><a-table-column title="下次执行"
          ><template #default="{ record }">{{
            formatDateTime(record.next_run_at)
          }}</template></a-table-column
        ><a-table-column title="操作"
          ><template #default="{ record }"
            ><a-space
              ><a-switch
                size="small"
                :checked="record.is_enabled"
                @change="toggle(record)" /><a-button
                type="link"
                aria-label="编辑任务"
                @click="edit(record)"
                ><EditOutlined /></a-button
              ><a-tooltip title="立即推送"
                ><a-button
                  type="link"
                  aria-label="立即推送"
                  :loading="pushing.has(record.id)"
                  :disabled="pushing.has(record.id)"
                  @click="pushNow(record)"
                  ><SendOutlined /></a-button></a-tooltip
              ><a-button type="link" aria-label="查看任务历史" @click="showHistory(record)"
                ><HistoryOutlined /></a-button
              ><a-button type="link" danger aria-label="删除任务" @click="remove(record)"
                ><DeleteOutlined /></a-button></a-space></template></a-table-column></a-table
    ></a-card>
    <a-modal
      v-model:open="open"
      :title="editing ? '编辑 QQ 任务' : '新建 QQ 任务'"
      ok-text="保存"
      cancel-text="取消"
      width="720px"
      @ok="save"
      ><a-form layout="vertical"
        ><div class="form-grid">
          <a-form-item label="任务名称"><a-input v-model:value="form.name" /></a-form-item
          ><a-form-item label="启用"><a-switch v-model:checked="form.is_enabled" /></a-form-item>
        </div>
        <a-form-item label="消息内容"
          ><a-textarea v-model:value="form.message" :rows="4"
        /></a-form-item>
        <div class="form-grid">
          <a-form-item label="发送频率"
            ><a-select v-model:value="form.frequency" :options="frequencyOptions" /></a-form-item
          ><a-form-item label="间隔"
            ><a-input-number
              v-model:value="form.interval_value"
              :min="1"
              :max="365"
              style="width: 100%"
          /></a-form-item>
        </div>
        <div class="form-grid">
          <a-form-item label="执行时间"
            ><a-input v-model:value="form.run_time" placeholder="09:00:00" /></a-form-item
          ><a-form-item v-if="form.frequency === 'monthly'" label="每月日期"
            ><a-input-number v-model:value="form.month_day" :min="1" :max="31" style="width: 100%"
          /></a-form-item>
        </div>
        <a-form-item v-if="form.frequency === 'weekly'" label="每周执行日"
          ><a-checkbox-group v-model:value="form.weekdays" :options="weekdayOptions" /></a-form-item
        ><a-form-item label="机器人"
          ><a-select
            v-model:value="form.bot_ids"
            mode="multiple"
            :options="
              bots
                .filter((bot) => bot.is_enabled)
                .map((bot) => ({ label: bot.name, value: bot.id }))
            "
            @change="changeTaskBots" /></a-form-item
        ><a-form-item
          label="发送群"
          extra="可选择已记录的群或手动填写 OpenID；找不到群时，在群内 @机器人后刷新。"
          ><div v-for="(group, index) in form.groups" :key="index" class="task-group-editor">
            <a-select
              v-model:value="group.bot_id"
              :options="
                bots
                  .filter((bot) => form.bot_ids.includes(bot.id))
                  .map((bot) => ({ label: bot.name, value: bot.id }))
              "
              placeholder="机器人"
              @change="changeGroupBot(group)"
            />
            <div class="task-group-picker">
              <div class="task-group-picker__controls">
                <a-auto-complete
                  v-model:value="group.group_openid"
                  :options="groupOptions(group.bot_id)"
                  :filter-option="filterGroup"
                  :disabled="!group.bot_id"
                  allow-clear
                  placeholder="输入或选择群 OpenID"
                /><a-button
                  :loading="groupLoading[group.bot_id]"
                  :disabled="!group.bot_id"
                  aria-label="刷新已加入群列表"
                  @click="loadGroups(group.bot_id, true)"
                  ><ReloadOutlined
                /></a-button>
              </div>
              <small v-if="groupLoading[group.bot_id]" class="muted">正在读取群列表…</small>
              <small v-else-if="groupErrors[group.bot_id]" class="task-group-picker__error">{{
                groupErrors[group.bot_id]
              }}</small>
              <small v-else-if="group.bot_id && !joinedGroups[group.bot_id]?.length" class="muted"
                >暂无已记录的群，可手动填写。</small
              >
            </div>
            <a-button type="link" danger @click="removeGroup(index)"><DeleteOutlined /></a-button>
          </div>
          <a-button type="dashed" block @click="addGroup">添加发送群</a-button></a-form-item
        ></a-form
      ></a-modal
    >
    <a-modal v-model:open="historyOpen" title="投递历史" :footer="null"
      ><a-list
        :data-source="history"
        :loading="historyLoading"
        :pagination="{
          ...historyPagination,
          onChange: (current: number, pageSize: number) => changeHistoryPage({ current, pageSize }),
        }"
        ><template #renderItem="{ item }"
          ><a-list-item
            ><div>
              <div>{{ item.target_name || item.target_summary }}</div>
              <div class="muted">{{ formatDateTime(item.created_at) }}</div>
              <div v-if="item.last_error" class="muted">{{ item.last_error }}</div>
            </div>
            <StatusPill :value="item.status" /></a-list-item></template></a-list
    ></a-modal>
  </div>
</template>

<style scoped>
.task-group-editor {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr) auto;
  align-items: start;
  gap: 8px;
  margin-bottom: 12px;
}
.task-group-picker {
  min-width: 0;
}
.task-group-picker__controls {
  display: flex;
  gap: 8px;
}
.task-group-picker__controls :deep(.ant-select) {
  flex: 1;
  min-width: 0;
}
.task-group-picker small {
  display: block;
  margin-top: 4px;
}
.task-group-picker__error {
  color: var(--accent);
}
@media (max-width: 600px) {
  .task-group-editor {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .task-group-editor > :first-child {
    grid-column: 1 / -1;
  }
}
</style>
