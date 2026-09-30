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
