from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled by X Sentinel",
    ["method", "path", "status"],
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)
POLL_RUNS = Counter(
    "x_sentinel_poll_runs_total",
    "X polling runs",
    ["status", "trigger"],
)
TWEETS_INGESTED = Counter(
    "x_sentinel_tweets_ingested_total",
    "New tweets inserted into MySQL",
)
POLL_DURATION = Histogram(
    "x_sentinel_poll_duration_seconds",
    "Duration of X polling runs",
    ["status"],
)
POLL_QUEUE_DUE = Gauge(
    "x_sentinel_poll_queue_due",
    "Number of monitored users due at the last worker scan",
)
WORKER_HEARTBEAT = Gauge(
    "x_sentinel_worker_heartbeat_timestamp_seconds",
    "Unix timestamp of the worker's latest heartbeat",
)
AI_JOBS = Counter(
    "x_sentinel_ai_jobs_total",
    "AI generation job outcomes",
    ["status", "provider"],
)
AI_JOB_DURATION = Histogram(
    "x_sentinel_ai_job_duration_seconds",
    "Duration of AI generation attempts",
    ["status", "provider"],
)
AI_QUEUE_DUE = Gauge(
    "x_sentinel_ai_queue_due",
    "Number of AI generation jobs due at the last worker scan",
)
AI_DRAFTS = Counter(
    "x_sentinel_ai_drafts_total",
    "AI drafts committed",
    ["provider"],
)
AI_WORKER_HEARTBEAT = Gauge(
    "x_sentinel_ai_worker_heartbeat_timestamp_seconds",
    "Unix timestamp of the AI worker's latest heartbeat",
)
QQ_DELIVERIES = Counter(
    "x_sentinel_qq_deliveries_total",
    "QQ notification delivery outcomes",
    ["status"],
)
QQ_DELIVERY_DURATION = Histogram(
    "x_sentinel_qq_delivery_duration_seconds",
    "Duration of QQ notification delivery attempts",
    ["status"],
)
QQ_QUEUE_DUE = Gauge(
    "x_sentinel_qq_queue_due",
    "Number of QQ notification deliveries due at the last worker scan",
)
QQ_WORKER_HEARTBEAT_METRIC = Gauge(
    "x_sentinel_qq_worker_heartbeat_timestamp_seconds",
    "Unix timestamp of the QQ worker's latest heartbeat",
)
