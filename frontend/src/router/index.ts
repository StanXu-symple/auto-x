import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { guestOnly: true, title: '登录' },
    },
    {
      path: '/',
      component: () => import('@/layouts/AppLayout.vue'),
      meta: { requiresAuth: true },
      children: [
        { path: '', redirect: '/dashboard' },
        {
          path: 'dashboard',
          name: 'dashboard',
          component: () => import('@/views/DashboardView.vue'),
          meta: { title: '仪表盘', description: '监控任务与系统运行概览' },
        },
        {
          path: 'accounts',
          name: 'accounts',
          component: () => import('@/views/AccountsView.vue'),
          meta: { title: '监听账号', description: '管理 X 用户与独立轮询策略' },
        },
        {
          path: 'tweets',
          name: 'tweets',
          component: () => import('@/views/TweetsView.vue'),
          meta: { title: '内容流', description: '检索和查看已采集的推文内容' },
        },
        {
          path: 'x-authorization',
          name: 'x-authorization',
          component: () => import('@/views/XAuthorizationView.vue'),
          meta: { title: 'X 数据源中心', description: '选择官方 API 或 twscrape，并安全管理访问凭据' },
        },
        {
          path: 'ai-data-source',
          name: 'ai-data-source',
          component: () => import('@/views/AiDataSourceView.vue'),
          meta: { title: 'AI 数据源', description: '统一管理 OpenAI 兼容地址、模型与 API Key' },
        },
        {
          path: 'ai-writing',
          name: 'ai-writing',
          component: () => import('@/views/AiWritingView.vue'),
          meta: { title: 'AI 创作', description: '把监听内容转化为可审核、可编辑的创作草稿' },
        },
        {
          path: 'xiaohongshu',
          name: 'xiaohongshu',
          component: () => import('@/views/XiaohongshuView.vue'),
          meta: { title: '小红书发布', description: '管理登录连接、图文内容与自动或延迟发布策略' },
        },
        {
          path: 'polling-logs',
          name: 'polling-logs',
          component: () => import('@/views/PollingLogsView.vue'),
          meta: { title: '轮询记录', description: '追踪每次采集任务的执行结果' },
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/SettingsView.vue'),
          meta: { title: '系统与设置', description: '服务器健康状态与全局轮询配置' },
        },
      ],
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { title: '页面不存在' },
    },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.guestOnly && auth.isAuthenticated) return { name: 'dashboard' }
  document.title = `${String(to.meta.title || '控制台')} · X Sentinel`
})

export default router
