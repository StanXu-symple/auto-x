<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRightOutlined } from '@ant-design/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { getErrorMessage } from '@/services/http'
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const loading = ref(false)
const error = ref('')
const form = reactive({ username: '', password: '' })
async function submit() {
  if (loading.value) return
  error.value = ''
  if (!form.username.trim() || !form.password) { error.value = '请输入用户名和密码'; return }
  loading.value = true
  try {
    await auth.login({ username: form.username.trim(), password: form.password })
    const target = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    await router.replace(target.startsWith('/') && !target.startsWith('//') && !target.startsWith('/login') ? target : '/dashboard')
  } catch (e) { error.value = getErrorMessage(e, '用户名或密码错误') }
  finally { loading.value = false }
}
</script>
<template>
  <main class="login-page">
    <section class="login-brand-panel">
      <div class="brand-lockup"><span class="brand-mark">a<span>×</span></span><div><strong>Auto-X</strong><small>CONTENT WORKSPACE</small></div></div>
      <div class="login-brand-panel__inner"><span class="page-eyebrow">LESS NOISE. MORE SIGNAL.</span><h1>让重要信号<br /><em>先被看见。</em></h1><p>从每一条值得关注的动态出发。<br />连接监听、创作与发布，让内容有序发生。</p></div>
      <div class="login-index"><span>COLLECT / CREATE / CONNECT</span><span>AX — 01</span></div>
    </section>
    <section class="login-panel" aria-labelledby="login-title">
      <div class="login-form-card">
        <div class="brand-lockup login-mobile-brand"><span class="brand-mark">a<span>×</span></span><strong>Auto-X</strong></div>
        <span class="page-eyebrow">YOUR WORKSPACE, READY.</span><h2 id="login-title">欢迎回来</h2><p>登录你的工作空间，继续关注值得关注的事。</p>
        <a-alert v-if="error" type="error" :message="error" show-icon class="error-banner" />
        <a-form layout="vertical" @finish="submit">
          <a-form-item label="用户名" name="username"><a-input v-model:value="form.username" size="large" autocomplete="username" placeholder="输入管理员用户名" aria-label="用户名" /></a-form-item>
          <a-form-item label="密码" name="password"><a-input-password v-model:value="form.password" size="large" autocomplete="current-password" placeholder="输入登录密码" aria-label="密码" /></a-form-item>
          <a-button html-type="submit" type="primary" size="large" :loading="loading">进入工作空间 <ArrowRightOutlined /></a-button>
        </a-form>
        <div class="login-note">Auto-X · 让内容工作更从容</div>
      </div>
    </section>
  </main>
</template>
