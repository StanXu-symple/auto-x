# X Sentinel 架构说明

## 目标

X Sentinel 是一个面向单团队/单管理员的 X（Twitter）公开账号监控与 AI 草稿系统。管理员可以在运行时新增、暂停、恢复或删除监听账号，并为每个账号单独调整轮询周期。系统将拉取到的公开 Post 持久化到 MySQL，使用 Redis 完成分布式互斥、Worker 心跳和短期缓存，并可通过独立 AI Worker 调用统一的 OpenAI 兼容数据源生成待人工审阅的草稿。

X 数据源可选择官方 API 或管理员显式启用的实验性 twscrape 模式；后者仅用于专用账号的 Cookie 会话，存在失效与账号限制风险。

## 组件图

```text
Browser -> Nginx / Vue -> FastAPI ---------> MySQL 8
                            |                  ^  ^
                            v                  |  |
                          Redis 7 <-> Polling Worker -> X API v2
                            ^                  |
                            |                  |
                            +---- AI Worker ---+
                                     |
                                     +---------> 统一 AI 数据源

Prometheus -> Nginx + FastAPI + both Workers + exporters -> Grafana
```

## 为什么 API 与 Worker 分离

- API 只处理管理请求和查询，不会因为某个 X 请求缓慢而阻塞管理台。
- Worker 可以独立扩容；Redis 锁保证多个 Worker 不会同时轮询同一个账号。
- 轮询配置存在 MySQL 中，Worker 每个调度 tick 都读取已到期记录，因此新增账号或调整周期无需重启。
- Worker 心跳存在 Redis 中，API 可从管理台明确显示“服务在线但 Worker 已停止”的异常状态。
- AI Worker 独立领取生成任务，因此慢模型请求、超时或 provider 限流不会占用 API 和轮询 Worker；AI provider 密钥也只需注入该进程。

## 动态轮询流程

1. Worker 周期性查询 `is_active = true` 且 `next_poll_at <= NOW()` 的账号。
2. 对账号获取带过期时间的 Redis 分布式锁。
3. 首次运行时通过用户名解析 X user id；后续直接复用。
4. 请求 `/2/users/{id}/tweets`，并带上 `since_id`；一轮达到页数上限时把分页 token 与本轮高水位持久化，下次从断点继续。
5. 只有所有分页读取完成后才推进 `last_tweet_id`，避免页数上限导致中间 Post 永久遗漏。
6. 在数据库事务中分块写入，并只对 X Post id 冲突做幂等处理。
7. 记录本次轮询结果、耗时、抓取数、入库数和错误。
8. 按数据库中最新的账号/全局周期计算 `next_poll_at`。如果 X 返回 429，则优先服从 `x-rate-limit-reset` 并暂停共享 Token 的后续请求。
9. 释放 Redis 锁，并持续刷新 Worker 心跳。

这是一种“至少执行一次 + 幂等写入”的模型：即使进程在网络请求后意外退出，下一次执行仍不会重复存储 Post。

## AI 草稿流程

1. 管理员启用 AI，并选择 provider、模型、默认 Skill、重试次数和输出限制；还可以按“监听用户 × AI 功能点”绑定专属 Skill；AI 默认关闭。
2. 新 Post 入库后可按配置自动创建任务，也可由管理员对指定 Post 手动创建。幂等键避免同一自动任务重复入队。
3. AI Worker 分批领取到期任务，使用数据库 claim token 与 Redis 租约避免多个实例重复提交。
4. 创建任务时按“手动覆盖 → 用户功能绑定 → 全局默认”解析 Skill，并读取该作者持久画像和最近 20 条动态；功能点、Skill 版本、解析来源与作者上下文写入任务审计快照。
5. Worker 动态读取唯一 AI 数据源，把功能点与管理员维护的 Skill 作为可信编辑指令，把原 Post 和作者近期动态放入明确标记的不可信数据区，再调用 OpenAI Responses 兼容端点。
6. 返回内容必须通过结构化输出校验；成功结果写入可编辑草稿，并保守更新作者身份、近期关注、动态关联、长期主题、证据与置信度；失败任务按配置退避重试，达到上限后保留错误状态供审计。
7. 草稿始终需要人工核验和后续操作，系统不包含自动发布到 X 的步骤。

## 数据模型

### `monitored_users`

保存监听目标、X user id、是否启用、单账号轮询周期、回复/转发过滤条件、最后一次成功时间、最后 Post id、下一次计划时间和最近错误。

### `tweets`

保存 Post 文本、发布时间、公开互动指标、实体/附件/引用关系及必要的原始响应。X Post id 有唯一索引。

### `polling_logs`

保存每次执行的状态、开始/结束时间、耗时、读取数、新增数、HTTP 状态、错误类型和错误详情，供审计与故障定位。

### `system_settings`

保存全局默认轮询周期及可在线修改的运行参数。单账号显式配置优先于全局默认值。

### `ai_settings` / `ai_skills`

`ai_settings` 保存是否启用、默认 Skill 和任务限制；`ai_skills` 保存可版本化的编辑说明与可选结构化输出约束。唯一的模型、Base URL 与加密 API Key 保存在 `ai_data_sources`。

### `ai_features` / `ai_user_skill_bindings` / `ai_user_profiles`

`ai_features` 定义 AI 功能点及其基础提示词；`ai_user_skill_bindings` 持久化监听用户、功能点与 Skill 的有序多对多关系；`ai_user_profiles` 保存从有证据的历史动态中持续优化的作者画像。绑定只影响其对应用户和功能点，停用 Skill 会同步停用相关绑定。

### `ai_generation_jobs` / `ai_drafts`

任务表保存来源 Post、幂等键、状态、重试与必要的脱敏审计快照；成功输出进入草稿表，供管理员继续编辑。删除来源 Post 时相关任务与草稿级联删除。

## 可靠性设计

- **去重：** X Post id 唯一约束。
- **并发控制：** Worker 全局并发上限 + 每账号 Redis 锁 + MySQL `poll_generation` fencing，失去租约的旧任务不能提交业务状态。
- **AI 并发控制：** 独立并发/批量上限、数据库 claim token 和 Redis 租约共同避免重复生成；任务结果仍以数据库状态为准。
- **故障恢复：** 网络错误采用指数退避；锁和心跳均有 TTL，进程崩溃后会自动恢复。
- **限流处理：** 读取 X 限流响应头，避免在窗口重置前反复请求。
- **健康检查：** liveness 只验证进程；readiness 同时验证 MySQL 与 Redis。
- **优雅退出：** API、轮询 Worker 和 AI Worker 响应终止信号，停止接收新任务并完成/取消在途工作。
- **可观测性：** 结构化日志、轮询审计表、Prometheus 指标、Grafana 面板。

## 安全边界

- 管理接口需要 JWT；初始管理员凭据、JWT 密钥与凭据加密主密钥来自环境变量，X/AI 访问凭据由管理台加密保存。
- AI API Key 加密写入 `ai_data_sources`，Redis 仅缓存密文，API 不返回明文；任务快照只记录数据源名称和版本。
- 原 Post、Skill 指令和必要上下文会发送到所选 AI provider。部署者需要自行确认数据处理协议、保留策略、地区与版权要求，并优先使用 HTTPS 和受控 Bridge。
- 管理员配置的 provider URL 会收到对应 Authorization 凭据；API 与 AI Worker 以 `AI_ALLOWED_PROVIDER_HOSTS` 做 hostname allowlist，并要求携带凭据的非本机目标使用 HTTPS。生产部署还应在网络层重复限制出站目标，降低误配置或管理员账号失陷导致的密钥外泄风险。
- 原 Post 永远按不可信数据处理，内置 prompt guard 会隔离其中的指令/链接；这只能降低 prompt injection 风险，不能替代人工审阅与最小权限网络策略。
- AI 结果只保存为草稿，不应被视为事实或直接自动发布；管理员发布前需核验来源、引用、敏感信息和平台合规性。
- `.env` 不进入版本控制；生产环境应以权限为 `0600` 的宿主机环境文件注入，或由部署平台在启动时注入环境变量。当前版本不直接读取 Docker secret 的 `*_FILE` 约定。
- 默认只读取公开账号与公开 Post，不保存 X 登录 Cookie。
- 建议仅通过 HTTPS 暴露 Nginx，并限制数据库、Redis、Prometheus 的公网访问。
- 生产环境需替换所有示例密码，并定期轮换 X Token 和 JWT 密钥。

## 扩展方向

当前模型适合轮询式监控。账号数量或实时性要求明显提高后，可以保持现有数据层和管理台，新增 X Filtered Stream 适配器；通知（Webhook、邮件、企业微信）也可以消费新入库事件，而无需修改轮询核心。
