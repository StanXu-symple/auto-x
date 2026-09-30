# Auto-X 一键更新傻瓜式文档

本文用于更新已经按[安装文档](Auto-X一键安装傻瓜式安装文档.md)部署的 Auto-X。先确认本次改动涉及哪些服务，再只在对应节点执行 `kejilion.sh` 的“更新”。以下是 2026-09-29 在 tc-2 更新 frontend、backend、worker 的实测流程。

## 一、确认发布版本和更新范围

1. 等待 Auto-X `main` 分支的 `Publish Auto-X images` 构建成功。
2. 记录该次构建对应的 **完整提交 SHA**，更新时设置 `KJ_AUTO_X_IMAGE_TAG=sha-<完整提交 SHA>`。不要使用 `latest` 猜测版本。
3. 在服务器执行 `cat /home/docker/auto-x/.auto-x-services`，记下原有服务清单。

本次目标提交为 `bef0dbce4c3603bc16c885b72f776c5ceef52a6d`，[Actions 构建](https://github.com/StanXu-symple/auto-x/actions/runs/36581730206)已成功。tc-2 更新 `frontend`、`backend`、`worker`；安装器会自动加入依赖的 `auth-center` 和本机必装的 `monitor-agent`。tc-2 的 `ai-worker`、`qq-worker` 不在本次选择中，继续运行原镜像。tc-1 的服务不在本次更新范围内。

## 二、在 tc-2 更新前备份

登录 tc-2，确认数据库当前版本、服务清单和容器健康状态：

```bash
ssh tc-2
cat /home/docker/auto-x/.auto-x-services
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker exec x-sentinel-postgres-1 sh -ec \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql --tuples-only --no-align --username="$POSTGRES_USER" --host=127.0.0.1 --port=5432 --dbname="$POSTGRES_DB" --command="SELECT version_num FROM alembic_version LIMIT 1"'
```

本次更新前数据库版本是 `0024_service_auth_bootstrap`。在执行会修改数据库的更新前，先保存备份和原服务清单：

```bash
umask 077
backup_stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p /home/docker/auto-x/backups
cp /home/docker/auto-x/.auto-x-services "/home/docker/auto-x/backups/services-${backup_stamp}.txt"
docker exec x-sentinel-postgres-1 sh -ec \
  'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump --username="$POSTGRES_USER" --host=127.0.0.1 --port=5432 --format=custom "$POSTGRES_DB"' \
  > "/home/docker/auto-x/backups/pre-update-${backup_stamp}.dump"
test -s "/home/docker/auto-x/backups/pre-update-${backup_stamp}.dump"
sha256sum "/home/docker/auto-x/backups/pre-update-${backup_stamp}.dump"
```

若备份失败，先查明原因，停止更新。本次实际备份文件为 `/home/docker/auto-x/backups/pre-0025-0026-20260929.dump`，权限 `600`。

## 三、通过 kejilion.sh 更新

在 tc-2 执行：

```bash
cd ~
KJ_AUTO_X_IMAGE_TAG=sha-bef0dbce4c3603bc16c885b72f776c5ceef52a6d \
AUTO_X_SERVICES=backend,frontend,worker \
bash kejilion.sh app auto-x
```

运行环境菜单输入 `3`（default），进入 Auto-X 应用菜单后输入 `2`（更新）。这是两个不同的菜单。等待安装器显示“Auto-X 已从 GitHub 项目更新完成”和“操作完成”，再退出菜单。

安装器会刷新应用列表、将 Auto-X 源码更新到 `main`、从镜像仓库拉取指定 SHA 的镜像，然后用 Docker Compose 启动选定服务。Compose 的 `migrate` 服务执行 `alembic upgrade head`；`backend`、`worker` 和 `auth-center` 等服务会等待迁移成功。本次 `0025_initial_sync_days` 和 `0026_qq_target_history` **由安装器流程自动执行**，不需要另开终端手工运行迁移命令。若 `migrate` 退出码不为 0 或服务未就绪，先保留现场、查看日志，再决定如何修复，不要直接继续下一台服务器。

## 四、恢复原服务清单

**部分服务更新会改写 `/home/docker/auto-x/.auto-x-services`。** 即使未选中的服务仍在运行，下次直接选“更新”时也会漏掉它们。更新成功后，立即把第一步备份的原清单恢复：

```bash
cat "/home/docker/auto-x/backups/services-${backup_stamp}.txt"
cp "/home/docker/auto-x/backups/services-${backup_stamp}.txt" \
   /home/docker/auto-x/.auto-x-services
cat /home/docker/auto-x/.auto-x-services
```

`backup_stamp` 仅在同一 shell 会话中有效；若重新登录，请将命令中的文件名替换为实际备份文件。本次 tc-2 的原清单为：

```text
backend,worker,ai-worker,qq-worker,auth-center,monitor-agent,frontend
```

本次已将原清单恢复。恢复这个选择文件不会重启或升级任何容器。

## 五、验收

在 tc-2 执行：

```bash
docker ps -a --filter name=x-sentinel-migrate --format '{{.Names}}|{{.Status}}|{{.Image}}'
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker exec x-sentinel-postgres-1 sh -ec \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql --tuples-only --no-align --username="$POSTGRES_USER" --host=127.0.0.1 --port=5432 --dbname="$POSTGRES_DB" --command="SELECT version_num FROM alembic_version LIMIT 1"'
curl -fsS -o /dev/null -w 'frontend=%{http_code}\n' http://127.0.0.1:8080/
curl -fsS -o /dev/null -w 'backend=%{http_code}\n' http://127.0.0.1:8200/api/v1/health/ready
curl -fsS -o /dev/null -w 'auth=%{http_code}\n' http://127.0.0.1:9100/health/ready
cat /home/docker/auto-x/.auto-x-services
```

本次实测：`migrate` 为 `Exited (0)`，数据库版本为 `0026_qq_target_history`；tc-2 的新镜像 `frontend`、`backend`、`worker` 以及依赖的 `auth-center`、`monitor-agent` 均为 healthy，三个 HTTP 检查均返回 200。未选的 `ai-worker`、`qq-worker` 也保持 healthy，仍运行先前的镜像。tc-1 的 xhs-worker、monitor-center、monitor-agent 均保持 healthy。

## 六、仅更新 frontend

仅当发布提交只修改前端、无需数据库迁移时，使用安装器的 `KJ_AUTO_X_UPDATE_FRONTEND_ONLY=1` 模式。先确认对应完整 SHA 的 GitHub Actions 镜像构建成功，并确认服务器上的安装器已包含此模式。此模式通过同一个 `kejilion.sh app auto-x` 菜单选择 `2` 更新，但只拉取并重建 frontend；不会运行 `migrate`，也不会更新 backend、auth-center、monitor-agent、worker 或原服务清单。它将前端镜像版本单独记录为 `.env` 中的 `FRONTEND_IMAGE_TAG`，后端继续使用原 `IMAGE_TAG`。

在 tc-2 执行：

```bash
ssh tc-2
git -C /root/apps rev-parse --short HEAD
cat /home/docker/auto-x/.auto-x-services
docker ps --filter name=x-sentinel-frontend-1 --format '{{.Names}}|{{.Status}}|{{.Image}}'
cd ~
KJ_AUTO_X_UPDATE_FRONTEND_ONLY=1 \
KJ_AUTO_X_IMAGE_TAG=sha-9c5d9be0ca1eab31737acb9a8168d388f7c9608b \
AUTO_X_SERVICES=frontend \
bash kejilion.sh app auto-x
```

运行环境输入 `3`（default），应用菜单输入 `2`（更新）。若镜像拉取失败，安装器保持旧 frontend 运行；若新 frontend 启动失败，安装器尝试恢复旧镜像。发生错误时先检查日志和容器状态，再决定是否重试。

验收：

```bash
docker ps --filter name=x-sentinel-frontend-1 --format '{{.Names}}|{{.Status}}|{{.Image}}'
docker ps --filter name=x-sentinel-backend-1 --format '{{.Names}}|{{.Status}}|{{.Image}}'
docker ps -a --filter name=x-sentinel-migrate-1 --format '{{.Names}}|{{.Status}}|{{.Image}}'
curl -fsS -o /dev/null -w 'frontend=%{http_code}\n' http://127.0.0.1:8080/
cat /home/docker/auto-x/.auto-x-services
```

frontend 应运行目标 SHA 镜像且为 healthy；backend 的镜像和运行时间、migrate 的退出时间、原服务清单应保持不变。完整更新时仍按前文使用全局 `KJ_AUTO_X_IMAGE_TAG`；安装器会将已有的 `FRONTEND_IMAGE_TAG` 同步到该版本，避免单独更新后的前端长期停留在旧标签。

2026-09-30 实测：tc-2 的 frontend 镜像为 `sha-9c5d9be0ca1eab31737acb9a8168d388f7c9608b` 且 healthy；frontend 页面和 backend 就绪接口均返回 HTTP 200。backend、auth-center、monitor-agent、worker、ai-worker、qq-worker 与 migrate 容器 ID 均未变化；数据库仍为 `0026_qq_target_history`，原 7 项服务清单保持不变。

## 七、占位符配置与 QQ 历史范围修复更新记录

2026-09-30，提交 `c79690f7af00a0e1583d8f93ec8a0c8b1d34d7c3` 已通过 [GitHub Actions 镜像构建](https://github.com/StanXu-symple/auto-x/actions/runs/36657802652)。该版本包含占位符表迁移 `0027_qq_placeholders`，也包含此前 `30e1d25` 的 qq-worker 历史范围复核修复，因此 tc-2 选择 `backend,frontend,worker,qq-worker` 更新；安装器自动加入 `auth-center,monitor-agent`。更新前先按第二节备份数据库和 `/home/docker/auto-x/.auto-x-services`，确认旧迁移版本为 `0026_qq_target_history`。

```bash
ssh tc-2
cd ~
KJ_AUTO_X_IMAGE_TAG=sha-c79690f7af00a0e1583d8f93ec8a0c8b1d34d7c3 \
AUTO_X_SERVICES=backend,frontend,worker,qq-worker \
bash kejilion.sh app auto-x
# 运行环境输入 3（default），Auto-X 应用菜单输入 2（更新）
```

安装器的 Compose `migrate` 服务自动执行 `alembic upgrade head`，不需要手工执行迁移。更新完成后，按第四节将保存的 7 项服务清单恢复到 `.auto-x-services`，再核对容器、数据库版本和默认数据。本次备份为 `/home/docker/auto-x/backups/pre-0027-20260930.dump`，原服务清单备份为 `/home/docker/auto-x/backups/services-pre-0027-20260930.txt`。

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker exec x-sentinel-postgres-1 sh -ec \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql --tuples-only --no-align --username="$POSTGRES_USER" --host=127.0.0.1 --port=5432 --dbname="$POSTGRES_DB" --command="SELECT version_num FROM alembic_version LIMIT 1" --command="SELECT placeholder, source_field FROM qq_placeholders ORDER BY placeholder"'
curl -fsS -o /dev/null -w 'frontend=%{http_code}\n' http://127.0.0.1:8080/
curl -fsS -o /dev/null -w 'backend=%{http_code}\n' http://127.0.0.1:8200/api/v1/health/ready
cat /home/docker/auto-x/.auto-x-services
```

本次实测：`migrate` 为 `Exited (0)`，数据库版本为 `0027_qq_placeholders`；`{author}`、`{username}`、`{text}`、`{url}`、`{posted_at}`、`{title}` 六行默认映射均已写入。tc-2 的目标容器均为 healthy，frontend 与 backend 返回 HTTP 200；原 7 项服务清单已恢复。tc-1 服务保持 healthy。

## 八、占位符删除与分页更新记录

2026-09-30，目标提交 `ca734f6ac3ce26adb924d1a8a4eacd05c49e77f1` 的 [GitHub Actions 镜像构建](https://github.com/StanXu-symple/auto-x/actions/runs/36661218234)成功。尽管此次主要更新 `frontend` 和 `backend`，目标提交还包含迁移 `0028_qq_placeholder_seed_once`，用于在 `app_settings` 中记录默认占位符已经初始化，避免用户删除默认占位符后被重新插入。**每次更新前都应核对当前数据库版本与目标提交中的 `backend/alembic/versions`；不能仅凭服务更新范围判断无需迁移。**

按第二节先备份数据库和原服务清单。本次 tc-2 升级前数据库为 `0027_qq_placeholders`，备份为 `/home/docker/auto-x/backups/pre-update-20260930-104848.dump`，原服务清单备份为 `/home/docker/auto-x/backups/services-20260930-104848.txt`。然后在 tc-2 执行：

```bash
cd ~
KJ_AUTO_X_IMAGE_TAG=sha-ca734f6ac3ce26adb924d1a8a4eacd05c49e77f1 \
AUTO_X_SERVICES=backend,frontend \
bash kejilion.sh app auto-x
# 运行环境输入 3（default），Auto-X 应用菜单输入 2（更新）
```

安装器自动加入 `auth-center`、`monitor-agent`，并由 Compose 的 `migrate` 服务执行 `alembic upgrade head`。更新成功后按第四节恢复原有 7 项服务清单，再按第五节验收。此次实测 `migrate` 为 `Exited (0)`，数据库为 `0028_qq_placeholder_seed_once`，`app_settings.qq_placeholder_defaults_seeded` 的值为 `{"initialized": true}`。`frontend`、`backend`、`auth-center`、`monitor-agent` 使用目标 SHA 且健康；`worker`、`qq-worker`、`ai-worker` 保持原镜像且健康；frontend、backend、auth 三个 HTTP 检查均返回 200。

## 九、内容类型监听与 QQ 消息模板更新记录

2026-09-30，提交 `92d7a4e` 和 `aca20ba` 已合入 `main`；目标提交 `aca20bae3cb363614740e86504fb9f80f8e99b84` 的 [GitHub Actions 镜像构建](https://github.com/StanXu-symple/auto-x/actions/runs/36664538268)成功。此次需在 tc-2 更新 `backend`、X 采集 `worker`、`qq-worker`、`frontend`，并执行迁移 `0029_tweet_type_target_mode`、`0030_qq_message_templates`。其中 `0029` 为历史内容补齐原创、回复、转推类型，且为现有群目标设置默认监听模式 `all`；`0030` 新建 QQ 消息模板表。

先按第二节备份数据库与原服务清单。本次升级前数据库为 `0028_qq_placeholder_seed_once`，备份文件为 `/home/docker/auto-x/backups/pre-update-20260930-112918.dump`，服务清单备份为 `/home/docker/auto-x/backups/services-20260930-112918.txt`。然后执行：

```bash
ssh tc-2
cd ~
KJ_AUTO_X_IMAGE_TAG=sha-aca20bae3cb363614740e86504fb9f80f8e99b84 \
AUTO_X_SERVICES=backend,worker,qq-worker,frontend \
bash kejilion.sh app auto-x
# 运行环境输入 3（default），Auto-X 应用菜单输入 2（更新）
```

安装器自动加入 `auth-center`、`monitor-agent`，Compose 的 `migrate` 服务自动执行 `alembic upgrade head`。更新成功后按第四节恢复原有 7 项服务清单，并按第五节核对健康状态。本次实测 `migrate` 为 `Exited (0)`，数据库版本为 `0030_qq_message_templates`，`qq_message_templates` 表存在；1064 条历史内容分类为原创 431、回复 478、转推 155，现有 1 个群目标的监听模式为 `all`。目标服务及自动加入的依赖服务均使用目标 SHA 且健康，`ai-worker` 保持原镜像且健康；frontend、backend、auth 三个 HTTP 检查均返回 200。

## 十、轮询记录删除与 AI 创作界面更新记录

2026-09-30，`dev` 的 `c6570ef`、`22f998c`、`261a237` 三项提交一并发布到 `main`，目标提交为 `261a237f54d0e57297dcdc89ff6e189921f49d6e`，[GitHub Actions 镜像构建](https://github.com/StanXu-symple/auto-x/actions/runs/36671709563)成功。`c6570ef` 修改轮询记录后端接口和 X 采集时的记录更新，因此这次选择 `backend,worker,frontend`；后两项分别更新 AI 数据源 API Key 占位符和 Skills 表格。目标提交没有新增 Alembic 迁移，但完整更新仍会运行 `migrate` 检查。

更新前按第二节备份。此次 tc-2 升级前数据库为 `0030_qq_message_templates`，备份文件为 `/home/docker/auto-x/backups/pre-update-20260930-130503.dump`，原服务清单备份为 `/home/docker/auto-x/backups/services-20260930-130503.txt`。在 tc-2 执行：

```bash
cd ~
KJ_AUTO_X_IMAGE_TAG=sha-261a237f54d0e57297dcdc89ff6e189921f49d6e \
AUTO_X_SERVICES=backend,worker,frontend \
bash kejilion.sh app auto-x
# 运行环境输入 3（default），Auto-X 应用菜单输入 2（更新）
```

安装器自动加入 `auth-center`、`monitor-agent`。更新成功后按第四节恢复原有 7 项服务清单。本次实测 `migrate` 为 `Exited (0)`，数据库版本仍为 `0030_qq_message_templates`；`backend`、`worker`、`frontend`、`auth-center`、`monitor-agent` 使用目标 SHA 且健康，`qq-worker`、`ai-worker` 保持原镜像且健康；frontend、backend、auth 三个 HTTP 检查均返回 200。

## 十一、账号状态与小红书 Cookie 提示更新记录

2026-09-30，`dev` 中尚未合并的 `4f40a66`、`90560ba`、`e655917`、`352a58b` 已快进合并到 `main`。其中三项仅改前端：监听账号状态中文显示、小红书已保存 Cookie 的密码占位提示，以及 Vue 文件格式整理；`90560ba` 只补充安装文档的跨节点 8006 端口检查。目标提交 `352a58bf9c596c252efb0d2b6d04bec47195654d` 的 [GitHub Actions 镜像构建](https://github.com/StanXu-symple/auto-x/actions/runs/36674957707)成功。无后端改动和数据库迁移，因此仅更新 tc-2 的 `frontend`。

更新前先保存 `/home/docker/auto-x/.env` 与 `.auto-x-services`；此次备份分别为 `/home/docker/auto-x/backups/env-pre-frontend-20260930-135122` 和 `/home/docker/auto-x/backups/services-pre-frontend-20260930-135122.txt`。然后按第六节的 frontend 专项模式执行：

```bash
ssh tc-2
cd ~
KJ_AUTO_X_UPDATE_FRONTEND_ONLY=1 \
KJ_AUTO_X_IMAGE_TAG=sha-352a58bf9c596c252efb0d2b6d04bec47195654d \
AUTO_X_SERVICES=frontend \
bash kejilion.sh app auto-x
# 运行环境输入 3（default），Auto-X 应用菜单输入 2（更新）
```

本次实测 frontend 镜像为目标 SHA、状态为 healthy，页面 HTTP 200；backend 容器和 migrate 容器的 ID 均未变化，数据库仍为 `0030_qq_message_templates`，原有 7 项服务清单保持不变。

## 十二、Camoufox 独立服务迁移

目标 Auto-X 提交为 `936774a32ec78c7a73987d65b5ea8968f032c80f`。新服务 `camoufox-worker` 与 `xhs-worker` 一起部署在 tc-1，认证中心、backend 和 frontend 在 tc-2 更新；本次无新增 Alembic 迁移。`camoufox-worker` 监听 `8007`，Compose 内存上限为 `2g`，沿用 tc-1 的 `x-sentinel_xhs_home` 卷。跨节点调用和认证说明见 [Camoufox Worker](camoufox-worker.md)。

更新前按第二节备份 tc-2 数据库，以及两台的 `.env`、`.auto-x-services`、`data/control-plane`；tc-1 另备份 `x-sentinel_xhs_home` 卷。核对两台的 `kejilion.sh`、`auto-x.sh` 和 `/root/apps/auto-x.conf` 是目标发布版本。下载 `kejilion.sh` 时不要用 `gh.kejilion.pro/raw.githubusercontent.com/...` 作为精确文件来源：该代理曾改写脚本中的 GitHub URL，使 SHA-256 与 Git 发布提交不符。应从 `https://gh.kejilion.pro/github.com/StanXu-symple/sh.git` 获取 Git 提交并校验文件哈希。

先在 tc-2 执行：

```bash
cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_IMAGE_TAG=sha-936774a32ec78c7a73987d65b5ea8968f032c80f \
AUTO_X_SERVICES=backend,auth-center,frontend \
bash kejilion.sh app auto-x
```

该入口执行应用菜单的 `2. 更新`。非交互入口跳过运行环境提问，tc-2 使用 default。确认新 auth-center 已导入 `xhs-worker` 服务身份，backend、frontend、auth-center 健康，数据库版本仍为 `0030_qq_message_templates`。将 tc-2 的 `.auto-x-services` 恢复为更新前的完整清单。

tc-1 必须明确指定 CN 镜像源，包括新增的 Camoufox 镜像：

```bash
cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.nju.edu.cn \
KJ_AUTO_X_IMAGE_TAG=sha-936774a32ec78c7a73987d65b5ea8968f032c80f \
KJ_AUTO_X_PULL_TIMEOUT_SECONDS=900 \
AUTO_X_SERVICES=xhs-worker,camoufox-worker,monitor-center,monitor-agent \
bash kejilion.sh app auto-x
```

使用应用定义提交 `46e0577d069b331dc563c6b8d1ee9b216fc69769` 或后续版本。它会将先前误写入 `.env` 的默认 Camoufox 代理改为 `ghcr.nju.edu.cn`，但保留自定义镜像地址。更新前检查 `.env` 中 `CAMOUFOX_WORKER_IMAGE=ghcr.nju.edu.cn/stanxu-symple/auto-x-camoufox-worker`；安装器拉完镜像后才停止旧 xhs-worker，避免同时打开浏览器配置。更新成功后恢复 tc-1 完整服务清单 `xhs-worker,camoufox-worker,monitor-center,monitor-agent`。

验收检查 tc-1 的 `camoufox-worker` 和 `xhs-worker` 健康、`8007` 正常监听、Camoufox 容器 `HostConfig.Memory=2147483648`、Nacos 中 `xsentinel-camoufox-worker` 注册健康，以及 tc-2 的 `/api/v1/xhs/status`。浏览器服务的 `/v1/status` 受认证中心 JWT 保护，未带令牌返回 401 是正常结果。最后用测试账号核对保存登录态、二维码回传与发布流程；测试发布可能产生平台内容，应使用指定测试账号。

2026-09-30 实测进度：tc-2 按上述版本更新成功，`auth-center` 已导入 `xhs-worker` 身份，backend、frontend、auth-center、monitor-agent 健康，数据库仍为 `0030_qq_message_templates`，原服务清单已恢复。tc-1 首次使用非交互入口时遗漏 `KJ_AUTO_X_IMAGE_REGISTRY`，Camoufox 镜像因此误用 default 代理；该次在停止旧服务前取消。修复安装器后再次确认 `.env` 和拉取日志均为 `ghcr.nju.edu.cn`。南京大学镜像源的 Camoufox 镜像约 1.10 GB，最大单层约 923 MB；第二次拉取在单层 566.2 MB 停滞，按用户要求中断。tc-1 旧 xhs-worker、monitor-center、monitor-agent 仍健康，`camoufox-worker` 尚未安装。继续时需要从本节 tc-1 命令重试，并在完成后执行上述验收；不能将 tc-2 的成功视为整个双节点迁移完成。

### 下载进度长时间不变时的排查

`ctr -n moby content active` 也会列出失败下载留下的临时层；必须同时检查下载进程和日志，不能仅凭 SIZE 判断任务仍在运行：

```bash
ssh tc-1
ps -p "$(cat /root/auto-x-camoufox-background-pull.pid)" -o pid,etime,args
tail -n 20 /root/auto-x-camoufox-background-pull.log
ctr -n moby content active
```

2026-09-30 17:59 排查发现，取消旧的 566.2 MB 临时层并从头下载后，南京大学源的后台拉取已于 17:45 因 `short read ... unexpected EOF` 退出，遗留数据为 524,288,131 字节，完整层应为 923,029,924 字节。tc-1 磁盘剩余约 41 GB、可用内存约 1.9 GiB，内核没有 OOM 或磁盘 I/O 错误记录。针对该层发送 Range 请求时，南京大学源返回 HTTP 200，未提供分段续传；官方 GHCR 在同一节点返回 HTTP 206 与正确的 Content-Range。该结果说明此次失败发生在镜像传输链路，不能通过无限等待遗留临时层解决，也不足以断言镜像源存在固定大小限制。

用户确认后改用官方 `ghcr.io` 拉取同一 SHA；首次尝试在连接 `pkg-containers.githubusercontent.com` 时遇到 `TLS handshake timeout`，尚未传输镜像层。后续探测该域名各 IPv4 的 TCP 与 TLS 总建连耗时约 0.4～11.4 秒，TLS 阶段约 0.2～8.1 秒；这些后续成功探测未复现超时，只说明连接时延存在波动。Docker 未配置出站代理。遇到此错误先报告，保留临时层，确认是否重试；不要改动业务容器、Nacos 或数据库来处理镜像下载问题。下载成功后再检查镜像 ID、重新标记为安装器使用的镜像地址，并使用 `KJ_AUTO_X_SKIP_PULL=1` 通过原菜单更新。

官方源的下一次无超时拉取于 2026-09-30 19:16 因 `read: connection reset by peer` 退出，临时层留在约 577.9 MB。该退出来自传输连接重置，不是安装器 900 秒上限；完整镜像尚不存在。用户确认后保留该层重试同一官方 SHA。重试前只归档原后台任务的 `.log`、`.exit`、`.pid` 记录，不删除 containerd 临时层；新任务使用 `nohup docker pull` 的监护 shell，不加 `timeout`。检查当前 `.exit` 是否存在、进程是否活跃及日志错误，不能把残留 SIZE 当成下载仍在运行。无超时不能避免网络错误，出现新的失败仍先报告确认。

### 切换国内加速源的实测记录

用户随后要求另找国内加速源。本次在 tc-1 比较公开 GHCR 代理：DaoCloud 对此仓库返回白名单拒绝，多个入口不可达或限流；南京大学和旧默认代理的该层请求忽略 Range。毫秒镜像 `ghcr.1ms.run` 最初探测曾返回 403，按 Docker 请求方式重测后大层返回 HTTP 206；指定 `600000000-616777215` 范围，16 MiB 样本完整下载，耗时 10.78 秒，约 1.48 MiB/s。目标 manifest 摘要为 `sha256:52fe46047fc4bc422aa3842a8f0fef007a89bedc6e3cb764b07b9a3b3b21748e`，最大层摘要及大小与先前官方清单一致。该结果验证本次镜像与样本可用，不能保证长期稳定或后续大层吞吐不变。

本次下载切换到 `ghcr.1ms.run/stanxu-symple/auto-x-camoufox-worker:sha-936774a32ec78c7a73987d65b5ea8968f032c80f`。只终止旧的官方 `docker pull` 客户端，保留 containerd 临时层，新的后台任务仍无超时限制。当前任务记录改为 `/root/auto-x-camoufox-domestic-pull.log`、`.pid` 和 `.exit`；检查当前记录，不要把被替换的官方任务退出码当作新任务结果。

这次续传达到完整层大小 923,029,924 字节后，Docker 最终校验报 `unexpected commit digest`：实际 SHA-256 为 `07fc7ad09ec53e400552f23d5af266c3cabba7c64904dafb838c0a0d77dd4218`，预期为 `b3908f6608aaa186444ca36386ffc0ec2d8c7c8de84bb45fcacab50a1c8b37b7`。镜像未提交，不能升级。只读比较 0、300,000,000、524,288,131、600,000,000 和 900,000,000 偏移的 1 MiB 样本，均与新源对应内容相同；这些样本不足以定位损坏字节，也不能断言根因是旧层或新源。

用户确认后，2026-09-30 21:33 将这个唯一失败 ingest 目录备份并移出内容存储，备份位于 `/home/docker/auto-x/backups/failed-camoufox-layer-20260930-213300/`。操作前核对 `ref` 恰为 `moby/1/layer-sha256:b3908f6608aaa186444ca36386ffc0ec2d8c7c8de84bb45fcacab50a1c8b37b7`、文件大小和失败摘要；操作后再次核对备份摘要，`ctr -n moby content active` 不再列出该层。备份目录权限 700、文件权限 600，其他完整层与业务卷保留。随后归档上次任务记录，从同一 `ghcr.1ms.run` 标签完整重拉该大层，仍不加 `timeout`。不要执行全局 `docker system prune` 或批量删除 containerd 目录。重拉成功必须以 Docker 校验提交完成及退出码 0 为准；若再次出现相同摘要错误，先报告确认，进一步核对代理内容与官方层摘要。

下载成功后按下节校验完整镜像与 revision，再标记为安装器当前配置的 `ghcr.nju.edu.cn` 别名并通过 `KJ_AUTO_X_SKIP_PULL=1` 完成菜单 2。别名指向已缓存的同一个镜像，更新时不会再次请求南京大学源。来源：[毫秒镜像](https://1ms.run)。

### 完整镜像下载成功后恢复菜单 2 更新

先等当前后台任务退出码为 0，确认完整镜像存在。以下命令中的 SHA 必须与本次目标版本一致；重新加标签复用的是同一个本机镜像，不会再下载镜像层：

```bash
ssh tc-1
cat /root/auto-x-camoufox-domestic-pull.exit
# 必须输出 0；文件不存在表示任务尚未结束，非 0 则先检查并报告错误
docker image inspect \
  ghcr.1ms.run/stanxu-symple/auto-x-camoufox-worker:sha-936774a32ec78c7a73987d65b5ea8968f032c80f \
  --format '{{.Id}} | revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
docker tag \
  ghcr.1ms.run/stanxu-symple/auto-x-camoufox-worker:sha-936774a32ec78c7a73987d65b5ea8968f032c80f \
  ghcr.nju.edu.cn/stanxu-symple/auto-x-camoufox-worker:sha-936774a32ec78c7a73987d65b5ea8968f032c80f

cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.nju.edu.cn \
KJ_AUTO_X_IMAGE_TAG=sha-936774a32ec78c7a73987d65b5ea8968f032c80f \
KJ_AUTO_X_SKIP_PULL=1 \
AUTO_X_SERVICES=xhs-worker,camoufox-worker,monitor-center,monitor-agent \
bash kejilion.sh app auto-x
```

`KJ_APPS_SKIP_REFRESH=1` 的前提是 `/root/apps` 已刷新到本次发布的应用定义；`KJ_AUTO_X_SKIP_PULL=1` 的前提是 backend、xhs-worker 和 camoufox-worker 三个目标 SHA 镜像全部缓存在本机。安装器会验证缺失镜像，再停止旧服务。tc-1 选全四项服务，保留 monitor-center 与 monitor-agent；更新后核对 `.auto-x-services` 仍为上述完整清单。该命令执行原安装器的菜单 2，沿用安装器的 Nacos 同步、卷挂载和启动流程。

### Nacos 缺失配置的升级补齐

旧部署的 Nacos 文档不会随源码模板自动增加字段。本次检查发现共享运行配置缺少 5 项 Camoufox 配置，独立监控拓扑仍只有 12 项，缺少浏览器实例。更新前保存 Nacos 的原共享配置和监控拓扑作为受限备份；安装器通过 `infra/scripts/nacos-config.py` 补齐以下默认值，已有 Nacos 配置值保留：

| 配置名 | 缺失时补入值 | 作用 |
| --- | --- | --- |
| `CAMOUFOX_SERVICE_NAME` | `xsentinel-camoufox-worker` | Nacos 服务发现名 |
| `CAMOUFOX_BROWSER_POOL_SIZE` | `1` | 浏览器池保留数量上限 |
| `CAMOUFOX_MAX_CONCURRENCY` | `1` | 浏览器任务并发上限 |
| `CAMOUFOX_JOB_TIMEOUT_SECONDS` | `290` | 单个浏览器任务超时秒数 |
| `CAMOUFOX_JOB_RESULT_TTL_SECONDS` | `600` | 完成结果保留秒数 |

安装器将本次所选服务传入 `--monitor-services`；只有选中 `camoufox-worker` 且 Nacos 拓扑缺少本节点相应容器选择器时，才向 `x-sentinel-monitor-topology.json` 增加浏览器监控记录。tc-1 使用 `tc1-camoufox-worker`，默认端口 8007，若设置了节点映射端口则使用 `CAMOUFOX_WORKER_HOST_PORT`。已有监控记录、采集周期、节点映射与自定义端口保留；重复更新不会新增重复记录。已有服务 ID 被其他记录占用时先报错，不覆盖配置。

先发布并刷新应用定义和 Auto-X `main` 源码，再运行本节中的同一更新入口。配置由安装器同步到 Nacos；应用启动时读取 Nacos 的生效值。升级修复只修改配置同步程序、安装器和文档，仍使用已发布且正在下载的 `sha-936774a32ec78c7a73987d65b5ea8968f032c80f` 运行镜像。验收共享配置包含上述 5 项，监控拓扑包含 13 项且浏览器实例健康。

2026-09-30 已发布配置同步修复 `495970e`（Auto-X dev/main）和应用定义 `d9ac418`（apps stanxu），针对配置默认值保留、拓扑追加、重复执行和 ID 冲突的 29 项测试通过。两台均已刷新安装器；从安装器执行源码刷新与配置同步阶段，下载继续在后台运行。原三个 Nacos Data ID 备份位于 tc-1 的 `/home/docker/auto-x/backups/pre-camoufox-nacos-20260930/`，文件权限 600。写入后逐项比较：原运行配置值、12 项监控记录、采集设置和两节点映射保留，新增 5 项运行默认值及 `tc1-camoufox-worker`（8007）记录。浏览器容器及监控健康仍须在镜像下载、安装器更新完成后验收。
