<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, Eye, EyeOff, LockKeyhole, Radio, ShieldCheck, UserRound } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { getErrorMessage } from '@/services/http'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const form = reactive({ username: '', password: '' })
const showPassword = ref(false)
const error = ref('')
const touched = ref(false)

const invalid = computed(() => touched.value && (!form.username.trim() || !form.password))

async function submit() {
  touched.value = true
  error.value = ''
  if (!form.username.trim() || !form.password) return

  try {
    await auth.login({ username: form.username.trim(), password: form.password })
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    await router.replace(redirect)
  } catch (requestError) {
    error.value = getErrorMessage(requestError, '用户名或密码错误')
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-visual">
      <div class="login-visual__grid" />
      <div class="login-visual__orb login-visual__orb--one" />
      <div class="login-visual__orb login-visual__orb--two" />
      <div class="login-visual__content">
        <div class="login-brand">
          <span class="brand-mark brand-mark--large"><Radio :size="27" /></span>
          <span><strong>Auto-X</strong><small>INTELLIGENCE CONSOLE</small></span>
        </div>
        <div class="radar-visual" aria-hidden="true">
          <span class="radar-visual__ring radar-visual__ring--one" />
          <span class="radar-visual__ring radar-visual__ring--two" />
          <span class="radar-visual__ring radar-visual__ring--three" />
          <span class="radar-visual__sweep" />
          <span class="radar-visual__dot radar-visual__dot--one" />
          <span class="radar-visual__dot radar-visual__dot--two" />
          <span class="radar-visual__dot radar-visual__dot--three" />
          <Radio :size="38" />
        </div>
        <div class="login-visual__copy">
          <span class="eyebrow"><span /> ALWAYS WATCHING</span>
          <h1>洞察每一次<br /><em>重要动态</em></h1>
          <p>持续监听你关心的 X 用户，将零散信号转化为清晰、及时、可追踪的情报。</p>
        </div>
        <div class="login-facts">
          <div><strong>24 / 7</strong><span>持续监控</span></div>
          <div><strong>&lt; 1 min</strong><span>最快响应</span></div>
          <div><strong>AES-256</strong><span>安全存储</span></div>
        </div>
      </div>
    </section>

    <section class="login-panel">
      <div class="login-panel__orb login-panel__orb--one" aria-hidden="true" />
      <div class="login-panel__orb login-panel__orb--two" aria-hidden="true" />
      <div class="login-panel__content">
        <div class="login-panel__context">
          <div><span>CONTROL ROOM ACCESS</span><strong>进入管理控制台</strong></div>
          <span class="login-panel__state"><i />系统在线</span>
        </div>
        <form class="login-card" novalidate @submit.prevent="submit">
          <div class="login-card__mobile-brand">
            <span class="brand-mark"><Radio :size="20" /></span><strong>Auto-X</strong>
          </div>
          <header>
            <span class="login-card__shield"><ShieldCheck :size="22" /></span>
            <h2>欢迎回来</h2>
            <p>登录你的管理员账号以继续</p>
          </header>

          <div v-if="error" class="form-alert form-alert--error" role="alert">{{ error }}</div>

          <label class="field">
            <span class="field__label">用户名</span>
            <span class="input-shell" :class="{ 'is-invalid': invalid && !form.username.trim() }">
              <UserRound :size="18" />
              <input v-model="form.username" autocomplete="username" placeholder="请输入用户名" autofocus />
            </span>
            <small v-if="invalid && !form.username.trim()" class="field__error">请输入用户名</small>
          </label>

          <label class="field">
            <span class="field__label">密码</span>
            <span class="input-shell" :class="{ 'is-invalid': invalid && !form.password }">
              <LockKeyhole :size="18" />
              <input v-model="form.password" :type="showPassword ? 'text' : 'password'" autocomplete="current-password" placeholder="请输入密码" />
              <button type="button" aria-label="显示或隐藏密码" @click="showPassword = !showPassword">
                <component :is="showPassword ? EyeOff : Eye" :size="18" />
              </button>
            </span>
            <small v-if="invalid && !form.password" class="field__error">请输入密码</small>
          </label>

          <button class="button button--primary button--large login-submit" type="submit" :disabled="auth.loading">
            <span v-if="auth.loading" class="spinner spinner--small" />
            <template v-else>安全登录 <ArrowRight :size="18" /></template>
          </button>
          <p class="login-card__secure"><LockKeyhole :size="13" />连接受 TLS 加密保护</p>
        </form>
        <div class="login-panel__notes" aria-label="登录安全说明">
          <div><span><ShieldCheck :size="15" /></span><p><strong>安全会话</strong><small>登录凭据仅用于当前控制台会话</small></p></div>
          <div><span><LockKeyhole :size="15" /></span><p><strong>传输加密</strong><small>连接由 TLS 保护，降低凭据泄露风险</small></p></div>
        </div>
      </div>
    </section>
  </main>
</template>
