# X 数据源接入说明

## 运行时数据源选择

管理台“X 数据源”提供两个互斥模式：

- `official_api`：保留原有 App-only Bearer Token 管理和官方 X API v2 读取。
- `twscrape`：使用专用 X 账号的 `auth_token`、`ct0` Cookie，通过 twscrape 读取网页 GraphQL。

当前选择保存在 MySQL `app_settings.x_source`。Worker 的每次读取都会重新获取该值并路由到对应客户端；切换模式会清除全局认证/限流闸门，并把活跃监听账号重新排队，无需重启服务。两类凭据分别加密保存，可同时保留，切换模式不会删除另一套凭据。

## 使用的官方接口

X Sentinel 采用 X API v2 的服务端 Bearer Token：

- `GET /2/users/by/username/{username}`：将用户名解析为稳定的 user id。
- `GET /2/users/{id}/tweets`：读取该用户发布的 Post。

时间线请求会使用 `since_id` 增量拉取，并可通过 `exclude=replies,retweets` 控制是否包含回复和转发。请求会显式获取 `created_at`、`public_metrics`、`entities`、`attachments`、`referenced_tweets` 等字段。

官方资料：

- [User Posts 时间线](https://docs.x.com/x-api/posts/timelines/introduction)
- [时间线集成与分页](https://docs.x.com/x-api/posts/timelines/integrate)
- [X API 限流](https://docs.x.com/x-api/fundamentals/rate-limits)
- [使用量与计费](https://docs.x.com/x-api/fundamentals/post-cap)

## 凭据

在 X Developer Console 创建 App 并生成 Bearer Token。登录管理台的“X 数据源”，选择官方 X API，再选择 Developer Console 或 API Key/Secret 换取方式，按引导获取后粘贴保存。Token 使用 `X_TOKEN_ENCRYPTION_KEY` 加密持久化到 MySQL，Redis 仅临时缓存密文；不要把真实 Token 写入代码、镜像或提交记录。

官方认证资料：

- [Developer Console 与 Keys and tokens](https://docs.x.com/fundamentals/developer-portal)
- [App-only Bearer Token](https://docs.x.com/fundamentals/authentication/oauth-2-0/application-only)
- [OAuth 2.0 PKCE 用户授权](https://docs.x.com/fundamentals/authentication/oauth-2-0/user-access-token)

### twscrape Cookies

建议创建只用于读取的专用 X 账号，在已登录浏览器的开发者工具中复制 `https://x.com` 下的 `auth_token` 和 `ct0`。管理台保存时把二者作为一个 JSON 凭据包加密到 MySQL，Redis 只缓存密文。twscrape 本身需要 SQLite 账号池，Worker 只在系统临时目录创建权限 `0600` 的运行时数据库，凭据轮换或 Worker 退出时删除。

twscrape 属于非官方方案，不保证长期可用。Cookie 代表登录会话，不得使用个人主账号、不得分享或写入日志；若账号出现异常登录、验证码或限制，应立即停用该模式并在 X 中撤销会话。使用前应自行确认 [twscrape 项目说明](https://github.com/vladkens/twscrape) 与 [X 服务条款](https://x.com/en/tos)。

## 限流与成本

限流和计费是两个独立约束。X 返回的以下响应头会被系统记录和处理：

- `x-rate-limit-limit`
- `x-rate-limit-remaining`
- `x-rate-limit-reset`

遇到 HTTP 429 时，Worker 会在 Redis 中设置当前数据源的全局限流闸门，并将相关任务推迟到限流窗口重置之后。官方模式会采用响应头中的重置时间；twscrape 账号池不可用或被限流时采用保守的延迟重试。管理员仍应在 X Developer Console 查看官方端点价格、账户余额和支出上限；接口价格会变化，不应硬编码在系统内。

一个粗略的请求量估算公式：

```text
每天请求数 ≈ 活跃账号数 × 86400 / 平均轮询秒数
```

例如 100 个账号每 5 分钟轮询一次，理论上每天约 28,800 次时间线请求，另加首次用户名解析请求。上线前应结合当前 X 套餐和限流评估成本。

## 数据范围

User Posts 时间线有官方定义的可回溯上限，轮询间隔过长可能导致高频账号在两次执行之间产生的内容超过单次/总回溯窗口。系统会分页读取本轮新增内容，但无法突破 X API 自身的数据范围。

## 合规建议

- 只监控业务上确有需要的公开账号。
- 按 X Developer Agreement、当地隐私法规和内部数据保留制度使用数据。
- 当前删除监听账号会级联删除该账号已采集的 Post 与轮询记录；操作前前端会明确确认。若内部审计要求保留历史，应先导出或改为软删除策略。
- 若要对外展示或长期保存内容，应再次核对 X 对内容再分发、删除同步和缓存时长的最新要求。
