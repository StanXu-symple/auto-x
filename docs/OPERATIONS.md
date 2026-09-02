# X Sentinel 运维手册

## 服务与端口

| 服务 | Compose 名称 | 对外端口 | 说明 |
| --- | --- | --- | --- |
| 管理台 / 反向代理 | `frontend` | `8080` | 唯一默认公网入口 |
| API | `backend` | 仅容器网络 `8000` | FastAPI 与 API Prometheus 指标 |
| 轮询进程 | `worker` | 仅容器网络 `8001` | 调度、X 请求、Worker 指标 |
| AI 生成进程 | `ai-worker` | 仅容器网络 `8002` | AI 任务、provider 请求与 Worker 指标 |
| MySQL | `mysql` | 不映射 | 持久业务数据 |
| Redis | `redis` | 不映射 | 锁、心跳、触发标记 |
| Prometheus | `prometheus` | `127.0.0.1:9090` | monitoring profile |
| Grafana | `grafana` | `127.0.0.1:3000` | monitoring profile |

管理台、Prometheus 与 Grafana 的宿主机端口可通过 `.env` 调整；API 和两个 Worker 的容器内指标端口固定为 `8000`、`8001`、`8002`。除非已有防火墙、认证和 TLS 保护，不要把 MySQL、Redis、Worker 指标或 Prometheus 暴露到公网。

## 上线流程

```bash
make init
# 编辑 .env，替换所有 change-me / development-only 值并填写 X_TOKEN_ENCRYPTION_KEY；X Token 启动后在管理台录入
make validate-prod-env
make prod-config
make prod-up
```

检查：

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml ps
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/api/v1/health/ready
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml logs --tail=100 backend worker ai-worker
```

`docker-compose.prod.yml` 会启用生产环境密钥校验和更严格的资源配置。每次启动都会先运行一次性 `migrate` 服务；只有 `alembic upgrade head` 成功后 API、轮询 Worker 与 AI Worker 才会启动。生产升级前先执行备份，再构建新镜像：

```bash
make prod-backup
make prod-up
```

### 外部 MySQL / Redis 模式

外部模式必须通过下面的定向目标启动；它只启动应用容器，不创建本地数据容器：

```bash
install -m 600 .env.external.example .env.external
# 填写专用 MySQL 应用账号、JWT/管理员密码和 X_TOKEN_ENCRYPTION_KEY
make validate-external-env ENV_FILE=.env.external
make external-config ENV_FILE=.env.external
make external-up ENV_FILE=.env.external
```

停止应用但不影响外部数据服务：

```bash
make external-down ENV_FILE=.env.external
```

示例文件已配置 MySQL `10.211.55.30:3306`、Redis `10.211.55.30:6537` 且 Redis 无密码。部署前应创建仅能访问 `xsentinel` 库的专用 MySQL 用户；应用不应长期以 `root` 运行。外部数据库/Redis 的备份、恢复、Exporter 和宿主机告警应在数据服务所在服务器配置，根目录的 `backup`/`restore` 目标只处理 Compose 自带的数据容器。

不要直接执行不带服务名的 `docker compose -f docker-compose.yml -f docker-compose.external.yml up`，因为 Compose 仍会把基础文件中没有 profile 的本地数据服务纳入启动集合。

## 健康检查

- `/api/v1/health/live`：API 进程是否存活。
- `/api/v1/health/ready`：API、MySQL 与 Redis 是否可服务。
- 管理台“系统设置”页：CPU、内存、磁盘、进程、MySQL、Redis、轮询 Worker 与 AI Worker 心跳。
- Prometheus：抓取前端 Nginx、API、两个 Worker、MySQL、Redis 和宿主机 exporter。
- Grafana：预置 `X Sentinel Overview` Dashboard。

预置 Prometheus 规则覆盖目标离线、两个 Worker 心跳、轮询失败率/积压、AI 失败率/队列积压、API 5xx、主机内存/磁盘、MySQL 连接数和 Redis 内存。规则会出现在 Prometheus/Grafana，但项目没有预设外部通知接收方；生产环境应配置 Alertmanager 或 Grafana Contact Point。管理台的系统页仍会直接检查 API 所连接的 MySQL、Redis 和 Worker。

Worker 容器还在运行但管理台显示心跳过期时，优先查看：

```bash
docker compose logs --tail=200 worker
docker compose restart worker
```

AI Worker 使用 Redis key `xsentinel:ai-worker:heartbeat` 上报短期 JSON 心跳，字段包括 `worker_id`、`status`、`last_heartbeat`、`active_tasks`、`provider`、`provider_ready`、`key_required` 与 `key_configured`。管理台只读取这些布尔状态，不会返回密钥原文；`provider_ready` 表示凭据/目标配置通过本地检查，不等同于已探测 provider 的远端可用性。Worker 还会在容器网络 `8002/metrics` 暴露任务计数、耗时、待处理队列和草稿计数。容器健康检查同时验证 Redis 与指标端点；Prometheus 的 `x-sentinel-ai-worker` job 和 Grafana AI 面板用于观察进程与队列。

AI 专用指标为 `x_sentinel_ai_jobs_total{status,provider}`、`x_sentinel_ai_job_duration_seconds{status,provider}`、`x_sentinel_ai_queue_due`、`x_sentinel_ai_drafts_total{provider}` 和 `x_sentinel_ai_worker_heartbeat_timestamp_seconds`；Python 进程与 GC 指标由 Prometheus client 一并暴露。

## AI Worker 与 provider

AI 默认关闭。先在管理台“AI 数据源”保存唯一的 OpenAI 兼容 Base URL、模型和 API Key 并完成连通测试，再到“AI 创作”启用功能。Key 加密写入 MySQL，Redis 仅缓存密文；模型、最大重试、请求超时与输出限制也由管理台维护。以下参数控制 Worker 本身：

- `AI_WORKER_SCAN_INTERVAL_SECONDS`：扫描到期任务的间隔，默认 `2`。
- `AI_WORKER_MAX_CONCURRENCY`：单实例并发 provider 请求数，默认 `3`。
- `AI_WORKER_BATCH_SIZE`：单轮领取任务数，默认 `50`。
- `AI_WORKER_LOCK_TTL_SECONDS`：任务租约 TTL，默认 `180`。
- `AI_WORKER_HEARTBEAT_TTL_SECONDS`：Redis 心跳 TTL，默认 `30`。

若把心跳 TTL/刷新周期显著调高，还应同步调整 Prometheus `XSentinelAIWorkerHeartbeatStale` 的 `60` 秒阈值和 Grafana 心跳面板阈值，避免健康 Worker 被误报。

扩容前先确认 provider 并发/费用限制；多个 AI Worker 可以共享队列，但总并发约为“实例数 × 单实例并发”。Key 轮换直接在“AI 数据源”输入新 Key 并保存，不需要重启 Worker。Provider 调用会发送原 Post 与所选 Skill 指令；远程目标必须使用 HTTPS，本机兼容网关允许 HTTP。生产网络应再用防火墙/代理限制 AI Worker 出站目标，并在 provider 侧设置费用上限。AI 输出只形成草稿，必须人工核验。

## 备份

备份脚本会生成事务一致的 MySQL dump、Redis RDB、元数据和 SHA-256 校验文件。默认保存到 `.env` 中的 `BACKUP_DIR`。

```bash
make backup
```

生产覆盖模式请使用 `make prod-backup`，确保脚本读取与线上一致的 Compose 模型和环境文件。

目录结构：

```text
backups/20260831T120000Z/
  mysql.sql.gz
  redis.rdb
  metadata.json
  SHA256SUMS
```

建议：

- 每天至少一次备份，按业务恢复点目标提高频率。
- 将备份同步到另一台主机或对象存储；本机磁盘损坏会同时影响数据卷和本机备份。
- 备份目录权限按敏感数据处理。
- 定期执行恢复演练，仅“脚本成功”不等于备份可恢复。

## 恢复

恢复会替换当前 MySQL 与 Redis 数据。脚本在操作前默认先为当前状态创建一份安全备份，并要求显式确认：

```bash
make restore BACKUP=backups/20260831T120000Z CONFIRM_RESTORE=yes
```

生产覆盖模式使用：

```bash
make prod-restore BACKUP=backups/20260831T120000Z CONFIRM_RESTORE=yes
```

恢复过程会：

1. 校验 `SHA256SUMS`。
2. 备份当前状态。
3. 停止 API、轮询 Worker 和 AI Worker 写入。
4. 重建目标 MySQL 数据库并导入 dump，把 Redis RDB 安装为 Redis 7 AOF base 文件。
5. 验证 Redis 备份标记，应用当前 Alembic 迁移，再重启应用。

恢复完成后检查账号数量、最近 Post、AI 任务/草稿、最近轮询记录和两个 Worker 心跳。

## 数据库迁移

项目同时提供开发期自动建表和 Alembic。开发环境可保留 `AUTO_CREATE_TABLES=true`；生产覆盖配置强制关闭自动建表，并在启动 API/两个 Worker 前通过 `migrate` 一次性服务执行迁移：

```bash
make migrate
make prod-migrate
make external-migrate ENV_FILE=.env.external
```

生产执行迁移前必须先备份并查看对应 migration 文件。多实例升级时，应先迁移数据库，再滚动替换 API 与 Worker。

## 密码轮换与持久卷

MySQL 官方镜像的 `MYSQL_*` 初始化变量只在空数据目录第一次启动时创建或设置账号。已有 `mysql_data` 卷时，仅修改 `.env` 不会修改数据库内密码，反而会让应用、健康检查或 Exporter 无法登录。

推荐轮换顺序：

1. 执行 `make prod-backup` 并验证备份。
2. 使用当前管理员凭据连接 MySQL，先执行 `SELECT user, host FROM mysql.user` 确认准确账号与 host，再对专用应用用户/Exporter 用户执行 `ALTER USER ... IDENTIFIED BY ...`。
3. 立即把相同新值写入权限为 `0600` 的 `.env`，再重建相关容器：`make prod-up`。
4. 验证 readiness、Worker 心跳、Exporter 和登录，再撤销旧凭据。

不要猜测 `root` 的 host 部分，也不要把密码直接写进 shell 历史。Grafana 的管理员环境变量同样不是现有数据卷的通用密码重置机制；已有实例应先通过 Grafana UI/CLI 修改，再同步部署配置。

## 日志与排错

```bash
# 应用日志
docker compose logs -f --tail=200 backend worker ai-worker frontend

# 数据库日志
docker compose logs --tail=200 mysql

# Redis 日志
docker compose logs --tail=200 redis

# 监控组件
docker compose --profile monitoring logs --tail=200 prometheus grafana
```

### 新账号一直处于 queued

1. 确认 `worker` 容器健康且管理台心跳正常。
2. 在“X 数据源”确认当前模式并测试对应凭据；官方模式还需确认 X 账户有余额且接口权限足够，twscrape 模式需确认 Cookie 会话仍有效。
3. 查看 Worker 日志和该账号最近的轮询记录。
4. 检查 MySQL/Redis 是否可用；立即轮询令牌持久化在 MySQL，Redis 负责每账号互斥和全局 X API 闸门。

### 出现 429

官方模式下这是 X API 限流，系统会读取 `x-rate-limit-reset` 并设置全局 Redis 闸门；twscrape 模式下也可能是登录账号被网页接口临时限制。不要连续点击“立即轮询”；应提高轮询周期或降低活跃账号数，并在“X 数据源”测试当前凭据。

### AI 任务一直 queued / retrying

1. 检查 `docker compose ps ai-worker`、管理台 AI Worker 心跳和 `docker compose logs --tail=200 ai-worker`。
2. 确认管理台启用了 AI、provider 名称与 `.env` 中实际配置的凭据相匹配。
3. 在管理台确认任务快照所用的当前 `base_url` / `bridge_url`，再从 `ai-worker` 容器验证该地址可达，并检查 TLS、代理、DNS、429 与 provider 余额；不要把 Authorization 输出到终端。
4. 查看 Grafana 的 AI queue、throughput、duration；队列持续增长时再调整并发，避免盲目放大费用或限流。
5. 不要把密钥打印到日志或粘贴进 Skill。需要排查配置时只确认“是否存在”，不要输出原值。

### 登录失败

- 首次初始化时，管理员密码会以哈希写入 MySQL。
- 修改已有环境的 `ADMIN_PASSWORD` 不应被当作自动密码重置机制；应按团队变更流程更新管理员凭据，或在确认可丢弃本地数据的全新环境重新初始化。
- 检查浏览器请求是否到达 `/api/v1/auth/login`，以及 Nginx 与 API 日志。

### 磁盘持续增长

- 查看 MySQL 数据卷、日志与 Prometheus 保留期。
- `PROMETHEUS_RETENTION` 默认 `15d`，可按磁盘预算缩短。
- Post 与轮询审计数据目前是业务记录，不会被浏览器缓存替代；如需自动清理，应先确定合规保留期，再增加数据库归档任务。

## 停机与销毁

普通停机保留数据：

```bash
docker compose down
```

不要在未备份并确认的情况下使用 `docker compose down -v`；`-v` 会删除 MySQL、Redis、Prometheus 和 Grafana 的命名卷。
