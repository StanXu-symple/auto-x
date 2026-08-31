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
  database: { status: string; latency_ms?: number; error?: string }
  redis: { status: string; latency_ms?: number; used_memory?: number; error?: string }
  worker: { status: string; last_heartbeat?: string; timestamp?: string; ttl_seconds?: number; worker_id?: string; error?: string; [key: string]: unknown }
  ai_worker?: { status: string; last_heartbeat?: string; timestamp?: string; ttl_seconds?: number; worker_id?: string; error?: string; active_tasks?: number; [key: string]: unknown }
}

export interface SystemSettings {
  global_poll_interval_seconds: number
  max_concurrency: number
  updated_at?: string
}

export type AiProvider = 'openai_responses' | 'codex_bridge'
export type AiReasoningEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'

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
  provider: AiProvider
  model: string
  base_url: string
  bridge_url?: string | null
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
