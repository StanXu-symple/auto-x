<script setup lang="ts">
import { usePagedTable } from '@/composables/usePagedTable'
import QQMessageTemplates from '@/components/QQMessageTemplates.vue'
import QQMessageTemplatePicker from '@/components/QQMessageTemplatePicker.vue'
import QQPlaceholderSettings from '@/components/QQPlaceholderSettings.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
} from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import { qqApi, monitoredUsersApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type {
  QQBotAccount,
  QQBotPayload,
  QQDelivery,
  QQNotificationTarget,
  QQTargetPayload,
  QQJoinedGroup,
  MonitoredUser,
  QQOverview,
} from '@/types'
import { formatDateTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import MetricCard from '@/components/MetricCard.vue'
import StatusPill from '@/components/StatusPill.vue'
const tab = ref('bots')
const loading = ref(false)
const overview = ref<QQOverview | null>(null)
const botOptions = ref<QQBotAccount[]>([])
const users = ref<MonitoredUser[]>([])
const botOpen = ref(false)
const targetOpen = ref(false)
const editingBot = ref<QQBotAccount | null>(null)
const editingTarget = ref<QQNotificationTarget | null>(null)
const botForm = reactive<QQBotPayload & { app_secret: string }>({
  name: '',
  app_id: '',
  app_secret: '',
  is_enabled: true,
})
const targetForm = reactive<QQTargetPayload>({
  bot_id: 0,
  name: '',
  group_openid: '',
  is_enabled: true,
  all_monitored_users: true,
  monitored_user_ids: [],
  listen_mode: 'all',
  message_template: '{author} (@{username})\n{text}\n{url}',
  template_variables: {},
})
const templateRevision = ref(0)
const templateFields = ref<{ value: string; label: string }[]>([])
async function loadTemplateFields() {
  try {
    const [placeholders, fields] = await Promise.all([
      qqApi.placeholders(),
      qqApi.placeholderFields(),
    ])
    templateFields.value = placeholders.map((item) => ({
      value: item.placeholder,
      label: fields.find((field) => field.value === item.source_field)?.label || item.source_field,
    }))
    return true
  } catch (e) {
    message.error(getErrorMessage(e, '读取占位符配置失败'))
    return false
  }
}
const customTemplateFields = computed(() =>
  Object.keys(targetForm.template_variables)
    .map((key) => '{' + key + '}')
    .join('、'),
)
const listenModeOptions = [
  { label: '全部内容', value: 'all' },
  { label: '仅原创（自己发布）', value: 'original' },
  { label: '仅回复', value: 'reply' },
  { label: '仅转推（含引用转推）', value: 'retweet' },
]
const targetHistoryOptions = [
  ...[1, 3, 7, 15, 30, 90, 180, 365].map((value) => ({ label: `最近 ${value} 天`, value })),
  { label: '自定义天数', value: 'custom' },
]
const targetHistorySelection = ref<number | string>(7)
const targetHistoryDays = ref<number | null>(7)
const targetSaving = ref(false)
const togglingTargets = ref<Set<number>>(new Set())
const joinedGroups = ref<QQJoinedGroup[]>([])
const groupsLoading = ref(false)
const groupsError = ref('')
let groupsRequest = 0
const groupOptions = computed(() =>
  joinedGroups.value.map((group) => ({
    value: group.group_openid,
    label: group.name ? `${group.name} · ${group.group_openid}` : group.group_openid,
  })),
)
function filterGroup(input: string, option: { value?: string; label?: string }) {
  return `${option.label || ''} ${option.value || ''}`.toLowerCase().includes(input.toLowerCase())
}
async function loadTargetGroups() {
  const request = ++groupsRequest
  const botId = targetForm.bot_id
  joinedGroups.value = []
  groupsError.value = ''
  groupsLoading.value = Boolean(botId)
  if (!botId) return
  try {
    const result = await qqApi.joinedGroups(botId)
    if (request === groupsRequest) joinedGroups.value = result
  } catch (e) {
    if (request === groupsRequest)
      groupsError.value = getErrorMessage(e, '读取群列表失败，可重试或手动填写 OpenID')
  } finally {
    if (request === groupsRequest) groupsLoading.value = false
  }
}
function changeTargetBot() {
  targetForm.group_openid = ''
  void loadTargetGroups()
}
function selectTargetGroup(value: string | number) {
  const group = joinedGroups.value.find((item) => item.group_openid === String(value))
  if (!targetForm.name.trim() && group?.name) targetForm.name = group.name
}
const {
  rows: bots,
  total: botTotal,
  loading: botsLoading,
  pagination: botPagination,
  load: loadBots,
  change: changeBotPage,
} = usePagedTable(qqApi.botPage, '无法加载机器人')
const {
  rows: targets,
  total: targetTotal,
  loading: targetsLoading,
  pagination: targetPagination,
  load: loadTargets,
  change: changeTargetPage,
} = usePagedTable(qqApi.targetPage, '无法加载群目标')
const {
  rows: deliveries,
  total: deliveryTotal,
  loading: deliveriesLoading,
  pagination: deliveryPagination,
  load: loadDeliveries,
  change: changeDeliveryPage,
  reset: resetDeliveries,
} = usePagedTable(qqApi.deliveries, '无法加载投递记录')
async function loadAll() {
  loading.value = true
  try {
    await Promise.all([
      loadBots(),
      loadTargets(),
      loadDeliveries(),
      qqApi.overview().then((value) => {
        overview.value = value
      }),
    ])
  } catch (e) {
    message.error(getErrorMessage(e, '无法加载 QQ 推送数据'))
  } finally {
    loading.value = false
  }
}
function editBot(item?: QQBotAccount) {
  editingBot.value = item || null
  Object.assign(
    botForm,
    item
      ? { name: item.name, app_id: item.app_id, app_secret: '', is_enabled: item.is_enabled }
      : { name: '', app_id: '', app_secret: '', is_enabled: true },
  )
  botOpen.value = true
}
async function saveBot() {
  if (!botForm.name || !botForm.app_id || (!editingBot.value && !botForm.app_secret))
    return message.warning('请填写机器人名称、App ID 和 Secret')
  try {
    editingBot.value
      ? await qqApi.updateBot(editingBot.value.id, {
          name: botForm.name,
          app_id: botForm.app_id,
          is_enabled: botForm.is_enabled,
        })
      : await qqApi.createBot(botForm)
    botOpen.value = false
    await loadAll()
    message.success('机器人已保存')
  } catch (e) {
    message.error(getErrorMessage(e, '保存机器人失败'))
  }
}
function removeBot(bot: QQBotAccount) {
  Modal.confirm({
    title: `删除机器人「${bot.name}」？`,
    okType: 'danger',
    okText: '删除',
    cancelText: '取消',
    onOk: async () => {
      await qqApi.removeBot(bot.id)
      await loadAll()
      message.success('机器人已删除')
    },
  })
}
async function testBot(bot: QQBotAccount) {
  try {
    const result = await qqApi.testBot(bot.id)
    result.valid ? message.success(result.message) : message.warning(result.message)
    await loadAll()
  } catch (e) {
    message.error(getErrorMessage(e, '测试失败'))
  }
}
async function editTarget(item?: QQNotificationTarget) {
  if (!(await loadTemplateFields())) return
  try {
    ;[botOptions.value, users.value] = await Promise.all([
      qqApi.bots(),
      monitoredUsersApi.list({ page: 1, page_size: 100 }).then((result) => result.items),
    ])
  } catch (e) {
    message.error(getErrorMessage(e, '无法加载群目标选项'))
    return
  }
  editingTarget.value = item || null
  targetHistorySelection.value = 7
  targetHistoryDays.value = 7
  const available = new Set(templateFields.value.map((field) => field.value))
  const identity = [
    available.has('{author}') ? '{author}' : '',
    available.has('{username}') ? '(@{username})' : '',
  ]
    .filter(Boolean)
    .join(' ')
  const defaultTemplate = [identity, ...['{text}', '{url}'].filter((field) => available.has(field))]
    .filter(Boolean)
    .join('\n')
  Object.assign(
    targetForm,
    item
      ? {
          bot_id: item.bot_id,
          name: item.name,
          group_openid: item.group_openid,
          is_enabled: item.is_enabled,
          all_monitored_users: item.all_monitored_users,
          listen_mode: item.listen_mode,
          monitored_user_ids: [...item.monitored_user_ids],
          message_template: item.message_template,
          template_variables: item.template_variables,
        }
      : {
          bot_id: botOptions.value[0]?.id || 0,
          name: '',
          group_openid: '',
          is_enabled: true,
          all_monitored_users: true,
          monitored_user_ids: [],
          listen_mode: 'all',
          message_template: defaultTemplate,
          template_variables: {},
        },
  )
  targetOpen.value = true
  void loadTargetGroups()
}
async function saveTarget() {
  if (targetSaving.value) return
  const days =
    targetHistorySelection.value === 'custom'
      ? targetHistoryDays.value
      : Number(targetHistorySelection.value)
  if (
    !editingTarget.value &&
    (days == null || !Number.isInteger(days) || days < 1 || days > 2147483647)
  )
    return message.warning('历史推送天数必须为正整数（最大 2147483647）')
  targetForm.name = targetForm.name.trim()
  targetForm.group_openid = targetForm.group_openid.trim()
  if (!targetForm.bot_id || !targetForm.name || !targetForm.group_openid)
    return message.warning('请填写机器人、目标名称和群 OpenID')
  targetSaving.value = true
  try {
    editingTarget.value
      ? await qqApi.updateTarget(editingTarget.value.id, targetForm)
      : await qqApi.createTarget({ ...targetForm, initial_sync_days: days! })
    targetOpen.value = false
    await loadAll()
    message.success('群目标已保存')
  } catch (e) {
    message.error(getErrorMessage(e, '保存群目标失败'))
  } finally {
    targetSaving.value = false
  }
}
async function toggleTarget(target: QQNotificationTarget) {
  if (togglingTargets.value.has(target.id)) return
  togglingTargets.value.add(target.id)
  const enabled = !target.is_enabled
  try {
    const updated = await qqApi.updateTarget(target.id, { is_enabled: enabled })
    Object.assign(target, updated)
    message.success(enabled ? '群目标推送已开启' : '群目标推送已关闭')
    await loadAll()
  } catch (e) {
    message.error(getErrorMessage(e, '切换群目标推送状态失败'))
  } finally {
    togglingTargets.value.delete(target.id)
  }
}
function removeTarget(target: QQNotificationTarget) {
  Modal.confirm({
    title: `删除目标「${target.name}」？`,
    okType: 'danger',
    okText: '删除',
    cancelText: '取消',
    onOk: async () => {
      await qqApi.removeTarget(target.id)
      await loadAll()
      message.success('群目标已删除')
    },
  })
}
async function testTarget(target: QQNotificationTarget) {
  try {
    const result = await qqApi.testTarget(target.id)
    message.success(result.message)
    await loadAll()
  } catch (e) {
    message.error(getErrorMessage(e, '测试失败'))
  }
}
const deletingDeliveries = ref<Set<QQDelivery['id']>>(new Set())
const clearingDeliveries = ref(false)
function removeDelivery(delivery: QQDelivery) {
  Modal.confirm({
    title: '删除这条投递记录？',
    content: '删除后不可恢复；尚未领取的消息将不再投递，已开始发送的消息可能仍会完成。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      deletingDeliveries.value.add(delivery.id)
      try {
        await qqApi.removeDelivery(delivery.id)
        message.success('投递记录已删除')
        await loadAll()
      } catch (e) {
        message.error(getErrorMessage(e, '删除投递记录失败'))
        throw e
      } finally {
        deletingDeliveries.value.delete(delivery.id)
      }
    },
  })
}
function clearDeliveries() {
  Modal.confirm({
    title: '清除全部投递记录？',
    content:
      '将永久删除所有投递记录（不只是当前页），包括任务投递历史和待发送记录。群目标、机器人和任务配置保留；后续新投递仍会产生记录，已经开始发送的消息可能仍会完成。',
    okText: '清除全部',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      clearingDeliveries.value = true
      try {
        await qqApi.clearDeliveries()
        await resetDeliveries()
        message.success('全部投递记录已清除')
        await loadAll()
      } catch (e) {
        message.error(getErrorMessage(e, '清除投递记录失败'))
        throw e
      } finally {
        clearingDeliveries.value = false
      }
    },
  })
}
async function retry(delivery: QQDelivery) {
  try {
    await qqApi.retryDelivery(delivery.id)
    await loadAll()
    message.success('投递已重试')
  } catch (e) {
    message.error(getErrorMessage(e, '重试失败'))
  }
}
onMounted(loadAll)
</script>

<template>
  <div class="page-stack">
    <PageHeader eyebrow="CHANNEL / 02" title="QQ 推送" description="管理机器人、群目标与投递记录"
      ><template #actions
        ><a-button @click="loadAll"><ReloadOutlined /> 刷新</a-button></template
      ></PageHeader
    >
    <div class="metric-grid">
      <MetricCard
        label="机器人"
        :value="overview?.total_bots || 0"
        :detail="`${overview?.enabled_bots || 0} 个已启用`"
      /><MetricCard
        label="群目标"
        :value="overview?.enabled_targets || 0"
        detail="已启用目标"
        accent="sage"
      /><MetricCard
        label="队列中"
        :value="overview?.queued_deliveries || 0"
        detail="等待投递"
        accent="slate"
      /><MetricCard
        label="失败投递"
        :value="overview?.failed_deliveries || 0"
        detail="需要关注"
        accent="ochre"
      />
    </div>
    <a-card :bordered="false"
      ><a-tabs v-model:activeKey="tab"
        ><a-tab-pane key="bots" tab="机器人"
          ><div class="toolbar">
            <span class="toolbar__hint">官方 QQ 机器人账号 · 共 {{ botTotal }} 个</span
            ><a-button type="primary" @click="editBot()"><PlusOutlined /> 新增机器人</a-button>
          </div>
          <a-table
            :data-source="bots"
            :loading="botsLoading"
            row-key="id"
            :pagination="botPagination"
            @change="changeBotPage"
            ><a-table-column title="名称" data-index="name" /><a-table-column
              title="App ID"
              data-index="app_id" /><a-table-column title="在线状态"
              ><template #default="{ record }"
                ><StatusPill :value="record.online_status" /></template></a-table-column
            ><a-table-column title="校验"
              ><template #default="{ record }"
                ><StatusPill :value="record.verification_status" /></template></a-table-column
            ><a-table-column title="操作"
              ><template #default="{ record }"
                ><a-space
                  ><a-button type="link" @click="editBot(record)"><EditOutlined /></a-button
                  ><a-button type="link" @click="testBot(record)">测试</a-button
                  ><a-button type="link" danger @click="removeBot(record)"
                    ><DeleteOutlined /></a-button></a-space></template></a-table-column></a-table></a-tab-pane
        ><a-tab-pane key="targets" tab="群目标"
          ><div class="toolbar">
            <span class="toolbar__hint">将监听内容发送到指定群 · 共 {{ targetTotal }} 个</span
            ><a-button type="primary" @click="editTarget()"><PlusOutlined /> 新增目标</a-button>
          </div>
          <a-table
            :data-source="targets"
            :loading="targetsLoading"
            row-key="id"
            :pagination="targetPagination"
            @change="changeTargetPage"
            ><a-table-column title="目标" data-index="name" /><a-table-column
              title="机器人"
              data-index="bot_name" /><a-table-column
              title="群 OpenID"
              data-index="group_openid" /><a-table-column title="监听模式"
              ><template #default="{ record }">{{
                listenModeOptions.find((option) => option.value === record.listen_mode)?.label ||
                '全部内容'
              }}</template></a-table-column
            ><a-table-column title="首次历史范围"
              ><template #default="{ record }">{{
                record.initial_sync_days == null
                  ? '未设置（旧目标）'
                  : `创建前 ${record.initial_sync_days} 天内`
              }}</template></a-table-column
            ><a-table-column title="范围"
              ><template #default="{ record }">{{
                record.all_monitored_users
                  ? '全部监听账号'
                  : `${record.monitored_user_ids.length} 个账号`
              }}</template></a-table-column
            ><a-table-column title="操作"
              ><template #default="{ record }"
                ><a-space
                  ><a-switch
                    :checked="record.is_enabled"
                    :loading="togglingTargets.has(record.id)"
                    :disabled="togglingTargets.has(record.id)"
                    checked-children="开启"
                    un-checked-children="关闭"
                    :aria-label="`${record.is_enabled ? '关闭' : '开启'} ${record.name} 的群目标推送`"
                    @change="toggleTarget(record)" /><a-button
                    type="link"
                    @click="editTarget(record)"
                    ><EditOutlined /></a-button
                  ><a-button type="link" :disabled="!record.is_enabled" @click="testTarget(record)"
                    ><SendOutlined /></a-button
                  ><a-button type="link" danger @click="removeTarget(record)"
                    ><DeleteOutlined /></a-button></a-space></template></a-table-column></a-table></a-tab-pane
        ><a-tab-pane key="deliveries" tab="投递记录"
          ><div class="toolbar">
            <span class="toolbar__hint">共 {{ deliveryTotal }} 条投递记录</span
            ><a-button
              danger
              :loading="clearingDeliveries"
              :disabled="deletingDeliveries.size > 0"
              @click="clearDeliveries"
              ><DeleteOutlined /> 一键清除</a-button
            >
          </div>
          <a-table
            :data-source="deliveries"
            :loading="deliveriesLoading"
            row-key="id"
            :pagination="deliveryPagination"
            @change="changeDeliveryPage"
            ><a-table-column title="目标"
              ><template #default="{ record }"
                >{{ record.target_name }} · {{ record.bot_name }}</template
              ></a-table-column
            ><a-table-column title="状态"
              ><template #default="{ record }"
                ><StatusPill :value="record.status" /></template></a-table-column
            ><a-table-column title="时间"
              ><template #default="{ record }">{{
                formatDateTime(record.created_at)
              }}</template></a-table-column
            ><a-table-column title="错误"
              ><template #default="{ record }">{{
                record.last_error || '—'
              }}</template></a-table-column
            ><a-table-column title="操作"
              ><template #default="{ record }"
                ><a-space
                  ><a-button
                    v-if="record.status === 'failed'"
                    type="link"
                    :disabled="clearingDeliveries || deletingDeliveries.has(record.id)"
                    @click="retry(record)"
                    >重试</a-button
                  ><a-tooltip title="删除投递记录"
                    ><a-button
                      type="text"
                      danger
                      aria-label="删除投递记录"
                      :loading="deletingDeliveries.has(record.id)"
                      :disabled="clearingDeliveries || deletingDeliveries.has(record.id)"
                      @click="removeDelivery(record)"
                      ><DeleteOutlined /></a-button></a-tooltip></a-space></template></a-table-column></a-table></a-tab-pane
        ><a-tab-pane key="templates" tab="消息模板"
          ><QQMessageTemplates
            :revision="templateRevision"
            @changed="templateRevision++" /></a-tab-pane
        ><a-tab-pane key="placeholders" tab="占位符配置"
          ><QQPlaceholderSettings @changed="loadTemplateFields" /></a-tab-pane></a-tabs></a-card
    ><a-modal
      v-model:open="botOpen"
      :title="editingBot ? '编辑机器人' : '新增机器人'"
      ok-text="保存"
      cancel-text="取消"
      @ok="saveBot"
      ><a-form layout="vertical"
        ><a-form-item label="名称"><a-input v-model:value="botForm.name" /></a-form-item
        ><a-form-item label="App ID"><a-input v-model:value="botForm.app_id" /></a-form-item
        ><a-form-item v-if="!editingBot" label="App Secret"
          ><a-input-password v-model:value="botForm.app_secret" /></a-form-item
        ><a-form-item label="启用"
          ><a-switch v-model:checked="botForm.is_enabled" /></a-form-item></a-form></a-modal
    ><a-modal
      v-model:open="targetOpen"
      title="群目标"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="targetSaving"
      @ok="saveTarget"
      ><a-form layout="vertical"
        ><a-form-item
          label="监听模式"
          extra="仅对已采集的内容进行筛选，首次历史推送和后续推送均生效。回复包含回复他人及自己的串文；转推包含引用转推。采集范围仍由监听账号的“包含回复 / 包含转推”控制。"
          ><a-select
            v-model:value="targetForm.listen_mode"
            :options="listenModeOptions" /></a-form-item
        ><a-form-item label="机器人"
          ><a-select
            v-model:value="targetForm.bot_id"
            :options="botOptions.map((bot) => ({ label: bot.name, value: bot.id }))"
            @change="changeTargetBot" /></a-form-item
        ><a-form-item
          label="群 OpenID"
          extra="可手动填写或选择已记录的群；找不到群时，可在群里 @机器人后刷新列表。"
        >
          <div class="target-group-picker">
            <a-auto-complete
              v-model:value="targetForm.group_openid"
              :options="groupOptions"
              :filter-option="filterGroup"
              :disabled="!targetForm.bot_id"
              allow-clear
              placeholder="输入或选择群 OpenID"
              @select="selectTargetGroup"
            />
            <a-button
              :loading="groupsLoading"
              :disabled="!targetForm.bot_id"
              aria-label="刷新已加入群列表"
              @click="loadTargetGroups"
              ><ReloadOutlined /> 刷新</a-button
            >
          </div>
          <div v-if="groupsLoading" class="muted">正在读取群列表…</div>
          <a-alert v-else-if="groupsError" type="warning" show-icon :message="groupsError" />
          <div v-else-if="targetForm.bot_id && !joinedGroups.length" class="muted">
            暂无已记录的群，仍可手动填写 OpenID。
          </div> </a-form-item
        ><a-form-item
          label="目标名称"
          extra="用于识别此推送目标；有已保存名称时自动带入，否则请自行填写。"
          ><a-input
            v-model:value="targetForm.name"
            placeholder="例如：AI 资讯交流群" /></a-form-item
        ><a-form-item label="监听范围"
          ><a-switch
            v-model:checked="targetForm.all_monitored_users"
            checked-children="全部账号"
            un-checked-children="指定账号" /><a-select
            v-if="!targetForm.all_monitored_users"
            v-model:value="targetForm.monitored_user_ids"
            mode="multiple"
            :options="users.map((user) => ({ label: `@${user.username}`, value: user.id }))"
            style="width: 100%; margin-top: 10px" /></a-form-item
        ><a-form-item
          v-if="!editingTarget"
          label="首次推送历史范围"
          required
          extra="以添加目标时间为准，将最近指定天数内已采集且符合监听范围的消息加入投递队列；更早的消息不推送，后续新消息正常推送。"
          ><a-select
            v-model:value="targetHistorySelection"
            :options="targetHistoryOptions"
            style="width: 100%" /><a-input-number
            v-if="targetHistorySelection === 'custom'"
            v-model:value="targetHistoryDays"
            :min="1"
            :max="2147483647"
            :step="1"
            addon-after="天"
            placeholder="请输入正整数"
            style="width: 100%; margin-top: 8px" /></a-form-item
        ><a-form-item extra="占位符使用单层花括号，发送时自动替换。点击下方占位符可追加到模板。"
          ><QQMessageTemplatePicker
            v-if="targetOpen"
            v-model:message-template="targetForm.message_template"
            v-model:template-variables="targetForm.template_variables"
            @saved="templateRevision++"
          /><a-textarea v-model:value="targetForm.message_template" :rows="4" />
          <div class="template-fields">
            <a-button
              v-for="field in templateFields"
              :key="field.value"
              size="small"
              @click="targetForm.message_template += field.value"
              >{{ field.value }} · {{ field.label }}</a-button
            >
          </div>
          <div v-if="Object.keys(targetForm.template_variables).length" class="muted">
            已配置自定义变量：{{ customTemplateFields }}
          </div></a-form-item
        ></a-form
      ></a-modal
    >
  </div>
</template>

<style scoped>
.template-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.target-group-picker {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.target-group-picker :deep(.ant-select) {
  flex: 1;
  min-width: 0;
}
</style>
