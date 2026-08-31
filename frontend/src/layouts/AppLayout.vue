<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  Activity,
  Bell,
  ChevronDown,
  FileClock,
  Gauge,
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
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()
const collapsed = ref(localStorage.getItem('x-sentinel-sidebar') === 'collapsed')
const profileOpen = ref(false)
const now = ref(new Date())

const navigation = [
  { label: '仪表盘', to: '/dashboard', icon: Gauge },
  { label: '监听账号', to: '/accounts', icon: UsersRound },
  { label: '内容流', to: '/tweets', icon: MessageSquareText },
  { label: 'AI 创作', to: '/ai-writing', icon: Sparkles },
  { label: '轮询记录', to: '/polling-logs', icon: FileClock },
  { label: '系统与设置', to: '/settings', icon: Settings },
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
          <strong>X Sentinel</strong>
          <span>监听控制台</span>
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
        <span class="sidebar__section-label sidebar-label">管理</span>
        <RouterLink
          v-for="item in navigation"
          :key="item.to"
          :to="item.to"
          class="nav-item"
          :title="collapsed ? item.label : undefined"
          @click="ui.sidebarOpen = false"
        >
          <component :is="item.icon" :size="19" />
          <span class="sidebar-label">{{ item.label }}</span>
        </RouterLink>
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
  </div>
</template>
