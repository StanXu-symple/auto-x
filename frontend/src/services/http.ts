import axios, { AxiosError } from 'axios'
import type { ApiErrorBody } from '@/types'

const TOKEN_KEY = 'x-sentinel-token'

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 20_000,
  headers: { 'Content-Type': 'application/json' },
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorBody>) => {
    if (error.response?.status === 401 && !error.config?.url?.includes('/auth/login')) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem('x-sentinel-user')
      if (window.location.pathname !== '/login') {
        window.location.assign(`/login?redirect=${encodeURIComponent(window.location.pathname + window.location.search)}`)
      }
    }
    return Promise.reject(error)
  },
)

export const getErrorMessage = (error: unknown, fallback = '请求失败，请稍后重试') => {
  if (!axios.isAxiosError<ApiErrorBody>(error)) {
    return error instanceof Error ? error.message : fallback
  }

  if (!error.response) return '无法连接到服务器，请检查网络或服务状态'
  const body = error.response.data
  if (typeof body?.detail === 'string') return body.detail
  if (Array.isArray(body?.detail)) {
    return body.detail.map((item) => item.msg).filter(Boolean).join('；') || fallback
  }
  if (typeof body?.error === 'string') return body.error
  return body?.message || body?.error?.message || fallback
}

export { TOKEN_KEY }
