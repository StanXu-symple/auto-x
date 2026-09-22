<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MenuOutlined, KeyOutlined, LogoutOutlined, DownOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import ConsoleNavigation from '@/components/ConsoleNavigation.vue'
import { useAuthStore } from '@/stores/auth'
import { authApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const mobileOpen = ref(false)
const passwordOpen = ref(false)
const passwordSaving = ref(false)
const password = reactive({ current_password: '', new_password: '', confirm: '' })
const initials = computed(() => (auth.user?.display_name || auth.user?.username || 'A').slice(0, 1).toUpperCase())
async function logout() { await auth.logout(); await router.replace('/login') }
function openPassword() { Object.assign(password, { current_password: '', new_password: '', confirm: '' }); passwordOpen.value = true }
async function changePassword() {
  if (!password.current_password || password.new_password.length < 12 || password.new_password !== password.confirm) return message.warning('请填写当前密码；新密码至少 12 位，两次输入须一致')
  passwordSaving.value = true
  try {
    await authApi.changePassword({ current_password: password.current_password, new_password: password.new_password })
    message.success('密码已更新，请重新登录'); passwordOpen.value = false; await logout()
  } catch (error) { message.error(getErrorMessage(error, '密码修改失败')) }
  finally { passwordSaving.value = false }
}
onMounted(() => { if (auth.isAuthenticated) auth.refreshUser().catch((error) => message.error(getErrorMessage(error, '无法刷新用户信息'))) })
</script>
<template>
  <a-layout class="console-shell">
    <a href="#main-content" class="skip-link">跳转到页面内容</a>
    <a-layout-sider :width="224" theme="light" class="console-sider">
      <RouterLink to="/dashboard" class="brand-lockup" aria-label="Auto-X 首页"><span class="brand-mark">a<span>×</span></span><div><strong>Auto-X</strong><small>CONTENT WORKSPACE</small></div></RouterLink>
      <ConsoleNavigation />
      <div class="sider-footer"><span class="small-brand">A quieter way<br />to stay ahead.</span><span class="edition-mark">AX / 01</span></div>
    </a-layout-sider>
    <a-layout class="console-main">
      <a-layout-header class="console-header">
        <div class="header-left"><a-button type="text" class="mobile-menu" aria-label="打开导航" @click="mobileOpen = true"><MenuOutlined /></a-button><span class="header-breadcrumb">工作空间 <span>/</span> <strong>{{ route.meta.title }}</strong></span></div>
        <a-dropdown placement="bottomRight" :trigger="['click']"><template #overlay><a-menu><a-menu-item key="password" @click="openPassword"><KeyOutlined /> 修改密码</a-menu-item><a-menu-item key="logout" @click="logout"><LogoutOutlined /> 退出登录</a-menu-item></a-menu></template><a-button type="text" class="profile-button" aria-label="用户菜单"><a-avatar size="small">{{ initials }}</a-avatar><span class="profile-name">{{ auth.user?.display_name || auth.user?.username || '管理员' }}</span><DownOutlined /></a-button></a-dropdown>
      </a-layout-header>
      <a-layout-content><main id="main-content" class="console-content" tabindex="-1"><RouterView /></main><footer class="content-footer"><span>Auto-X · 内容情报工作台</span><span>Collect. Create. Connect.</span></footer></a-layout-content>
    </a-layout>
    <a-drawer v-model:open="mobileOpen" title="Auto-X" placement="left" :width="280" class="navigation-drawer"><ConsoleNavigation @navigate="mobileOpen = false" /></a-drawer>
    <a-modal v-model:open="passwordOpen" title="修改登录密码" ok-text="更新密码" cancel-text="取消" :confirm-loading="passwordSaving" @ok="changePassword">
      <a-form layout="vertical"><a-form-item label="当前密码"><a-input-password v-model:value="password.current_password" autocomplete="current-password" /></a-form-item><a-form-item label="新密码"><a-input-password v-model:value="password.new_password" autocomplete="new-password" placeholder="至少 12 个字符" /></a-form-item><a-form-item label="确认新密码"><a-input-password v-model:value="password.confirm" autocomplete="new-password" @press-enter="changePassword" /></a-form-item></a-form>
    </a-modal>
  </a-layout>
</template>
