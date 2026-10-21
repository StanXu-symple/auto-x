# Backend 微服务与脚本文件对应清单

按 2026-10-20 工作区代码整理，依据 Python 入口、实际调用关系和 Compose 配置；这是源码清单，不代表某台服务器当前已经启动了全部服务。文中路径均相对项目根目录。

当前后端是**一套 Python 代码、多个独立进程、共享 PostgreSQL / Redis**。`backend/` 包含全部后端服务，Compose 中的 `backend` 只是其中的管理 API。`backend/app/services/` 是业务模块目录，不是“一份文件就是一个微服务”。

## 1. 服务与入口总表

以下是微服务 HTTP 模式的对应关系。命令在 `backend/` 下执行，Uvicorn 实际部署还带 `--host 0.0.0.0`。

| 服务 / Compose 名称 | Python 入口文件 | 启动命令 | 默认容器端口与用途 |
| --- | --- | --- | --- |
| 管理 API `backend` | [backend/app/main.py](../backend/app/main.py) | `uvicorn app.main:app --port 8200` | `8200`：业务 API、健康检查、指标 |
| X 轮询 `worker` | [backend/app/worker.py](../backend/app/worker.py) | `python -m app.worker` | `8201`：Prometheus 指标 |
| AI 生成与发布调度 `ai-worker` | [backend/app/ai_worker.py](../backend/app/ai_worker.py) | `python -m app.ai_worker` | `8202`：Prometheus 指标 |
| Skill 管理 `skill-worker` | [backend/app/skill_worker.py](../backend/app/skill_worker.py) | `uvicorn app.skill_worker:app --port 8204` | `8204`：Skill 管理、绑定、解析、校验及指标 |
| QQ 投递 `qq-worker` | [backend/app/qq_worker.py](../backend/app/qq_worker.py) | `python -m app.qq_worker` | `8203`：NoneBot / QQ 回调；`8004`：指标 |
| 小红书业务 `xhs-worker` | [backend/app/xhs_service.py](../backend/app/xhs_service.py) | `uvicorn app.xhs_service:app --port 8006` | `8006`：内部任务 API、指标 |
| 浏览器执行 `camoufox-worker` | [backend/app/camoufox_service.py](../backend/app/camoufox_service.py) | `uvicorn app.camoufox_service:app --port 8007` | `8007`：内部浏览器任务 API、指标 |
| 认证中心 `auth-center` | [backend/app/control_plane/auth.py](../backend/app/control_plane/auth.py) | `uvicorn app.control_plane.auth:app --port 9100` | `9100`：用户认证、服务 Token、JWKS |
| 监控中心 `monitor-center` | [backend/app/control_plane/monitor.py](../backend/app/control_plane/monitor.py) | `uvicorn app.control_plane.monitor:app --port 9102` | `9102`：聚合节点与服务资源 |
| 节点采集 `monitor-agent` | [backend/app/control_plane/agent.py](../backend/app/control_plane/agent.py) | `uvicorn app.control_plane.agent:app --port 9101` | `9101`：本机 Docker 与宿主机资源 |

`worker`、`ai-worker` 的端口只提供指标，不提供业务 REST API。表中是默认容器端口，跨主机发现时登记的是配置的宿主机可达地址和端口。

基础 `docker-compose.yml` 中，小红书使用另一种兼容入口：`python -m app.xhs_worker`，通过 Redis 队列收任务，指标端口 `8005`。叠加微服务配置后才替换为上表的 HTTP 入口；两者是同一业务服务的两种运行方式。

另外两个一次性任务不属于常驻业务服务：

| Compose 名称 | 执行内容 | 文件 |
| --- | --- | --- |
| `migrate` | `alembic upgrade head`，升级公共数据库结构 | `backend/alembic.ini`、`backend/alembic/env.py`、`backend/alembic/versions/*.py` |
| `log-init` | Alpine 容器内修复日志卷权限 | 命令直接定义在 `docker-compose.yml`，没有独立 Python 入口 |

## 2. 每个服务的核心脚本

这里按“入口 → 实际业务 → 调用客户端”列出主链路；公共模块可以同时被多个服务使用，不表示某个进程只能导入表内文件。

### 2.1 管理 API：backend

| 文件 | 作用 |
| --- | --- |
| `backend/app/main.py` | FastAPI 应用、启动/关闭、异常与请求处理、Redis/数据库检查、Nacos 注册、指标 |
| `backend/app/api/router.py` | 汇总管理 API 路由；默认统一挂到 `/api/v1` |
| `backend/app/api/deps.py` | 数据库/Redis 注入、管理员身份和会话校验 |
| `backend/app/api/errors.py` | 公共 API 异常定义 |
| `backend/app/services/auth_center.py` | 调用认证中心、获取 JWKS、验证用户 Token |
| `backend/app/services/system_health.py` | 组合数据库、Worker 心跳、进程与监控中心资源信息 |
| `backend/app/services/runtime_log_client.py` | 获取远程服务日志 |
| `backend/app/services/ai_publish_read.py` | 查询 AI 发布分发记录 |
| `backend/app/services/xhs_publish_results.py` | 将小红书任务权威结果更新到文章/发布记录 |

业务路由全部位于 `backend/app/api/routes/`：

| 业务 / 默认路径（均以 `/api/v1` 开头） | 路由文件 |
| --- | --- |
| 登录、当前用户、改密、登出 `/auth` | `auth.py`；认证操作转交 auth-center |
| 概览 `/dashboard` | `dashboard.py` |
| 监听账号 `/monitored-users` | `monitored_users.py` |
| 推文、截图 `/tweets` | `tweets.py` |
| 轮询记录 `/polling-logs` | `polling_logs.py` |
| 轮询设置 `/settings` | `settings.py` |
| X 官方凭据 `/x-credentials`、数据源 `/x-sources` | `x_credentials.py`、`x_sources.py` |
| AI 设置、Skill、任务、草稿 `/ai` | `ai.py`；设置与 Skill 由 `api/skill_proxy.py` 转发；另注册 `/tweets/{id}/generate` |
| AI 监听任务 `/ai/listen-tasks` | `ai_listen.py` 挂载 HTTP 转发路由，实际逻辑在 `skills/listening_routes.py` |
| AI 数据源 `/ai-data-source` | `ai_data_source.py` |
| 文章管理、素材、手动发布 `/articles` | `articles.py` |
| QQ 机器人、群目标、定时任务与投递 `/qq` | `qq.py` |
| QQ 占位符 `/qq/placeholders` | `qq_placeholders.py` |
| QQ 消息模板 `/qq/message-templates` | `qq_message_templates.py` |
| 小红书状态、登录、上传、发布 `/xhs` | `xhs.py` |
| 小红书完成回调 `/internal/xhs/jobs/{job_id}/completion` | `xhs_results.py`；服务间认证接口 |
| 系统资源与日志 `/system` | `system.py` |
| 健康检查 `/health` | `health.py` |

`/accounts`、`/posts`、`/poll-runs` 是既有路由的兼容别名，分别复用账号、推文、轮询记录文件。

### 2.2 X 轮询：worker

| 文件 | 作用 |
| --- | --- |
| `backend/app/worker.py` | `PollingWorker`：扫描到期账号、并发调度、心跳、优雅退出；同时启动推文截图任务处理 |
| `backend/app/services/poller.py` | 抢占账号、续租、分页拉取、幂等入库、记录轮询结果；创建后续 AI / QQ / 截图任务 |
| `backend/app/services/x_source_client.py` | 在官方 X API 与 twscrape 之间选择数据源 |
| `backend/app/services/x_client.py` | 官方 X API 请求、分页、限流和响应类型 |
| `backend/app/services/twscrape_client.py` | twscrape 数据源适配 |
| `backend/app/services/x_credentials.py` | 官方凭据存储、缓存与加解密；加解密函数也被其他模块复用 |
| `backend/app/services/twscrape_credentials.py` | twscrape 凭据 |
| `backend/app/services/settings_service.py` | 动态轮询设置与有效轮询间隔 |
| `backend/app/services/tweet_types.py` | 推文类型识别与过滤 |
| `backend/app/services/tweet_screenshots.py` | 截图任务入库、领取、重试、保存结果 |
| `backend/app/services/tweet_screenshot_client.py` | 将原帖截图请求发送给 camoufox-worker |

`tweet_screenshots.py` 没有独立常驻服务；截图任务由 `worker.py` 内的协程处理，真正打开浏览器由 camoufox-worker 完成。

### 2.3 AI 生成与发布调度：ai-worker

| 文件 | 作用 |
| --- | --- |
| `backend/app/ai_worker.py` | `AIGenerationWorker`：领取生成任务、调用模型、写入草稿、重试；同时运行历史补生成与发布分发 |
| `backend/app/services/ai_provider.py` | 模型供应商请求、提示词与结构化响应处理 |
| `backend/app/services/ai_data_source.py` | 唯一 AI 数据源配置、加密凭据与缓存；API 也使用 |
| `backend/app/services/ai_defaults.py` | 默认模型、功能点等常量 |
| `backend/app/services/ai_jobs.py` | 通过 HTTP 解析 Skill、构建作者上下文、冻结快照、创建自动/手动生成任务；API 和轮询服务也调用 |
| `backend/app/services/skill_client.py` | 向 Nacos 发现 skill-worker，通过带 Token 的 HTTP 获取绑定和有效 Skill |
| `backend/app/services/ai_listen_backfill.py` | 历史补生成分批扫描与游标；创建请求已移入 skill-worker |
| `backend/app/services/ai_publish.py` | `AIPublishDispatcher`：创建发布分发、重试与对账；调用 XHS 或创建 QQ 投递 |
| `backend/app/services/ai_publish_read.py` | API 查询发布状态 |

`ai_publish.py` 当前不是独立 publish-worker。发布调度运行在 AI Worker 进程内；实际小红书发布和 QQ 发送继续交给对应服务。

### 2.4 QQ 投递：qq-worker

| 文件 | 作用 |
| --- | --- |
| `backend/app/qq_worker.py` | `QQDeliveryWorker` + NoneBot QQ 适配器：机器人同步、Gateway / Webhook、群事件、任务扫描、发送、重试与定时调度 |
| `backend/app/services/qq_notifications.py` | 拼接通知、生成投递记录、Redis 唤醒、模板处理；API / 轮询 / AI 发布也使用 |
| `backend/app/services/qq_schedule.py` | 计算定时任务下次执行时间 |
| `backend/app/services/qq_groups.py` | 记录和更新机器人加入的 QQ 群 |
| `backend/app/services/qq_placeholders.py` | 消息占位符校验、解析与内容渲染；部分管理功能供 API 使用 |

`backend/app/api/routes/qq*.py` 属于管理 API；QQ Worker 自己接收平台事件并消费投递任务。前端 Nginx 将 `/qq/webhook` 转发到 QQ Worker。

### 2.5 小红书业务编排：xhs-worker

| 文件 | 作用 |
| --- | --- |
| `backend/app/xhs_service.py` | HTTP 模式 ASGI 入口，调用共享 `create_app()` |
| `backend/app/xhs_worker.py` | `XiaohongshuWorker`：读取用户凭据、准备文章素材、调用浏览器服务；另保留 Redis 队列启动入口 |
| `backend/app/services/browser_job_api.py` | 与 Camoufox 共用的 HTTP 任务框架：认证、任务提交/查询、并发控制、生命周期与 Nacos 注册 |
| `backend/app/services/browser_job_store.py` | PostgreSQL 中持久化浏览器相关任务状态与结果 |
| `backend/app/services/camoufox_client.py` | XHS → Camoufox 的 HTTP 调用 |
| `backend/app/services/xhs_credentials.py` | 小红书用户凭据读写与加解密；管理 API 也使用 |
| `backend/app/services/xhs_completion_delivery.py` | 小红书任务完成后向 backend 重试发送完成通知 |
| `backend/app/services/xhs_jobs.py` | XHS 任务契约、错误处理、心跳/状态及提交适配；API / AI Worker 也使用 |
| `backend/app/services/xhs_client.py` | backend / AI Worker → XHS 的 HTTP 客户端，同时是其他浏览器客户端的复用基类 |
| `backend/app/services/xhs_limits.py` | 标题、正文等平台限制 |
| `backend/app/services/xhs_verification.py` | 验证图片存取与清理，供多端复用 |

HTTP 模式的调用为 `xhs_service.py → browser_job_api.create_app() → XiaohongshuWorker`。它复用 Worker 对象的执行和心跳方法，不启动该对象的 Redis `BLPOP` 主循环。`xhs_service.py` 和 `xhs_worker.py` 不应计作两个微服务。

完成回调发送 `job_id`，由 backend 从共享数据库读取结果并更新业务记录；不是把网络回调正文直接当作发布权威结果。

### 2.6 浏览器执行：camoufox-worker

| 文件 | 作用 |
| --- | --- |
| `backend/app/camoufox_service.py` | ASGI 入口，调用 `create_app(browser_service=True)` |
| `backend/app/camoufox_worker.py` | `CamoufoxWorker`：执行器生命周期、依赖检查、浏览器状态、心跳与进程资源 |
| `backend/app/camoufox_executor.py` | `CamoufoxExecutor`：执行小红书 `login / post` 和 X 原帖 `x_screenshot` |
| `backend/app/services/browser_job_api.py` | 与 XHS 共用的认证任务 API，按命名空间和 Worker 工厂区分业务 |
| `backend/app/services/browser_job_store.py` | 持久化任务状态，供两个 HTTP 服务共用 |
| `backend/app/services/xhs_browser_pool.py` | 创建、复用、回收浏览器与用户 profile，控制浏览器并发 |
| `backend/app/xhs_cli_compat.py` | 小红书发布页面与 xhs-cli 兼容实现，供浏览器池直接调用；另保留 CLI 入口，不是独立常驻微服务 |
| `backend/app/services/x_tweet_capture.py` | 原帖 DOM 识别、内容核对、截图裁切 |
| `backend/app/services/browser_screenshot_artifacts.py` | 截图产物保存、读取和过期清理；截图客户端也复用 |

这个服务独占实际浏览器运行时和持久化 profile。文件名中的 `xhs` 不代表一定在 XHS 业务进程内执行，例如 `xhs_browser_pool.py` 实际由浏览器服务使用。

### 2.7 认证中心：auth-center

| 文件 | 作用 |
| --- | --- |
| `backend/app/control_plane/auth.py` | 独立 FastAPI 应用：登录、会话、改密、登出、用户/服务 Token、JWKS、限流与健康检查 |
| `backend/app/control_plane/auth_store.py` | PostgreSQL 认证存储：身份、凭据、授权、会话、撤销、签名密钥与轮换 |
| `backend/app/models/admin.py` | 管理员模型 |
| `backend/app/models/service_auth.py` | 服务身份/授权、用户会话及签名材料等认证模型 |
| `backend/app/core/security.py` | 密码哈希等安全工具 |
| `backend/app/services/auth_center.py` | 业务 API 调用认证中心的客户端，不是另一份认证服务 |

服务间 Token 的通用获取与验证在 `control_plane/security.py`；现有授权以 PostgreSQL 为权威，初始化 JSON/私钥文件用于引导，不能用修改拓扑文件替代已有授权的数据变更。

### 2.8 监控中心：monitor-center

| 文件 | 作用 |
| --- | --- |
| `backend/app/control_plane/monitor.py` | 独立 FastAPI 应用与 `MonitorCollector`：发现节点 Agent，聚合资源快照 |
| `backend/app/control_plane/config.py` | 拓扑、运行配置、节点标识的加载与校验 |
| `backend/app/control_plane/contracts.py` | 服务发现、Token、资源实例与快照的数据契约 |
| `backend/app/control_plane/client.py` | backend 调用 monitor-center 的客户端与快照合并逻辑 |

管理台资源链路为 `API → system_health.py → MonitoringClient → monitor-center → monitor-agent`。

### 2.9 节点采集：monitor-agent

| 文件 | 作用 |
| --- | --- |
| `backend/app/control_plane/agent.py` | 独立 FastAPI 应用与 `DockerCollector`：采集本节点匹配拓扑的 Docker 容器，缓存资源快照 |
| `backend/app/control_plane/host_resources.py` | 采集宿主机 CPU、内存等资源 |
| `backend/app/control_plane/resources.py` | 将 Docker 原始统计转换为 CPU / working-set 内存等指标 |
| `backend/app/control_plane/config.py`、`contracts.py` | 共用拓扑、节点选择及响应协议 |

每台 Docker 主机部署一个 Agent。Nacos 注册名按节点拓扑确定，可由配置覆盖，不应把所有节点统一登记成同一个固定节点身份。

### 2.10 Skill 管理：skill-worker

| 文件 | 作用 |
| --- | --- |
| `backend/app/skill_worker.py` | 独立 FastAPI 服务、Nacos 注册、健康检查与内部解析 API |
| `backend/app/skills/routes.py` | 创作设置、Skill CRUD、功能点管理 |
| `backend/app/skills/listening_routes.py` | 监听任务的配置、切换、暂停恢复、队列决策和补生成管理 |
| `backend/app/skills/listening.py` | 选择校验、预览、统计及响应组装 |
| `backend/app/skills/store.py` | 按明确选择的 ID 顺序解析有效 Skill 与磁盘指令 |
| `backend/app/skills/backfill.py` | 补生成请求及配置快照冻结 |
| `backend/app/skills/bootstrap.py` | 内置 Skill 幂等初始化 |
| `backend/app/services/skill_client.py` | backend / worker / ai-worker 共用的 Nacos + HTTP 客户端 |
| `backend/app/api/skill_proxy.py` | 保留既有页面接口的转发层 |
| `backend/app/schemas/skill_service.py` | 内部解析协议 |

共享数据库事务、故障行为和部署顺序见 [Skill Worker](skill-worker.md)。模型执行仍使用入队时的快照。

## 3. 跨服务公共文件

### 配置、存储、协议和可观测性

| 路径 | 作用 / 范围 |
| --- | --- |
| `backend/app/core/config.py` | 应用设置及环境变量/Nacos 配置入口 |
| `backend/app/core/logging.py`、`time.py`、`process_stats.py` | 日志、时间处理、进程及子进程资源采样 |
| `backend/app/db/base.py`、`session.py`、`init_db.py` | ORM 基类、异步会话/引擎、初始化；由各入口按需使用 |
| `backend/app/control_plane/nacos.py` | Nacos 发现、注册、续心跳、重连与退出注销 |
| `backend/app/control_plane/nacos_config.py` | Nacos Config 同步/异步客户端与配置合并 |
| `backend/app/control_plane/security.py` | `TokenClient` / `ServiceVerifier`，服务 Token 获取与认证 |
| `backend/app/services/metrics.py` | 各进程复用的 Prometheus 指标定义；实际数值分别在各进程内维护 |
| `backend/app/services/runtime_logs.py` | 日志路径、读取、流式输出与 SSE 格式 |
| `backend/app/services/article_media.py` | 文章图片、素材与媒体处理；API / AI / QQ / XHS 复用 |
| `backend/app/services/tweet_screenshot_media.py` | 原帖截图的文件路径、元信息与产物处理 |
| `backend/app/__init__.py` | 包和版本信息；其他目录的 `__init__.py` 为包初始化文件 |

### models / schemas 按业务域对应

`models/` 是共享数据库模型，`schemas/` 是请求、响应及服务间协议；这两个目录不对应额外进程。

| 业务域 | `backend/app/models/` | `backend/app/schemas/` | 主要使用方 |
| --- | --- | --- | --- |
| 管理员与服务认证 | `admin.py`、`service_auth.py` | `auth.py`；另有控制面 `contracts.py` | auth-center、backend、鉴权组件 |
| X 账号、推文、轮询 | `monitored_user.py`、`tweet.py`、`polling_log.py`、`setting.py` | `monitored_user.py`、`tweet.py`、`polling.py` | backend、worker；AI / QQ 查询来源数据 |
| X 数据源凭据 | `x_credential.py` | `x_credential.py`、`x_source.py` | backend、worker |
| AI 生成、监听、草稿、文章发布历史 | `ai.py` | `ai.py`、`article.py` | backend、worker、ai-worker、发布结果处理方 |
| AI 数据源 | `ai_data_source.py` | `ai_data_source.py` | backend、ai-worker |
| AI 发布分发 | `ai_publish.py` | `ai_publish.py` | backend、ai-worker、QQ / XHS 结果处理 |
| QQ | `qq.py`、`qq_placeholder.py`、`qq_message_template.py` | `qq.py`、`qq_placeholder.py`、`qq_message_template.py` | backend、qq-worker；worker / AI 生成投递 |
| XHS 凭据 | `xhs_credential.py` | 相关管理参数见路由与 XHS 协议 | backend、xhs-worker |
| 浏览器任务结果 | `browser_job.py` | `xhs_service.py`、`camoufox.py` | XHS / Camoufox HTTP 服务、backend、ai-worker |
| 原帖截图 | `tweet_screenshot.py` | `tweet.py` 中截图响应 | worker、backend、ai-worker |
| 公共分页、概览与健康 | 复用上述模型 | `common.py`、`dashboard.py`、`system.py` | backend 与资源查询 |

`backend/tests/` 是测试代码；`backend/alembic/versions/` 是数据库版本脚本，均不应按文件数量计作微服务。

## 4. Compose、镜像与脚本怎么对应

### Compose 配置层

| 文件 | 对应范围 |
| --- | --- |
| [docker-compose.yml](../docker-compose.yml) | 基础服务定义：数据、迁移、认证、API、Skill、四类任务 Worker、浏览器、前端；监控套件通过 `monitoring` profile 选择 |
| [docker-compose.microservices.yml](../docker-compose.microservices.yml) | 微服务覆盖：XHS 切换 HTTP、服务认证/发现、跨节点网络、加入 monitor-center / monitor-agent |
| `docker-compose.backend.yml` | backend 的组件覆盖；frontend 仍使用基础定义 |
| `docker-compose.ai-worker.yml` | ai-worker 的组件覆盖 |
| `docker-compose.skill-worker.yml` | skill-worker 的组件覆盖 |
| `docker-compose.worker.yml` | worker 的服务认证与控制网络覆盖 |
| `docker-compose.xhs-worker.yml` | xhs-worker 的 HTTP 组件覆盖 |
| `docker-compose.camoufox-worker.yml` | camoufox-worker 的组件覆盖 |
| `docker-compose.auth-center.yml` | auth-center 的组件覆盖 |
| `docker-compose.monitor-center.yml` | monitor-center 的组件覆盖 |
| `docker-compose.monitor-agent.yml` | monitor-agent 的组件覆盖 |
| `docker-compose.prod.yml` | 生产参数、资源与重启策略覆盖，不负责把 XHS 自动切换为 HTTP |
| `docker-compose.external.yml` | 外部 PostgreSQL / Redis 部署的依赖与配置覆盖 |

组件文件通过 `extends` 复用微服务定义，并与基础文件叠加使用。`qq-worker` 没有独立组件 YAML，使用基础定义；这不影响它们作为独立进程部署。

### Dockerfile 与依赖

统一使用 [backend/Dockerfile](../backend/Dockerfile)：

| 构建 target | 使用方 | 特点 |
| --- | --- | --- |
| `core` | backend、worker、ai-worker、skill-worker、qq-worker、auth-center、monitor-center、monitor-agent、migrate | 共用普通后端镜像；默认命令是 API，各容器通过 Compose 覆盖命令 |
| `xhs-worker` | 小红书业务服务 | 继承 core 的兼容镜像，不安装浏览器二进制；HTTP 命令由微服务 YAML 覆盖 |
| `camoufox-worker` | 浏览器服务 | 安装 Firefox 系统依赖与 Camoufox 二进制 |

三个 target 都复制整个 `app/`，所以镜像里看见某份文件，并不代表当前容器会启动对应服务。Python 依赖集中在 `backend/requirements.txt` 和 `backend/pyproject.toml`。

### 启停与运维脚本

项目中没有 `backend/scripts/`；运维脚本实际位于 `infra/scripts/`。

| 脚本 / 命令 | 实际管理范围 |
| --- | --- |
| `start.sh` | 本地管理台检查：迁移、auth-center `9100`、skill-worker `8204`、API `8200`、Vite `5173`；不启动任务消费 Worker |
| `stop.sh` | 转调 `shutdown.sh` |
| `shutdown.sh` | 停止上述本地 frontend / API / skill-worker / auth-center，不负责 Compose 容器 |
| `Makefile` 的 `make up` | 启动基础 Compose；XHS 为 Redis 队列入口 |
| `make microservices-up` | 点名启动 backend、skill-worker、auth-center、monitor-center、monitor-agent 及其 Compose 依赖；不等于启动全部业务 Worker |
| `make external-up` | 当前只点名 auth-center、skill-worker、backend、worker、ai-worker、qq-worker、frontend |
| `make restart` / `make logs` | 当前只列 auth-center、skill-worker、backend、worker、ai-worker、qq-worker、frontend；不覆盖 XHS / Camoufox |
| `infra/scripts/microservices-init.sh` | 初始化拓扑、引导密钥、客户端凭据文件 |
| `infra/scripts/network-groups.sh` | 创建控制面、应用、数据、监控命名网络 |
| `infra/scripts/nacos-config.py` | 发布/同步共享运行配置、监控拓扑及相关引导配置 |
| `infra/scripts/validate-prod-env.sh` | 校验 production / external 环境配置 |
| `infra/scripts/backup.sh`、`restore.sh` | 数据库与 Redis 备份/恢复 |
| `infra/scripts/migrate-monitor-node.py`、`migrate-monitor-grant.sql` | 监控节点身份与数据库授权迁移 |
| `infra/postgres/init/10-monitoring-user.sh` | PostgreSQL 首次初始化监控账号 |

## 5. 主要调用关系

```text
管理台 → backend API
           ├→ auth-center：登录、会话、用户 Token
           ├→ skill-worker HTTP：Skill、绑定、监听配置与校验
           ├→ PostgreSQL / Redis：配置、数据查询、创建任务
           ├→ xhs-worker HTTP → camoufox-worker → 小红书
           └→ monitor-center → monitor-agent → Docker / 宿主机资源

worker → X 官方 API / twscrape → PostgreSQL 推文
   ├→ skill-worker HTTP：入队前解析；ai-worker 领取前校验
   ├→ AI 生成任务 → ai-worker → 模型供应商 → 草稿
   ├→ QQ 投递记录 → qq-worker → QQ 平台
   └→ 截图任务 → camoufox-worker → X 原帖截图

ai-worker 内的 AIPublishDispatcher
   ├→ xhs-worker → camoufox-worker → 小红书
   └→ QQ 投递记录 → qq-worker → QQ 平台

xhs-worker → backend 内部完成回调 → 读取数据库任务结果并更新发布状态
```

Nacos 提供服务发现和配置，auth-center 为内部调用签发服务 Token。X 轮询、AI、QQ 等任务主要通过共享数据库、Redis 唤醒/租约协作，不是所有服务之间都走 HTTP。

定位代码时，先找第 1 节的进程入口，再查对应业务模块；需要调整登录就看 auth-center，需要调整原帖截图执行就看 Camoufox，需要调整自动发布调度就看 ai-worker。
