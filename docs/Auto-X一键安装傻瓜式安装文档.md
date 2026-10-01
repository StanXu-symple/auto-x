# Auto-X 一键安装傻瓜式文档

本文用于在 `tc-2` 与 `hn-1` 两台 Debian Docker 主机上安装 Auto-X。从既有 tc-1 迁移时，先按[更新文档第十三节](Auto-X一键更新傻瓜式文档.md#十三tc-1-auto-x-服务迁移到-hn-1)备份、迁移 Nacos 节点与授权并复制持久卷，再执行 hn-1 安装；仅更换本地拓扑文件不会覆盖已有 Nacos 配置。`tc-1` 仅保留现有 Nacos 容器及 `/home/docker/nacos/data`、`/home/docker/nacos/logs`。安装前先检查两台主机的 Auto-X 目录和容器状态；已有安装时不要把本流程当作清理命令执行。

## 一、先记住正确命令

正确入口是：

```bash
bash kejilion.sh app auto-x
```

`app` 是 kejilion 的应用市场入口，`auto-x` 是应用名称。不要写成 `bash kejilion.sh auto-x`，后者缺少应用市场入口参数。

执行命令后，`kejilion.sh` 会先询问运行环境，之后才进入 Auto-X 应用菜单。两台服务器分别选择：

| 服务器 | 输入 | 运行环境 |
| --- | --- | --- |
| `tc-2` | `3`（也可直接回车） | `default`，GitHub 直连 |
| `hn-1` | `3` | `default`；显式指定 `KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io` 使用官方镜像 |

这里的 `3` 和 `1` 是 **运行环境选项**，不是 Auto-X 服务编号。出现 `请选择 [3/default]:` 时输入对应数字并回车；随后进入应用菜单，再选择安装。

安装菜单中选择：

```text
1. 安装
```

安装器会自动下载最新的应用配置和 Auto-X `main` 分支源码，生成本机所需的默认值，并将应用配置同步到 Nacos。

## 二、安装前检查

在每台服务器执行：

```bash
cd ~
hostname
docker --version
docker compose version
test -x ~/kejilion.sh && echo "kejilion.sh 已存在"
```

如果提示找不到 `kejilion.sh`，先把 kejilion 脚本放到家目录：

```bash
cd ~
chmod +x kejilion.sh
```

安装器需要服务器能够访问：

- Nacos：`http://118.25.197.211:9999/nacos`
- Auto-X GitHub 仓库或当前配置的 GitHub 代理
- Auto-X 容器镜像仓库

如果实际 Nacos 地址发生变化，以实际地址为准。Nacos 地址可以填写带 `/nacos` 的控制台地址，程序会自动处理接口路径。


hn-1 → tc-2（`43.172.88.37`）必须可达 TCP `5432`、`6379`、`9100`、`9101`；tc-2 → hn-1（`177.2.18.14`）必须可达 `8006`、`8007`、`9101`、`9102`。安全组可按对端公网 IP 的 `/32` 放行。安装前 hn-1 端口未监听返回 Connection refused 是正常现象；启动后须从 tc-2 容器验证 HTTP 调用。

若 hn-1 本机的 8007 健康接口为 200，而 tc-2 连接超时，检查 `iptables -nvL DOCKER-USER --line-numbers` 是否有历史容器 IP 的 DROP 规则；宿主机端口监听或安全组放行不足以排除 Docker 转发拦截。本次用户确认将 8007 对公网开放，恢复与持久化命令见[更新文档的 Docker 防火墙恢复步骤](Auto-X一键更新傻瓜式文档.md#docker-防火墙拦截-8007-的恢复步骤)，浏览器业务 API 仍需认证中心签发的 JWT。

## 三、推荐安装顺序

推荐先安装 `tc-2`，再安装 `hn-1`：

1. `tc-2` 运行 backend、auth-center、Worker、frontend，并作为默认数据服务节点。
2. `hn-1` 运行小红书 Worker、独立 Camoufox 浏览器 Worker、monitor-center、monitor-agent，并从 Nacos 读取共享配置。

两台机器使用同一个 Nacos namespace 和 group，并共用下述三个 Data ID。第一次安装会生成数据库密码、Redis 密码、管理员密码、JWT/X 密钥、认证中心密钥和服务客户端凭据并发布到 Nacos；第二台安装时会复用 Nacos 中已有值。

监控配置单独使用两个 Data ID，均在同一个 `public` namespace、`X_SENTINEL` group 中：

| Data ID | 内容 | 来源与作用 |
| --- | --- | --- |
| `x-sentinel-monitor-topology.json` | 完整的 `services.tc-dual.json` JSON：采集周期、超时、节点、13 个服务 | 首台安装时初始化；之后由 Nacos 管理，monitor-center 和 monitor-agent 启动时直接读取。升级旧部署并选中 camoufox-worker 时，安装器仅补入本节点缺少的浏览器监控记录。 |
| `x-sentinel-monitor-nodes.json` | `{"nodes":{"hn-1":{"advertise_ip":"..."},"tc-2":{"advertise_ip":"..."}}}` | 安装器将节点 ID 和检测到的本机公网注册地址绑定；agent 按本机 `NACOS_ADVERTISE_IP` 找到自己对应的节点 ID。 |

`x-sentinel-config.json` 继续保存其他应用运行配置，不再用来控制监控拓扑。`MONITOR_STALE_SECONDS` 仍用于 backend 判断监控快照是否过期；拓扑自身的 `stale_seconds` 在新的拓扑 Data ID 中。两份新配置是合法 JSON，直接在 Nacos 控制台编辑，不要写 JSON 注释。修改后重启 monitor-center 和相关 monitor-agent 才会加载新值。

`x-sentinel-monitor-topology.json` 的六个顶层字段与仓库中的 `infra/microservices/services.tc-dual.json` 完全一致：`interval_seconds`、`stale_seconds`、`timeout_seconds`、`concurrency`、`nodes`、`services`。`x-sentinel-monitor-nodes.json` 的实际结构是：

```json
{
  "nodes": {
    "hn-1": {"advertise_ip": "177.2.18.14"},
    "tc-2": {"advertise_ip": "43.172.88.37"}
  }
}
```

扩容时先在拓扑 Data ID 的 `nodes` 和 `services` 中增加新节点与服务，再用安装器在新节点首次登记地址。监控服务启动时从 Nacos 读取；节点地址必须与该机器本机 `NACOS_ADVERTISE_IP` 一致。此处使用的是可跨主机访问的注册地址，不是 Docker 容器内部地址。

首次安装时命令中的 `KJ_AUTO_X_MONITOR_NODE_ID` 只用于在 Nacos 中登记这台机器；`KJ_AUTO_X_TOPOLOGY_FILE` 只在 Nacos 尚无拓扑时提供初始内容。它们不再作为本机运行配置。之后运行安装器更新时，Nacos 已有的拓扑和节点地址为准，不会被本地文件覆盖。Nacos 连接地址、命名空间和凭据仍是安装器必须保存的连接引导信息，`NACOS_ADVERTISE_IP` 仍由安装器自动探测。

## 四、在 tc-2 安装核心服务

登录 tc-2：

```bash
ssh tc-2
cd ~
KJ_AUTO_X_MONITOR_NODE_ID=tc-2 \
KJ_AUTO_X_TOPOLOGY_FILE=/home/docker/auto-x/infra/microservices/services.tc-dual.json \
bash kejilion.sh app auto-x
```

在运行环境提示处输入 `3` 并回车，然后在应用菜单中选择 `1` 安装。

服务选择输入：

```text
backend,worker,ai-worker,qq-worker,auth-center,monitor-agent,frontend
```

如果安装器提示 `monitor-agent` 或依赖服务会自动加入，直接确认即可。

出现 `输入应用对外服务端口，回车默认使用8080端口:` 时，`tc-2` 直接回车，使用默认的 `8080`。安装器会把该值写入 `APP_PORT`；若端口已占用，应先查明占用来源，再决定是否改端口。

随后按提示填写 Nacos：

```text
Nacos 地址：      http://118.25.197.211:9999/nacos
Nacos 命名空间：  public
Nacos 用户名：    nacos
Nacos 密码：      输入实际 Nacos 密码
```

密码输入时不会回显。不要把真实密码写入命令行、Git 或本文档。

安装器会自动完成以下动作：

1. 拉取应用配置和 Auto-X 源码。
2. 创建本机 `.env` 和数据目录。
3. 生成 PostgreSQL、Redis、JWT、X Token、管理员和认证中心所需的随机值。
4. 初始化控制面私钥、公钥、客户端授权和服务 secret 文件。
5. 读取 Nacos 中已有配置；已有值优先，本地只补充缺少的值。
6. 将最终配置发布到 `x-sentinel-config.json`。
7. 将完整生效配置写入本机 Compose 引导缓存，并启动所选服务。

安装结束后应看到 `Auto-X 安装完成`。检查服务：

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker compose ls
```

预期至少包含：

```text
x-sentinel-backend-1
x-sentinel-worker-1
x-sentinel-ai-worker-1
x-sentinel-qq-worker-1
x-sentinel-auth-center-1
x-sentinel-monitor-agent-1
x-sentinel-frontend-1
x-sentinel-postgres-1
x-sentinel-redis-1
```

## 五、在 hn-1 安装监控和小红书服务

登录 hn-1（`177.2.18.14`）。它使用官方 `ghcr.io`；首次大镜像可按下文无超时预拉取，再让安装器复用完整缓存：

```bash
ssh hn-1
cd ~
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io \
KJ_AUTO_X_MONITOR_NODE_ID=hn-1 \
KJ_AUTO_X_TOPOLOGY_FILE=/home/docker/auto-x/infra/microservices/services.tc-dual.json \
bash kejilion.sh app auto-x
```

在运行环境提示处输入 `3` 并回车，然后在应用菜单中选择 `1` 安装。这是两个不同的菜单。

`hn-1` 不运行 backend，因此安装器会跳过“应用对外服务端口”提示，直接进入源码下载和 Nacos 配置。

服务选择输入：

```text
xhs-worker,camoufox-worker,monitor-center,monitor-agent
```

不要在 hn-1 手工添加 `auth-center`。认证中心由 tc-2 提供，hn-1 会通过 Nacos 服务发现和共享服务凭据访问它。

Nacos 信息填写为与 tc-2 完全相同的值：

```text
Nacos 地址：      http://118.25.197.211:9999/nacos
Nacos 命名空间：  public
Nacos 用户名：    nacos
Nacos 密码：      输入实际 Nacos 密码
```

安装器会自动探测本机注册地址，并把 hn-1 的 monitor-agent、monitor-center、xhs-worker 和 camoufox-worker 注册到 Nacos。通常不需要手工填写 `NACOS_ADVERTISE_IP`。首次建立这套双节点配置时，上述启动命令中的节点 ID 和双节点拓扑必须分别照写；安装器先写入两份独立的 Nacos 配置，再启动监控服务。以后更新时从 Nacos 读取，节点 ID 不再保存在本机 `.env` 中。

安装结束后检查：

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker compose ls
```

预期包含：

```text
x-sentinel-xhs-worker-1
x-sentinel-camoufox-worker-1
x-sentinel-monitor-center-1
x-sentinel-monitor-agent-1
```

Nacos 仍在 tc-1，迁移和安装时应始终保留：

```bash
ssh tc-1 'docker ps --filter name=^nacos$'
```

`hn-1` 没有部署 frontend，因此本机 `8080` 不提供管理页面。安装器完成提示和应用管理菜单都会显示“此节点未部署 frontend”，不显示 `hn-1:8080`；管理页面请访问 `tc-2` 的 `http://43.172.88.37:8080`。

## 六、安装完成后的快速验收

### 1. 容器健康状态

两台服务器分别执行：

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep x-sentinel
```

服务状态应为 `healthy`。`Exited (0)` 的 migrate 或 log-init 一次性容器属于正常现象。

### 2. HTTP 健康检查

在对应服务器执行：

```bash
curl -fsS http://127.0.0.1:8200/api/v1/health/ready
curl -fsS http://127.0.0.1:9100/health/ready
curl -fsS http://127.0.0.1:9101/health/live
curl -fsS http://127.0.0.1:9102/health/live
curl -fsS http://127.0.0.1:8006/health/live
curl -fsS http://127.0.0.1:8007/health/live
```

不存在的服务端口可以跳过。返回 HTTP 200 即表示对应服务已就绪。

双节点部署还须从 **tc-2 的 backend 容器**检查 hn-1 注册的 xhs-worker 地址，不能只在 hn-1 本机测试 `127.0.0.1:8006`。当前 hn-1 的 Nacos 注册地址为 `177.2.18.14:8006`：

```bash
ssh tc-2
docker exec x-sentinel-backend-1 python -c \
  'import urllib.request; print(urllib.request.urlopen("http://177.2.18.14:8006/health/live", timeout=6).status)'
```

应输出 `200`。如果 hn-1 本机检查正常、tc-2 容器访问超时，则先核对 hn-1 云安全组与主机防火墙是否允许来自 tc-2 的 `8006/TCP`，并确认 Nacos 中 `xsentinel-xhs-worker` 注册的 IP、端口与实际映射一致。此时 `/xhs` 的“保存登录态”请求可能一直等待，前端代理日志出现 `POST /api/v1/xhs/login` 的 `499`；恢复跨节点连接后再重试，不要将本机健康检查视作完整验收。

### 3. Nacos 服务注册

在 Nacos 控制台检查 `X_SENTINEL` group 下是否出现类似实例：

```text
xsentinel-backend
xsentinel-worker
xsentinel-ai-worker
xsentinel-qq-worker
xsentinel-auth-center
xsentinel-xhs-worker
xsentinel-camoufox-worker
xsentinel-monitor-center
xsentinel-monitor-agent-<节点名>
```

`frontend` 是由 Nginx 提供的前端页面，不注册到 Nacos；通过 `tc-2` 的 `http://43.172.88.37:8080` 验证页面可访问。

后端和监控服务通过 Nacos 发现 `xsentinel-auth-center`，本部署不需要单独填写 `SERVICE_AUTH_URL`。双节点监控验收应看到 `xsentinel-monitor-agent-hn-1` 和 `xsentinel-monitor-agent-tc-2` 各有一个健康实例；监控中心的 13 个资源实例应全部为 healthy。xhs-worker 通过 Nacos 发现 `xsentinel-camoufox-worker`，用认证中心签发的服务 JWT 调用其 API；浏览器服务 `/v1/status` 未带令牌返回 401 属于正常鉴权结果。8007/TCP 应允许调用节点访问其 Nacos 公网注册地址。

### 浏览器安装检测与实际运行

容器 healthy 和 `/health/live` 为 200 只确认服务进程。还需从 tc-2 backend 调用 XHS 状态，确认 `installed: true`。Camoufox SDK 0.5 将浏览器放在容器内 `/opt/xsentinel-cache/camoufox/browsers/official/<版本>/`；不能只检查缓存根目录或手工硬编码版本号。安装检测使用 SDK 的当前版本解析，禁止自动下载，核对实际可执行文件；旧缓存不兼容时返回 false 并保留文件。

如果浏览器文件存在但 installed 为 false，按[更新文档第十四节](Auto-X一键更新傻瓜式文档.md#十四camoufox-新版安装路径检测修复)核对并更新。实际浏览器烟测只创建临时本地页面、检查渲染和点击，不使用账号 profile 或发布内容。

### 4. 配置中心内容

Data ID：

```text
x-sentinel-config.json
```

Group：

```text
X_SENTINEL
```

配置中会包含数据库、Redis、管理员、JWT/X、认证中心、provider 和 Worker 运行参数。Nacos 的连接地址、账号和密码仍保留在每台主机的本地引导文件中，因为应用必须先用它们连接 Nacos。

另外检查同一 Group 下的 `x-sentinel-monitor-topology.json` 和 `x-sentinel-monitor-nodes.json`：前者应有 13 个 `services`，后者应有 `hn-1`、`tc-2` 两个节点。编辑监控配置只需改这两个 Data ID；安装器后续更新会保留已有的远端内容，并在部署 camoufox-worker 时补入缺少的浏览器监控记录。浏览器的 5 项运行配置由安装器补充到 `x-sentinel-config.json`，已有值优先，详见[更新文档](Auto-X一键更新傻瓜式文档.md#nacos-缺失配置的升级补齐)。

## 七、常见问题处理

### Nacos 验证失败

确认地址、用户名和密码：

```bash
curl -I http://118.25.197.211:9999/nacos
```

如果 Nacos 使用防火墙，确保 9999/TCP 可访问；Nacos 2.x 服务发现还建议放行 9848/TCP。

### 安装入口不是你的已发布版本

核对 `/root/apps` 的远端、分支、提交和工作区。hn-1 曾使用官方仓库的 main，且存在 16 个额外提交；用户确认后完整保留在 `/root/auto-x-hn1-entry-backup/apps`，再由本地已发布的 apps/stanxu Git bundle 重建 `/root/apps`。原 kejilion.sh 同目录备份；本地 kejilion.sh、auto-x.sh 通过 SSH 标准输入传输，逐个核对 SHA-256。不要 reset 或覆盖未备份仓库。

```bash
git -C /root/apps remote -v
git -C /root/apps status --short --branch
git -C /root/apps rev-parse HEAD
bash -n /root/kejilion.sh
bash -n /root/auto-x.sh
```

只有 `/root/apps` 为目标发布版本、工作区干净且包含 auto-x.conf 时，才能用 `KJ_APPS_SKIP_REFRESH=1` 跳过重复刷新。Auto-X 应用源码仍由安装器从 main 获取。

### hn-1 无超时预拉取大镜像

当前迁移使用已发布运行镜像 `sha-936774a32ec78c7a73987d65b5ea8968f032c80f`。先逐个下载三个镜像，不添加 timeout；建议放在有退出码记录的后台任务中。后台下载进度查看 `/root/auto-x-hn1-images.log`、`.exit` 和 `ctr -n moby content active`；临时层存在不代表进程还活着。

```bash
for name in backend xhs-worker camoufox-worker; do
  docker pull "ghcr.io/stanxu-symple/auto-x-${name}:sha-936774a32ec78c7a73987d65b5ea8968f032c80f" || break
done
```

三个目标镜像全部完整下载、Docker 校验通过后，执行第五节安装入口时加 `KJ_AUTO_X_IMAGE_TAG=sha-936774a32ec78c7a73987d65b5ea8968f032c80f KJ_AUTO_X_SKIP_PULL=1`。此时安装器验证本机完整镜像缓存并直接启动；不会触发它默认的 900 秒拉取上限。单纯把超时设为 0 不受当前安装器支持。镜像下载失败时先报告，不删除全局缓存或业务卷。旧 tc-1 的南京大学/官方/国内代理失败记录保留在[更新文档](Auto-X一键更新傻瓜式文档.md#十二camoufox-独立服务迁移)，当前迁移不在 tc-1 重试下载。

### 提示安装目录已经存在

检查：

```bash
ls -ld /home/docker/auto-x
```

如果这是中断后重试且 `/home/docker/auto-x` 是安装器克隆的 Git 仓库，直接重新执行安装入口。只有目录不是 Auto-X Git 仓库、安装器明确拒绝接管时，才先核对目录内容并处理；不要删除 tc-1 的 `/home/docker/nacos`。

### 服务启动后反复重启

查看对应服务日志：

```bash
docker logs --tail 200 x-sentinel-backend-1
docker logs --tail 200 x-sentinel-auth-center-1
docker logs --tail 200 x-sentinel-monitor-agent-1
```

优先检查 Nacos 是否可达、Nacos 配置是否存在、两台机器的时间是否同步，以及需要的端口是否放行。不要直接手工 `docker run` 替代安装器；修复配置后重新执行同一个安装入口。

若 monitor-center 中某节点暂时显示 unknown，先从 monitor-center 所在主机检查对应 agent 的公网端口及认证中心 `9100`，再从该节点的 monitor-agent 容器检查它能否访问 Nacos 注册的认证中心地址。网络短时超时可能自行恢复，复查监控中心结果后再决定是否修改配置；`SERVICE_AUTH_URL` 保持空值，由 Nacos 服务发现定位认证中心。

### 想重新安装但不想复用旧 Nacos 配置

在 Nacos 控制台删除或清空 `x-sentinel-config.json` 后，再执行安装。这样安装器会重新生成缺失的应用配置；如果保留该 Data ID，安装器会按照“远端优先”继续复用其中已有值。监控拓扑和节点映射分别保存在另外两个 Data ID 中；重置应用配置不会自动重置它们。

## 八、最短操作清单

### tc-2

```bash
ssh tc-2
cd ~
KJ_AUTO_X_MONITOR_NODE_ID=tc-2 \
KJ_AUTO_X_TOPOLOGY_FILE=/home/docker/auto-x/infra/microservices/services.tc-dual.json \
bash kejilion.sh app auto-x
# 运行环境选择 3（default），然后在应用菜单选择 1（安装）
# 选择 backend,worker,ai-worker,qq-worker,auth-center,monitor-agent,frontend
# 应用对外服务端口直接回车，使用 8080
# 填写 Nacos 地址、public、nacos、Nacos 密码
```

### hn-1

```bash
ssh hn-1
cd ~
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.io \
KJ_AUTO_X_MONITOR_NODE_ID=hn-1 \
KJ_AUTO_X_TOPOLOGY_FILE=/home/docker/auto-x/infra/microservices/services.tc-dual.json \
bash kejilion.sh app auto-x
# 运行环境选择 3（default），然后在应用菜单选择 1（安装）
# 选择 xhs-worker,camoufox-worker,monitor-center,monitor-agent；此节点没有对外端口提示
# 填写与 tc-2 相同的 Nacos 地址、命名空间、账号和密码
```

完成后通过 `tc-2` 的 `http://43.172.88.37:8080` 访问管理页面，并按第六节分别检查两台主机的健康状态；不需要手工创建数据库、Redis、账号、密钥或复制控制面文件。

### 历史记录：2026-09-29 已有安装的监控配置更新

以下保留当时 tc-1/tc-2 的实际命令与验收；当前拓扑为 hn-1/tc-2，请使用前文及更新文档第十三节。



先将 Auto-X 代码推到 `dev`、合并到 `main`，等待 GitHub Actions 的 `Publish Auto-X images` 成功。安装器配置需发布到 `apps` 仓库的 `stanxu` 分支；两台机器的 `/root/apps` 都应更新到该分支的目标提交。镜像标签使用 Actions 为 **main 提交完整 SHA** 发布的 `sha-<完整提交 SHA>`，不要凭 `latest` 判断版本。本次验收提交是 `76efad32ebacd601c2600eb3fb0814208223bff4`，其 [Actions 运行记录](https://github.com/StanXu-symple/auto-x/actions/runs/36555604474) 已成功。

先更新 tc-2：

```bash
ssh tc-2
KJ_AUTO_X_IMAGE_TAG=sha-76efad32ebacd601c2600eb3fb0814208223bff4 \
KJ_AUTO_X_MONITOR_NODE_ID=tc-2 \
KJ_AUTO_X_TOPOLOGY_FILE=/home/docker/auto-x/infra/microservices/services.tc-dual.json \
KJ_APP_NONINTERACTIVE=1 KJ_APP_ACTION=update KJ_APP_PORT=8080 \
AUTO_X_SERVICES=backend,worker,ai-worker,qq-worker,auth-center,monitor-agent,frontend \
bash /root/kejilion.sh app auto-x
```

确认 tc-2 的目标服务都为 healthy，再更新 tc-1。以下命令使用已核对的 `/root/apps` 安装器，跳过 tc-1 非交互模式下失败的直连 GitHub 应用列表刷新；源码仍由安装器从 Auto-X `main` 拉取，镜像仍按精确 SHA 拉取：

```bash
ssh tc-1
git -C /root/apps status --short --branch
git -C /root/apps rev-parse --short HEAD
# 确认安装器是目标提交、工作区干净后执行：
KJ_APPS_SKIP_REFRESH=1 \
KJ_AUTO_X_IMAGE_REGISTRY=ghcr.nju.edu.cn \
KJ_AUTO_X_IMAGE_TAG=sha-76efad32ebacd601c2600eb3fb0814208223bff4 \
KJ_AUTO_X_MONITOR_NODE_ID=tc-1 \
KJ_AUTO_X_TOPOLOGY_FILE=/home/docker/auto-x/infra/microservices/services.tc-dual.json \
KJ_APP_NONINTERACTIVE=1 KJ_APP_ACTION=update KJ_APP_PORT=8006 \
AUTO_X_SERVICES=xhs-worker,monitor-center,monitor-agent \
bash /root/kejilion.sh app auto-x
```

`KJ_AUTO_X_MONITOR_NODE_ID` 在本次迁移中只用于把本机检测到的公网地址首次登记到 Nacos。已有节点映射之后，更新可以省略这个参数；安装器不会让旧本地文件覆盖 Nacos 拓扑。后续发布请替换上面的完整 SHA，先确认对应 Actions 运行成功。

2026-09-29 本次更新验收：两台机器源码均为 `76efad3`，目标容器均运行对应 `sha-76efad32ebacd601c2600eb3fb0814208223bff4` 镜像且 healthy；Nacos 中拓扑含 12 个服务、节点映射含 `tc-1` 和 `tc-2`；monitor-center 汇总的两台主机和 12 个实例均为 healthy。


## 公网多节点配置自动化（源码更新说明）

安装器自动通过多个 HTTPS 地址探测当前主机公网 IPv4，检测失败停止安装，不回退到内网地址。每台主机独立保存 `NACOS_ADVERTISE_IP`，不会将某台机器的注册地址覆盖到其他节点。

先安装数据节点（当前安装器以包含 auth-center 的首个节点为默认数据节点）。它将 PostgreSQL、Redis 的公网地址、实际宿主机映射端口及已有账号密码同步至 Nacos。后续业务节点只填写相同的 Nacos 连接信息，安装器会在 Compose 创建容器、执行迁移之前同步这些值。若配置仍只有 postgres/redis 容器名，安装器停止并要求先安装数据节点。

数据节点本机使用 Docker 容器名及容器端口，跨节点使用 Nacos 中的公网端点，避免本机公网回环。新数据节点不会静默覆盖已有另一数据节点的地址。

2026-09-29 实测：`tc-2` 的 backend、worker、ai-worker、qq-worker、auth-center、monitor-agent、frontend、PostgreSQL 和 Redis 容器均为 healthy；`tc-1` 的 xhs-worker、monitor-center、monitor-agent 均为 healthy，Nacos 容器仍运行。`tc-1` 的 PostgreSQL 与 Redis 配置指向 `tc-2`，跨节点连接已验证。云安全组放行仍需通过云平台管理，服务器安装器不能直接保证云侧规则。
