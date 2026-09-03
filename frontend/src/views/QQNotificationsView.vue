<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CirclePlus,
  Edit3,
  ExternalLink,
  KeyRound,
  MessageSquare,
  RefreshCw,
  RotateCcw,
  Send,
  ServerCog,
  ShieldCheck,
  Trash2,
  UsersRound,
} from 'lucide-vue-next'
import BaseModal from '@/components/BaseModal.vue'
import { monitoredUsersApi, qqApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type {
  MonitoredUser,
  QQBotAccount,
  QQDelivery,
  QQJoinedGroup,
  QQNotificationTarget,
  QQOverview,
} from '@/types'
import { formatDateTime } from '@/utils/format'

const DEFAULT_TEMPLATE = '{title}\n@{username} · {posted_at}\n{text}\n{url}'
const loading = ref(true)
const refreshing = ref(false)
const overview = ref<QQOverview | null>(null)
const bots = ref<QQBotAccount[]>([])
const targets = ref<QQNotificationTarget[]>([])
const monitoredUsers = ref<MonitoredUser[]>([])
const deliveries = ref<QQDelivery[]>([])
const deliveryTotal = ref(0)
const deliveryPage = ref(1)
const deliveryStatus = ref('')
const actionKey = ref('')
const botModalOpen = ref(false)
const targetModalOpen = ref(false)
const editingBotId = ref<number | null>(null)
const editingTargetId = ref<number | null>(null)
const joinedGroups = ref<QQJoinedGroup[]>([])
const groupsLoading = ref(false)
const groupsError = ref('')
const groupInputMode = ref<'select' | 'manual'>('select')
let groupRequestId = 0

const botForm = reactive({ name: '', app_id: '', app_secret: '', is_enabled: true })
const targetForm = reactive({
  bot_id: null as number | null,
  name: '',
  group_openid: '',
  is_enabled: true,
  all_monitored_users: true,
  monitored_user_ids: [] as number[],
  message_template: DEFAULT_TEMPLATE,
})

const deliveryPages = computed(() => Math.max(1, Math.ceil(deliveryTotal.value / 20)))
const hasBots = computed(() => bots.value.length > 0)

function verificationLabel(status: string) {
  return ({ valid: '认证有效', invalid: '凭据无效', error: '验证异常', unverified: '待验证' } as Record<string, string>)[status] || status
}

function onlineStatusLabel(status: string) {
  return ({ online: '在线', connecting: '连接中', offline: '离线', disabled: '已停用' } as Record<string, string>)[status] || status
}

function deliveryLabel(status: string) {
  return ({ queued: '排队中', sending: '发送中', retry_wait: '等待重试', sent: '已送达', failed: '失败', cancelled: '已取消' } as Record<string, string>)[status] || status
}

async function loadDeliveries() {
  const page = await qqApi.deliveries({
    page: deliveryPage.value,
    page_size: 20,
    status: deliveryStatus.value || undefined,
  })
  deliveries.value = page.items
  deliveryTotal.value = page.total
}

async function loadAll(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true
  try {
    const [summary, botRows, targetRows, users] = await Promise.all([
      qqApi.overview(),
      qqApi.bots(),
      qqApi.targets(),
      monitoredUsersApi.list({ page: 1, page_size: 100 }),
    ])
    overview.value = summary
    bots.value = botRows
    targets.value = targetRows
    monitoredUsers.value = users.items
    await loadDeliveries()
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '读取 QQ 推送配置失败'))
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function openBot(bot?: QQBotAccount) {
  editingBotId.value = bot?.id ?? null
  botForm.name = bot?.name ?? ''
  botForm.app_id = bot?.app_id ?? ''
  botForm.app_secret = ''
  botForm.is_enabled = bot?.is_enabled ?? true
  botModalOpen.value = true
}

async function saveBot() {
  if (!botForm.name.trim() || !botForm.app_id.trim()) return ElMessage.warning('请填写机器人名称和 AppID')
  if (!editingBotId.value && botForm.app_secret.length < 8) return ElMessage.warning('AppSecret 至少需要 8 个字符')
  actionKey.value = 'save-bot'
  try {
    const payload = {
      name: botForm.name.trim(),
      app_id: botForm.app_id.trim(),
      is_enabled: botForm.is_enabled,
      ...(botForm.app_secret ? { app_secret: botForm.app_secret } : {}),
    }
    if (editingBotId.value) await qqApi.updateBot(editingBotId.value, payload)
    else await qqApi.createBot({ ...payload, app_secret: botForm.app_secret })
    botModalOpen.value = false
    ElMessage.success(editingBotId.value ? '机器人配置已更新' : '机器人已加密保存')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '保存机器人失败'))
  } finally {
    actionKey.value = ''
  }
}

async function toggleBot(bot: QQBotAccount) {
  const next = !bot.is_enabled
  actionKey.value = `bot-toggle-${bot.id}`
  try {
    await qqApi.updateBot(bot.id, { is_enabled: next })
    bot.is_enabled = next
    ElMessage.success(next ? '机器人已启用' : '机器人已停用')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '更新机器人状态失败'))
  } finally {
    actionKey.value = ''
  }
}

async function testBot(bot: QQBotAccount) {
  actionKey.value = `bot-test-${bot.id}`
  try {
    const result = await qqApi.testBot(bot.id)
    result.valid ? ElMessage.success(result.message) : ElMessage.warning(result.message)
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '验证 QQ 凭据失败'))
  } finally {
    actionKey.value = ''
  }
}

async function removeBot(bot: QQBotAccount) {
  try {
    await ElMessageBox.confirm(`将同时删除“${bot.name}”的 ${bot.target_count} 个群目标，历史投递记录仍会保留。`, '删除机器人', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' })
  } catch { return }
  actionKey.value = `bot-delete-${bot.id}`
  try {
    await qqApi.removeBot(bot.id)
    ElMessage.success('机器人及群目标已删除')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '删除机器人失败'))
  } finally { actionKey.value = '' }
}

function openTarget(target?: QQNotificationTarget) {
  if (!hasBots.value) return ElMessage.warning('请先添加 QQ 机器人')
  editingTargetId.value = target?.id ?? null
  targetForm.bot_id =
    target?.bot_id ?? bots.value.find((item) => item.is_enabled)?.id ?? bots.value[0]?.id ?? null
  targetForm.name = target?.name ?? ''
  targetForm.group_openid = target?.group_openid ?? ''
  targetForm.is_enabled = target?.is_enabled ?? true
  targetForm.all_monitored_users = target?.all_monitored_users ?? true
  targetForm.monitored_user_ids = [...(target?.monitored_user_ids ?? [])]
  targetForm.message_template = target?.message_template ?? DEFAULT_TEMPLATE
  groupInputMode.value = target ? 'manual' : 'select'
  targetModalOpen.value = true
  void loadJoinedGroups(true)
}

async function loadJoinedGroups(onOpen = false) {
  const requestId = ++groupRequestId
  const botId = targetForm.bot_id
  joinedGroups.value = []
  groupsError.value = ''
  groupsLoading.value = !!botId
  if (!botId) return
  try {
    const rows = await qqApi.joinedGroups(botId)
    if (requestId !== groupRequestId || botId !== targetForm.bot_id || !targetModalOpen.value) return
    joinedGroups.value = rows
    if (onOpen && rows.some((group) => group.group_openid === targetForm.group_openid)) {
      groupInputMode.value = 'select'
    }
  } catch (error) {
    if (requestId === groupRequestId && targetModalOpen.value) {
      groupsError.value = getErrorMessage(error, '获取群列表失败，请重试或手动填写 OpenID')
    }
  } finally {
    if (requestId === groupRequestId) groupsLoading.value = false
  }
}

function changeTargetBot() {
  targetForm.group_openid = ''
  targetForm.name = ''
  void loadJoinedGroups()
}

function selectJoinedGroup(openid: string) {
  const group = joinedGroups.value.find((item) => item.group_openid === openid)
  if (group) targetForm.name = group.name || `通知群 ${openid.slice(-8)}`
}

async function saveTarget() {
  if (!targetForm.bot_id || !targetForm.name.trim() || !targetForm.group_openid.trim()) return ElMessage.warning('请填写机器人、群名称和群 OpenID')
  if (groupInputMode.value === 'select') {
    const group = joinedGroups.value.find((item) => item.group_openid === targetForm.group_openid)
    if (groupsLoading.value || !group || (group.target_id && group.target_id !== editingTargetId.value)) {
      return ElMessage.warning('请选择当前机器人可用的群，或切换为手动填写')
    }
  }
  if (!targetForm.all_monitored_users && !targetForm.monitored_user_ids.length) return ElMessage.warning('请至少选择一个监听账号')
  actionKey.value = 'save-target'
  try {
    const payload = {
      bot_id: targetForm.bot_id,
      name: targetForm.name.trim(),
      group_openid: targetForm.group_openid.trim(),
      is_enabled: targetForm.is_enabled,
      all_monitored_users: targetForm.all_monitored_users,
      monitored_user_ids: targetForm.all_monitored_users ? [] : targetForm.monitored_user_ids,
      message_template: targetForm.message_template.trim(),
    }
    if (editingTargetId.value) await qqApi.updateTarget(editingTargetId.value, payload)
    else await qqApi.createTarget(payload)
    targetModalOpen.value = false
    ElMessage.success(editingTargetId.value ? '群目标已更新' : '群目标已添加')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '保存群目标失败'))
  } finally { actionKey.value = '' }
}

async function toggleTarget(target: QQNotificationTarget) {
  const next = !target.is_enabled
  actionKey.value = `target-toggle-${target.id}`
  try {
    await qqApi.updateTarget(target.id, { is_enabled: next })
    target.is_enabled = next
    ElMessage.success(next ? '群推送已启用' : '群推送已暂停')
    await loadAll(true)
  } catch (error) { ElMessage.error(getErrorMessage(error, '更新群目标失败')) }
  finally { actionKey.value = '' }
}

async function testTarget(target: QQNotificationTarget) {
  actionKey.value = `target-test-${target.id}`
  try {
    const result = await qqApi.testTarget(target.id)
    ElMessage.success(`${result.message} #${result.delivery_id}`)
    await loadAll(true)
  } catch (error) { ElMessage.error(getErrorMessage(error, '测试消息入队失败')) }
  finally { actionKey.value = '' }
}

async function removeTarget(target: QQNotificationTarget) {
  try {
    await ElMessageBox.confirm(`停止向“${target.name}”发送新消息？历史投递记录仍会保留。`, '删除群目标', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' })
  } catch { return }
  actionKey.value = `target-delete-${target.id}`
  try {
    await qqApi.removeTarget(target.id)
    ElMessage.success('群目标已删除')
    await loadAll(true)
  } catch (error) { ElMessage.error(getErrorMessage(error, '删除群目标失败')) }
  finally { actionKey.value = '' }
}

async function retryDelivery(delivery: QQDelivery) {
  actionKey.value = `delivery-retry-${delivery.id}`
  try {
    await qqApi.retryDelivery(delivery.id)
    ElMessage.success(`投递 #${delivery.id} 已重新排队`)
    await loadAll(true)
  } catch (error) { ElMessage.error(getErrorMessage(error, '重新投递失败')) }
  finally { actionKey.value = '' }
}

async function changeDeliveryFilter() {
  deliveryPage.value = 1
  try { await loadDeliveries() }
  catch (error) { ElMessage.error(getErrorMessage(error, '读取投递记录失败')) }
}

async function movePage(direction: number) {
  deliveryPage.value = Math.min(deliveryPages.value, Math.max(1, deliveryPage.value + direction))
  try { await loadDeliveries() }
  catch (error) { ElMessage.error(getErrorMessage(error, '读取投递记录失败')) }
}

onMounted(() => loadAll())
const statusTimer = window.setInterval(async () => {
  if (loading.value || refreshing.value) return
  try {
    const rows = await qqApi.bots()
    bots.value = rows
  } catch {
    // The main loader displays request failures; status polling stays quiet.
  }
}, 5000)
onBeforeUnmount(() => window.clearInterval(statusTimer))
</script>

<template>
  <div class="qq-page" v-loading="loading">
    <section class="qq-toolbar">
      <div>
        <span class="qq-eyebrow"><Bot :size="14" /> OFFICIAL QQ DELIVERY</span>
        <h2>QQ 推送适配</h2>
        <p>轮询任务发现新内容后，由独立 Worker 通过已授权机器人投递到指定群。机器人被群管理员添加后，QQ 会发送入群事件，Worker 会自动回复确认。</p>
      </div>
      <div class="qq-toolbar__actions">
        <el-button :loading="refreshing" @click="loadAll(true)"><RefreshCw v-if="!refreshing" :size="16" />刷新</el-button>
        <el-button type="primary" @click="openBot()"><CirclePlus :size="16" />添加机器人</el-button>
      </div>
    </section>

    <section class="qq-summary" aria-label="QQ 推送概览">
      <article><span><Bot :size="18" /></span><div><small>机器人</small><strong>{{ overview?.enabled_bots || 0 }}<em>/ {{ overview?.total_bots || 0 }}</em></strong></div></article>
      <article><span><UsersRound :size="18" /></span><div><small>启用群目标</small><strong>{{ overview?.enabled_targets || 0 }}</strong></div></article>
      <article><span><MessageSquare :size="18" /></span><div><small>待处理投递</small><strong>{{ overview?.queued_deliveries || 0 }}</strong></div></article>
      <article><span><ServerCog :size="18" /></span><div><small>QQ Worker</small><strong class="status-text" :class="overview?.worker_status">{{ overview?.worker_status === 'online' ? '运行中' : overview?.worker_status === 'offline' ? '离线' : '未知' }}</strong></div></article>
    </section>

    <aside class="platform-notice">
      <AlertCircle :size="20" />
      <div><strong>腾讯官方主动消息权限限制</strong><p>自 2025-04-21 起，QQ 开放平台不再提供通用主动消息能力。只有已获得相应群消息权限的机器人才能完成自动投递；凭据验证成功仅代表 AppID 与 AppSecret 有效，平台拒绝会原样记录在投递日志中。</p></div>
      <a href="https://bot.q.qq.com/wiki/" target="_blank" rel="noopener noreferrer">官方文档 <ExternalLink :size="14" /></a>
    </aside>

    <section class="qq-section">
      <header><div><h3>机器人授权</h3><p>每个 AppSecret 单独加密持久化，可同时运行多个机器人。</p></div><span>{{ bots.length }} 个</span></header>
      <div v-if="bots.length" class="bot-grid">
        <article v-for="bot in bots" :key="bot.id" class="bot-card">
          <div class="bot-card__top"><span class="bot-avatar"><Bot :size="20" /></span><div><strong>{{ bot.name }}</strong><small>AppID {{ bot.app_id }}</small></div><span class="verification" :class="bot.verification_status">{{ verificationLabel(bot.verification_status) }}</span><span class="online-status" :class="bot.online_status"><i />{{ onlineStatusLabel(bot.online_status) }}</span></div>
          <div class="bot-meta"><span><KeyRound :size="15" /><small>AppSecret</small><strong>{{ bot.secret_hint }}</strong></span><span><UsersRound :size="15" /><small>群目标</small><strong>{{ bot.target_count }}</strong></span><span><ShieldCheck :size="15" /><small>凭据版本</small><strong>v{{ bot.version }}</strong></span></div>
          <p v-if="bot.last_error" class="row-error"><AlertCircle :size="14" />{{ bot.last_error }}</p>
          <footer><el-switch :model-value="bot.is_enabled" :loading="actionKey === `bot-toggle-${bot.id}`" inline-prompt active-text="启用" inactive-text="停用" @change="toggleBot(bot)" /><div><el-button circle title="编辑机器人" @click="openBot(bot)"><Edit3 :size="15" /></el-button><el-button :loading="actionKey === `bot-test-${bot.id}`" @click="testBot(bot)"><RefreshCw v-if="actionKey !== `bot-test-${bot.id}`" :size="15" />验证</el-button><el-button circle type="danger" plain title="删除机器人" :loading="actionKey === `bot-delete-${bot.id}`" @click="removeBot(bot)"><Trash2 v-if="actionKey !== `bot-delete-${bot.id}`" :size="15" /></el-button></div></footer>
        </article>
      </div>
      <div v-else class="qq-empty"><span><Bot :size="22" /></span><strong>尚未配置机器人</strong><p>添加腾讯 QQ 开放平台 AppID 与 AppSecret 后即可创建群目标。</p><el-button type="primary" @click="openBot()"><CirclePlus :size="16" />添加第一个机器人</el-button></div>
    </section>

    <section class="qq-section">
      <header><div><h3>群投递目标</h3><p>把一个机器人连接到多个群，并按监听账号分配消息。</p></div><el-button :disabled="!hasBots" @click="openTarget()"><CirclePlus :size="16" />添加群目标</el-button></header>
      <div v-if="targets.length" class="target-list">
        <article v-for="target in targets" :key="target.id" class="target-row">
          <span class="target-icon"><UsersRound :size="18" /></span>
          <div class="target-main"><strong>{{ target.name }}</strong><small>{{ target.bot_name }} · {{ target.group_openid }}</small></div>
          <div class="target-scope"><small>推送范围</small><strong>{{ target.all_monitored_users ? '全部监听账号' : `${target.monitored_user_ids.length} 个指定账号` }}</strong></div>
          <el-switch :model-value="target.is_enabled" :loading="actionKey === `target-toggle-${target.id}`" @change="toggleTarget(target)" />
          <div class="target-actions"><el-button circle title="编辑群目标" @click="openTarget(target)"><Edit3 :size="15" /></el-button><el-button circle title="发送测试消息" :loading="actionKey === `target-test-${target.id}`" @click="testTarget(target)"><Send v-if="actionKey !== `target-test-${target.id}`" :size="15" /></el-button><el-button circle type="danger" plain title="删除群目标" :loading="actionKey === `target-delete-${target.id}`" @click="removeTarget(target)"><Trash2 v-if="actionKey !== `target-delete-${target.id}`" :size="15" /></el-button></div>
        </article>
      </div>
      <div v-else class="qq-empty qq-empty--compact"><span><UsersRound :size="21" /></span><strong>还没有群投递目标</strong><p>创建后，新推文会按绑定范围自动进入 Redis 队列。</p></div>
    </section>

    <section class="qq-section delivery-section">
      <header><div><h3>投递记录</h3><p>查看消息状态、平台错误与自动重试过程。</p></div><el-select v-model="deliveryStatus" class="status-filter" placeholder="全部状态" clearable @change="changeDeliveryFilter"><el-option label="排队中" value="queued" /><el-option label="发送中" value="sending" /><el-option label="等待重试" value="retry_wait" /><el-option label="已送达" value="sent" /><el-option label="失败" value="failed" /><el-option label="已取消" value="cancelled" /></el-select></header>
      <div class="delivery-table-wrap">
        <table class="delivery-table">
          <thead><tr><th>状态</th><th>投递目标</th><th>消息</th><th>尝试</th><th>时间</th><th aria-label="操作" /></tr></thead>
          <tbody>
            <tr v-for="delivery in deliveries" :key="delivery.id">
              <td><span class="delivery-status" :class="delivery.status"><i />{{ deliveryLabel(delivery.status) }}</span></td>
              <td><strong>{{ delivery.target_name }}</strong><small>{{ delivery.bot_name }} · {{ delivery.kind === 'test' ? '测试' : delivery.kind === 'batch' ? '手动批量推送' : '新推文' }}</small></td>
              <td><p>{{ delivery.last_error || delivery.message_body }}</p><small v-if="delivery.last_error" class="error-copy">{{ delivery.group_openid }}</small></td>
              <td><strong>{{ delivery.attempts }} / {{ delivery.max_attempts }}</strong><small v-if="delivery.status === 'retry_wait'">下次 {{ formatDateTime(delivery.next_attempt_at) }}</small></td>
              <td><strong>{{ formatDateTime(delivery.completed_at || delivery.created_at) }}</strong><small>#{{ delivery.id }}</small></td>
              <td><el-button v-if="['failed', 'cancelled'].includes(delivery.status)" circle title="重新投递" :loading="actionKey === `delivery-retry-${delivery.id}`" @click="retryDelivery(delivery)"><RotateCcw v-if="actionKey !== `delivery-retry-${delivery.id}`" :size="15" /></el-button></td>
            </tr>
            <tr v-if="!deliveries.length"><td colspan="6"><div class="table-empty"><CheckCircle2 :size="20" /><span>当前筛选条件下没有投递记录</span></div></td></tr>
          </tbody>
        </table>
      </div>
      <footer class="pagination"><span>共 {{ deliveryTotal }} 条</span><div><el-button circle :disabled="deliveryPage <= 1" @click="movePage(-1)"><ChevronLeft :size="15" /></el-button><strong>{{ deliveryPage }} / {{ deliveryPages }}</strong><el-button circle :disabled="deliveryPage >= deliveryPages" @click="movePage(1)"><ChevronRight :size="15" /></el-button></div></footer>
    </section>

    <BaseModal :open="botModalOpen" :title="editingBotId ? '编辑 QQ 机器人' : '添加 QQ 机器人'" description="凭据会使用服务端加密密钥持久化到 MySQL。" @close="botModalOpen = false">
      <el-form class="qq-form" label-position="top" @submit.prevent="saveBot">
        <el-form-item label="机器人名称"><el-input v-model="botForm.name" maxlength="100" placeholder="例如：运营通知机器人" /></el-form-item>
        <el-form-item label="AppID"><el-input v-model="botForm.app_id" maxlength="64" placeholder="QQ 开放平台 AppID" /></el-form-item>
        <el-form-item :label="editingBotId ? 'AppSecret（留空则不更换）' : 'AppSecret'"><el-input v-model="botForm.app_secret" type="password" show-password maxlength="500" placeholder="QQ 开放平台 AppSecret" /></el-form-item>
        <div class="form-switch"><div><strong>启用机器人</strong><small>停用后，其下所有群目标停止创建新投递。</small></div><el-switch v-model="botForm.is_enabled" /></div>
      </el-form>
      <template #footer><el-button @click="botModalOpen = false">取消</el-button><el-button type="primary" :loading="actionKey === 'save-bot'" @click="saveBot"><ShieldCheck v-if="actionKey !== 'save-bot'" :size="16" />加密保存</el-button></template>
    </BaseModal>

    <BaseModal :open="targetModalOpen" :title="editingTargetId ? '编辑群目标' : '添加群目标'" description="新推文入库后会按这里的范围生成独立投递任务。" width="large" @close="targetModalOpen = false">
      <el-form class="qq-form target-form" label-position="top" @submit.prevent="saveTarget">
        <div class="form-grid"><el-form-item label="发送机器人"><el-select v-model="targetForm.bot_id" placeholder="选择机器人" @change="changeTargetBot"><el-option v-for="bot in bots" :key="bot.id" :label="`${bot.name} · ${bot.app_id}`" :value="bot.id" /></el-select></el-form-item><el-form-item label="群名称（本地备注）"><el-input v-model="targetForm.name" maxlength="100" placeholder="例如：监控通知群" /></el-form-item></div>
        <el-form-item label="目标群">
          <div class="group-picker">
            <div class="group-picker__toolbar">
              <el-radio-group v-model="groupInputMode" size="small">
                <el-radio-button value="select">选择已加入的群</el-radio-button>
                <el-radio-button value="manual">手动填写</el-radio-button>
              </el-radio-group>
              <el-button :loading="groupsLoading" :disabled="!targetForm.bot_id" @click="loadJoinedGroups()"><RefreshCw v-if="!groupsLoading" :size="14" />刷新群列表</el-button>
            </div>
            <el-select v-if="groupInputMode === 'select'" v-model="targetForm.group_openid" filterable :loading="groupsLoading" :disabled="!targetForm.bot_id" placeholder="选择机器人已加入的群" @change="selectJoinedGroup">
              <el-option v-for="group in joinedGroups" :key="group.group_openid" :value="group.group_openid" :label="`${group.name || 'QQ群'} · ${group.group_openid}${group.target_id ? '（已配置目标）' : ''}`" :disabled="!!group.target_id && group.target_id !== editingTargetId" />
            </el-select>
            <el-input v-else v-model="targetForm.group_openid" maxlength="128" placeholder="填写 group_openid，非 QQ 群号" />
            <small v-if="groupsError" class="group-picker__error" role="alert">{{ groupsError }}</small>
            <small v-else-if="groupsLoading" class="template-help">正在获取所选机器人的群列表…</small>
            <small v-else-if="!joinedGroups.length" class="template-help">尚未记录到群。请先将机器人加入群，或在已有群中 @ 一次机器人，再刷新列表；也可手动填写。</small>
            <small class="template-help">列表来自已接收的入群和群消息事件，退群后移除。需先配置 QQ 事件回调；接入前未收到事件的群不会自动列出。群名为本地备注。</small>
          </div>
        </el-form-item>
        <div class="form-switch"><div><strong>接收全部监听账号</strong><small>关闭后可选择需要推送的特定 X 账号。</small></div><el-switch v-model="targetForm.all_monitored_users" /></div>
        <el-form-item v-if="!targetForm.all_monitored_users" label="监听账号"><el-select v-model="targetForm.monitored_user_ids" multiple filterable collapse-tags collapse-tags-tooltip placeholder="选择一个或多个账号"><el-option v-for="user in monitoredUsers" :key="user.id" :label="`@${user.username}${user.display_name ? ` · ${user.display_name}` : ''}`" :value="Number(user.id)" /></el-select></el-form-item>
        <el-form-item label="消息模板"><el-input v-model="targetForm.message_template" type="textarea" :rows="5" maxlength="2000" show-word-limit /><small class="template-help">可用变量：<code>{title}</code> 标题块（每条单推出现一次；批量推送只在整批最上方出现一次）；<code>{author}</code> 显示名称；<code>{username}</code> X 用户名；<code>{text}</code> 正文；<code>{url}</code> 原文链接；<code>{posted_at}</code> 发布时间（yyyy-MM-dd HH:mm:ss）。批量推送会读取所选机器人和群的这条单条模板，将多条正文合并后按 QQ 2000 字符限制拆分。</small></el-form-item>
        <div class="form-switch"><div><strong>启用群推送</strong><small>关闭后保留配置，但不再创建新投递。</small></div><el-switch v-model="targetForm.is_enabled" /></div>
      </el-form>
      <template #footer><el-button @click="targetModalOpen = false">取消</el-button><el-button type="primary" :loading="actionKey === 'save-target'" @click="saveTarget"><Send v-if="actionKey !== 'save-target'" :size="16" />保存目标</el-button></template>
    </BaseModal>
  </div>
</template>

<style scoped>
.group-picker{display:flex;flex-direction:column;gap:9px;width:100%}.group-picker__toolbar{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}.group-picker__error{color:#b33d4d;font-size:12px;line-height:1.5}.online-status{display:inline-flex;align-items:center;gap:5px;padding:5px 8px;border-radius:999px;color:#707783;background:#f1f2f5;font-size:9px;font-weight:700;white-space:nowrap}.online-status i{width:6px;height:6px;border-radius:50%;background:currentColor}.online-status.online{color:#087855;background:#eaf8f2}.online-status.connecting{color:#9a6800;background:#fff7df}.online-status.offline,.online-status.disabled{color:#9a626b;background:#fff0f2}
.qq-page{display:flex;flex-direction:column;gap:18px;max-width:1320px;margin:0 auto;color:#17191c}.qq-toolbar{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:6px 0 2px}.qq-toolbar h2{margin:7px 0 5px;font-size:24px;font-weight:680;letter-spacing:0}.qq-toolbar p,.qq-section header p{margin:0;color:#727986;font-size:13px;line-height:1.6}.qq-eyebrow{display:flex;align-items:center;gap:7px;color:#635bff;font-size:10px;font-weight:750;letter-spacing:.12em}.qq-toolbar__actions{display:flex;gap:9px;flex-shrink:0}.qq-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.qq-summary article{display:flex;align-items:center;min-height:92px;padding:18px;border:1px solid #e7e9ee;border-radius:16px;background:#fff;box-shadow:0 6px 22px rgba(20,25,38,.045);gap:13px}.qq-summary article>span,.bot-avatar,.target-icon,.qq-empty>span{display:grid;place-items:center;flex:0 0 auto;width:40px;height:40px;border:1px solid #e9e7ff;border-radius:12px;color:#635bff;background:#f8f7ff}.qq-summary article>div{display:flex;flex-direction:column;gap:3px}.qq-summary small,.bot-meta small,.target-scope small{color:#858b96;font-size:10px}.qq-summary strong{font-size:23px;font-weight:680}.qq-summary strong em{color:#a2a7af;font-size:12px;font-style:normal;font-weight:500}.status-text{font-size:15px!important}.status-text.online{color:#07865e}.status-text.offline{color:#d04c5b}.platform-notice{display:grid;grid-template-columns:auto 1fr auto;align-items:start;padding:15px 17px;border:1px solid #eadfac;border-radius:16px;color:#755b00;background:#fffdf4;gap:11px}.platform-notice strong{font-size:12px}.platform-notice p{margin:3px 0 0;color:#756a42;font-size:11px;line-height:1.65}.platform-notice a{display:flex;align-items:center;gap:5px;color:#635bff;font-size:11px;white-space:nowrap}.qq-section{overflow:hidden;border:1px solid #e7e9ee;border-radius:16px;background:#fff;box-shadow:0 8px 28px rgba(20,25,38,.045)}.qq-section>header{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:19px 20px;border-bottom:1px solid #eceef2}.qq-section>header h3{margin:0 0 4px;font-size:15px}.qq-section>header>span{color:#858b96;font-size:12px}.bot-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;padding:16px}.bot-card{display:flex;flex-direction:column;min-width:0;padding:16px;border:1px solid #e8eaf0;border-radius:14px;background:#fff;transition:border-color .18s,box-shadow .18s}.bot-card:hover{border-color:#d5d0ff;box-shadow:0 8px 24px rgba(42,40,70,.06)}.bot-card__top{display:flex;align-items:center;min-width:0;gap:11px}.bot-card__top>div{display:flex;overflow:hidden;flex:1;flex-direction:column;gap:3px}.bot-card__top strong,.target-main strong{overflow:hidden;font-size:13px;text-overflow:ellipsis;white-space:nowrap}.bot-card__top small,.target-main small{overflow:hidden;color:#7d838e;font-size:10px;text-overflow:ellipsis;white-space:nowrap}.verification{padding:5px 8px;border-radius:999px;color:#716b7f;background:#f1f2f5;font-size:9px;font-weight:700;white-space:nowrap}.verification.valid{color:#087855;background:#eaf8f2}.verification.invalid,.verification.error{color:#b63f50;background:#fff0f2}.verification.unverified{color:#9a6800;background:#fff7df}.bot-meta{display:grid;grid-template-columns:repeat(3,1fr);margin:15px 0;padding:12px 0;border-top:1px solid #eff0f3;border-bottom:1px solid #eff0f3}.bot-meta span{display:grid;grid-template-columns:auto 1fr;min-width:0;padding:0 10px;border-right:1px solid #eff0f3;gap:2px 7px}.bot-meta span:first-child{padding-left:0}.bot-meta span:last-child{padding-right:0;border:0}.bot-meta svg{grid-row:1/3;color:#8d879c}.bot-meta strong{overflow:hidden;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.row-error{display:flex;align-items:flex-start;margin:0 0 12px;padding:8px 10px;border-radius:8px;color:#b33d4d;background:#fff2f3;font-size:10px;line-height:1.5;gap:6px}.row-error svg{flex:0 0 auto}.bot-card footer{display:flex;align-items:center;justify-content:space-between;margin-top:auto}.bot-card footer>div,.target-actions{display:flex;gap:5px}.target-list{display:flex;flex-direction:column}.target-row{display:grid;grid-template-columns:auto minmax(180px,1.5fr) minmax(130px,1fr) auto auto;align-items:center;padding:14px 18px;border-bottom:1px solid #eff0f3;gap:14px}.target-row:last-child{border:0}.target-icon{width:38px;height:38px;border-radius:11px}.target-main,.target-scope{display:flex;min-width:0;flex-direction:column;gap:4px}.target-scope strong{font-size:11px}.qq-empty{display:flex;align-items:center;justify-content:center;min-height:220px;flex-direction:column;padding:32px;text-align:center}.qq-empty>span{margin-bottom:12px}.qq-empty strong{font-size:13px}.qq-empty p{margin:5px 0 15px;color:#858b96;font-size:11px}.qq-empty--compact{min-height:160px}.status-filter{width:150px}.delivery-table-wrap{overflow-x:auto}.delivery-table{width:100%;min-width:870px;border-collapse:collapse}.delivery-table th{padding:11px 15px;color:#9298a3;background:#fafbfc;font-size:9px;font-weight:700;text-align:left;text-transform:uppercase}.delivery-table td{padding:13px 15px;border-top:1px solid #eff0f3;font-size:11px;vertical-align:middle}.delivery-table td>strong,.delivery-table td>small{display:block}.delivery-table td>small{margin-top:3px;color:#9298a3;font-size:9px}.delivery-table td p{display:-webkit-box;overflow:hidden;max-width:390px;margin:0;color:#4d525b;line-height:1.45;-webkit-box-orient:vertical;-webkit-line-clamp:2}.delivery-table .error-copy{color:#bb5360}.delivery-status{display:inline-flex;align-items:center;gap:6px;padding:5px 8px;border-radius:999px;color:#666d78;background:#f1f2f5;font-size:9px;font-weight:700;white-space:nowrap}.delivery-status i{width:5px;height:5px;border-radius:50%;background:currentColor}.delivery-status.sent{color:#087855;background:#eaf8f2}.delivery-status.failed,.delivery-status.cancelled{color:#ba4051;background:#fff0f2}.delivery-status.queued,.delivery-status.sending{color:#554ac7;background:#f0efff}.delivery-status.retry_wait{color:#9a6800;background:#fff7df}.table-empty{display:flex;align-items:center;justify-content:center;min-height:110px;color:#8a909a;gap:8px}.pagination{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-top:1px solid #eff0f3;color:#8b919b;font-size:10px}.pagination>div{display:flex;align-items:center;gap:10px}.pagination strong{color:#515660;font-size:10px}.qq-form{display:flex;flex-direction:column}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.form-switch{display:flex;align-items:center;justify-content:space-between;margin-bottom:17px;padding:12px 13px;border:1px solid #e8eaf0;border-radius:12px;background:#fafbfc;gap:16px}.form-switch>div{display:flex;flex-direction:column;gap:3px}.form-switch strong{font-size:12px}.form-switch small,.template-help{color:#858b96;font-size:10px;line-height:1.5}.qq-form :deep(.el-select){width:100%}.qq-form :deep(.el-textarea__inner){line-height:1.55}.target-form :deep(.el-form-item){margin-bottom:17px}.delivery-section{margin-bottom:20px}@media(max-width:900px){.qq-summary{grid-template-columns:repeat(2,1fr)}.bot-grid{grid-template-columns:1fr}.target-row{grid-template-columns:auto minmax(0,1fr) auto auto}.target-scope{grid-column:2/3}.target-actions{grid-column:4;grid-row:1/3}}@media(max-width:620px){.qq-page{gap:13px}.qq-toolbar{align-items:flex-start;flex-direction:column}.qq-toolbar__actions{width:100%}.qq-toolbar__actions .el-button{flex:1}.qq-summary{grid-template-columns:1fr 1fr;gap:8px}.qq-summary article{min-height:80px;padding:12px;border-radius:14px}.qq-summary article>span{display:none}.platform-notice{grid-template-columns:auto 1fr}.platform-notice a{grid-column:2}.qq-section{border-radius:14px}.qq-section>header{align-items:flex-start;padding:16px;flex-direction:column}.bot-grid{padding:10px}.bot-meta{grid-template-columns:1fr}.bot-meta span{grid-template-columns:auto 1fr;padding:7px 0;border-right:0;border-bottom:1px solid #eff0f3}.target-row{grid-template-columns:auto minmax(0,1fr) auto;padding:13px}.target-scope{grid-column:2}.target-actions{grid-column:2/4;grid-row:auto;justify-content:flex-end}.form-grid{grid-template-columns:1fr}.status-filter{width:100%}}
</style>
