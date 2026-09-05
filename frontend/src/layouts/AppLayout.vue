<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  Activity,
  Bell,
  Bot,
  BrainCircuit,
  ChevronDown,
  FileClock,
  Gauge,
  KeyRound,
  LockKeyhole,
  LogOut,
  Menu,
  MessageSquareText,
  PanelLeftClose,
  Radio,
  Settings,
  ShieldCheck,
  Sparkles,
  UserRound,
  UsersRound,
  X,
  BookOpen,
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { authApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import BaseModal from '@/components/BaseModal.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()
const collapsed = ref(localStorage.getItem('x-sentinel-sidebar') === 'collapsed')
const profileOpen = ref(false)
const passwordModalOpen = ref(false)
const passwordSubmitting = ref(false)
const passwordError = ref('')
const passwordTouched = ref(false)
const passwordForm = reactive({ current: '', next: '', confirm: '' })
const now = ref(new Date())

const navigation = [
  {
    label: '概览',
    items: [
      { label: '仪表盘', to: '/dashboard', icon: Gauge },
      { label: '运行监控', to: '/monitoring', icon: Activity },
    ],
  },
  {
    label: '监听',
    items: [
      { label: '监听账号', to: '/accounts', icon: UsersRound },
      { label: '内容流', to: '/tweets', icon: MessageSquareText },
      { label: '轮询记录', to: '/polling-logs', icon: FileClock },
    ],
  },
  {
    label: '内容与渠道',
    items: [
      { label: 'X 数据源', to: '/x-authorization', icon: KeyRound },
      { label: 'AI 数据源', to: '/ai-data-source', icon: BrainCircuit },
      { label: 'AI 创作', to: '/ai-writing', icon: Sparkles },
      { label: 'QQ 推送', to: '/qq-notifications', icon: Bot },
      { label: 'QQ 任务', to: '/qq-tasks', icon: Bell },
      { label: '小红书管理', to: '/xhs', icon: BookOpen },
    ],
  },
  { label: '系统', items: [{ label: '系统与设置', to: '/settings', icon: Settings }] },
]

const initials = computed(() => {
  const value = auth.user?.display_name || auth.user?.username || 'A'
  return value.slice(0, 1).toUpperCase()
})

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  localStorage.setItem('x-sentinel-sidebar', collapsed.value ? 'collapsed' : 'expanded')
}

function logout() {
  auth.logout()
  router.push('/login')
}

const passwordInvalid = computed(() => {
  if (!passwordTouched.value) return false
  return (
    !passwordForm.current ||
    passwordForm.next.length < 12 ||
    passwordForm.next === passwordForm.current ||
    passwordForm.confirm !== passwordForm.next
  )
})

function openPasswordModal() {
  profileOpen.value = false
  passwordError.value = ''
  passwordTouched.value = false
  passwordForm.current = ''
  passwordForm.next = ''
  passwordForm.confirm = ''
  passwordModalOpen.value = true
}

function closePasswordModal() {
  if (!passwordSubmitting.value) passwordModalOpen.value = false
}

async function submitPasswordChange() {
  passwordTouched.value = true
  passwordError.value = ''
  if (passwordInvalid.value) return

  passwordSubmitting.value = true
  try {
    await authApi.changePassword({
      current_password: passwordForm.current,
      new_password: passwordForm.next,
    })
    passwordModalOpen.value = false
    ElMessage.success('密码已更新')
  } catch (error) {
    const message = getErrorMessage(error, '密码修改失败，请稍后重试')
    passwordError.value =
      message === 'Current password is incorrect'
        ? '当前密码不正确'
        : message === 'New password must be different from the current password'
          ? '新密码不能与当前密码相同'
          : message
  } finally {
    passwordSubmitting.value = false
  }
}

let clockTimer: number | undefined
onMounted(() => {
  clockTimer = window.setInterval(() => (now.value = new Date()), 30_000)
  if (!auth.user) auth.refreshUser().catch(() => undefined)
})
onBeforeUnmount(() => window.clearInterval(clockTimer))
</script>

<template>
  <div class="app-shell" :class="{ 'app-shell--collapsed': collapsed }">
    <Transition name="fade">
      <button v-if="ui.sidebarOpen" class="mobile-overlay" aria-label="关闭菜单" @click="ui.sidebarOpen = false" />
    </Transition>

    <aside class="sidebar" :class="{ 'sidebar--mobile-open': ui.sidebarOpen }">
      <div class="sidebar__brand">
        <div class="brand-mark"><Radio :size="20" /></div>
        <div class="brand-copy">
          <strong>Auto-X</strong>
          <span>社媒自动化控制台</span>
        </div>
        <button class="sidebar__mobile-close icon-button" aria-label="关闭菜单" @click="ui.sidebarOpen = false"><X :size="18" /></button>
      </div>

      <div class="sidebar__workspace">
        <div class="workspace-icon"><ShieldCheck :size="16" /></div>
        <div class="sidebar-label">
          <span>工作空间</span>
          <strong>Production</strong>
        </div>
        <span class="live-indicator sidebar-label">LIVE</span>
      </div>

      <nav class="sidebar__nav" aria-label="主导航">
        <div v-for="group in navigation" :key="group.label" class="nav-group">
          <span class="sidebar__section-label sidebar-label">{{ group.label }}</span>
          <RouterLink
            v-for="item in group.items"
            :key="item.to"
            :to="item.to"
            class="nav-item"
            :title="collapsed ? item.label : undefined"
            @click="ui.sidebarOpen = false"
          >
            <component :is="item.icon" :size="18" />
            <span class="sidebar-label">{{ item.label }}</span>
          </RouterLink>
        </div>
      </nav>

      <div class="sidebar__footer">
        <div class="connection-card">
          <span class="connection-card__pulse"><span /></span>
          <div class="sidebar-label">
            <strong>调度器已连接</strong>
            <span>实时监听运行中</span>
          </div>
        </div>
        <button class="sidebar-collapse" type="button" @click="toggleCollapsed">
          <PanelLeftClose :size="18" />
          <span class="sidebar-label">收起侧边栏</span>
        </button>
      </div>
    </aside>

    <section class="main-area">
      <header class="topbar">
        <div class="topbar__left">
          <button class="topbar__menu icon-button" aria-label="打开菜单" @click="ui.sidebarOpen = true"><Menu :size="20" /></button>
          <div>
            <span class="topbar__eyebrow">Auto-X · Control Room</span>
            <h1>{{ route.meta.title }}</h1>
            <p>{{ route.meta.description }}</p>
          </div>
        </div>
        <div class="topbar__right">
          <div class="topbar__time">
            <Activity :size="14" />
            <span>{{ now.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }) }} · {{ now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }) }}</span>
          </div>
          <button class="icon-button topbar__notification" aria-label="通知">
            <Bell :size="18" />
            <span />
          </button>
          <div class="profile-menu">
            <button class="profile-button" type="button" @click="profileOpen = !profileOpen">
              <span class="avatar">{{ initials }}</span>
              <span class="profile-button__copy">
                <strong>{{ auth.user?.display_name || auth.user?.username || '管理员' }}</strong>
                <small>{{ auth.user?.role || 'Administrator' }}</small>
              </span>
              <ChevronDown :size="15" />
            </button>
            <Transition name="dropdown">
              <div v-if="profileOpen" class="profile-dropdown">
                <div class="profile-dropdown__head">
                  <UserRound :size="16" />
                  <span>{{ auth.user?.email || auth.user?.username }}</span>
                </div>
                <button type="button" @click="openPasswordModal"><KeyRound :size="16" />修改密码</button>
                <button type="button" @click="logout"><LogOut :size="16" />退出登录</button>
              </div>
            </Transition>
          </div>
        </div>
      </header>

      <main class="page-content">
        <RouterView v-slot="{ Component }">
          <Transition name="page" mode="out-in">
            <component :is="Component" :key="route.path" />
          </Transition>
        </RouterView>
      </main>
    </section>

    <BaseModal
      :open="passwordModalOpen"
      title="修改登录密码"
      description="更新后，新密码会安全地保存到数据库。"
      width="small"
      :close-on-backdrop="!passwordSubmitting"
      @close="closePasswordModal"
    >
      <form class="password-form sentinel-form" @submit.prevent="submitPasswordChange">
        <div v-if="passwordError" class="form-alert form-alert--error" role="alert">{{ passwordError }}</div>

        <label class="password-field">
          <span>当前密码</span>
          <el-input v-model="passwordForm.current" type="password" show-password autocomplete="current-password" size="large" placeholder="输入当前密码" />
          <small v-if="passwordTouched && !passwordForm.current">请输入当前密码</small>
        </label>

        <label class="password-field">
          <span>新密码</span>
          <el-input v-model="passwordForm.next" type="password" show-password autocomplete="new-password" size="large" placeholder="至少 12 个字符" />
          <small v-if="passwordTouched && passwordForm.next.length < 12">新密码至少需要 12 个字符</small>
          <small v-else-if="passwordTouched && passwordForm.next === passwordForm.current">新密码不能与当前密码相同</small>
        </label>

        <label class="password-field">
          <span>确认新密码</span>
          <el-input v-model="passwordForm.confirm" type="password" show-password autocomplete="new-password" size="large" placeholder="再次输入新密码" />
          <small v-if="passwordTouched && passwordForm.confirm !== passwordForm.next">两次输入的新密码不一致</small>
        </label>
      </form>

      <template #footer>
        <div class="modal-actions">
          <button class="button button--secondary" type="button" :disabled="passwordSubmitting" @click="closePasswordModal">取消</button>
          <button class="button button--primary" type="button" :disabled="passwordSubmitting" @click="submitPasswordChange">
            <span v-if="passwordSubmitting" class="spinner spinner--small" />
            <LockKeyhole v-else :size="16" />
            {{ passwordSubmitting ? '正在保存' : '更新密码' }}
          </button>
        </div>
      </template>
    </BaseModal>
  </div>
</template>
