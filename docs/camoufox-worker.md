# Camoufox Worker

小红书执行链路为 `backend → xhs-worker → camoufox-worker`；X 截图链路为 `worker → camoufox-worker`。小红书业务和数据库凭据读取保留在 xhs-worker；浏览器创建、持久化、复用、回收、CLI 登录态保存与页面操作由 camoufox-worker 独占。任务 API 提供小红书 `login`、`post` 和公开 X 帖子的 `x_screenshot`，未开放任意 Python/JavaScript 执行或远程调试端口。

## 服务与资源

- Nacos 服务名：`xsentinel-camoufox-worker`，默认 HTTP 端口 `8007`。
- Docker 镜像：`CAMOUFOX_WORKER_IMAGE`，Dockerfile target `camoufox-worker`。
- Docker Compose：最大内存 `2g`、`/dev/shm` 为 `512m`、默认一个浏览器和一个执行任务。
- `XHS_WORKER_IMAGE` 标签保留用于兼容安装器，但镜像已不包含浏览器二进制。
- `xhs_home` 旧持久卷只挂载到 camoufox-worker，保留原来的 `users/{admin_id}/browser-profile`。跨主机迁移时需停旧浏览器，再复制该卷；不复制时会依靠数据库 Cookie 创建新配置目录。
- 两个服务使用独立上传目录。业务图片通过 multipart 上传，二维码通过 API 轮询回传，不要求共享文件系统。
- X 截图优先复用池内空闲的持久化浏览器，通过独立临时页面操作，结束后关闭该页面；不修改小红书发布页面或 Cookie。没有可复用会话时创建公共浏览器 profile。两类任务共享浏览器数量和并发上限，默认串行执行。最终 PNG 通过认证 API 下载到 polling worker 的存储目录；Camoufox 节点可部署在另一台主机。

## Nacos 配置中心

沿用 `NACOS_CONFIG_DATA_ID`（默认 `x-sentinel-config.json`）和现有优先级：Nacos 配置优先于环境变量。以下设置在服务启动时读取，修改后重启对应服务生效：

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| CAMOUFOX_SERVICE_NAME | xsentinel-camoufox-worker | 服务发现名 |
| CAMOUFOX_BROWSER_POOL_SIZE | 1 | 保留的浏览器上限 |
| CAMOUFOX_MAX_CONCURRENCY | 1 | 同时执行的浏览器任务数 |
| CAMOUFOX_JOB_TIMEOUT_SECONDS | 290 | 浏览器任务超时 |
| CAMOUFOX_JOB_RESULT_TTL_SECONDS | 600 | 结果保存时间 |

`XHS_JOB_TIMEOUT_SECONDS` 默认 300 秒，应留出浏览器上传和结果回传的时间；延长浏览器超时时，也应相应延长 XHS 任务超时。浏览器同步 SDK 在超时后仍可能正在结束操作，服务会保留它的槽位和上传文件直到退出，避免同时创建额外浏览器。

端口、注册地址、镜像和挂载路径属于节点配置，不从共享 Nacos 文档覆盖：`CAMOUFOX_WORKER_HOST_PORT`、`CAMOUFOX_WORKER_BIND_IP`、`CAMOUFOX_SERVICE_ADVERTISE_IP`、`CAMOUFOX_SERVICE_ADVERTISE_PORT`。跨主机时设置可达的 `NACOS_ADVERTISE_IP`。

## 认证

xhs-worker 持有独立 `xhs-worker.secret`；polling worker 持有独立 `screenshot-worker.secret`，以客户端身份 `screenshot-worker` 调用截图任务。两者均向 `xsentinel-auth-center` 申请 audience 为 `camoufox-worker` 的 JWT。浏览器 API 校验签名、签发者、有效期、audience 和 `browser:execute` scope，使用认证中心 JWKS 支持密钥轮换。

安装器生成新身份，将摘要和授权并入 `SERVICE_AUTH_CLIENTS_JSON`，将秘密保存在 `SERVICE_CLIENT_XHS_WORKER_SECRET` / `SERVICE_CLIENT_SCREENSHOT_WORKER_SECRET` 并同步到各节点的受限文件。既有客户端、已撤销授权不会被恢复。认证中心在启动时把新增身份导入 PostgreSQL，因此已有集群升级需要同步并重启认证中心节点。浏览器服务只拉取公钥，不挂载私钥、客户端秘密或完整 clients.json。

## API

除 `/health/live` 和 `/metrics` 外，接口均要求 `Authorization: Bearer <service-token>`。

| 方法与路径 | 功能 |
| --- | --- |
| GET /v1/status | 状态、浏览器池数量及占用 |
| POST /v1/jobs | multipart 任务：`job` JSON 字段和 `images` 文件列表，返回 202 |
| GET /v1/jobs/{job_id} | 查询 running / succeeded / failed |
| GET /v1/jobs/{job_id}/screenshot | 下载成功截图任务的 image/png；同样要求服务 JWT |
| GET /v1/verification/{admin_id}?version=... | 当前二维码 PNG data URI，版本未变时省略图片 |
| DELETE /v1/browsers/xhs/{admin_id} | 等待空闲后回收浏览器，保留磁盘配置；有任务时返回 409 |

发布 job 示例（Cookie 字段使用现有 X_TOKEN_ENCRYPTION_KEY 加密，示例并非可用凭据）：

```json
{
  "job_id": "0123456789abcdef0123456789abcdef",
  "admin_id": 7,
  "payload": {
    "operation": "post",
    "encrypted_a1": "encrypted-value",
    "encrypted_web_session": "encrypted-value",
    "cookie_version": 1,
    "title": "标题",
    "content": "正文",
    "image_count": 1
  }
}
```

登录任务使用 `operation: login` 和两个 encrypted Cookie 字段，不上传图片。保留原 CLI `login --cookie` 的保存行为；这一步不代表已经验证平台账号是否有效。

截图 job 示例（无需小红书 Cookie，不上传图片）：

```json
{
  "job_id": "0123456789abcdef0123456789abcdef",
  "admin_id": 7,
  "payload": {
    "operation": "x_screenshot",
    "tweet_id": "2105178959327756396",
    "username": "thsottiaux",
    "expected_text": "What’s up dot",
    "expected_media_count": 0
  }
}
```

服务由作者和帖子 ID 构造固定的 HTTPS X 地址，核对页面上的目标帖链接、作者和正文，等待字体和媒体加载后截图完整目标 `article`。不依赖 `css-g5y9jx` 等生成类名；不会截整个页面或把其他回复当成目标帖。X 登录墙、删帖或加载失败会让任务失败并返回原因，公共访问不保证适用于所有帖子。

成功状态中的 `data` 提供 `screenshot_ready`、`size_bytes`、SHA256、尺寸、作者、帖子 ID、原帖地址和截图时间。PNG 独立下载，避免把图片内容放入 Redis。客户端把结果轮询和图片下载固定到提交任务的实例。Camoufox 的临时 PNG 保留时间为任务结果 TTL 加 120 秒；polling worker 保存成功后，长期文件和数据库状态由 worker 管理。完整使用方式见 [X 帖子截图](tweet-screenshots.md)。

相同 job ID 和相同请求返回已有状态，不重复发布；不同内容复用 ID 返回 409。每个账号通过 Redis 锁限制并发，节点本身达到容量返回 429。客户端选定实例后把上传、结果和二维码查询固定到该实例。POST 不自动重试；超时或服务重启时需核对平台结果，不能保证对外发布恰好执行一次。

## 升级顺序

1. 发布包含本改动的 backend、xhs-worker、camoufox-worker 镜像。
2. 在认证中心节点运行更新，让安装器生成并同步新服务身份、让认证中心导入 PostgreSQL。即使浏览器部署在另一台主机，也先完成这一步。
3. 更新原 xhs-worker 和 polling worker 节点。安装器读取旧服务列表后会自动加入 camoufox-worker，保留旧浏览器卷。先拉取全部镜像再停止旧服务，避免两个进程同时打开同一 profile。若已部署远程浏览器，可设置 `KJ_AUTO_X_CAMOUFOX_REMOTE=1`。
4. 检查 Nacos 服务健康、Camoufox 的 `/v1/status` 显示 `installed: true`，验证新帖截图和小红书登录态保存、发布及二维码回传。backend 和 worker 分开部署时，先配置截图共享存储，见截图文档。

实际应用定义位于 `kejilion/apps/auto-x.conf`。新增 `kejilion/sh/auto-x.sh` 为薄入口：

```bash
bash auto-x.sh update
# 只部署独立浏览器节点（认证中心需已就绪）
bash auto-x.sh install camoufox-worker
# XHS 使用已经部署的远端浏览器节点
KJ_AUTO_X_CAMOUFOX_REMOTE=1 bash auto-x.sh update xhs-worker,monitor-agent
# 新帖监听使用已经部署的远端浏览器节点
KJ_AUTO_X_CAMOUFOX_REMOTE=1 bash auto-x.sh update worker,monitor-agent
```

`KJ_AUTO_X_CAMOUFOX_REMOTE=1` 需在以后按服务列表更新时继续提供，或在节点的部署环境中持久设置。`KJ_AUTO_X_TWEET_SCREENSHOT_ENABLED=false` 可在安装器中关闭自动截图并避免仅因 worker 自动加入浏览器；Nacos 已有该配置时仍以 Nacos 为准，应同步关闭配置中心的 `TWEET_SCREENSHOT_ENABLED`。不同节点均需使用相同 Nacos 配置、Redis 和 Cookie 加密密钥。浏览器 API 的 8007 端口必须对调用节点可达。
