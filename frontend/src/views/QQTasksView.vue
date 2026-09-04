<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Bell, CalendarClock, Clock3, History, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import { ElMessage, ElMessageBox } from 'element-plus'
import BaseModal from '@/components/BaseModal.vue'
import EmptyState from '@/components/EmptyState.vue'
import { qqApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type { QQBotAccount, QQJoinedGroup, QQScheduledTask } from '@/types'

type TaskForm = Omit<QQScheduledTask, 'id' | 'last_run_at' | 'next_run_at' | 'created_at' | 'updated_at'>

const tasks = ref<QQScheduledTask[]>([])
const bots = ref<QQBotAccount[]>([])
const groups = ref<Array<QQJoinedGroup & { bot_id: number }>>([])
const open = ref(false)
const editing = ref<number | null>(null)
const loading = ref(false)
const pageLoading = ref(true)
const historyOpen = ref(false)
const historyLoading = ref(false)
const historyTask = ref<QQScheduledTask | null>(null)
const history = ref<any[]>([])

const emptyForm = (): TaskForm => ({
  name: '', message: '', frequency: 'daily', interval_value: 1, run_time: '09:00:00',
  weekdays: [], month_day: 1, is_enabled: true, send_immediately: false, bot_ids: [], groups: [],
})
const form = reactive<TaskForm>(emptyForm())
const frequencyOptions = [
  { label: '每秒', value: 'secondly' }, { label: '每分钟', value: 'minutely' },
  { label: '每小时', value: 'hourly' }, { label: '每天', value: 'daily' },
  { label: '每周', value: 'weekly' }, { label: '每月', value: 'monthly' },
] as const
const weekdayOptions = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
const intervalUnit = computed(() => ({ secondly: '秒', minutely: '分钟', hourly: '小时' } as Record<string, string>)[form.frequency] || '')

function timePart(index: number) { return Number(form.run_time.split(':')[index] || 0) }
function setTimePart(index: number, value: number | undefined) {
  const parts = form.run_time.split(':').map(Number)
  while (parts.length < 3) parts.push(0)
  parts[index] = Number(value || 0)
  form.run_time = parts.slice(0, 3).map((part) => String(part).padStart(2, '0')).join(':')
}
const scheduleMinute = computed({ get: () => timePart(1), set: (value: number | undefined) => setTimePart(1, value) })
const scheduleSecond = computed({ get: () => timePart(2), set: (value: number | undefined) => setTimePart(2, value) })

function frequencyLabel(value: string) {
  return frequencyOptions.find((option) => option.value === value)?.label || value
}
function scheduleLabel(task: QQScheduledTask) {
  const [, minute = '00', second = '00'] = task.run_time.split(':')
  if (task.frequency === 'secondly') return `每 ${task.interval_value} 秒`
  if (task.frequency === 'minutely') return `每 ${task.interval_value} 分钟，第 ${Number(second)} 秒`
  if (task.frequency === 'hourly') return `每 ${task.interval_value} 小时，${minute}:${second}`
  if (task.frequency === 'weekly') return `${frequencyLabel(task.frequency)} · ${task.weekdays.map((day) => weekdayOptions[day - 1]).join('、')} · ${task.run_time}`
  if (task.frequency === 'monthly') return `每月 ${task.month_day} 日 · ${task.run_time}`
  return `每天 · ${task.run_time}`
}

function formatDateTime(value: string | null) {
  if (!value) return '尚未执行'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间无效'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(date)
}

async function load() {
  pageLoading.value = true
  try { [tasks.value, bots.value] = await Promise.all([qqApi.tasks(), qqApi.bots()]) }
  catch (error) { ElMessage.error(getErrorMessage(error, '读取 QQ 任务失败')) }
  finally { pageLoading.value = false }
}
async function chooseBots() {
  groups.value = []
  for (const id of form.bot_ids) {
    const joined = await qqApi.joinedGroups(id)
    groups.value.push(...joined.map((group) => ({ ...group, bot_id: id })))
  }
}
function edit(task?: QQScheduledTask) {
  editing.value = task?.id ?? null
  Object.assign(form, task ? {
    ...task,
    run_time: task.run_time.length === 5 ? `${task.run_time}:00` : task.run_time,
    weekdays: [...(task.weekdays || [])], bot_ids: [...task.bot_ids], groups: task.groups.map((group) => ({ ...group })),
    send_immediately: false,
  } : emptyForm())
  open.value = true
  if (form.bot_ids.length) void chooseBots()
}
async function save() {
  if (!form.name.trim() || !form.message.trim()) return ElMessage.warning('请填写任务名称和消息内容')
  if (!form.bot_ids.length || !form.groups.length) return ElMessage.warning('请至少选择一个机器人和发送群')
  loading.value = true
  try {
    const payload = {
      ...form, name: form.name.trim(), message: form.message.trim(),
      month_day: form.frequency === 'monthly' ? form.month_day : null,
    }
    editing.value ? await qqApi.updateTask(editing.value, payload) : await qqApi.createTask(payload)
    open.value = false
    await load()
    ElMessage.success('任务已保存')
  } catch (error) { ElMessage.error(getErrorMessage(error, '保存失败')) }
  finally { loading.value = false }
}
async function toggleTask(task: QQScheduledTask) {
  try { await qqApi.updateTask(task.id, { ...task, send_immediately: false }) }
  catch (error) {
    task.is_enabled = !task.is_enabled
    ElMessage.error(getErrorMessage(error, '更新任务状态失败'))
  }
}
async function remove(task: QQScheduledTask) {
  try { await ElMessageBox.confirm('删除后不会再产生新的投递任务，确定删除吗？', '删除任务', { type: 'warning' }) }
  catch { return }
  await qqApi.removeTask(task.id)
  await load()
}
async function showHistory(task: QQScheduledTask) {
  historyTask.value = task
  historyLoading.value = true
  historyOpen.value = true
  try { history.value = (await qqApi.deliveries({ task_id: task.id, page: 1, page_size: 100 })).items }
  finally { historyLoading.value = false }
}
async function clearHistory() {
  if (!historyTask.value) return
  try { await ElMessageBox.confirm('清除后将删除该任务的全部推送记录，确定继续吗？', '清除历史', { type: 'warning' }) }
  catch { return }
  await qqApi.clearTaskHistory(historyTask.value.id)
  history.value = []
  ElMessage.success('历史已清除')
}

onMounted(load)
</script>

<template>
  <div class="qq-tasks-page qq-tasks-view page-stack" v-loading="pageLoading">
    <section class="summary-strip">
      <div><span class="summary-strip__icon"><CalendarClock :size="18" /></span><span><small>任务总数</small><strong>{{ tasks.length }}</strong></span></div>
      <i />
      <div><span class="summary-strip__icon is-success"><Bell :size="18" /></span><span><small>运行中</small><strong>{{ tasks.filter((task) => task.is_enabled).length }}</strong></span></div>
      <div class="summary-strip__action"><el-button type="primary" @click="edit()"><Plus :size="16" />新建任务</el-button></div>
    </section>

    <section class="panel data-panel">
      <header class="data-toolbar task-toolbar">
        <div><strong>定时消息</strong><span>按固定计划向已授权的 QQ 群发送消息</span></div>
        <el-tooltip content="刷新任务" placement="top"><el-button circle :loading="pageLoading" @click="load"><RefreshCw v-if="!pageLoading" :size="16" /></el-button></el-tooltip>
      </header>
      <div v-if="tasks.length" class="table-wrap">
        <el-table :data="tasks" row-key="id" class="sentinel-table" table-layout="auto">
          <el-table-column label="任务" min-width="240"><template #default="{ row }"><div class="task-name"><strong>{{ row.name }}</strong><span>{{ row.message }}</span></div></template></el-table-column>
          <el-table-column label="执行计划" min-width="250"><template #default="{ row }"><div class="task-schedule"><Clock3 :size="15" /><span><strong>{{ scheduleLabel(row) }}</strong><small>下次 {{ formatDateTime(row.next_run_at) }}</small></span></div></template></el-table-column>
          <el-table-column label="目标" width="100"><template #default="{ row }">{{ row.groups.length }} 个群</template></el-table-column>
          <el-table-column label="状态" width="105"><template #default="{ row }"><el-switch v-model="row.is_enabled" @change="toggleTask(row)" /></template></el-table-column>
          <el-table-column label="操作" width="190" align="right"><template #default="{ row }"><div class="row-actions"><el-button link @click="showHistory(row)"><History :size="14" />历史</el-button><el-button link @click="edit(row)">编辑</el-button><el-button circle size="small" type="danger" plain title="删除任务" @click="remove(row)"><Trash2 :size="14" /></el-button></div></template></el-table-column>
        </el-table>
      </div>
      <EmptyState v-else-if="!pageLoading" title="还没有定时任务" description="创建任务后，QQ Worker 会按照计划向所选群发送消息">
        <template #icon><CalendarClock :size="25" /></template>
        <el-button type="primary" @click="edit()"><Plus :size="16" />新建任务</el-button>
      </EmptyState>
    </section>

    <el-dialog v-model="historyOpen" class="qq-task-history-dialog" title="推送历史" width="760px">
      <div v-loading="historyLoading"><el-table :data="history" max-height="420"><el-table-column label="时间（GMT+8）" width="180"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column><el-table-column label="目标" prop="target_name"/><el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="row.status === 'sent' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'">{{ row.status === 'sent' ? '成功' : row.status === 'failed' ? '失败' : row.status }}</el-tag></template></el-table-column><el-table-column label="失败原因" prop="last_error"/></el-table><el-empty v-if="!history.length" description="暂无推送历史"/></div>
      <template #footer><el-button type="danger" plain :disabled="!history.length" @click="clearHistory">清除历史</el-button><el-button @click="historyOpen = false">关闭</el-button></template>
    </el-dialog>

    <BaseModal class="qq-task-dialog" :open="open" :title="editing ? '编辑 QQ 任务' : '新建 QQ 任务'" width="large" @close="open = false">
      <el-form class="sentinel-form" label-position="top" @submit.prevent="save">
        <div class="form-grid"><el-form-item label="任务名称"><el-input v-model="form.name" maxlength="100" placeholder="例如：每日报告提醒" /></el-form-item><el-form-item label="发送频率"><el-select v-model="form.frequency"><el-option v-for="option in frequencyOptions" :key="option.value" :label="option.label" :value="option.value" /></el-select></el-form-item></div>
        <el-form-item label="固定消息"><el-input v-model="form.message" type="textarea" :rows="4" maxlength="2000" show-word-limit placeholder="输入要定时发送的消息" /></el-form-item>
        <div class="schedule-editor">
          <el-form-item v-if="['secondly', 'minutely', 'hourly'].includes(form.frequency)" label="执行间隔"><el-input-number v-model="form.interval_value" :min="1" :max="365" /><span class="field-help">每 {{ form.interval_value }} {{ intervalUnit }}执行一次</span></el-form-item>
          <el-form-item v-if="form.frequency === 'minutely'" label="每轮触发秒数"><el-input-number v-model="scheduleSecond" :min="0" :max="59" /><span class="field-help">在第 {{ scheduleSecond }} 秒执行</span></el-form-item>
          <template v-else-if="form.frequency === 'hourly'"><el-form-item label="每轮触发分钟"><el-input-number v-model="scheduleMinute" :min="0" :max="59" /></el-form-item><el-form-item label="触发秒数"><el-input-number v-model="scheduleSecond" :min="0" :max="59" /></el-form-item></template>
          <el-form-item v-else-if="form.frequency !== 'secondly'" label="发送时间"><el-time-picker v-model="form.run_time" format="HH:mm:ss" value-format="HH:mm:ss" /></el-form-item>
        </div>
        <el-form-item v-if="form.frequency === 'weekly'" label="每周几"><el-select v-model="form.weekdays" multiple><el-option v-for="(label, index) in weekdayOptions" :key="label" :label="label" :value="index + 1" /></el-select></el-form-item>
        <el-form-item v-if="form.frequency === 'monthly'" label="每月几号"><el-input-number v-model="form.month_day" :min="1" :max="31" /></el-form-item>
        <div class="form-grid"><el-form-item label="QQ 机器人"><el-select v-model="form.bot_ids" multiple collapse-tags @change="chooseBots"><el-option v-for="bot in bots.filter((item) => item.is_enabled)" :key="bot.id" :label="bot.name" :value="bot.id" /></el-select></el-form-item><el-form-item label="发送群"><el-select v-model="form.groups" multiple value-key="group_openid" collapse-tags><el-option v-for="group in groups" :key="group.group_openid" :label="group.name || group.group_openid" :value="{ bot_id: group.bot_id, group_openid: group.group_openid }" /></el-select></el-form-item></div>
        <div class="task-options"><label><span><strong>启用任务</strong><small>保存后由 QQ Worker 按计划执行</small></span><el-switch v-model="form.is_enabled" /></label><label v-if="!editing"><span><strong>保存后立即推送</strong><small>首次保存时先发送一条测试消息</small></span><el-switch v-model="form.send_immediately" /></label></div>
      </el-form>
      <template #footer><el-button @click="open = false">取消</el-button><el-button type="primary" :loading="loading" @click="save">保存任务</el-button></template>
    </BaseModal>
  </div>
</template>
