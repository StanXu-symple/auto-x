# X Sentinel

一个可自托管的 X（Twitter）账号定时监听与 AI 草稿平台。前端使用 Vue 3 + Element Plus，后端使用 FastAPI，MySQL 保存监听配置、历史内容和生成审计，Redis 提供分布式锁、限流闸门和 Worker 心跳。支持运行中动态增加账号、分别调整轮询周期，并内置统一 OpenAI 兼容数据源、AI 草稿生成、服务器监控、Prometheus 指标和 Grafana 面板。

## 已包含的功能

- 动态新增、编辑、暂停、恢复和删除监听账号
- 全局默认轮询周期 + 单账号独立轮询周期，修改后无需重启
- 立即轮询、增量抓取、分页补齐、Post 唯一键去重
- 可选择是否采集回复与转发
- X API 全局限流闸门、429 退避、分页断点续传与下次执行时间调整
- Post 内容流、关键词搜索、账号筛选和分页
- 内置 AI Skill、手动/自动生成队列与可编辑草稿，统一使用一个 OpenAI 兼容账号
- AI 任务幂等、重试、请求/响应审计和独立 Worker，不会自动发布到 X
- 小红书 MCP 扫码登录、图文编辑，以及手动、立即自动和延迟发布队列
- 每次轮询的状态、耗时、读取数、新增数和错误审计
- CPU、内存、磁盘、负载、进程运行时间监控
- MySQL、Redis、API、Worker 心跳状态监控
- 管理员密码登录、JWT 鉴权和环境变量密钥管理
- Prometheus 指标与预置 Grafana Dashboard
- Docker Compose 一键启动、健康检查、持久卷和备份脚本

## 架构

```text
Vue 3 / Nginx -> FastAPI -----------> MySQL
                    |                  ^  ^
                    v                  |  |
                  Redis <-> Polling Worker -> 官方 X API / twscrape
                    ^                  |
                    +---- AI Worker ---+----> 统一 AI 数据源
                    +---- XHS Worker ------> xiaohongshu-mcp -> 小红书

Prometheus -> Nginx + API + both Workers + exporters -> Grafana
```

详细设计见 [架构说明](docs/ARCHITECTURE.md)，X 官方接口见 [X API 接入说明](docs/X_API.md)，小红书部署和账号风险见 [小红书接入说明](docs/XIAOHONGSHU.md)。

## 快速启动

### 1. 准备配置

需要 Docker 24+ 与 Docker Compose v2.24+。

```bash
install -m 600 .env.example .env
```

至少修改这些值：

```dotenv
MYSQL_PASSWORD=replace-with-a-strong-password
MYSQL_ROOT_PASSWORD=replace-with-another-strong-password
MYSQL_EXPORTER_PASSWORD=replace-with-an-exporter-password
REDIS_PASSWORD=replace-with-a-strong-password
JWT_SECRET_KEY=replace-with-at-least-32-random-characters
ADMIN_PASSWORD=replace-with-a-strong-admin-password
X_TOKEN_ENCRYPTION_KEY=generate-a-separate-secret-with-at-least-32-characters
GRAFANA_ADMIN_PASSWORD=replace-with-a-strong-grafana-password
```

生成随机密钥的一种方式：

```bash
openssl rand -hex 32
```

### 2. 启动核心服务

```bash
make up
```

`migrate` 一次性服务会先执行 `alembic upgrade head`；迁移成功后 API、轮询 Worker 和 AI Worker 才会启动。

等待健康检查通过：

```bash
docker compose ps
```

打开 [http://localhost:8080](http://localhost:8080)，使用 `.env` 中的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录。
首次登录后可从右上角头像菜单进入“修改密码”；新密码将使用 Argon2 哈希后持久化到 MySQL，后续启动不会被 `.env` 中的初始密码覆盖。

### 3. 启动监控套件（可选）

```bash
docker compose --profile monitoring up -d
```

- X Sentinel：[http://localhost:8080](http://localhost:8080)
- Prometheus：[http://127.0.0.1:9090](http://127.0.0.1:9090)
- Grafana：[http://127.0.0.1:3000](http://127.0.0.1:3000)

Grafana 使用 `.env` 中的 `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`，数据源、告警规则和 X Sentinel Dashboard 会自动配置。预置规则默认只在 Prometheus/Grafana 中显示状态；若要向邮件、企业微信等渠道推送，还需按所在环境接入 Alertmanager 或 Grafana Contact Point。

## AI 草稿生成

AI 功能默认关闭；不配置 AI 数据源也可以正常使用账号监听。启用前进入管理台“AI 数据源”，填写配置名称、OpenAI 兼容 Base URL、模型和 API Key，保存并执行连通测试；再到“AI 创作”选择默认 Skill、重试次数和输出限制并启用自动生成。独立 `ai-worker` 领取任务、动态读取当前唯一数据源，并把结果保存为可继续编辑的草稿。系统不会自动发布到 X。

AI API Key 使用服务端凭据加密密钥持久化到 MySQL，Redis 只缓存密文，API 不返回明文，任务快照和日志也不会包含 Key。Worker 每次执行前重新读取数据源，并把数据源名称与版本写入审计快照。携带凭据的远程地址必须使用 HTTPS，本机兼容网关可使用 HTTP。

“AI 创作 → 用户策略与画像”支持按“监听用户 × AI 功能点”绑定一个或多个 Skill。每次新建 AI 会话按“手动覆盖 → 用户功能绑定 → 全局默认”解析 Skill，并把解析结果和版本写入任务快照。上下文同时包含该作者已有画像及最近 20 条动态；成功生成后会更新“他是谁、近期关注、动态关联、长期主题、证据和置信度”，供下一次创作继续使用。原帖与近期动态始终作为不可信引用数据处理，不能覆盖系统、功能点或 Skill 指令。

## 小红书发布

小红书功能默认关闭。先启动开源 `xiaohongshu-mcp` 执行端，再进入“小红书发布”配置 MCP 地址、可选访问令牌并扫码登录。系统支持文章草稿、立即自动发布和延迟发布，独立 `xhs-worker` 负责重试、每日上限与审计。Cookie 只保存在 MCP 执行端，不会写入本项目的 MySQL 或 Redis。Docker 用户可执行 `docker compose --profile xiaohongshu up -d`，详细步骤见 [小红书接入说明](docs/XIAOHONGSHU.md)。

## 使用外部 MySQL 与 Redis

项目包含专用覆盖配置，示例已填写 `10.211.55.30:3306` 和 `10.211.55.30:6537`，Redis 密码留空：

```bash
install -m 600 .env.external.example .env.external
# 编辑 .env.external：填写专用数据库账号、JWT/管理员密码和 X Token 加密密钥
make external-config ENV_FILE=.env.external
make external-up ENV_FILE=.env.external
```

外部模式不会启动本地 MySQL/Redis，仍会先自动执行 Alembic 迁移。不要让应用长期使用 MySQL `root`；先由数据库管理员创建仅对 `xsentinel` 数据库有权限的专用账号，再填写 `MYSQL_USER` / `MYSQL_PASSWORD`。外部数据服务的备份、恢复和主机级监控应由它们所在服务器负责。

## 第一次使用

1. 登录管理台，进入“X 数据源”。
2. 选择“官方 X API”或实验性的“twscrape”，按页面引导保存并测试对应凭据，然后点击“启用此数据源”。
3. 进入“监听账号”，输入不带 `@` 的 X 用户名。
4. 设置该账号的轮询周期，以及是否包含回复/转发。
5. 保存后 Worker 会在下一调度 tick 自动执行；也可以点击“立即轮询”。
6. 在“内容流”查看新增 Post，在“轮询记录”查看执行与错误，在“系统监控”确认服务状态。
7. 如需 AI 草稿，先配置 provider 凭据，再到 AI 设置启用功能、选择 Skill；可手动生成，或在小规模验证后开启自动生成。

Worker 会在每次用户名解析和时间线读取前从 MySQL 获取当前数据源，因此切换后不需要重启；系统会清除旧认证闸门并立即重新排队活跃账号。官方 X API 当前采用按量计费，轮询周期越短、账号越多，请求成本越高。twscrape 不消耗官方 API Credits，但属于非官方网页接口，可能随 X 页面更新失效，并存在验证码、Cookie 失效及账号受限风险。

## 配置项

配置完整示例位于 [.env.example](.env.example)。常用项：

| 配置 | 作用 | 默认值 |
| --- | --- | --- |
| `APP_PORT` | 管理台对外端口 | `8080` |
| `ADMIN_USERNAME` | 管理员用户名 | `admin` |
| `ADMIN_PASSWORD` | 管理员密码 | 无安全默认值，必须修改 |
| `JWT_SECRET_KEY` | JWT 签名密钥 | 必须修改 |
| `X_TOKEN_ENCRYPTION_KEY` | 加密 MySQL 中 X Token 的服务端密钥，至少 32 位 | 空 |
| `X_TOKEN_CACHE_TTL_SECONDS` | Redis 密文缓存有效期 | `300` |
| `DEFAULT_POLL_INTERVAL_SECONDS` | 新账号默认轮询周期 | `300` |
| `WORKER_SCAN_INTERVAL_SECONDS` | Worker 检查到期任务的间隔 | `2` |
| `WORKER_MAX_CONCURRENCY` | 单 Worker 最大并发账号数 | `5` |
| `AI_WORKER_MAX_CONCURRENCY` | 单 AI Worker 最大并发任务数 | `3` |
| `AI_WORKER_BATCH_SIZE` | 每轮领取 AI 任务上限 | `50` |
| `MYSQL_*` | MySQL 数据库与凭据 | 见示例文件 |
| `REDIS_PASSWORD` | Redis 密码；外部实例无密码时可留空 | 本地模式必须修改 |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `TZ` | 容器显示时区 | `Asia/Shanghai` |

所有持久时间以 UTC 保存，前端按浏览器本地时区显示。

## 常用运维命令

```bash
# 查看状态
docker compose ps

# 跟踪 API 与两个 Worker 日志
docker compose logs -f backend worker ai-worker

# 重启 Worker
docker compose restart worker

# 重启 AI Worker
docker compose restart ai-worker

# 停止服务（保留数据卷）
docker compose down

# 更新并重建
docker compose up -d --build
```

备份与恢复说明见 [运维手册](docs/OPERATIONS.md)。

## 本地开发

### 后端

先准备可从宿主机访问的 MySQL 与 Redis。Compose 默认数据容器只在内部网络 `expose`，不会映射宿主机端口；如果在宿主机直接运行 Python，请使用单独安装的数据服务或自行添加仅绑定 `127.0.0.1` 的开发端口映射。然后：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
install -m 600 .env.example .env
# 把 .env 中的 MYSQL_HOST/PORT、REDIS_HOST/PORT 改为宿主机可达地址，并填写密码
alembic upgrade head
uvicorn app.main:app --reload
```

另开终端启动轮询 Worker；需要调试 AI 流程时再开一个终端启动 AI Worker：

```bash
cd backend
source .venv/bin/activate
python -m app.worker

# 需要先在 backend/.env 中配置对应 provider 凭据
python -m app.ai_worker

# 小红书发布队列
python -m app.xhs_worker
```

API 文档默认位于 [http://localhost:8000/docs](http://localhost:8000/docs)。

### 前端

```bash
cd frontend
npm install
npm run dev
```

Vite 开发地址通常为 [http://localhost:5173](http://localhost:5173)，开发代理会把 `/api` 转发给 FastAPI。

## 测试与质量检查

```bash
cd backend
pytest
ruff check .

cd ../frontend
npm run type-check
npm run build
```

根目录也提供常用快捷命令：

```bash
make test
make build
make up
make logs
```

## 目录结构

```text
backend/                  FastAPI、数据模型、X/AI 适配器与两个 Worker
frontend/                 Vue 3 管理台与生产 Nginx 镜像
infra/
  grafana/                数据源与 Dashboard provisioning
  prometheus/             Prometheus 抓取配置
  scripts/                备份/恢复与运维脚本
docs/                     架构、API 接入和运维文档
docker-compose.yml        核心服务与可选 monitoring profile
docker-compose.prod.yml   生产环境覆盖配置
docker-compose.external.yml 外部 MySQL/Redis 覆盖配置
```

## API

所有管理 API 位于 `/api/v1`，除登录外均需 `Authorization: Bearer <token>`：

- `POST /auth/login`
- `GET|POST /monitored-users`
- `GET|PATCH|DELETE /monitored-users/{id}`
- `POST /monitored-users/{id}/pause`
- `POST /monitored-users/{id}/resume`
- `POST /monitored-users/{id}/poll`
- `GET /tweets`
- `GET /polling-logs`
- `GET /dashboard/summary`
- `GET /system/metrics`
- `GET|PUT /settings`
- `GET|PATCH /ai/settings`
- `GET|POST|PATCH|DELETE /ai/skills`
- `GET /ai/jobs`、`GET /ai/jobs/{id}`、`POST /ai/jobs/{id}/retry`
- `POST /tweets/{id}/generate`、`PATCH /ai/drafts/{id}`

另外提供 `/api/v1/health/live`、`/api/v1/health/ready` 与容器网络内的 `/metrics`。完整字段以运行时 OpenAPI 文档为准。

## 生产部署建议

- 用长随机值替换示例中的全部密码和密钥。
- 只把 frontend/Nginx 暴露到公网，MySQL、Redis 和 Prometheus 保持内网访问。
- 不要把 AI Worker 指标端口暴露到公网；限制 AI Worker 只能访问批准的 provider 地址。
- 在 Nginx 前配置 HTTPS，或在 `docker-compose.prod.yml` 的反向代理层终止 TLS。
- 为 MySQL 数据卷配置定期备份和异地保留；定期做恢复演练。
- 对磁盘占用设置告警，并根据合规要求配置 Post 和轮询日志保留期。
- 先评估 X API 当前价格、限流和数据使用条款，再扩大账号数量或缩短轮询周期。

## 说明

本项目不包含 X Developer 账号、AI API Key、付费额度或真实 Token。能否读取某个账号以及可读取的历史范围，取决于该账号可见性、你的 X 数据源权限与 X 当时的产品政策；AI 输出可能不准确，发布前必须人工核验。
