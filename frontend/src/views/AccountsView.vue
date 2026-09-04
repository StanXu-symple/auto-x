<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useDebounceFn } from '@vueuse/core'
import { ElMessageBox } from 'element-plus'
import {
  AlertCircle,
  Clock3,
  Edit3,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Trash2,
  UserRoundSearch,
  Zap,
} from 'lucide-vue-next'
import { monitoredUsersApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { useUiStore } from '@/stores/ui'
import type { CreateMonitoredUserPayload, MonitoredUser, UpdateMonitoredUserPayload } from '@/types'
import { formatDateTime, formatInterval, formatRelative } from '@/utils/format'
import AccountEditorModal from '@/components/AccountEditorModal.vue'
import EmptyState from '@/components/EmptyState.vue'
import PaginationBar from '@/components/PaginationBar.vue'
import StatusBadge from '@/components/StatusBadge.vue'

const ui = useUiStore()
const accounts = ref<MonitoredUser[]>([])
const total = ref(0)
const loading = ref(true)
const error = ref('')
const filters = reactive({ page: 1, page_size: 15, search: '', status: 'all' })
const editorOpen = ref(false)
const editing = ref<MonitoredUser | null>(null)
const editorLoading = ref(false)
const editorError = ref('')
const actionLoading = ref<string | null>(null)

const activeCount = computed(() => accounts.value.filter((account) => account.is_active).length)
const pausedCount = computed(() => accounts.value.length - activeCount.value)
const filterDescription = computed(() => {
  if (filters.search.trim()) return `搜索：${filters.search.trim()}`
  if (filters.status === 'active') return '仅显示运行中的账号'
  if (filters.status === 'paused') return '仅显示已暂停的账号'
  return `当前页 ${accounts.value.length} 个账号`
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await monitoredUsersApi.list({
      page: filters.page,
      page_size: filters.page_size,
      search: filters.search.trim() || undefined,
      is_active: filters.status === 'active' ? true : filters.status === 'paused' ? false : undefined,
    })
    accounts.value = result.items
    total.value = result.total
  } catch (requestError) {
    error.value = getErrorMessage(requestError, '无法加载监听账号')
  } finally {
    loading.value = false
  }
}

const debouncedSearch = useDebounceFn(() => {
  filters.page = 1
  load()
}, 350)

watch(() => filters.search, debouncedSearch)
watch(() => filters.status, () => {
  filters.page = 1
  load()
})

function openCreate() {
  editing.value = null
  editorError.value = ''
  editorOpen.value = true
}

function openEdit(account: MonitoredUser) {
  editing.value = account
  editorError.value = ''
  editorOpen.value = true
}

async function saveAccount(payload: CreateMonitoredUserPayload | UpdateMonitoredUserPayload) {
  editorLoading.value = true
  editorError.value = ''
  try {
    if (editing.value) {
      await monitoredUsersApi.update(editing.value.id, payload as UpdateMonitoredUserPayload)
      ui.toast('监听配置已更新', 'success', `@${editing.value.username} 的轮询策略已生效`)
    } else {
      const account = await monitoredUsersApi.create(payload as CreateMonitoredUserPayload)
      ui.toast('监听账号已添加', 'success', `@${account.username} 已加入调度队列`)
    }
    editorOpen.value = false
    await load()
  } catch (requestError) {
    editorError.value = getErrorMessage(requestError, '保存账号失败')
  } finally {
    editorLoading.value = false
  }
}

async function toggleAccount(account: MonitoredUser) {
  const key = `${account.id}:toggle`
  actionLoading.value = key
  try {
    if (account.is_active) await monitoredUsersApi.pause(account.id)
    else await monitoredUsersApi.resume(account.id)
    ui.toast(account.is_active ? '监听已暂停' : '监听已恢复', 'success', `@${account.username}`)
    await load()
  } catch (requestError) {
    ui.toast('操作失败', 'error', getErrorMessage(requestError))
  } finally {
    actionLoading.value = null
  }
}

async function pollNow(account: MonitoredUser) {
  const key = `${account.id}:poll`
  actionLoading.value = key
  try {
    await monitoredUsersApi.pollNow(account.id)
    ui.toast('轮询任务已提交', 'success', `正在获取 @${account.username} 的最新内容`)
    window.setTimeout(load, 1200)
  } catch (requestError) {
    ui.toast('无法启动轮询', 'error', getErrorMessage(requestError))
  } finally {
    actionLoading.value = null
  }
}

async function removeAccount(account: MonitoredUser) {
  actionLoading.value = `${account.id}:delete`
  try {
    await monitoredUsersApi.remove(account.id)
    ui.toast('监听账号已删除', 'success', `@${account.username} 不再参与轮询`)
    if (accounts.value.length === 1 && filters.page > 1) filters.page -= 1
    await load()
  } catch (requestError) {
    ui.toast('删除失败', 'error', getErrorMessage(requestError))
  } finally {
    actionLoading.value = null
  }
}

async function requestDelete(account: MonitoredUser) {
  try {
    await ElMessageBox.confirm(
      `删除 @${account.username} 后，将同时删除该账号已采集的推文与轮询历史。此操作不可撤销。`,
      '删除监听账号？',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning', confirmButtonClass: 'el-button--danger' },
    )
    await removeAccount(account)
  } catch (reason) {
    if (reason !== 'cancel' && reason !== 'close') ui.toast('删除失败', 'error', getErrorMessage(reason))
  }
}

function handleAccountCommand(command: string, account: MonitoredUser) {
  if (command === 'toggle') toggleAccount(account)
  if (command === 'delete') requestDelete(account)
}

function changePage(page: number) {
  filters.page = page
  load()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function nextPollLabel(value?: string | null) {
  const relative = formatRelative(value)
  return relative.includes('前') ? `已逾期 ${relative}` : relative
}

onMounted(load)
</script>

<template>
  <div class="accounts-view page-stack">
    <section class="summary-strip">
      <div><span class="summary-strip__icon"><Radio :size="18" /></span><span><small>账号总数</small><strong>{{ total }}</strong></span></div>
      <i />
      <div><span class="summary-strip__icon is-success"><Zap :size="18" /></span><span><small>当前页运行中</small><strong>{{ activeCount }}</strong></span></div>
      <i />
      <div><span class="summary-strip__icon is-neutral"><Pause :size="18" /></span><span><small>当前页已暂停</small><strong>{{ pausedCount }}</strong></span></div>
      <div class="summary-strip__action"><el-button type="primary" @click="openCreate"><Plus :size="17" />添加监听账号</el-button></div>
    </section>

    <section class="panel data-panel">
      <header class="data-toolbar">
        <div class="toolbar-heading"><strong>监听账号</strong><span>独立控制每个账号的采集状态和轮询节奏</span></div>
        <div class="toolbar-controls">
          <el-input v-model="filters.search" class="element-search" placeholder="搜索用户名或显示名称" clearable><template #prefix><Search :size="16" /></template></el-input>
          <el-radio-group v-model="filters.status" class="element-segmented" size="small">
            <el-radio-button v-for="option in [{ value: 'all', label: '全部' }, { value: 'active', label: '运行中' }, { value: 'paused', label: '已暂停' }]" :key="option.value" :value="option.value">{{ option.label }}</el-radio-button>
          </el-radio-group>
          <el-tooltip content="刷新列表" placement="top"><el-button circle aria-label="刷新列表" :loading="loading" @click="load"><RefreshCw v-if="!loading" :size="16" /></el-button></el-tooltip>
        </div>
      </header>

      <div class="content-result-bar"><span>共 <strong>{{ total }}</strong> 个账号</span><span>{{ filterDescription }}</span></div>

      <div v-if="error && !accounts.length" class="error-panel error-panel--embedded">
        <AlertCircle :size="21" /><div><strong>账号列表加载失败</strong><span>{{ error }}</span></div><button class="button button--secondary" @click="load">重试</button>
      </div>

      <div v-if="loading && !accounts.length" class="table-skeleton">
        <span v-for="index in 7" :key="index" />
      </div>

      <div v-else-if="accounts.length" class="table-wrap">
        <el-table v-loading="loading" :data="accounts" row-key="id" class="sentinel-table" table-layout="auto">
          <el-table-column label="账号" min-width="220">
            <template #default="{ row: account }"><div class="account-cell"><span class="avatar avatar--account"><img v-if="account.avatar_url" :src="account.avatar_url" alt="" referrerpolicy="no-referrer" /><template v-else>{{ (account.display_name || account.username).slice(0, 1).toUpperCase() }}</template></span><span><strong>{{ account.display_name || account.username }}</strong><small>@{{ account.username }}</small><small v-if="account.x_user_id" class="account-id">ID {{ account.x_user_id }}</small></span></div></template>
          </el-table-column>
          <el-table-column label="状态" width="154"><template #default="{ row: account }"><div class="account-status-cell"><StatusBadge :status="account.is_active ? account.status || 'active' : 'paused'" /><small v-if="account.last_error" class="row-error" :title="account.last_error">{{ account.last_error }}</small></div></template></el-table-column>
          <el-table-column label="轮询频率" min-width="130"><template #default="{ row: account }"><span class="interval-cell"><Clock3 :size="15" />{{ formatInterval(account.effective_poll_interval_seconds) }}<el-tag v-if="account.poll_interval_seconds == null" size="small" effect="plain">默认</el-tag></span></template></el-table-column>
          <el-table-column label="上次轮询" min-width="150"><template #default="{ row: account }"><span class="date-cell"><strong>{{ formatRelative(account.last_polled_at) }}</strong><small>{{ formatDateTime(account.last_polled_at) }}</small></span></template></el-table-column>
          <el-table-column label="下次轮询" min-width="150"><template #default="{ row: account }"><span class="date-cell"><strong>{{ account.is_active ? nextPollLabel(account.next_poll_at) : '不适用' }}</strong><small>{{ account.is_active ? formatDateTime(account.next_poll_at) : '监听已暂停' }}</small></span></template></el-table-column>
          <el-table-column label="操作" fixed="right" width="150" align="right">
            <template #default="{ row: account }">
              <div class="row-actions">
                <el-tooltip :content="account.is_active ? '立即轮询' : '恢复监听后可立即轮询'" placement="top"><el-button circle size="small" :loading="actionLoading === `${account.id}:poll`" :disabled="!account.is_active || (!!actionLoading && actionLoading !== `${account.id}:poll`)" @click="pollNow(account)"><RefreshCw v-if="actionLoading !== `${account.id}:poll`" :size="15" /></el-button></el-tooltip>
                <el-tooltip content="编辑配置" placement="top"><el-button circle size="small" @click="openEdit(account)"><Edit3 :size="15" /></el-button></el-tooltip>
                <el-dropdown trigger="click" @command="handleAccountCommand($event, account)">
                  <el-button circle size="small"><MoreHorizontal :size="16" /></el-button>
                  <template #dropdown><el-dropdown-menu><el-dropdown-item command="toggle"><component :is="account.is_active ? Pause : Play" :size="15" />{{ account.is_active ? '暂停监听' : '恢复监听' }}</el-dropdown-item><el-dropdown-item command="delete" divided><Trash2 :size="15" />删除账号</el-dropdown-item></el-dropdown-menu></template>
                </el-dropdown>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <EmptyState v-else-if="!loading" :title="filters.search || filters.status !== 'all' ? '没有匹配的账号' : '开始监听第一个账号'" :description="filters.search || filters.status !== 'all' ? '调整搜索词或筛选条件后重试' : '添加公开 X 用户，系统会自动按频率采集新内容'">
        <template #icon><UserRoundSearch :size="26" /></template>
        <el-button v-if="!filters.search && filters.status === 'all'" type="primary" @click="openCreate"><Plus :size="17" />添加监听账号</el-button>
      </EmptyState>

      <PaginationBar v-if="total > filters.page_size" :page="filters.page" :page-size="filters.page_size" :total="total" @change="changePage" />
    </section>

    <AccountEditorModal :open="editorOpen" :account="editing" :loading="editorLoading" :server-error="editorError" @close="editorOpen = false" @submit="saveAccount" />
  </div>
</template>
