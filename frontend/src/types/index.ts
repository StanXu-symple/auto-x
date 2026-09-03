export type EntityId = number | string

export interface ApiEnvelope<T> {
  data: T
  message?: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages?: number
}

export interface PaginationQuery {
  page?: number
  page_size?: number
}

export interface AuthUser {
  id: EntityId
  username: string
  email?: string
  display_name?: string
  role?: string
  is_active?: boolean
}

export interface LoginPayload {
  username: string
  password: string
}

export interface ChangePasswordPayload {
  current_password: string
  new_password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in?: number
  user: AuthUser
}

export type MonitorStatus = 'active' | 'paused' | 'polling' | 'error' | 'pending'

export interface MonitoredUser {
  id: EntityId
  username: string
  x_user_id?: string | null
  display_name?: string | null
  avatar_url?: string | null
  is_active: boolean
  poll_interval_seconds: number | null
  effective_poll_interval_seconds: number
  include_replies?: boolean
  include_retweets?: boolean
  last_polled_at?: string | null
  next_poll_at?: string | null
  last_tweet_id?: string | null
  status: MonitorStatus | string
  last_error?: string | null
  consecutive_failures: number
  tweet_count: number
  created_at: string
  updated_at?: string
}

export interface CreateMonitoredUserPayload {
  username: string
  poll_interval_seconds: number | null
  include_replies?: boolean
  include_retweets?: boolean
}

export interface UpdateMonitoredUserPayload {
  poll_interval_seconds?: number | null
  include_replies?: boolean
  include_retweets?: boolean
}

export interface ManualPollResponse {
  message: string
  user_id: EntityId
  scheduled_at?: string
}

export interface MonitoredUserQuery extends PaginationQuery {
  search?: string
  status?: string
  is_active?: boolean
}

export type TweetMediaType = 'photo' | 'video' | 'animated_gif'

export interface TweetMedia {
  id?: string
  type: TweetMediaType | string
  url?: string
  preview_image_url?: string
}

export interface TweetAuthor {
  username: string
  display_name?: string
  avatar_url?: string
}

export interface Tweet {
  id: EntityId
  tweet_id: string
  monitored_user_id: EntityId
  username: string
  author_id: string
  text: string
  lang: string | null
  conversation_id: string | null
  posted_at: string
  like_count: number
  retweet_count: number
  reply_count: number
  quote_count: number
  bookmark_count: number
  impression_count: number
  entities: Record<string, unknown> | null
  attachments: Record<string, unknown> | null
  referenced_tweets: Array<Record<string, unknown>> | null
  raw_payload?: Record<string, unknown> | null
  fetched_at: string
}

export interface TweetQuery extends PaginationQuery {
  search?: string
  username?: string
  monitored_user_id?: EntityId
  posted_after?: string
  posted_before?: string
  include_raw?: boolean
}

export type PollingRunStatus = 'running' | 'success' | 'partial' | 'failed' | 'skipped'

export interface PollingRun {
  id: EntityId
  monitored_user_id?: EntityId
  username?: string
  trigger?: 'scheduled' | 'manual' | string
  status: PollingRunStatus | string
  started_at: string
  finished_at?: string | null
  duration_ms?: number | null
  tweets_fetched: number
  tweets_inserted: number
  http_status?: number | null
  error_message?: string | null
  worker_id?: string | null
  rate_limit_reset_at?: string | null
}

export interface PollingRunQuery extends PaginationQuery {
  monitored_user_id?: EntityId
  status?: string
  trigger?: string
  started_after?: string
  started_before?: string
}

export interface DashboardCounts {
  monitored_users: number
  active_users: number
  paused_users: number
  tweets: number
  tweets_last_24h: number
}

export interface PollingTrendPoint {
  time?: string
  label?: string
  timestamp?: string
  success: number
  failed: number
  tweets?: number
}

export interface ServiceHealth {
  name: string
  status: 'healthy' | 'degraded' | 'down' | 'unknown' | string
  latency_ms?: number | null
  message?: string | null
  checked_at?: string
}

export interface DashboardPollingSummary {
  runs_last_24h: number
  successful_runs_last_24h: number
  failed_runs_last_24h: number
  success_rate: number
  due_users: number
}

export interface DashboardSummary {
  generated_at: string
  counts: DashboardCounts
  polling: DashboardPollingSummary
  server: SystemMetrics
  recent_tweets: Tweet[]
  recent_runs: PollingRun[]
}

export interface ResourceMetric {
  percent: number
  used?: number
  total?: number
  unit?: string
  load_1m?: number
  load_5m?: number
  load_15m?: number
}

export interface WorkerMetric {
  id?: string
  name: string
  status: string
  active_tasks?: number
  processed_tasks?: number
  failed_tasks?: number
  last_heartbeat?: string
}

export interface RuntimeResourceMetric {
  cpu_percent?: number | null
  memory_used_bytes?: number | null
  memory_total_bytes?: number | null
  memory_percent?: number | null
  rss_bytes?: number | null
}

export interface ServiceRuntimeMetric extends RuntimeResourceMetric {
  status: string
  latency_ms?: number
  error?: string
  resource_error?: string
  [key: string]: unknown
}

export interface WorkerRuntimeMetric extends RuntimeResourceMetric {
  status: string
  last_heartbeat?: string
  timestamp?: string
  ttl_seconds?: number
  worker_id?: string
  active_tasks?: number
  error?: string
  [key: string]: unknown
}

export interface SystemMetrics {
  generated_at: string
  cpu_percent: number
  memory: {
    total_bytes: number
    available_bytes: number
    used_bytes: number
    percent: number
  }
  disk: {
    total_bytes: number
    used_bytes: number
    free_bytes: number
    percent: number
  }
  uptime_seconds: number
  load_average?: number[]
  process: {
    pid?: number
    cpu_percent?: number
    rss_bytes?: number
    threads?: number
    open_files?: number
  }
  database: ServiceRuntimeMetric
  redis: ServiceRuntimeMetric
  worker: WorkerRuntimeMetric
  ai_worker?: WorkerRuntimeMetric
  xhs_worker?: WorkerRuntimeMetric
  qq_worker?: WorkerRuntimeMetric
}

export type QQVerificationStatus = 'unverified' | 'valid' | 'invalid' | 'error'
export type QQOnlineStatus = 'online' | 'connecting' | 'offline' | 'disabled'
export type QQDeliveryStatus = 'queued' | 'sending' | 'retry_wait' | 'sent' | 'failed' | 'cancelled'

export interface QQOverview {
  total_bots: number
  enabled_bots: number
  enabled_targets: number
  queued_deliveries: number
  failed_deliveries: number
  worker_status: string
  worker_last_heartbeat: string | null
}

export interface QQBotAccount {
  id: number
  name: string
  app_id: string
  secret_hint: string
  is_enabled: boolean
  verification_status: QQVerificationStatus | string
  last_verified_at: string | null
  last_error: string | null
  version: number
  target_count: number
  online_status: QQOnlineStatus | string
  created_at: string
  updated_at: string
}

export interface QQBotPayload {
  name: string
  app_id: string
  app_secret?: string
  is_enabled: boolean
}

export interface QQBotTestResult {
  valid: boolean
  verification_status: 'valid' | 'invalid' | 'error'
  message: string
  checked_at: string
}

export interface QQJoinedGroup {
  group_openid: string
  name: string | null
  target_id: number | null
  last_event_at: string
}

export interface QQNotificationTarget {
  id: number
  bot_id: number
  bot_name: string
  name: string
  group_openid: string
  is_enabled: boolean
  all_monitored_users: boolean
  monitored_user_ids: number[]
  message_template: string
  created_at: string
  updated_at: string
}

export interface QQTargetPayload {
  bot_id: number
  name: string
  group_openid: string
  is_enabled: boolean
  all_monitored_users: boolean
  monitored_user_ids: number[]
  message_template: string
}

export interface QQDelivery {
  id: number
  target_id: number | null
  source_tweet_id: number | null
  kind: 'tweet' | 'test' | string
  bot_name: string
  bot_app_id: string
  target_name: string
  group_openid: string
  message_body: string
  status: QQDeliveryStatus | string
  attempts: number
  max_attempts: number
  next_attempt_at: string
  provider_message_id: string | null
  last_error: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface QQBatchPushPayload {
  bot_id: EntityId
  group_openids: string[]
  tweet_ids: EntityId[]
}

export interface QQBatchPushResult {
  message: string
  delivery_ids: EntityId[]
  batch_count: number
}

export interface SystemSettings {
  global_poll_interval_seconds: number
  max_concurrency: number
  updated_at?: string
}

export type XCredentialAcquisitionMethod = 'developer_console' | 'api_exchange'
export type XCredentialVerificationStatus = 'unverified' | 'valid' | 'invalid' | 'error'

export interface XCredentialStatus {
  configured: boolean
  token_hint: string | null
  acquisition_method: XCredentialAcquisitionMethod | null
  verification_status: XCredentialVerificationStatus | null
  last_verified_at: string | null
  last_error: string | null
  updated_at: string | null
  version: number | null
  cache_active: boolean
  cache_ttl_seconds: number | null
}

export interface XCredentialSavePayload {
  bearer_token: string
  acquisition_method: XCredentialAcquisitionMethod
}

export interface XCredentialTestResult {
  valid: boolean
  verification_status: 'valid' | 'invalid' | 'error'
  message: string
  checked_at: string
}

export type XSourceProvider = 'official_api' | 'twscrape'

export interface TwscrapeCredentialStatus {
  configured: boolean
  account_hint: string | null
  verification_status: XCredentialVerificationStatus | null
  last_verified_at: string | null
  last_error: string | null
  updated_at: string | null
  version: number | null
  cache_active: boolean
  cache_ttl_seconds: number | null
}

export interface XSourceStatus {
  active_provider: XSourceProvider
  official_api: XCredentialStatus
  twscrape: TwscrapeCredentialStatus
  updated_at: string | null
}

export interface TwscrapeCredentialSavePayload {
  account_label: string
  auth_token: string
  ct0: string
  acknowledged_risk: boolean
}

export interface XSourceTestResult {
  provider: XSourceProvider
  valid: boolean
  verification_status: 'valid' | 'invalid' | 'error'
  message: string
  checked_at: string
}

export type AiProvider = 'openai_responses' | 'codex_bridge'
export type AiReasoningEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'

export interface AiDataSourceStatus {
  configured: boolean
  name: string | null
  protocol: 'openai_responses'
  base_url: string | null
  model: string | null
  key_hint: string | null
  verification_status: 'unverified' | 'valid' | 'invalid' | 'error' | null
  last_verified_at: string | null
  last_error: string | null
  version: number | null
  cache_active: boolean
  cache_ttl_seconds: number | null
  updated_at: string | null
}

export interface AiDataSourceSavePayload {
  name: string
  protocol: 'openai_responses'
  base_url: string
  model: string
  api_key?: string | null
}

export interface AiDataSourceTestResult {
  valid: boolean
  verification_status: 'valid' | 'invalid' | 'error'
  message: string
  models: string[]
  checked_at: string
}

export type XhsPublishStrategy = 'manual' | 'automatic' | 'delayed'
export type XhsPublishStatus = 'draft' | 'queued' | 'publishing' | 'retry_wait' | 'published' | 'failed' | 'cancelled'
export type XhsVisibility = '公开可见' | '仅自己可见' | '仅互关好友可见'

export interface XhsConnectionStatus {
  configured: boolean
  name: string | null
  connector: 'xiaohongshu_mcp' | null
  mcp_url: string | null
  token_configured: boolean
  token_hint: string | null
  verification_status: 'unverified' | 'valid' | 'invalid' | 'error' | null
  login_status: 'unknown' | 'logged_in' | 'logged_out' | string
  risk_acknowledged: boolean
  last_verified_at: string | null
  last_error: string | null
  version: number | null
  cache_active: boolean
  cache_ttl_seconds: number | null
  updated_at: string | null
}

export interface XhsConnectionSavePayload {
  name: string
  connector: 'xiaohongshu_mcp'
  mcp_url: string
  auth_token?: string | null
  risk_acknowledged: boolean
}

export interface XhsConnectionTestResult {
  valid: boolean
  logged_in: boolean
  verification_status: string
  login_status: string
  message: string
  checked_at: string
}

export interface XhsPublishSettings {
  enabled: boolean
  default_strategy: XhsPublishStrategy
  default_delay_minutes: number
  max_attempts: number
  daily_publish_limit: number
  default_visibility: XhsVisibility
  declare_original: boolean
  worker_status: string
  worker_last_heartbeat: string | null
  updated_at: string
}

export interface XhsPublishJob {
  id: EntityId
  source_ai_draft_id: EntityId | null
  title: string
  content: string
  images: string[]
  tags: string[]
  products: string[]
  visibility: XhsVisibility
  is_original: boolean
  strategy: XhsPublishStrategy
  status: XhsPublishStatus
  scheduled_at: string | null
  attempts: number
  max_attempts: number
  last_error: string | null
  platform_note_id: string | null
  platform_url: string | null
  published_at: string | null
  created_at: string
  updated_at: string
}

export interface XhsPublishJobCreatePayload {
  source_ai_draft_id?: number | null
  title: string
  content: string
  images: string[]
  tags: string[]
  products?: string[]
  visibility?: XhsVisibility
  is_original?: boolean
  strategy?: XhsPublishStrategy
  scheduled_at?: string | null
}

export interface AiSettings {
  enabled: boolean
  auto_generate: boolean
  provider: AiProvider
  model: string
  base_url: string
  bridge_url?: string | null
  provider_ready: boolean | null
  key_configured: boolean | null
  key_status: 'configured' | 'missing' | 'not_required' | 'worker_managed' | 'unknown' | string
  worker_status?: string | null
  worker_last_heartbeat?: string | null
  prompt_template: string | null
  language: string
  tone: string
  require_review: boolean
  reasoning_effort: AiReasoningEffort
  default_skill_ids: EntityId[]
  max_attempts: number
  max_output_tokens: number
  request_timeout_seconds: number
  updated_at?: string | null
}

export interface UpdateAiSettingsPayload {
  enabled: boolean
  auto_generate: boolean
  prompt_template: string
  language: string
  tone: string
  require_review: boolean
  reasoning_effort: AiReasoningEffort
  default_skill_ids: EntityId[]
  max_attempts: number
  max_output_tokens: number
  request_timeout_seconds: number
}

export interface AiSkill {
  id: EntityId
  name: string
  description?: string | null
  instructions: string
  output_schema?: Record<string, unknown> | null
  is_active: boolean
  version?: number
  remote_skill_id?: string | null
  remote_skill_version?: string | null
  created_at: string
  updated_at?: string | null
}

export interface AiSkillPayload {
  name: string
  description?: string
  instructions: string
  output_schema?: Record<string, unknown> | null
  is_active: boolean
  remote_skill_id?: string | null
  remote_skill_version?: string | null
}

export interface AiSkillQuery extends PaginationQuery {
  active?: boolean
}

export interface AiFeature {
  id: EntityId
  code: string
  name: string
  description?: string | null
  base_prompt: string
  is_active: boolean
}

export interface AiUserSkillBinding {
  monitored_user_id: EntityId
  username: string
  feature: AiFeature
  skill_ids: EntityId[]
  skills: AiSkill[]
  resolution_source: 'user_feature_binding' | 'global_default' | 'manual_override' | string
}

export interface AiUserProfile {
  monitored_user_id: EntityId
  username: string
  identity_summary: string
  focus_summary: string
  relationship_summary: string
  recurring_topics: string[]
  evidence: Array<{ tweet_id?: string; reason?: string }>
  confidence: number
  version: number
  last_source_tweet_id?: EntityId | null
  updated_at?: string | null
}

export type AiJobStatus =
  | 'queued'
  | 'running'
  | 'retry_wait'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export type AiDraftStatus = 'draft' | 'approved' | 'rejected'

export interface AiDraft {
  id: EntityId
  job_id: EntityId
  source_tweet_id: EntityId
  source_x_tweet_id?: string | null
  title: string
  content: string
  excerpt?: string | null
  status: AiDraftStatus | string
  metadata?: Record<string, unknown> | null
  revision: number
  created_at: string
  updated_at?: string | null
}

export interface AiJob {
  id: EntityId
  source_tweet_id: EntityId
  source_x_tweet_id?: string | null
  source_username?: string | null
  source_text?: string | null
  source_tweet?: Pick<Tweet, 'id' | 'tweet_id' | 'username' | 'text' | 'posted_at'> | null
  status: AiJobStatus | string
  feature_code?: string
  draft?: AiDraft | null
  error_message?: string | null
  last_error?: string | null
  provider?: string | null
  model?: string | null
  tone?: string | null
  language?: string | null
  skill_ids?: EntityId[]
  skill_id?: EntityId | null
  skill_snapshot?: Array<Record<string, unknown>> | Record<string, unknown> | null
  idempotency_key: string
  skills?: Array<Pick<AiSkill, 'id' | 'name'>>
  attempts: number
  max_attempts: number
  next_attempt_at?: string | null
  manual?: boolean
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  updated_at?: string | null
  request_snapshot?: Record<string, unknown> | null
  response_snapshot?: Record<string, unknown> | null
  prompt_hash?: string | null
  source_text_hash?: string | null
}

export interface AiJobQuery extends PaginationQuery {
  status?: string
}

export interface GenerateTweetPayload {
  skill_ids?: EntityId[]
  feature_code?: string
  idempotency_key?: string
}

export interface AiJobActionResponse {
  message?: string
  job_id?: EntityId
  id?: EntityId
  status?: string
}

export interface UpdateAiDraftPayload {
  title?: string
  content?: string
  excerpt?: string
  status?: AiDraftStatus | string
  metadata?: Record<string, unknown> | null
  revision: number
}

export interface ApiErrorBody {
  detail?: string | Array<{ msg?: string; loc?: Array<string | number> }>
  message?: string
  error?: string | {
    code?: string
    message?: string
    details?: unknown
    request_id?: string
  }
}
