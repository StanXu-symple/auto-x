import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi } from '@/services/api'
import { TOKEN_KEY } from '@/services/http'
import type { AuthUser, LoginPayload } from '@/types'

const USER_KEY = 'x-sentinel-user'

const readUser = (): AuthUser | null => {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    localStorage.removeItem(USER_KEY)
    return null
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY))
  const user = ref<AuthUser | null>(readUser())
  const loading = ref(false)
  const isAuthenticated = computed(() => Boolean(token.value))

  async function login(payload: LoginPayload) {
    loading.value = true
    try {
      const result = await authApi.login(payload)
      token.value = result.access_token
      user.value = result.user
      localStorage.setItem(TOKEN_KEY, result.access_token)
      localStorage.setItem(USER_KEY, JSON.stringify(result.user))
      return result
    } finally {
      loading.value = false
    }
  }

  async function refreshUser() {
    if (!token.value) return null
    const result = await authApi.me()
    user.value = result
    localStorage.setItem(USER_KEY, JSON.stringify(result))
    return result
  }

  function logout() {
    token.value = null
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  }

  return { token, user, loading, isAuthenticated, login, logout, refreshUser }
})
