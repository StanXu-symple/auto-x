# X Sentinel

一个可自托管的 X（Twitter）账号定时监听与 AI 草稿平台。前端使用 Vue 3 + Element Plus，后端使用 FastAPI，PostgreSQL 保存监听配置、历史内容和生成审计，Redis 提供分布式锁、限流闸门和 Worker 心跳。支持运行中动态增加账号、分别调整轮询周期，并内置统一 OpenAI 兼容数据源、AI 草稿生成、服务器监控、Prometheus 指标和 Grafana 面板。

## 已包含的功能

- 动态新增、编辑、暂停、恢复和删除监听账号
- 全局默认轮询周期 + 单账号独立轮询周期，修改后无需重启
- 立即轮询、增量抓取、分页补齐、Post 唯一键去重
- 可选择是否采集回复与转发
- X API 全局限流闸门、429 退避、分页断点续传与下次执行时间调整
- Post 内容流、关键词搜索、账号筛选和分页
- 内置 AI Skill、手动/自动生成队列与可编辑草稿，统一使用一个 OpenAI 兼容账号
- AI 任务幂等、重试、请求/响应审计和独立 Worker，不会自动发布到 X
- 腾讯 QQ 官方机器人适配，支持多机器人、多群目标和按监听账号分流
- 每次轮询的状态、耗时、读取数、新增数和错误审计
- CPU、内存、磁盘、负载、进程运行时间监控
- PostgreSQL、Redis、API、Worker 心跳状态监控
- 管理员密码登录、JWT 鉴权和环境变量密钥管理
- Prometheus 指标与预置 Grafana Dashboard
- Docker Compose 一键启动、健康检查、持久卷和备份脚本

## 架构

```text
Vue 3 / Nginx -> FastAPI -----------> PostgreSQL
                    |                  ^  ^
                    v                  |  |
                  Redis <-> Polling Worker -> 官方 X API / twscrape
                    ^                  |
                    +---- AI Worker ---+----> 统一 AI 数据源
                    +---- QQ Worker -------> NoneBot2 -> 腾讯 QQ 开放平台

Prometheus -> Nginx + API + Workers + exporters -> Grafana

```

当服务部署在多台 Docker 主机时，可启用可选的控制平面。控制平面提供基于 Nacos 的服务注册发现、短时服务认证中心、监控中心，以及每台 Docker 主机上的一个资源监控 Agent。它通过 Docker cgroup 采集所有容器的 CPU 和 working-set 内存，让 PostgreSQL、Redis、API 和各类 Worker 使用统一的资源指标。先执行 `make microservices-init`，再配置 `NACOS_SERVER_ADDR`、`NACOS_USERNAME`、`NACOS_PASSWORD`；只有在自动探测地址不适用时才需要设置 `NACOS_ADVERTISE_IP`。如需将主机加入命名网络组，可配置 `NETWORK_GROUPS` 并执行 `make network-groups`。

小红书 Worker 支持作为独立的 HTTP 微服务运行。启用该模式后，API 会通过 Nacos 发现 `xsentinel-xhs-worker`，并使用 `httpx` 与 Pydantic 契约进行服务调用；将 `XHS_TRANSPORT` 设置为 `http` 即可。默认的 `redis` 传输模式继续兼容单机部署下的原始队列 Worker。

详细设计见 [架构说明](docs/ARCHITECTURE.md)，X 官方接口见 [X API 接入说明](docs/X_API.md)。

## 快速启动

### 1. 准备配置

需要 Docker 24+ 与 Docker Compose v2.24+。

```bash
install -m 600 .env.example .env
```

至少修改这些值：

```dotenv
POSTGRES_PASSWORD=replace-with-a-strong-password
POSTGRES_EXPORTER_PASSWORD=replace-with-an-exporter-password
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
首次登录后可从右上角头像菜单进入“修改密码”；新密码将使用 Argon2 哈希后持久化到 PostgreSQL，后续启动不会被 `.env` 中的初始密码覆盖。

### 3. 启动监控套件（可选）

```bash
docker compose --profile monitoring up -d
```

- X Sentinel：[http://localhost:8080](http://localhost:8080)
- Prometheus：[http://127.0.0.1:9090](http://127.0.0.1:9090)
- Grafana：[http://127.0.0.1:3000](http://127.0.0.1:3000)

Grafana 使用 `.env` 中的 `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`，数据源、告警规则和 X Sentinel Dashboard 会自动配置。预置规则默认只在 Prometheus/Grafana 中显示状态；若要向邮件、企业微信等渠道推送，还需按所在环境接入 Alertmanager 或 Grafana Contact Point。

## 按需部署服务

`docker-compose.yml` 提供基础设施和单机服务；微服务覆盖文件按组件拆分：

```text
docker-compose.backend.yml
docker-compose.xhs-worker.yml
docker-compose.auth-center.yml
docker-compose.monitor-center.yml
docker-compose.monitor-agent.yml
```

使用 `apps/auto-x.conf` 安装时，可选择要部署的服务，例如：

```text
backend,frontend,xhs-worker
```

选择结果保存在 `/home/docker/auto-x/.auto-x-services`，后续更新会沿用该列表。输入 `all` 部署全部服务。PostgreSQL 和 Redis 作为本地基础设施按依赖启动；如果使用外部数据服务，请改用外部配置覆盖文件。

手动部署时也可以直接组合 Compose 文件：

```bash
docker compose -f docker-compose.yml \
  -f docker-compose.backend.yml \
  -f docker-compose.xhs-worker.yml \
  up -d backend frontend xhs-worker
```

## Nacos 与微服务控制平面

先准备可访问的 Nacos 2.x 服务。每台主机的 `.env` 只需要保留 Nacos
连接信息、服务注册身份、宿主机端口/网络和本地密钥文件路径等启动引导项：

```dotenv
NACOS_SERVER_ADDR=http://127.0.0.1:8848
NACOS_NAMESPACE=public
NACOS_GROUP=X_SENTINEL
NACOS_USERNAME=nacos
NACOS_PASSWORD=replace-with-your-nacos-password
NACOS_CONFIG_ENABLED=true
NACOS_CONFIG_DATA_ID=x-sentinel-config.json
NACOS_CONFIG_GROUP=X_SENTINEL
NACOS_CONFIG_REQUIRED=true
```

首次安装或从旧版 `.env` 迁移时，将可集中管理的运行配置同步到 Nacos：

```bash
make nacos-config
```

Data ID 内容既可以使用 `.env` 风格的扁平键，也可以按服务分组。例如：

```json
{
  "postgres": {
    "host": "10.211.55.30",
    "port": 5432,
    "database": "xsentinel",
    "user": "xsentinel",
    "password": "replace-with-database-password"
  },
  "redis": {
    "host": "10.211.55.30",
    "port": 6379,
    "db": 0,
    "password": "replace-with-redis-password"
  },
  "worker": {"max_concurrency": 5, "scan_interval_seconds": 2},
  "ai_worker": {"max_concurrency": 3},
  "qq_worker": {"max_concurrency": 5},
  "xhs": {"browser_pool_size": 1, "job_timeout_seconds": 300}
}
```

同步工具采用“远端优先”：Nacos 中已有值不会被本地 `.env` 覆盖，只会补充新版本新增的配置项。PostgreSQL/Redis 的地址、端口、库名、账号、密码与连接池参数，JWT 签名密钥、X 凭据加密密钥，以及轮询、AI、QQ、小红书 Worker 的运行参数都可以放在该 JSON 配置中。为保证 Compose 自带的数据容器与远端配置一致，工具只把最终的 PostgreSQL/Redis 引导值原子回写到本地 `.env`，并将文件收紧为所有者可读（可写时为 `0600`）；应用进程仍以 Nacos Config 为权威来源。

Nacos 连接凭据、`NACOS_ADVERTISE_IP`、服务名/监听端口、Docker 网络与宿主机端口、挂载的密钥文件路径、初始管理员密码和 provider API Key 不进入共享配置。它们分别属于启动引导、单实例身份或独立秘密，继续由本机部署配置和管理台管理。由于 Nacos 文档包含数据库/Redis 密码及共享加密密钥，应为 namespace 配置最小权限账号，并在跨主机通信时使用受保护的内网或 TLS 入口。

应用在进程启动时读取一次 Nacos Config；修改远端配置后重启受影响的 API/Worker。`make prod-up`、`make external-up` 和对应的生产迁移命令会在 `NACOS_CONFIG_REQUIRED=true` 时自动同步配置、校验远端生产密钥，并刷新 Compose 所需的 PostgreSQL/Redis 本地引导缓存；也可单独执行 `make nacos-config`。required 模式下读取失败或 Data ID 不存在会阻止实例启动；设为 `false` 时会回退到本地环境变量。

`NACOS_ADVERTISE_IP` 是服务注册到 Nacos 后供其他服务访问的地址。不配置时程序会自动探测非回环 IPv4；跨 Docker 主机部署时建议显式填写本机 LAN/VPC 地址，并确保对应服务端口已放行。它不是 Nacos 服务端地址，不能填写 `NACOS_SERVER_ADDR`。

初始化认证密钥、客户端授权和服务拓扑：

```bash
make microservices-init
make network-groups
make microservices-up
```

控制平面服务包括 `auth-center`、`monitor-center` 和每台 Docker 主机一个 `monitor-agent`。监控 Agent 通过 Docker Engine API 采集容器 CPU 与 working-set 内存；监控中心通过 Nacos 发现 Agent 和其他服务。服务之间不再写死 URL；服务注册身份和每台主机的监控拓扑仍保留为本机引导配置。

## AI 草稿生成

AI 功能默认关闭；不配置 AI 数据源也可以正常使用账号监听。启用前进入管理台“AI 数据源”，填写配置名称、OpenAI 兼容 Base URL、模型和 API Key，保存并执行连通测试；再到“AI 创作”选择默认 Skill、重试次数和输出限制并启用自动生成。独立 `ai-worker` 领取任务、动态读取当前唯一数据源，并把结果保存为可继续编辑的草稿。系统不会自动发布到 X。

AI API Key 使用服务端凭据加密密钥持久化到 PostgreSQL，Redis 只缓存密文，API 不返回明文，任务快照和日志也不会包含 Key。Worker 每次执行前重新读取数据源，并把数据源名称与版本写入审计快照。携带凭据的远程地址必须使用 HTTPS，本机兼容网关可使用 HTTP。

“AI 创作 → 用户策略与画像”支持按“监听用户 × AI 功能点”绑定一个或多个 Skill。每次新建 AI 会话按“手动覆盖 → 用户功能绑定 → 全局默认”解析 Skill，并把解析结果和版本写入任务快照。上下文同时包含该作者已有画像及最近 20 条动态；成功生成后会更新“他是谁、近期关注、动态关联、长期主题、证据和置信度”，供下一次创作继续使用。原帖与近期动态始终作为不可信引用数据处理，不能覆盖系统、功能点或 Skill 指令。


## QQ 群推送

进入“QQ 推送”添加腾讯 QQ 开放平台 AppID/AppSecret，再创建一个或多个群目标。AppSecret 使用 `X_TOKEN_ENCRYPTION_KEY` 加密写入 PostgreSQL；新推文与 QQ 投递 Outbox 在同一事务提交，独立 `qq-worker` 通过 Redis 唤醒并使用 NoneBot2 `nonebot-adapter-qq` 发送，Redis 故障时会扫描 PostgreSQL 恢复。投递状态、平台错误和重试过程可在管理台追踪。

腾讯 QQ 开放平台自 2025-04-21 起不再提供通用主动消息能力。只有实际获得对应群主动消息权限的机器人才能完成自动投递；AppID/AppSecret 验证通过不代表该权限已开通。

机器人入群由群主或管理员在 QQ 客户端发起并完成平台审批。平台推送 `GROUP_ADD_ROBOT` 事件后，`qq-worker` 会记录该机器人对应的群 OpenID，并尝试回复入群提示；提示消息发送失败不影响群记录保存。

添加群目标时，先选择发送机器人，再从“选择已加入的群”下拉框选择群；OpenID 自动填入，群名称可作为本地备注修改。列表按 AppID 隔离，来自 PostgreSQL 保存的入群和群消息事件，收到退群事件后移除，重复或乱序事件不会恢复旧状态。它是已观察到的群列表，不是 QQ 全量历史群列表；此前已加入的群可在群里 @ 一次机器人后刷新，也可切换“手动填写”。入群事件不包含群名，未命名的群显示 OpenID。

事件接入：每个机器人的 QQ 开放平台后台需配置公网 HTTPS 回调地址 `https://你的域名/qq/webhook`，完成平台验证，并订阅机器人入群、退群和群 @ 消息事件。前端 Nginx 已将该路径转发到 `qq-worker:8003`，NoneBot QQ 适配器按 AppID 和 AppSecret 验签。`qq-worker` 使用 QQ Gateway WebSocket 保持已启用机器人在线，并同时提供 Webhook；新增、停用或修改机器人的接入配置约 15 秒内同步。若 QQ 后台仍显示离线，先确认 `qq-worker` 正常运行、开放平台已开启对应 Gateway 事件权限，以及容器可以访问 `api.sgroup.qq.com`。部署更新时执行 `alembic upgrade head`（新增 `0010_qq_joined_groups`），再更新后端、QQ Worker 和前端；本地开发的同一路径代理到 `localhost:8003`。

## 使用外部 PostgreSQL 与 Redis

项目包含专用覆盖配置，示例已填写 `10.211.55.30:5432` 和 `10.211.55.30:6537`，Redis 密码留空：

```bash
install -m 600 .env.external.example .env.external
# 编辑 .env.external：填写专用数据库账号、JWT/管理员密码和 X Token 加密密钥
make external-config ENV_FILE=.env.external
make external-up ENV_FILE=.env.external
```

外部模式不会启动本地 PostgreSQL/Redis，仍会先自动执行 Alembic 迁移。先由数据库管理员创建拥有 `xsentinel` 数据库的专用账号，再填写 `POSTGRES_USER` / `POSTGRES_PASSWORD`；应用无需超级用户权限。外部数据服务的备份、恢复和主机级监控应由它们所在服务器负责。

## 第一次使用

1. 登录管理台，进入“X 数据源”。
2. 选择“官方 X API”或实验性的“twscrape”，按页面引导保存并测试对应凭据，然后点击“启用此数据源”。
3. 进入“监听账号”，输入不带 `@` 的 X 用户名。
4. 设置该账号的轮询周期，以及是否包含回复/转发。
5. 保存后 Worker 会在下一调度 tick 自动执行；也可以点击“立即轮询”。
6. 在“内容流”查看新增 Post，在“轮询记录”查看执行与错误，在“系统监控”确认服务状态。
7. 如需 AI 草稿，先配置 provider 凭据，再到 AI 设置启用功能、选择 Skill；可手动生成，或在小规模验证后开启自动生成。

Worker 会在每次用户名解析和时间线读取前从 PostgreSQL 获取当前数据源，因此切换后不需要重启；系统会清除旧认证闸门并立即重新排队活跃账号。官方 X API 当前采用按量计费，轮询周期越短、账号越多，请求成本越高。twscrape 不消耗官方 API Credits，但属于非官方网页接口，可能随 X 页面更新失效，并存在验证码、Cookie 失效及账号受限风险。

## 配置项

配置完整示例位于 [.env.example](.env.example)。常用项：

| 配置 | 作用 | 默认值 |
| --- | --- | --- |
| `APP_PORT` | 管理台对外端口 | `8080` |
| `ADMIN_USERNAME` | 管理员用户名 | `admin` |
| `ADMIN_PASSWORD` | 管理员密码 | 无安全默认值，必须修改 |
| `JWT_SECRET_KEY` | JWT 签名密钥 | 必须修改 |
| `X_TOKEN_ENCRYPTION_KEY` | 加密 PostgreSQL 中 X Token 的服务端密钥，至少 32 位 | 空 |
| `X_TOKEN_CACHE_TTL_SECONDS` | Redis 密文缓存有效期 | `300` |
| `DEFAULT_POLL_INTERVAL_SECONDS` | 新账号默认轮询周期 | `300` |
| `WORKER_SCAN_INTERVAL_SECONDS` | Worker 检查到期任务的间隔 | `2` |
| `WORKER_MAX_CONCURRENCY` | 单 Worker 最大并发账号数 | `5` |
| `AI_WORKER_MAX_CONCURRENCY` | 单 AI Worker 最大并发任务数 | `3` |
| `AI_WORKER_BATCH_SIZE` | 每轮领取 AI 任务上限 | `50` |
| `POSTGRES_*` | PostgreSQL 数据库与凭据 | 见示例文件 |
| `REDIS_PASSWORD` | Redis 密码；外部实例无密码时可留空 | 本地模式必须修改 |
| `NACOS_SERVER_ADDR` | Nacos 服务端地址 | 空（启用微服务时必填） |
| `NACOS_USERNAME` / `NACOS_PASSWORD` | Nacos 登录凭据 | 空 |
| `NACOS_CONFIG_DATA_ID` / `NACOS_CONFIG_GROUP` | 共享运行配置的 Data ID / Group | `x-sentinel-config.json` / `X_SENTINEL` |
| `NACOS_CONFIG_REQUIRED` | Nacos Config 不可用时是否拒绝启动 | `false`（生产建议 `true`） |
| `NACOS_ADVERTISE_IP` | 注册到 Nacos 的可达 IP；留空自动探测 | 自动探测 |
| `XHS_TRANSPORT` | 小红书调用模式：`redis` 或 `http` | `redis` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `TZ` | 容器显示时区 | `Asia/Shanghai` |

所有持久时间以 UTC 保存，前端按浏览器本地时区显示。

## 常用运维命令

```bash
# 查看状态
docker compose ps

# 跟踪 API 与 Worker 日志
docker compose logs -f backend worker ai-worker qq-worker

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

先准备可从宿主机访问的 PostgreSQL 与 Redis。Compose 默认数据容器只在内部网络 `expose`，不会映射宿主机端口；如果在宿主机直接运行 Python，请使用单独安装的数据服务或自行添加仅绑定 `127.0.0.1` 的开发端口映射。然后：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
install -m 600 .env.example .env
# 把 .env 中的 POSTGRES_HOST/PORT、REDIS_HOST/PORT 改为宿主机可达地址，并填写密码
alembic upgrade head
uvicorn app.main:app --reload
```

原生进程模式同样支持 Nacos Config：在 `backend/.env` 设置引导项后，可从仓库根目录执行 `make nacos-config ENV_FILE=backend/.env` 完成首次同步，再运行 `start.sh` 或各 Python 进程。

另开终端启动轮询 Worker；需要调试 AI 流程时再开一个终端启动 AI Worker：

```bash
cd backend
source .venv/bin/activate
python -m app.worker

# 需要先在 backend/.env 中配置对应 provider 凭据
python -m app.ai_worker

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
backend/                  FastAPI、数据模型、X/AI/QQ 适配器与独立 Worker
frontend/                 Vue 3 管理台与生产 Nginx 镜像
infra/
  grafana/                数据源与 Dashboard provisioning
  prometheus/             Prometheus 抓取配置
  scripts/                备份/恢复与运维脚本
docs/                     架构、API 接入和运维文档
docker-compose.yml        核心服务与可选 monitoring profile
docker-compose.prod.yml   生产环境覆盖配置
docker-compose.external.yml 外部 PostgreSQL/Redis 覆盖配置
docker-compose.backend.yml / docker-compose.*.yml 按服务拆分的微服务配置
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
- 只把 frontend/Nginx 暴露到公网，PostgreSQL、Redis 和 Prometheus 保持内网访问。
- 不要把 AI Worker 指标端口暴露到公网；限制 AI Worker 只能访问批准的 provider 地址。
- 在 Nginx 前配置 HTTPS，或在 `docker-compose.prod.yml` 的反向代理层终止 TLS。
- 为 PostgreSQL 数据卷配置定期备份和异地保留；定期做恢复演练。
- 对磁盘占用设置告警，并根据合规要求配置 Post 和轮询日志保留期。
- 先评估 X API 当前价格、限流和数据使用条款，再扩大账号数量或缩短轮询周期。

## 说明

本项目不包含 X Developer 账号、AI API Key、付费额度或真实 Token。能否读取某个账号以及可读取的历史范围，取决于该账号可见性、你的 X 数据源权限与 X 当时的产品政策；AI 输出可能不准确，发布前必须人工核验。
