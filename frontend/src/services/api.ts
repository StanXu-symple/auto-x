import type { AxiosResponse } from 'axios'
import { http } from './http'
import type {
  AiJob,
  AiJobActionResponse,
  AiJobQuery,
  AiFeature,
  AiDraft,
  AiDataSourceSavePayload,
  AiDataSourceStatus,
  AiDataSourceTestResult,
  AiSettings,
  AiSkill,
  AiSkillQuery,
  AiSkillPayload,
  AiUserProfile,
  AiUserSkillBinding,
  ApiEnvelope,
  AuthUser,
  ChangePasswordPayload,
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
  QQBotAccount,
  QQBotPayload,
  QQBotTestResult,
  QQBatchPushPayload,
  QQBatchPushResult,
  QQDelivery,
  QQJoinedGroup,
  QQNotificationTarget,
  QQOverview,
  QQTargetPayload,
  QQScheduledTask,
  SystemMetrics,
  SystemSettings,
  Tweet,
  TweetQuery,
  UpdateMonitoredUserPayload,
  UpdateAiDraftPayload,
  UpdateAiSettingsPayload,
  XCredentialSavePayload,
  XCredentialStatus,
  XCredentialTestResult,
  XSourceProvider,
  XSourceStatus,
  XSourceTestResult,
  TwscrapeCredentialSavePayload,
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
  async changePassword(payload: ChangePasswordPayload) {
    return dataOf(await http.patch<Wrapped<{ message: string }>>('/auth/password', payload))
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

export const qqApi = {
  async tasks() { return dataOf(await http.get<Wrapped<QQScheduledTask[]>>('/qq/tasks')) },
  async createTask(payload: Omit<QQScheduledTask,'id'|'last_run_at'|'next_run_at'|'created_at'|'updated_at'>) { return dataOf(await http.post<Wrapped<QQScheduledTask>>('/qq/tasks', payload)) },
  async updateTask(id: EntityId, payload: Omit<QQScheduledTask,'id'|'last_run_at'|'next_run_at'|'created_at'|'updated_at'>) { return dataOf(await http.patch<Wrapped<QQScheduledTask>>(`/qq/tasks/${id}`, payload)) },
  async removeTask(id: EntityId) { await http.delete(`/qq/tasks/${id}`) },
  async batchPush(payload: QQBatchPushPayload) {
    return dataOf(await http.post<Wrapped<QQBatchPushResult>>('/qq/batch-push', payload))
  },
  async overview() {
    return dataOf(await http.get<Wrapped<QQOverview>>('/qq/overview'))
  },
  async bots() {
    return dataOf(await http.get<Wrapped<QQBotAccount[]>>('/qq/bots'))
  },
  async joinedGroups(botId: EntityId) {
    return dataOf(await http.get<Wrapped<QQJoinedGroup[]>>(`/qq/bots/${botId}/groups`))
  },
  async createBot(payload: QQBotPayload & { app_secret: string }) {
    return dataOf(await http.post<Wrapped<QQBotAccount>>('/qq/bots', payload))
  },
  async updateBot(id: EntityId, payload: Partial<QQBotPayload>) {
    return dataOf(await http.patch<Wrapped<QQBotAccount>>(`/qq/bots/${id}`, payload))
  },
  async testBot(id: EntityId) {
    return dataOf(await http.post<Wrapped<QQBotTestResult>>(`/qq/bots/${id}/test`))
  },
  async removeBot(id: EntityId) {
    await http.delete(`/qq/bots/${id}`)
  },
  async targets() {
    return dataOf(await http.get<Wrapped<QQNotificationTarget[]>>('/qq/targets'))
  },
  async createTarget(payload: QQTargetPayload) {
    return dataOf(await http.post<Wrapped<QQNotificationTarget>>('/qq/targets', payload))
  },
  async updateTarget(id: EntityId, payload: Partial<QQTargetPayload>) {
    return dataOf(
      await http.patch<Wrapped<QQNotificationTarget>>(`/qq/targets/${id}`, payload),
    )
  },
  async testTarget(id: EntityId) {
    return dataOf(
      await http.post<Wrapped<{ message: string; delivery_id: number }>>(
        `/qq/targets/${id}/test`,
      ),
    )
  },
  async removeTarget(id: EntityId) {
    await http.delete(`/qq/targets/${id}`)
  },
  async deliveries(params: { page?: number; page_size?: number; status?: string; task_id?: EntityId } = {}) {
    return pageOf(
      await http.get<Wrapped<PaginatedResponse<QQDelivery>>>('/qq/deliveries', { params }),
    )
  },
  async clearTaskHistory(id: EntityId) { await http.delete(`/qq/tasks/${id}/history`) },
  async retryDelivery(id: EntityId) {
    return dataOf(
      await http.post<Wrapped<{ message: string; delivery_id: number }>>(
        `/qq/deliveries/${id}/retry`,
      ),
    )
  },
}

export const xhsApi = {
  async status() { return dataOf(await http.get<Wrapped<any>>('/xhs/status')) },
  async login(cookie: string) { return dataOf(await http.post<Wrapped<any>>('/xhs/login', { cookie })) },
  async post(payload: { title: string; content: string; images: string[] }) { return dataOf(await http.post<Wrapped<any>>('/xhs/posts', payload)) },
}

export const xCredentialsApi = {
  async status() {
    return dataOf(await http.get<Wrapped<XCredentialStatus>>('/x-credentials/status'))
  },
  async save(payload: XCredentialSavePayload) {
    return dataOf(await http.put<Wrapped<XCredentialStatus>>('/x-credentials/bearer-token', payload))
  },
  async test() {
    return dataOf(await http.post<Wrapped<XCredentialTestResult>>('/x-credentials/test'))
  },
  async remove() {
    await http.delete('/x-credentials/bearer-token')
  },
}

export const xSourcesApi = {
  async status() {
    return dataOf(await http.get<Wrapped<XSourceStatus>>('/x-sources/status'))
  },
  async selectProvider(provider: XSourceProvider) {
    return dataOf(await http.put<Wrapped<XSourceStatus>>('/x-sources/provider', { provider }))
  },
  async saveTwscrape(payload: TwscrapeCredentialSavePayload) {
    return dataOf(await http.put<Wrapped<XSourceStatus>>('/x-sources/twscrape/credentials', payload))
  },
  async testTwscrape() {
    return dataOf(await http.post<Wrapped<XSourceTestResult>>('/x-sources/twscrape/test'))
  },
  async removeTwscrape() {
    await http.delete('/x-sources/twscrape/credentials')
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
  async features() {
    return dataOf(await http.get<Wrapped<AiFeature[]>>('/ai/features'))
  },
  async userSkillBinding(userId: EntityId, featureCode: string) {
    return dataOf(
      await http.get<Wrapped<AiUserSkillBinding>>(
        `/ai/users/${userId}/skill-bindings/${featureCode}`,
      ),
    )
  },
  async saveUserSkillBinding(userId: EntityId, featureCode: string, skillIds: EntityId[]) {
    return dataOf(
      await http.put<Wrapped<AiUserSkillBinding>>(
        `/ai/users/${userId}/skill-bindings/${featureCode}`,
        { skill_ids: skillIds },
      ),
    )
  },
  async userProfile(userId: EntityId) {
    return dataOf(await http.get<Wrapped<AiUserProfile>>(`/ai/users/${userId}/profile`))
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

export const aiDataSourceApi = {
  async status() {
    return dataOf(await http.get<Wrapped<AiDataSourceStatus>>('/ai-data-source'))
  },
  async save(payload: AiDataSourceSavePayload) {
    return dataOf(await http.put<Wrapped<AiDataSourceStatus>>('/ai-data-source', payload))
  },
  async test() {
    return dataOf(await http.post<Wrapped<AiDataSourceTestResult>>('/ai-data-source/test'))
  },
  async models() {
    return dataOf(await http.get<Wrapped<{ models: string[] }>>('/ai-data-source/models'))
  },
  async remove() {
    await http.delete('/ai-data-source')
  },
}
