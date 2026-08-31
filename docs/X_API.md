# X API 接入说明

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

在 X Developer Console 创建 Project/App，生成 Bearer Token，然后写入部署环境的 `X_BEARER_TOKEN`。不要把真实 Token 写入代码、镜像或提交记录。

## 限流与成本

限流和计费是两个独立约束。X 返回的以下响应头会被系统记录和处理：

- `x-rate-limit-limit`
- `x-rate-limit-remaining`
- `x-rate-limit-reset`

遇到 HTTP 429 时，Worker 会在 Redis 中设置共享 Bearer Token 的全局限流闸门，并将相关任务推迟到限流窗口重置之后，避免其他账号继续撞击同一端点。管理员仍应在 X Developer Console 查看当前端点价格、账户余额和支出上限；接口价格会变化，不应硬编码在系统内。

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
