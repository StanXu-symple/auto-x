import type { AxiosResponse } from 'axios'
import { http } from './http'
import type {
  AiJob,
  AiJobActionResponse,
  AiJobQuery,
  AiDraft,
  AiSettings,
  AiSkill,
  AiSkillQuery,
  AiSkillPayload,
  ApiEnvelope,
  AuthUser,
  CreateMonitoredUserPayload,
  DashboardSummary,
  EntityId,
  GenerateTweetPayload,
  LoginPayload,
  LoginResponse,
  ManualPollResponse,
  MonitoredUser,
  MonitoredUserQuery,
  PaginatedResponse,
  PollingRun,
  PollingRunQuery,
  SystemMetrics,
  SystemSettings,
  Tweet,
  TweetQuery,
  UpdateMonitoredUserPayload,
  UpdateAiDraftPayload,
  UpdateAiSettingsPayload,
} from '@/types'

type Wrapped<T> = T | ApiEnvelope<T>

const dataOf = <T>(response: AxiosResponse<Wrapped<T>>): T => {
  const payload = response.data
  if (payload && typeof payload === 'object' && 'data' in payload) return payload.data
  return payload as T
}

const pageOf = <T>(response: AxiosResponse<Wrapped<PaginatedResponse<T>>>): PaginatedResponse<T> => {
  const page = dataOf(response)
  return {
    items: page.items || [],
    total: Number(page.total || 0),
    page: Number(page.page || 1),
    page_size: Number(page.page_size || 20),
    pages: page.pages,
  }
}

const itemsOf = <T>(response: AxiosResponse<Wrapped<T[] | PaginatedResponse<T>>>): T[] => {
  const payload = dataOf(response)
  return Array.isArray(payload) ? payload : payload.items || []
}

export const authApi = {
  async login(payload: LoginPayload) {
    return dataOf(await http.post<Wrapped<LoginResponse>>('/auth/login', payload))
  },
  async me() {
    return dataOf(await http.get<Wrapped<AuthUser>>('/auth/me'))
  },
}

export const dashboardApi = {
  async summary() {
    return dataOf(await http.get<Wrapped<DashboardSummary>>('/dashboard/summary'))
  },
}

export const monitoredUsersApi = {
  async list(params: MonitoredUserQuery) {
    return pageOf(await http.get<Wrapped<PaginatedResponse<MonitoredUser>>>('/monitored-users', { params }))
  },
  async create(payload: CreateMonitoredUserPayload) {
    return dataOf(await http.post<Wrapped<MonitoredUser>>('/monitored-users', payload))
  },
  async update(id: EntityId, payload: UpdateMonitoredUserPayload) {
    return dataOf(await http.patch<Wrapped<MonitoredUser>>(`/monitored-users/${id}`, payload))
  },
  async remove(id: EntityId) {
    await http.delete(`/monitored-users/${id}`)
  },
  async pause(id: EntityId) {
    return dataOf(await http.post<Wrapped<MonitoredUser>>(`/monitored-users/${id}/pause`))
  },
  async resume(id: EntityId) {
    return dataOf(await http.post<Wrapped<MonitoredUser>>(`/monitored-users/${id}/resume`))
  },
  async pollNow(id: EntityId) {
    return dataOf(await http.post<Wrapped<ManualPollResponse>>(`/monitored-users/${id}/poll`))
  },
}

export const tweetsApi = {
  async list(params: TweetQuery) {
    return pageOf(await http.get<Wrapped<PaginatedResponse<Tweet>>>('/tweets', { params }))
  },
}

export const pollingLogsApi = {
  async list(params: PollingRunQuery) {
    return pageOf(await http.get<Wrapped<PaginatedResponse<PollingRun>>>('/polling-logs', { params }))
  },
}

export const systemApi = {
  async metrics() {
    return dataOf(await http.get<Wrapped<SystemMetrics>>('/system/metrics'))
  },
  async settings() {
    return dataOf(await http.get<Wrapped<SystemSettings>>('/settings'))
  },
  async updateSettings(payload: Partial<SystemSettings>) {
    return dataOf(await http.put<Wrapped<SystemSettings>>('/settings', payload))
  },
}

export const aiApi = {
  async settings() {
    return dataOf(await http.get<Wrapped<AiSettings>>('/ai/settings'))
  },
  async updateSettings(payload: UpdateAiSettingsPayload) {
    return dataOf(await http.patch<Wrapped<AiSettings>>('/ai/settings', payload))
  },
  async skills(params: AiSkillQuery = { page: 1, page_size: 100 }) {
    return itemsOf(await http.get<Wrapped<AiSkill[] | PaginatedResponse<AiSkill>>>('/ai/skills', { params }))
  },
  async createSkill(payload: AiSkillPayload) {
    return dataOf(await http.post<Wrapped<AiSkill>>('/ai/skills', payload))
  },
  async updateSkill(id: EntityId, payload: AiSkillPayload) {
    return dataOf(await http.patch<Wrapped<AiSkill>>(`/ai/skills/${id}`, payload))
  },
  async removeSkill(id: EntityId) {
    await http.delete(`/ai/skills/${id}`)
  },
  async jobs(params: AiJobQuery) {
    return pageOf(await http.get<Wrapped<PaginatedResponse<AiJob>>>('/ai/jobs', { params }))
  },
  async updateDraft(id: EntityId, payload: UpdateAiDraftPayload) {
    return dataOf(await http.patch<Wrapped<AiDraft>>(`/ai/drafts/${id}`, payload))
  },
  async retryJob(id: EntityId) {
    return dataOf(await http.post<Wrapped<AiJob>>(`/ai/jobs/${id}/retry`))
  },
  async generateFromTweet(tweetId: EntityId, payload: GenerateTweetPayload = {}) {
    return dataOf(await http.post<Wrapped<AiJobActionResponse>>(`/tweets/${tweetId}/generate`, payload))
  },
}
