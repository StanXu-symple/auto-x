# Auto-X 一键更新傻瓜式文档

当前 Auto-X 服务节点为 hn-1/tc-2，Nacos 暂留 tc-1。从 tc-1 迁移请直接阅读第十三节；下文带日期的 tc-1 命令是历史记录。

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

> 历史计划与下载排查记录。用户后续决定迁往 hn-1，当前安装按第十三节执行，不再在 tc-1 重试。

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

## 十三、tc-1 Auto-X 服务迁移到 hn-1

### 当前节点与准备条件

| 节点 | 当前用途 | 公网地址 |
| --- | --- | --- |
| hn-1 | xhs-worker、camoufox-worker、monitor-center、monitor-agent | 177.2.18.14 |
| tc-2 | 核心应用、认证中心、PostgreSQL、Redis、frontend | 43.172.88.37 |
| tc-1 | 暂留 Nacos；验收后原 Auto-X 三项服务保持停止，原卷和备份保留 | 118.25.197.211 |

本次复用已构建的 `sha-936774a32ec78c7a73987d65b5ea8968f032c80f`，数据库为 `0030_qq_message_templates`。迁移脚本和模板先 push dev、合入 main；它们在宿主机执行，运行镜像无需为这些配置修改重新下载。默认拓扑文件保留名称 `infra/microservices/services.tc-dual.json` 以兼容既有安装命令，内容已改成 hn-1/tc-2。

hn-1 必须能访问 tc-1 Nacos 9999，以及 tc-2 的 TCP 5432、6379、9100、9101。首次预检四个 tc-2 端口超时，按用户要求暂停；用户调整网络后全部复测通过。tc-2 必须能访问 hn-1 的 8006、8007、9101、9102，待安装后从 backend 容器验收。

hn-1 原 `/root/apps` 为官方 main，额外 16 个提交；经用户确认完整保留在 `/root/auto-x-hn1-entry-backup/apps`，本地已发布 apps/stanxu 通过 Git bundle 复制并核对。`kejilion.sh` 和 `auto-x.sh` 同样由本地已发布版本复制，核对 SHA-256；原脚本保留在该备份目录。应用定义版本为 `d9ac418`，sh 为 `b2436ce`。Auto-X 应用源码通过安装器从 main 拉取。

### 1. 先下载并验证完整镜像

在 hn-1 按[安装文档的无超时预拉取步骤](Auto-X一键安装傻瓜式安装文档.md#hn-1-无超时预拉取大镜像)下载 backend、xhs-worker、camoufox-worker 三个目标镜像。当前后台任务记录为 `/root/auto-x-hn1-images.log`、`.pid`、`.exit`，未设置 timeout。三镜像必须下载完成、退出码 0、revision 对应目标 SHA，才能停旧服务。

### 2. 备份与 Nacos 迁移预检查

tc-2 本次默认 GitHub 代理源码刷新失败，官方直连 `git ls-remote` 成功；经用户确认后设置 `KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git`，通过同一安装器函数刷新成功，运行容器未变。后续优先使用该显式仓库参数。

在 tc-2 通过安装器的源码刷新阶段获取 main，保留 `.env`、`.auto-x-services`、数据目录及运行容器。仅准备源码可使用已核对的安装定义函数：

```bash
# git 已安装；只调用原安装器的源码刷新阶段，不启动更新流程
install() { command -v "$1" >/dev/null; }
docker_app_plus() { :; }
prepare_source() {
  local gh_proxy="https://" canshu=default
  local KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git
  source /root/apps/auto-x.conf
  auto_x_sync_source
}
prepare_source
```

此命令同样可在新 hn-1 上预备源码。若网络刷新失败先报告，不能改用未发布本地源码。先按第二节备份数据库、清单和控制面文件，然后校验：

```bash
cd /home/docker/auto-x
python3 infra/scripts/migrate-monitor-node.py --env-file .env \
  --from-node tc-1 --to-node hn-1 --advertise-ip 177.2.18.14
```

默认只读校验，不发布、不修改本地配置。迁移范围：

- `x-sentinel-monitor-topology.json`：节点名、默认 agent 服务名、该节点服务的 node、默认 tc1- ID 前缀及 agent 显示名。
- `x-sentinel-monitor-nodes.json`：节点名与地址，hn-1 地址为 177.2.18.14。
- `x-sentinel-config.json`：仅 monitor 的 `agent:tc-1` 授权改为 `agent:hn-1`；保留 tc-2、密钥、凭据及其他运行配置。

保留采集参数、自定义字段、端口及其他节点。目标节点、IP、服务 ID 或授权冲突会拒绝写入。Nacos 没有跨 Data ID 事务，脚本先完整校验、保存三个原文档再发布并逐项回读；重复执行不重复，部分写入后可重试。若运行发生错误，先报告用户确认。

**已存在的 monitor 客户端以 PostgreSQL 授权为准。** 只改 Nacos 或重启 auth-center 不会重新导入授权，因此必须同步数据库中的 monitor audience；这是身份记录更新，无新增 Alembic 结构迁移。

### 3. 切换前停旧应用并复制卷

镜像与源码准备完成后，在 tc-1 仅停止下列应用；保留 Nacos：

```bash
docker stop --time 45 x-sentinel-xhs-worker-1 \
  x-sentinel-monitor-center-1 x-sentinel-monitor-agent-1
```

停止后备份 `x-sentinel_xhs_home`、`x-sentinel_xhs_uploads`、`x-sentinel_article_uploads` 的实际 Mountpoint，以 tar 保存原权限、属主。经 SSH 标准输入传到 hn-1 的受限备份目录，逐份核对 SHA-256；在 hn-1 创建同名 Docker 卷，确认目标卷为空后解包，再比较文件清单、大小与内容摘要。不要传完整旧 `.env`；仅复制 `NACOS_*` 连接引导字段，运行配置由安装器从 Nacos 同步。

本次停止前核对：xhs_home 有一个 148 字节的 `users/1/.xhs-cli/cookies.json`；xhs_uploads、article_uploads 均为空。旧登录态卷在新版本仅挂载给 camoufox-worker；不要让旧浏览器与新浏览器同时使用同一登录态。

### 4. 发布节点映射并更新数据库授权

在 tc-2 执行，备份目录名称必须为本次新目录：

```bash
cd /home/docker/auto-x
umask 077
migration_stamp="$(date +%Y%m%d-%H%M%S)"
python3 infra/scripts/migrate-monitor-node.py --env-file .env \
  --from-node tc-1 --to-node hn-1 --advertise-ip 177.2.18.14 \
  --apply --backup-dir "backups/pre-hn1-nacos-${migration_stamp}"

docker exec x-sentinel-postgres-1 sh -ec \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql --username="$POSTGRES_USER" --host=127.0.0.1 --dbname="$POSTGRES_DB" --command="COPY service_auth_grants TO STDOUT WITH CSV HEADER"' \
  > "backups/pre-hn1-grants-${migration_stamp}.csv"
docker exec -i x-sentinel-postgres-1 sh -ec \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql --username="$POSTGRES_USER" --host=127.0.0.1 --dbname="$POSTGRES_DB" -v ON_ERROR_STOP=1 -v old_node=tc-1 -v new_node=hn-1' \
  < infra/scripts/migrate-monitor-grant.sql
```

SQL 在事务内锁定授权表，保留原 scopes 与其他身份；冲突拒绝提交，重复执行无副作用。已有相同目标授权会复用，切换后的旧 monitor→agent:tc-1 授权移除。

### 5. 通过原安装器在 hn-1 安装

按[安装文档第五节](Auto-X一键安装傻瓜式安装文档.md#五在-hn-1-安装监控和小红书服务)，在 hn-1 运行原菜单 1。自动化命令等价于菜单选择安装，不会另开独立 docker run：

```bash
cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=install KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io \
KJ_AUTO_X_IMAGE_TAG=sha-936774a32ec78c7a73987d65b5ea8968f032c80f \
KJ_AUTO_X_SKIP_PULL=1 KJ_AUTO_X_MONITOR_NODE_ID=hn-1 \
KJ_AUTO_X_TOPOLOGY_FILE=/home/docker/auto-x/infra/microservices/services.tc-dual.json \
AUTO_X_SERVICES=xhs-worker,camoufox-worker,monitor-center,monitor-agent \
bash kejilion.sh app auto-x
```

前提是三完整镜像和已发布安装入口均已核对，Nacos 节点迁移完成。正常交互入口选择环境 3，再选择安装 1，四项服务和相同 Nacos 信息。hn-1 不启动本机数据服务或 auth-center；服务发现与运行配置由 Nacos 提供。

### 6. 验收与回退

按安装文档第六节验收，并确认：四项服务使用目标 SHA 且 healthy，Camoufox Memory 为 2147483648、ShmSize 为 536870912；旧 Cookie 文件摘要不变；Nacos 的 XHS、Camoufox、monitor-center 注册到 177.2.18.14，两个 agent 分别为 hn-1、tc-2；13 项监控实例和两主机均 healthy；backend→XHS→Camoufox 状态调用成功、installed 为 true；无 JWT 的浏览器 API 返回 401。

tc-1 Nacos 始终运行，旧三个 Auto-X 容器保持停止且卷保留；tc-2 原七项服务清单保持不变。验收仅调用健康和状态 API，不执行真实平台发布。

若切换失败，先报告用户。回退需先停止 hn-1 四项服务，从备份恢复三个 Nacos Data ID，并用 SQL 反向迁移 hn-1→tc-1 的 monitor grant，再启动旧三服务；不能在两个节点同时运行相同 XHS/monitor-center。确认回退前保留新节点日志及数据，避免遗漏切换后新增登录态。

### 2026-09-30 迁移实测记录

hn-1 官方三镜像无超时下载完成，Docker 校验通过，revision 均为 `936774a32ec78c7a73987d65b5ea8968f032c80f`。通过上述 kejilion 安装入口完成四项服务启动；Nacos 同步本地补充 0 项，四项容器 healthy，8006/8007/9101/9102 的 liveness 为 200。Camoufox Memory 为 2147483648、ShmSize 为 536870912，无 JWT 的 `/v1/status` 为 401；tc-2 backend 能发现所有迁移服务的新公网地址，监控两主机和 13 个实例均 healthy。

tc-2 备份为 `/home/docker/auto-x/backups/pre-hn1-20260930/`；三份 Nacos 原配置为 tc-2 的 `/home/docker/auto-x/backups/pre-hn1-nacos-20260930/`。tc-1 切换备份为 `/home/docker/auto-x/backups/pre-hn1-cutover-20260930/`，hn-1 卷归档为 `/root/auto-x-hn1-volume-backup/`。Cookie 文件摘要保持 `0662aa4c3e14e8ce6a065512af316684043b92935480a97382b3192bb787f0e2`；旧三服务已停止，tc-1 Nacos 始终运行。

浏览器业务验收发现 `installed: false`，已按要求暂停修复并报告。只读定位结果：XHS→Camoufox 使用 Nacos 的 `177.2.18.14:8007`，认证调用返回 HTTP 200，xhs CLI 存在。浏览器实际在 `/opt/xsentinel-cache/camoufox/browsers/official/152.0.4-beta.31-3a7958c8`；旧代码仅检查缓存根目录的 `camoufox-bin`/`camoufox`，未识别 SDK 0.5 的多版本目录，因此误判为未安装。容器 healthy 只能确认服务进程，业务验收还必须检查带令牌的 installed 状态；修复须发布镜像后通过菜单 2 更新，不能注入容器源码。


## 十四、Camoufox 新版安装路径检测修复

2026-10-01 用户确认检查修复。hn-1 的已部署 SDK 是 0.5.6，实际可执行文件为 `/opt/xsentinel-cache/camoufox/browsers/official/152.0.4-beta.31-3a7958c8/camoufox-bin`。旧 `browser_installed()` 只查缓存根目录，导致带令牌 `/v1/status` 和 tc-2 的 XHS 状态误报 installed=false。

修复通过 SDK 的 `camoufox_path(download_if_missing=False)` 解析当前版本，再通过 `launch_path(browser_path=...)` 获取启动文件并核对可读、可执行。缺失、不支持或权限错误返回 false；不兼容旧缓存不交给 SDK 清理，避免状态检查删文件。无需更改 Nacos 配置、手工复制浏览器或重新运行 camoufox fetch。

本地 12 项针对性测试通过，覆盖多版本路径、禁止下载、缺失与不支持、权限、缓存保留和状态传递。发布前在 hn-1 已安装浏览器内烟测成功：临时本地页面启动、渲染、按钮点击及关闭；未使用真实账号 profile、未执行平台发布。

修复提交 `604635583a8a2e47264e945c94b84329df89311c` 已按 dev→main 发布，[Actions 镜像构建](https://github.com/StanXu-symple/auto-x/actions/runs/36800420736)成功。安装器使用统一 IMAGE_TAG，并按服务清单加载 Compose 片段；hn-1 采用原完整四项清单更新，保持 monitor-center 在 Compose 模型中，避免部分选择配合 remove-orphans 移除现有服务。

### 更新步骤

1. 备份 hn-1 的 `.env`、`.auto-x-services` 和控制面文件，记录旧四容器 ID、镜像与 Cookie 摘要。本次受限备份目录为 `/home/docker/auto-x/backups/pre-browser-detection-20261001/`，还保存四个业务持久卷及三个 Nacos 原文档。
2. 对照 Actions 的目标 SHA，在 hn-1 无超时预拉 backend、xhs-worker、camoufox-worker 三镜像。新旧大浏览器层摘要一致时复用本机缓存；须以 Docker 完整校验和 revision 为准。本次新浏览器大层为 `sha256:fb3dac68e0209104b05f5fec8617fccab8fa0da5cdccae410f7ca58e1b041b2f`，923,029,341 字节，和旧层摘要不同，需要重新下载。后台记录为 `/root/auto-x-browser-detection-pull.log`、`.pid`、`.exit`，未设置 timeout。
3. 验证安装器版本和完整镜像后通过原菜单 2 更新：

```bash
cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io \
KJ_AUTO_X_IMAGE_TAG=sha-604635583a8a2e47264e945c94b84329df89311c \
KJ_AUTO_X_SKIP_PULL=1 \
AUTO_X_SERVICES=xhs-worker,camoufox-worker,monitor-center,monitor-agent \
bash kejilion.sh app auto-x
```

本次没有新增 Alembic 迁移；完整更新由 migrate 检查当前 head。四项原服务清单保持不变。tc-2 与 tc-1 无需更新应用，Nacos 仍留 tc-1。

### 验收

- 四项容器 healthy，镜像 revision 对应本次发布。
- XHS→Camoufox 带令牌 `/v1/status` installed=true；tc-2 backend→XHS 状态同样 true。
- Camoufox 实际启动、临时页面渲染与点击成功，无 JWT API 仍为 401。
- Memory=2147483648、ShmSize=536870912；Cookie 摘要、Nacos 三文档、数据库版本与原服务清单保留。
- 两主机及 13 个实例监控健康；原 tc-1 三项应用保持停止，Nacos 正常。

出现新错误先报告用户确认，不注入容器源码；经确认后使用备份镜像与原安装器入口回退。

### 2026-10-01 修复部署实测记录

hn-1 三个官方 GHCR 镜像无超时下载完成，后台退出码为 0，Docker 完整校验及三个镜像 revision 均对应 `604635583a8a2e47264e945c94b84329df89311c`。通过上述 `kejilion.sh app auto-x` 菜单 2 更新入口完成四项服务升级；`xhs-worker`、`camoufox-worker`、`monitor-center`、`monitor-agent` 均为 healthy，revision 为目标 SHA，`migrate` 退出码为 0。

浏览器容器内 SDK 仍为 0.5.6，解析到 `/opt/xsentinel-cache/camoufox/browsers/official/152.0.4-beta.31-3a7958c8/camoufox-bin`，新版安装检测返回 true。hn-1 的 xhs-worker 通过 Nacos 发现 `177.2.18.14:8007`，使用认证中心 JWT 调用 `/v1/status` 返回 HTTP 200、status=online、installed=true；tc-2 backend 的 XHS 服务客户端同样返回 online、installed=true。新容器内实际启动浏览器、渲染临时本地页面、点击按钮及关闭均成功；无 JWT 的 `/v1/status` 返回 401。

Camoufox 内存上限为 2,147,483,648 字节，共享内存为 536,870,912 字节。Cookie 摘要保持 `0662aa4c3e14e8ce6a065512af316684043b92935480a97382b3192bb787f0e2`，hn-1 完整四项清单与备份逐字一致。三个 Nacos Data ID 的 JSON 内容与备份完全一致；共享配置因安装器重新序列化，仅键顺序变化，两份监控配置的原始文本也逐字一致。配置验收应同时比较原始文本和解析后的 JSON；键顺序变化无需恢复，不应据此误判配置值被修改。

tc-2 原七项清单保持不变，数据库仍为 `0030_qq_message_templates`，frontend、backend、auth-center HTTP 检查均为 200。从 tc-2 backend 获取的监控快照中，hn-1、tc-2 两主机及 13 个实例全部 healthy。tc-1 原三项 Auto-X 应用保持停止，Nacos 持续运行。本次运行镜像固定为上述修复 SHA；后续验收记录和测试格式提交无需再次重建服务器容器。

## 十五、X 帖子自动截图升级

目标提交为 `055fe5a93d0c480676f4373c6383e69f8c57bc93`，已推送 dev、合入 main，[Actions 镜像构建](https://github.com/StanXu-symple/auto-x/actions/runs/36809603197)成功。安装定义为 apps/stanxu 的 `3606fff1b73168fdfd343bf5d3d2d572c90eddc6`。功能和配置见 [X 帖子自动截图](tweet-screenshots.md)。此次新增迁移 `0031_tweet_screenshots`；安装器通过 migrate 自动执行，不需另开终端手工迁移。

首次更新在离线镜像校验处发现旧默认源未迁移：tc-2 已下载 `ghcr.io` 镜像，但 `.env` 的 BACKEND_IMAGE、FRONTEND_IMAGE 仍为历史 `ghcr.dockerproxy.net` 地址。安装器因此报告缺少本机镜像，旧七项容器 ID、健康状态未变化，原完整清单已恢复。用户确认修正后，apps/stanxu 发布修复 `24016c6`；本节更新要求使用该提交或后续版本。无需再添加镜像别名，也不修改应用 Nacos 业务参数。

修复在完整安装、更新及仅更新 frontend 的镜像校验前处理显式 `KJ_AUTO_X_IMAGE_REGISTRY`：仅当保存的地址精确匹配 `ghcr.io`、`ghcr.dockerproxy.net`、`ghcr.nju.edu.cn` 下的 `stanxu-symple/auto-x-<服务名>` 标准地址时，切换到显式源；用户自定义域名、仓库或路径保留。仅更新 frontend 时只处理 FRONTEND_IMAGE，并在 Compose 校验或本机镜像缺失时恢复原地址。未显式指定源的 default 更新不触发历史源迁移；交互 CN 模式继续迁移到南京大学源。镜像标签仍由 IMAGE_TAG、FRONTEND_IMAGE_TAG 控制。

### 节点、存储和备份

tc-2 更新 backend、worker、frontend 及依赖的 auth-center、monitor-agent；hn-1 按原完整四项清单更新。backend 和 worker 都在 tc-2，使用同一个 `x-sentinel_tweet_screenshots` 命名卷，worker 可写、backend 只读。hn-1 的 Camoufox 通过认证 API 传回 PNG，最终图片保存于 tc-2，不需要在 hn-1 挂载 tc-2 的存储目录。

更新前按第二节备份数据库、原服务清单、`.env` 和控制面；另保存 Nacos 三个原文档及 hn-1 业务卷、Cookie 摘要。本次两台备份目录均为 `/home/docker/auto-x/backups/pre-tweet-screenshots-20261001/`。tc-2 升级前数据库为 `0030_qq_message_templates`，数据库备份为该目录中的 `database.dump`。核对并备份两台 `/root/apps/auto-x.conf` 后，将干净仓库快进到上述已发布安装定义。

### 无超时预拉镜像

等待 Actions 成功后，在 tc-2 预拉 backend、frontend 两镜像，在 hn-1 预拉 backend、xhs-worker、camoufox-worker 三镜像；标签均为上述完整 SHA，镜像源为官方 `ghcr.io/stanxu-symple/auto-x-<服务名>`。后台记录为各节点的 `/root/auto-x-tweet-screenshot-pull.log`、`.pid`、`.exit`。下载不设置 timeout，退出码必须为 0，完整镜像 revision 必须与目标一致，才能设置 `KJ_AUTO_X_SKIP_PULL=1` 复用缓存。

### 通过菜单 2 分阶段更新

正常交互入口为 `bash kejilion.sh app auto-x`，运行环境选 3、应用菜单选 2。以下自动化入口执行同一个菜单 2：先更新认证中心，使其从 Nacos 同步文件导入新的 `screenshot-worker` 身份；再更新浏览器节点；最后启用新版采集、API 和前端。

tc-2 第一阶段：

```bash
cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io \
KJ_AUTO_X_IMAGE_TAG=sha-055fe5a93d0c480676f4373c6383e69f8c57bc93 \
KJ_AUTO_X_SKIP_PULL=1 KJ_AUTO_X_CAMOUFOX_REMOTE=1 \
AUTO_X_SERVICES=auth-center,monitor-agent \
bash kejilion.sh app auto-x
cp /home/docker/auto-x/backups/pre-tweet-screenshots-20261001/services.txt \
   /home/docker/auto-x/.auto-x-services
```

确认认证中心 healthy、截图身份有 `camoufox-worker: browser:execute` 授权，再在 hn-1 执行：

```bash
cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io \
KJ_AUTO_X_IMAGE_TAG=sha-055fe5a93d0c480676f4373c6383e69f8c57bc93 \
KJ_AUTO_X_SKIP_PULL=1 \
AUTO_X_SERVICES=xhs-worker,camoufox-worker,monitor-center,monitor-agent \
bash kejilion.sh app auto-x
```

确认四项 healthy 后，在 tc-2 第二阶段执行：

```bash
cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io \
KJ_AUTO_X_IMAGE_TAG=sha-055fe5a93d0c480676f4373c6383e69f8c57bc93 \
KJ_AUTO_X_SKIP_PULL=1 KJ_AUTO_X_CAMOUFOX_REMOTE=1 \
AUTO_X_SERVICES=backend,worker,frontend,auth-center,monitor-agent \
bash kejilion.sh app auto-x
cp /home/docker/auto-x/backups/pre-tweet-screenshots-20261001/services.txt \
   /home/docker/auto-x/.auto-x-services
```

`KJ_AUTO_X_CAMOUFOX_REMOTE=1` 必须在 tc-2 的采集更新中指定，表示继续通过 Nacos 使用 hn-1 的浏览器，避免安装器自动在 tc-2 加入第二个浏览器。部分更新后立即恢复 tc-2 原七项清单；hn-1 保留原四项清单。已有 Nacos 值优先；安装器只补截图四项运行默认值和新服务身份，已有身份的撤销授权不会恢复。

### 验收

- 目标服务 healthy、revision 正确；数据库为 `0031_tweet_screenshots`，截图表存在。
- tc-2 worker 通过 Nacos 和 JWT 访问 hn-1 Camoufox；backend 和 worker 的截图卷来源相同，权限符合服务 UID 10001。
- 使用已入库的公开原帖验证排队、截图、PNG 下载和校验，不执行平台发布；登录墙、删帖或正文不匹配应保留失败原因，不可当作成功。
- Nacos 保留原配置值及监控拓扑；两节点完整清单、hn-1 Cookie 和 2 GB 浏览器内存上限保留。
- 原未选择的 ai-worker、qq-worker 保持运行；两主机及 13 项监控健康；tc-1 Nacos 正常。

### 2026-10-01 当前部署与待完成验收

安装器修复 `24016c6a9d0ae57cd28638e6fb63a3dc3cf0d508` 已在两台 `/root/apps` 生效，镜像地址由原安装器自动切换到官方 GHCR。后台镜像预拉均退出 0，所有目标 revision 为 `055fe5a93d0c480676f4373c6383e69f8c57bc93`。tc-2 第一阶段更新 auth-center、monitor-agent 后，migrate 成功执行 `0031_tweet_screenshots`，数据库导入 `screenshot-worker → camoufox-worker: browser:execute` 授权；其余容器 ID 保持。

随后通过菜单 2 完成 hn-1 原四项服务和 tc-2 的 backend、worker、frontend、auth-center、monitor-agent 更新，目标容器均 healthy。tc-2 原七项清单恢复；hn-1 原四项清单、Cookie 摘要、Camoufox Memory=2147483648、ShmSize=536870912 保留。未选的 ai-worker、qq-worker ID 未变且 healthy。backend 与 worker 挂载同一 `x-sentinel_tweet_screenshots` 卷，前者只读、后者可写。Nacos 仅增加四项截图运行默认值和截图服务 secret，客户端 JSON 中仅增加 screenshot-worker；原运行值、旧身份授权、监控拓扑及节点映射保留。

**截图业务验收尚未通过。** 使用用户给出的、已入库的 `2105178959327756396`，管理员正常登录 HTTP 200、排队 HTTP 202；worker 首次尝试记录“X 截图服务请求超时”。只读复查发现 tc-2 宿主机、backend、worker 到 hn-1 `177.2.18.14:8007` 均连接超时，8006、9101、9102 可达。hn-1 本机浏览器健康接口 HTTP 200、无重启或 OOM，8007 已监听并映射到 0.0.0.0。

hn-1 DOCKER-USER 残留规则只允许本机来源，随后按容器 IP 丢弃 TCP；其中 `172.18.0.4` 正好成为更新后 Camoufox 在 control 网络的地址，DROP 计数增长。旧容器 IP 规则可能在重建后匹配到其他服务，不能仅凭容器 healthy 判定跨节点调用正常，也不能全局清空防火墙。

用户自行调整网络后再次复测：tc-2 到 8006、9101、9102 连接成功，到 8007 在 6 秒连接探测内仍超时；hn-1 本机浏览器健康接口仍为 HTTP 200，DOCKER-USER 中该容器的 TCP DROP 规则仍存在，计数为 91。认证截图客户端连接超时，同一帖子再次排队后仍记录“X 截图服务请求超时”，未保存成功图片。再次报告后，用户要求使用 firewalld；检查确认 hn-1 未安装该组件。用户随后明确要求不安装，直接调整 Docker 防火墙，将 8007 对公网开放。

### Docker 防火墙拦截 8007 的恢复步骤

以下是本次用户确认的公网开放方案。在 hn-1 执行，公网地址必须与 Nacos 注册的本机地址一致。Docker 会先把 8007 转发到容器；只添加宿主机 INPUT 规则无法覆盖 DOCKER-USER 的拦截。用 conntrack 原目标地址和端口匹配，避免固定容器 IP 造成重建后失效。

1. 备份当前规则和旧持久化文件，确认 Docker 正常运行：

```bash
umask 077
firewall_backup="/root/auto-x-firewall-backup-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 "$firewall_backup"
iptables-save > "$firewall_backup/iptables-before.v4"
ip6tables-save > "$firewall_backup/iptables-before.v6"
test ! -f /etc/iptables/rules.v4 || cp -a /etc/iptables/rules.v4 "$firewall_backup/rules.v4.before"
docker ps --format '{{.Names}} {{.ID}} {{.Status}}' > "$firewall_backup/containers-before.txt"
```

2. 新建重复执行不会增加规则的脚本，放在现有 DROP 之前；只匹配转发到本机公网 TCP 8007 的连接：

```bash
cat > /usr/local/sbin/auto-x-open-browser-port <<'RULE'
#!/bin/sh
set -eu
if ! /usr/sbin/iptables -w 10 -C DOCKER-USER -p tcp -m conntrack --ctstate DNAT --ctorigdst 177.2.18.14 --ctorigdstport 8007 --ctdir ORIGINAL -m comment --comment auto-x-public-camoufox-8007 -j ACCEPT 2>/dev/null; then
    /usr/sbin/iptables -w 10 -I DOCKER-USER 1 -p tcp -m conntrack --ctstate DNAT --ctorigdst 177.2.18.14 --ctorigdstport 8007 --ctdir ORIGINAL -m comment --comment auto-x-public-camoufox-8007 -j ACCEPT
fi
RULE
chmod 700 /usr/local/sbin/auto-x-open-browser-port
/usr/local/sbin/auto-x-open-browser-port
```

3. 使用 systemd 在 Docker 启动后补回规则，并随 Docker 重启重新执行。仅重启此规则服务，无需重启 Docker 或业务容器：

```bash
cat > /etc/systemd/system/auto-x-browser-firewall.service <<'UNIT'
[Unit]
Description=Allow public TCP 8007 for Auto-X Camoufox through Docker firewall
Requires=docker.service
After=docker.service
PartOf=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/auto-x-open-browser-port
RemainAfterExit=yes

[Install]
WantedBy=docker.service
UNIT
chmod 644 /etc/systemd/system/auto-x-browser-firewall.service
systemctl daemon-reload
systemctl enable --now auto-x-browser-firewall.service
systemctl restart auto-x-browser-firewall.service
systemctl is-enabled auto-x-browser-firewall.service
systemctl is-active auto-x-browser-firewall.service
iptables -nvL DOCKER-USER --line-numbers
```

本机已有 `/etc/iptables/rules.v4` 时，还应只向该文件的第一条 `-A DOCKER-USER` 之前插入相同放行规则，保留其他原文；避免后续人工加载旧文件时再次覆盖修复。本次已同步该文件。不要把整套旧 Docker NAT/容器 IP 规则重新加载到正在运行的 Docker。

```bash
python3 - <<'PY'
from pathlib import Path
p = Path('/etc/iptables/rules.v4')
if p.exists():
    content = p.read_text()
    rule = '-A DOCKER-USER -p tcp -m conntrack --ctstate DNAT --ctorigdst 177.2.18.14 --ctorigdstport 8007 --ctdir ORIGINAL -m comment --comment auto-x-public-camoufox-8007 -j ACCEPT'
    if rule not in content.splitlines():
        lines = content.splitlines(keepends=True)
        index = next(i for i, line in enumerate(lines) if line.startswith('-A DOCKER-USER '))
        lines.insert(index, rule + '\n')
        p.write_text(''.join(lines))
PY
```

4. 从 tc-2 的 worker 调用经 Nacos 发现的浏览器健康及带 JWT 状态接口，分别应为 200、200（installed=true）；未带 JWT 的 `/v1/status` 应为 401。然后重新排队原失败帖子，验收 PNG 保存与下载。本机 curl 成功不足以代表跨节点已通。

本次备份目录为 `/root/auto-x-firewall-backup-20261001T041528Z/`。重复运行规则脚本及重启规则服务后仍只有一条放行规则，systemd 单元语法检查通过，原业务容器 ID 保持。tc-2 worker 到 hn-1 的上述三项接口验收均通过。未实际重启服务器或 Docker，开机及 Docker 重启自动恢复由已启用的单元依赖保证。

需要撤销本次开放时，执行以下命令，保留备份；无需恢复整套 Docker 自动生成的旧规则：

```bash
systemctl disable --now auto-x-browser-firewall.service
iptables -w 10 -D DOCKER-USER -p tcp -m conntrack --ctstate DNAT --ctorigdst 177.2.18.14 --ctorigdstport 8007 --ctdir ORIGINAL -m comment --comment auto-x-public-camoufox-8007 -j ACCEPT
test ! -f /etc/iptables/rules.v4 || sed -i '/auto-x-public-camoufox-8007/d' /etc/iptables/rules.v4
rm /usr/local/sbin/auto-x-open-browser-port /etc/systemd/system/auto-x-browser-firewall.service
systemctl daemon-reload
```

### 2026-10-01 网络恢复后的截图验收

从 tc-2 worker 经 Nacos 发现 `177.2.18.14:8007` 后，`/health/live` 返回 200，带服务 JWT 的 `/v1/status` 返回 200、online、installed=true，未认证状态接口返回 401。公网放行规则已有请求命中；本机健康检查为 200。

原帖 `2105178959327756396` 再次通过正常管理员登录排队，登录 200、排队 202；任务进入 running、attempts=1，浏览器实际执行后返回 `Requested X post was not found; it may require login or be unavailable`。此时已不再是跨节点连接超时，无法仅凭该错误判断登录要求、帖子可用性或页面定位问题。图片尚未成功保存和下载，截图业务验收仍未通过。已保留失败原因并报告用户，等待确认进一步排查；未修改页面定位逻辑或注入 X 登录态。验收登录已正常注销，返回 200。

### 2026-10-01 X 页面结构兼容问题定位

用户确认继续排查后，在 hn-1 使用正在部署的同一 Camoufox 镜像启动自动删除的临时诊断容器（1 GB 内存、256 MB 共享内存），隔离业务持久浏览器及登录态。使用无登录 Cookie 的临时页面访问原帖，导航 HTTP 200；第 2、10、30、65 秒均能看到作者 `@thsottiaux`、正文 `What’s up dot` 和发布时间。页面标题是 `Tibo on X: "What’s up dot" / X`，四个 article 包含原帖及回复。无页面脚本异常，页面未出现 role=dialog/aria-modal 弹框，现有遮挡检测返回 false。

原帖 DOM 没有 `[data-testid="User-Name"]`、`[data-testid="tweetText"]` 或 `<time>`。作者通过 `/thsottiaux` 链接显示，正文在 `dir="auto"` 的元素内，时间是直接指向 `/thsottiaux/status/2105178959327756396` 的文本链接。当前 `_ARTICLE_METADATA` 依赖旧页面的三个节点，因此把所有 article 的作者、正文和时间链接提取为空，等待至 65 秒后误报帖子未找到。这是已复现的页面结构兼容问题；目标帖子可用，hn-1 网络与 Camoufox 能正常加载该页面，tc-2 的采集身份和原帖正文也匹配。

拟修复方案：保留旧结构支持，为新版公开页面新增作者链接、时间原帖链接和正文识别，并适配媒体计数与加载检查；继续严格校验帖子 ID、作者、正文及媒体，排除引用和回复混入，不通过放宽身份校验来绕过错误。补充新版真实 DOM 回归和同一原帖线上验证，发布 dev→main、等待 Actions 镜像成功后，按原安装器菜单 2 更新 hn-1。无需数据库迁移；安装器使用统一镜像标签且完整清单避免移除已有服务，hn-1 更新时保留原四项清单。当前仅完成定位并等待用户确认修复，尚未修改运行代码或生成成功 PNG。

## 十六、X 新版公开页面截图兼容修复

2026-10-01 用户确认修复并继续升级。新增公开页面结构识别，保留原选择器；新版作者必须由主页链接和 `@账号` 同时确认，时间链接必须是原帖状态链接且排除浏览数、操作链接及正文内链接。正文从独立 `dir="auto"` 块读取，排除引用卡片与嵌套 article，去除格式化 script、style、svg，保留提及、表情、换行。原帖图片兼容 `pbs.twimg.com/media/`，头像和外链预览不计数，引用媒体不能满足原帖媒体数量，截图范围内图片仍全部等待加载。身份、正文、图片完整性及遮挡校验继续执行。

针对性验证包含 13 项真实浏览器 DOM 测试、23 项截图捕获测试，以及 41 项持久截图、RPC、共享浏览器池相关测试，全部通过。真实 DOM 测试使用隔离临时 Chrome 页面，媒体通过固定 PNG 响应模拟，不访问平台；本机 Chrome 需要在正常权限下启动，沙箱内启动失败不代表页面识别失败。

本次修改的运行代码在 hn-1 的 camoufox-worker 中执行；tc-2 当前的截图调用、数据库迁移 0031 和存储卷已就绪，无需再次更新 tc-2。安装器使用统一镜像标签，hn-1 仍按原完整四项清单执行菜单 2，避免 remove-orphans 移除原有服务。待修复提交发布、Actions 成功后，按第十五节无超时预拉 hn-1 的 backend、xhs-worker、camoufox-worker 三镜像，校验 revision，再使用相同更新入口并将 IMAGE_TAG 固定为修复提交 SHA。升级前再次备份 hn-1 原配置、清单、控制面及 Cookie 摘要；更新后复验 2 GB 内存、8007 规则、原帖截图入库和 PNG 下载。

修复提交 `162f7844eb055bfc599c43c936c444615dfd6604` 已按 dev→main 发布，[Actions 镜像构建](https://github.com/StanXu-symple/auto-x/actions/runs/36816894928)成功。hn-1 三镜像预拉退出码为 0、revision 均为该 SHA；浏览器大层复用完整缓存，未重新下载。备份为 `/home/docker/auto-x/backups/pre-x-public-dom-20261001/`，含三份 Nacos 当前配置、原清单与 Cookie 摘要。后台下载记录为 `/root/auto-x-x-public-dom-pull.log`、`.pid`、`.exit`。

hn-1 使用菜单 2 等价入口：

```bash
cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io \
KJ_AUTO_X_IMAGE_TAG=sha-162f7844eb055bfc599c43c936c444615dfd6604 \
KJ_AUTO_X_SKIP_PULL=1 \
AUTO_X_SERVICES=xhs-worker,camoufox-worker,monitor-center,monitor-agent \
bash kejilion.sh app auto-x
```

### 2026-10-01 部署实测及待解决的悬浮标题遮挡

上述安装器更新成功，hn-1 四项服务均为 healthy、revision=162f784，migrate 退出码 0；数据库仍为 `0031_tweet_screenshots`。Nacos 三份 JSON 与此次备份完全相同，原四项清单与 Cookie 摘要保留。Camoufox Memory=2147483648、ShmSize=536870912，无 OOM 或重启；8007 公网规则在重建后继续生效。tc-2 未再次升级，两主机和 13 项监控全部 healthy，XHS 状态为 online、installed=true。

原帖首次尝试已通过身份、正文和媒体检查，但 `Locator.screenshot` 等待元素稳定时超时；第二次遇到页面同时出现多份相同原帖，严格身份检查拒绝任意选一份；第三次内置自动重试成功。没有为这两次渲染异常另改代码或重启服务。隔离诊断使用同一发布镜像执行原捕获函数也成功，耗时 13.3 秒。自动重试成功只代表此帖最终完成，不保证平台每次渲染均稳定。

数据库截图状态为 succeeded、attempts=3、last_error=null。正常登录下载 HTTP 200、Content-Type=image/png，PNG 15,759 字节、566×157，SHA256 为 `b187aee361f16d05a842beca87c7997b5cebdb7ab5ceae09f3237c49b8d615a0`；CRC、像素数据、尺寸、摘要及卷内文件字节校验全部通过。容器内文件为 `/var/lib/xsentinel/tweet-screenshots/2105178959327756396/07960c89365f42748744786b734f45cb.png`，tc-2 宿主机位置为 `/var/lib/docker/volumes/x-sentinel_tweet_screenshots/_data/2105178959327756396/07960c89365f42748744786b734f45cb.png`；UID=10001、权限 640。匿名下载返回 401，验收登录已注销。

**完整截图视觉验收尚未通过。** 下载 PNG 后发现新版页面顶部 53 像素的 sticky DIV（文字为 Post）覆盖了头像与作者。原捕获流程把 article 滚动到 y=0，现有 screenshot style 只隐藏 `header[role="banner"]`，新版标题 DIV 没有该语义标记；登录弹框检查返回 false 不能排除这种悬浮标题。已报告用户，等待确认在截图期间临时隐藏与目标 article 重叠的外部悬浮标题，保留 article 自身内容及登录弹框校验，并补真实浏览器回归后重新发布升级。在此项修复验收前，不能把 PNG 成功保存和下载写成完整截图验收通过。

## 十七、跨节点实时日志与文章原文链接升级

### 原因和修复

2026-10-01 `/runtime-logs` 的小红书和浏览器 Worker 返回 `lines: []`。只读检查确认 hn-1 两个 Worker 都健康、正常写入日志，而 tc-2 backend 仅查本机日志文件；两台 Docker 主机的同名 `runtime_logs` 卷不共享。HTTP 200 只表示 SSE 入口建立，不能证明远端日志已读取。

修复使 backend 通过 Nacos 发现对应 Worker，调用其 `/v1/logs/stream`。认证中心使用新 `runtime-logs` 身份，只有 `xhs-worker: logs:read` 和 `camoufox-worker: logs:read` 两项只读授权。安装器已有的 `microservices-init.sh` 和 `nacos-config.py` 流程自动补入身份及 Nacos `SERVICE_CLIENT_RUNTIME_LOGS_SECRET`，backend 挂载 `runtime-logs.secret`；已有身份、凭据、撤销授权和业务配置保留，无需修改本机业务参数或额外开放端口。

Worker 只提供自身日志，校验 audience、scope 和令牌时效；每条连接最多 45 秒，页面自动重连以刷新短期令牌并重新发现服务。取不到远端时，首次连接返回 503，已建立的连接发出明确错误事件；真正的空日志才显示等待输出。连接关闭时释放远端 HTTP 连接。本轮还包含用户本地提交 `1aba6df9ebfc447c55890f00f3ff3d562cd5a0bb`（文章预览“查看原文”），更新 backend、frontend。无新增 Alembic 迁移，安装器仍自动执行 `alembic upgrade head`，预期数据库保持 `0031_tweet_screenshots`。

### 发布、备份与菜单 2

按 dev→main 发布，等待对应完整 SHA 的 `Publish Auto-X images` 成功。使用 apps/stanxu `24016c6` 或后续安装定义，它已支持显式官方镜像源和本次源码中的配置同步工具。先备份两节点 `.env`、完整服务清单、控制面文件、容器记录及三份 Nacos 原文档；tc-2 另备份数据库，hn-1 保存 Cookie 摘要及浏览器资源限制。备份目录权限 700、敏感文件 600。

按第十五节无超时预拉：tc-2 下载 backend、frontend；hn-1 下载 backend、xhs-worker、camoufox-worker。Docker 退出码必须为 0、镜像 revision 与本次 SHA 一致，再使用 `KJ_AUTO_X_SKIP_PULL=1`。本轮不需要改 kejilion.sh 菜单或上传源码包，原菜单 2 会从 main 获取配置同步工具及 Compose。

把下述 `<完整SHA>` 替换成已成功构建的发布 SHA。先在 tc-2 更新认证中心，导入新的只读日志身份：

```bash
cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io KJ_AUTO_X_IMAGE_TAG=sha-<完整SHA> \
KJ_AUTO_X_SKIP_PULL=1 KJ_AUTO_X_CAMOUFOX_REMOTE=1 \
AUTO_X_SERVICES=auth-center,monitor-agent \
bash kejilion.sh app auto-x
# 成功后按第四节立即恢复 tc-2 原七项服务清单
```

确认认证中心健康且 `runtime-logs` 有两项 `logs:read` 授权，再在 hn-1 按完整四项清单更新：

```bash
cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io KJ_AUTO_X_IMAGE_TAG=sha-<完整SHA> \
KJ_AUTO_X_SKIP_PULL=1 \
AUTO_X_SERVICES=xhs-worker,camoufox-worker,monitor-center,monitor-agent \
bash kejilion.sh app auto-x
```

确认四项 healthy 后，在 tc-2 更新 API 和前端：

```bash
cd /root
KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io KJ_AUTO_X_IMAGE_TAG=sha-<完整SHA> \
KJ_AUTO_X_SKIP_PULL=1 KJ_AUTO_X_CAMOUFOX_REMOTE=1 \
AUTO_X_SERVICES=backend,frontend,auth-center,monitor-agent \
bash kejilion.sh app auto-x
# 成功后恢复 tc-2 原七项清单；hn-1 保留原四项清单
```

### 验收与故障恢复

- 正常管理员登录后，通过 frontend 8080 的原日志 URL，两个 Worker 均返回非空最近日志，并有实时追加事件；本地 Worker 日志仍正常。
- hn-1 日志接口无令牌、错误 audience、缺少 `logs:read` 均返回 401；日志令牌不能提交浏览器任务。
- 文章 API 返回正确 `source_url`，前端文章预览显示“查看原文”，未关联原文时按钮禁用。
- 目标镜像 revision 正确、服务 healthy；数据库仍为 0031，tc-2 原七项与 hn-1 原四项清单、已有 Nacos 值、Cookie 摘要、2 GB 浏览器限制保留。
- 两主机与 13 项监控健康，tc-1 Nacos 正常；已有截图卷保留。本轮日志升级不代表此前悬浮标题截图问题已修复。

出现新问题先报告用户确认。认证失败先检查 auth-center 是否已重新加载安装器同步的 clients.json 并导入新身份，再检查 Nacos 中该身份的密钥摘要匹配和数据库授权；不输出真实 secret/JWT，也不临时放宽 Worker 鉴权。源码与镜像版本必须对应本次发布；回退同样使用备份版本与原安装器菜单 2。

### 2026-10-01 实际发布与验收

运行版本 `c207dc6dbe8119c169b607724508cdda83ac730a` 已推 dev、快进合入 main，[Actions 36822905387](https://github.com/StanXu-symple/auto-x/actions/runs/36822905387)成功，包含用户本地 `1aba6df`。82 项针对性测试通过，1 项因本机缺少 Docker 跳过；前端类型检查和生产构建通过。两节点官方 GHCR 镜像无超时预拉退出 0、revision 全部正确，浏览器大层复用缓存。

备份：tc-2 `/home/docker/auto-x/backups/pre-runtime-logs-20261001T060907Z/`，hn-1 `/home/docker/auto-x/backups/pre-runtime-logs-20261001T060906Z/`；tc-2 database.dump 为 539,843 字节，pg_restore --list 可读取 332 行归档清单。临时备份工具首次未设置 parse_env(include_excluded=True)，导致没有读到 Nacos 引导字段；仅创建了不完整备份目录，没有改业务配置或容器。修正工具调用后已完整备份。记录为 UTC 时间戳。

按上述菜单 2 完成 tc-2 认证中心→hn-1 完整四项→tc-2 backend/frontend 三阶段更新。tc-2 的 backend、frontend、auth-center、monitor-agent，以及 hn-1 四项服务均 healthy、revision=c207dc6；tc-2 原 worker、ai-worker、qq-worker 容器 ID 与升级前一致。migrate 退出 0，数据库保持 `0031_tweet_screenshots`。两节点完整服务清单与备份逐字一致。

从 tc-2 backend 经 frontend 的原 `/api/v1/system/logs/stream` 请求验收：xhs-worker 和 camoufox-worker 均 HTTP 200、ready 各返回 200 行，分别在 8.3 秒和 9.6 秒收到实时 log 事件；本地 worker 也返回 200 行并在 2.1 秒收到追加。匿名页面接口返回 401；两 Worker 直接匿名日志请求返回 401，日志只读令牌提交 `/v1/jobs` 同样返回 401。文章列表的两条记录均有有效 `https://x.com/` 原文链接，HTTP 200。

首次验收登录的注销请求曾返回 503；随后复测登录、me、logout 均返回 200，认证中心未复现异常，未修改认证配置。首次验收的残留会话按精确登录时间与来源唯一定位，通过认证中心既有 revoke_session 方法清理；没有影响用户其他会话。XHS online、installed=true；两主机及 13 个监控实例全部 healthy，tc-1 Nacos 持续运行。Nacos 逐项对比保留全部既有值与既有身份，只新增 runtime-logs 及其 secret；两份监控 JSON 不变，两节点 secret 文件与 Nacos 一致。hn-1 Cookie 摘要保留、浏览器 Memory=2147483648、ShmSize=536870912，无 OOM 或重启；8007 公网规则仍只有一条。已有截图卷保留，悬浮标题遮挡仍是另一项待确认修复。

## 十八、小红书图片上传失败诊断升级

### 已确认的失败阶段与诊断范围

2026-10-01 14:31:15（北京时间），任务 `5c69ecd214fe44fda148466c039ba2bb` 的一张图片上传到 `ros-upload-d4.xhscdn.com` 时触发浏览器 `requestfailed`。当时已进入发布页并找到图片输入控件，尚未填写标题正文或点击发布。旧代码只保留请求地址，遗漏 Playwright `request.failure`，无法据此确定连接重置、CORS、超时或其他具体原因。

只读复测中 hn-1 宿主机和实际浏览器容器的 DNS、TCP、TLS 正常；隔离无登录态的 Camoufox 访问 CDN 根地址也收到 HTTP 响应。两个 Worker 健康、无重启，浏览器 cgroup 没有 OOM，未配置出站代理。这些结果仅证明复测时根地址可达，不能证明原签名图片上传正常，也不能将该次失败直接归因于 tc-2 或 hn-1。

本次只补执行代码的诊断信息，保留原成功判定、失败停止流程，不自动重新上传或发布。新增：

- `image_upload_request_failed`：真实浏览器失败原因、方法、资源类型、去查询参数的地址、已完成图片数和上传耗时。
- `image_upload_http_error`：上传及 OPTIONS 预检的 HTTP 错误；预检仅观察，不计入上传成功数，也不改变失败判定。
- `image_upload_diagnostics`：预期/已完成图片数、耗时、最近 12 项网络观察、CORS/网络错误类别、页面预览数量、加载状态、上传状态和错误代码。
- 阶段日志同时写入 `camoufox-worker.log`，可从 `/runtime-logs` 的“浏览器 Worker”读取；保留 CLI 使用的 `XHS_STAGE` stderr 帧。

不记录请求正文、任意页面/console 文本、Cookie、Authorization 或签名查询参数。上传完成、失败或超时后移除全部四类事件监听器，防止持久页面积累重复监听。

### 发布与安装器更新

先完成针对性测试及 Ruff 检查，push dev 并快进合入 main，等待完整发布 SHA 对应的 Actions 成功。没有新增迁移、Nacos 配置或授权；tc-2 保持当前运行版本。本次代码在 hn-1 浏览器服务中执行，但该节点安装器使用统一镜像标签，仍选择原完整四项清单，避免遗失现有服务。

升级前备份 hn-1 `.env`、`.auto-x-services`、`data/control-plane`、容器记录及三份 Nacos JSON，保存 Cookie 摘要、浏览器资源限制和防火墙规则。备份目录权限 700、敏感文件 600。按第十五节从官方 GHCR 无超时预拉目标 SHA 的 backend、xhs-worker、camoufox-worker 三镜像；退出码 0、revision 全部正确后再跳过安装器重复拉取。

```bash
ssh hn-1
cd /root
TERM=xterm KJ_APP_INTERACTIVE=1 KJ_APP_ACTION=update KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_REPO_URL=https://github.com/StanXu-symple/auto-x.git \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io KJ_AUTO_X_IMAGE_TAG=sha-<完整SHA> \
KJ_AUTO_X_SKIP_PULL=1 \
AUTO_X_SERVICES=xhs-worker,camoufox-worker,monitor-center,monitor-agent \
bash kejilion.sh app auto-x
```

以上执行原菜单 `2. 更新`。只有确认 `/root/apps` 已包含本次所需安装定义时才使用 `KJ_APPS_SKIP_REFRESH=1`；本次复用 apps/stanxu `24016c6`。安装器仍运行 `alembic upgrade head`，预期数据库保持 `0031_tweet_screenshots`。完成后核对四项 healthy、目标 revision、完整清单、Cookie 摘要、2 GB 内存、512 MB 共享内存、8007 规则，以及 Nacos 三份 JSON 与备份相同。

### 用户重新上传复测

先在 `/runtime-logs` 连接“浏览器 Worker”日志，然后由用户在 `/xhs` 重新发起上传；部署验收不自行调用真实上传或发布。若再次失败，保留任务 ID、北京时间和上述阶段日志，用具体 `failure`、HTTP/预检结果和页面状态继续定位。升级完成及诊断日志可读取不代表原上传故障已经修复，实际业务结果以此次用户复测为准。出现新的部署问题先报告用户确认，保留现场后再处理。

### 2026-10-01 实际升级记录

诊断提交 `8f33ac17b38f1075cb769cf4bcb8490b7f46cb48` 已推 dev、快进合入 main，[Actions 36827068979](https://github.com/StanXu-symple/auto-x/actions/runs/36827068979)成功。52 项针对性测试及 Ruff 检查通过。hn-1 官方三镜像无超时预拉退出 0、revision 全部正确，大浏览器层复用缓存；任务记录为 `/root/auto-x-xhs-upload-diagnostics-pull.log`、`.pid`、`.exit`。

备份位于 `/home/docker/auto-x/backups/pre-xhs-upload-diagnostics-20261001T065125Z/`，时间戳为 UTC，包含 20 个文件。更新前经 Nacos 与 JWT 只读确认浏览器 online、installed=true、active_tasks=0、browser_pool_busy=0。已发布镜像在无网络、无业务卷的短命隔离容器中以假请求验证：`NS_ERROR_NET_RESET` 能进入诊断文件日志，签名参数不出现，四类监听器完整移除；这是诊断回归，不是真实上传结果。

随后通过本节原安装器菜单 2 更新成功。hn-1 四项均 healthy、revision=8f33ac1，无重启或 OOM；migrate 退出 0。原完整清单、Cookie 摘要、浏览器所有持久挂载来源、Memory=2147483648、ShmSize=536870912 保留；8007 公网规则仍只有一条。三份 Nacos JSON 与升级前备份完全相同。

tc-2 经当前 backend 的既有 Nacos 发现及 JWT 日志读取流程验收，两 Worker 各返回 200 行最近日志；XHS online、installed=true。两主机与 13 个监控实例全部 healthy。tc-2 本轮没有升级，数据库保持 `0031_tweet_screenshots`。尚未重新执行真实图片上传或发布，等待用户从页面重新发起并提供结果。
