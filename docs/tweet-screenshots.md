# X 帖子自动截图

新帖监听继续使用现有 X 数据源。帖子成功入库时创建唯一截图任务，由 polling worker 内部处理器扫描执行；轮询任务无需等待浏览器。处理器经 Nacos 发现 `xsentinel-camoufox-worker`，向认证中心取得服务 JWT，然后提交 `x_screenshot` 任务、查询结果并下载 PNG。

## 截图范围和校验

截图包含帖子自己的完整 `article`：作者、正文、媒体及该帖卡片内容。服务使用帖子 ID 对应的时间链接定位目标，再校验作者、原帖地址和预期正文；等待字体及媒体加载后截图。没有依赖 `css-g5y9jx r-16y2uox r-1wbh5a2 r-1ny4l3l` 等会变化的类名。引用、回复或相邻帖子不会因位置接近而被当成目标帖。

同时支持旧版带 `User-Name`、`tweetText`、`time` 的页面与新版公开页面。新版通过作者主页链接及对应 `@账号`、独立 `dir="auto"` 正文、指向原帖的时间提示链接识别，不把浏览数或回复操作链接当作时间。嵌套 article、引用卡片和正文内的状态链接不能提供原帖身份；正文提取保留提及、表情与换行，去除格式化脚本和外链展示文字。媒体计数兼容没有 `tweetPhoto` 标记的原帖图片，头像、外链预览和引用图片不计入原帖媒体；截图范围内的引用图片仍须加载完成。

截图复用 Camoufox 服务管理的持久化浏览器，在独立临时页面执行，和小红书任务共享浏览器池与并发上限。默认只有一个浏览器任务同时执行，截图不会启动独立的无人管理浏览器。公共 X 页面遇到登录墙、删帖、作者不匹配、正文不匹配或媒体加载失败时会记录错误并重试，不能保证每条帖子都能成功访问。

## 数据和接口

数据库的截图记录关联已存储的帖子，保留任务状态、尝试次数、重试时间、失败原因、PNG 相对路径、SHA256、尺寸和截图时间。帖子 ID 唯一，重复入库不会重复创建任务。失败按配置重试，每次等待 `TWEET_SCREENSHOT_RETRY_SECONDS × 本次尝试次数`，达到上限后保留失败状态；浏览器忙时保持排队，不扣除截图尝试次数。进程重启后，持久任务由处理器继续扫描。原生转发没有可独立校验的转发卡片，记录为失败并跳过；原创、回复和引用帖可按自身帖子 ID 截图。

自动捕获适用于升级后新入库的帖子，包括首次监听时补入的历史帖子。已到期的任务优先处理发布时间较新的帖子，减少历史补采对新帖截图的延迟。帖子列表和详情响应包含 `screenshot` 元数据及成功图片的 `image_url`。需要给已有帖子截图或手动重试时，使用管理员登录令牌调用接口（`id` 使用 X 帖子 ID，即响应的 `tweet_id`，如 `2105178959327756396`，不使用数据库整数主键 `id`）：

| 方法与路径 | 用途 |
| --- | --- |
| POST /api/v1/tweets/{id}/screenshot | 排队或重试该帖截图 |
| GET /api/v1/tweets/{id}/screenshot | 下载已有 PNG |

接口需要本站管理员认证，POST 返回 202 和截图任务元数据；已有运行中或成功任务不会重复捕获，失败任务会重置尝试次数后重新排队。尚未成功或文件不存在时 GET 返回 404。浏览器任务 API 则使用 `screenshot-worker` 客户端身份，audience 为 `camoufox-worker`，scope 为 `browser:execute`；调用方 secret 仅挂载到 polling worker。

## 页面操作

帖子列表增加截图状态列。打开帖子详情可查看截图时间、失败原因和成功 PNG 预览，并下载原图。尚未截图的帖子提供“生成截图”，失败的帖子提供“重试截图”；排队或执行中可刷新详情查看状态。预览和下载经本站认证接口读取，图片不使用公开静态地址。

文章管理会自动关联来源帖子的成功截图，在查看、编辑和发布预览中显示，无需下载后重新上传。已有文章也会自动显示；如果文章先生成、截图后完成，刷新文章列表即可看到。手动上传的图片继续保留，原帖截图独立关联，保存正文不会丢失。

发布 QQ 或小红书时，服务会将原帖截图保存为文章媒体副本，并按“原帖截图、手动上传图片”的顺序发送。副本位于当前管理员的文章上传目录，兼容现有发布服务与远程图片传输；重复发布复用同一副本，删除文章会清理副本而保留帖子原图。小红书的 18 张图片上限包含原帖截图。未成功或文件缺失的截图不会作为可用媒体。

## 配置

运行参数在 Nacos `x-sentinel-config.json` 管理，修改后重启 worker 生效：

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| TWEET_SCREENSHOT_ENABLED | true | 新帖自动截图及后台处理开关 |
| TWEET_SCREENSHOT_MAX_ATTEMPTS | 3 | 最大自动尝试次数 |
| TWEET_SCREENSHOT_RETRY_SECONDS | 30 | 失败后重试等待时间 |
| TWEET_SCREENSHOT_SCAN_INTERVAL_SECONDS | 5 | 截图任务扫描间隔 |

浏览器继续使用 `CAMOUFOX_BROWSER_POOL_SIZE=1`、`CAMOUFOX_MAX_CONCURRENCY=1`，Compose 最大内存 `2g`。截图排队等待空闲容量；提高并发时应同时评估浏览器内存。

节点本地配置：

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| TWEET_SCREENSHOT_DIR | /var/lib/xsentinel/tweet-screenshots | 容器内 PNG 根目录 |
| TWEET_SCREENSHOT_VOLUME | tweet_screenshots | Compose 存储来源：命名卷或主机绝对路径 |
| TWEET_SCREENSHOT_CLIENT_SECRET_FILE | /run/xsentinel/screenshot-worker.secret（Compose） | worker 调用认证中心的 secret 文件 |

这些文件路径和挂载参数不从共享 Nacos 文档覆盖。Compose 为 worker 挂载可写截图存储、为 backend 挂载同一来源的只读存储。Camoufox 使用自己的临时 artifact 目录，通过 API 传输 PNG，不需要访问最终存储卷。

### 同一主机

默认命名卷即可。新镜像已创建由服务 UID `10001` 所有的截图目录，首次创建卷时继承目录权限。备份时需同时备份 PostgreSQL 截图记录和 `tweet_screenshots` 卷。

### backend 与 worker 在不同主机

Docker 同名卷不会跨主机共享。先在两台主机挂载同一个共享文件系统，再在各节点 `.env` 中配置，例如：

```dotenv
TWEET_SCREENSHOT_VOLUME=/mnt/xsentinel-shared/tweet-screenshots
TWEET_SCREENSHOT_DIR=/var/lib/xsentinel/tweet-screenshots
```

共享目录须允许 worker 的 UID `10001` 写入、backend 的 UID `10001` 读取。重建两个服务后验证 worker 保存的 PNG 可由 backend 下载。只给远程 Camoufox 配置本地路径不能让 backend 获得文件。

## 升级和验证

1. 发布新的 backend、worker、camoufox-worker 镜像，并执行数据库迁移。
2. 先更新认证中心节点，安装器将新 `screenshot-worker` 身份和 secret 同步到 Nacos，重启认证中心以导入新身份。已有身份的撤销授权不会恢复。
3. 更新 polling worker、backend 和浏览器节点。worker 默认自动加入本地浏览器；已有远端浏览器时使用 `KJ_AUTO_X_CAMOUFOX_REMOTE=1 bash auto-x.sh update worker,monitor-agent`。
4. 检查 Nacos 中浏览器健康和认证授权，确认截图存储权限。监听一个新帖，查看截图状态，再用登录态调用下载接口；也可对已有帖 POST 排队验证。

如需关闭，修改 Nacos 的 `TWEET_SCREENSHOT_ENABLED=false` 并重启 worker。安装器选择服务时可同时提供 `KJ_AUTO_X_TWEET_SCREENSHOT_ENABLED=false`，避免仅因截图自动加入本地浏览器。
