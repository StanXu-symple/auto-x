# Auto-X 一键安装傻瓜式文档

本文用于在两台 Debian Docker 主机上重新安装 Auto-X。当前清理结果如下：

- `tc-1`：已删除 Auto-X 全部容器、网络、镜像、安装目录和源码目录；保留 Nacos 容器及 `/home/docker/nacos/data`、`/home/docker/nacos/logs`。
- `tc-2`：已删除 Auto-X 全部容器、网络、镜像、安装目录和源码目录。
- 两台主机的 `/root/apps` 中原有 Auto-X 配置也已移除，下一次运行会重新拉取应用配置。

## 一、先记住正确命令

正确入口是：

```bash
bash kejilion.sh app auto-x
```

`app` 是 kejilion 的应用市场入口，`auto-x` 是应用名称。不要写成 `bash kejilion.sh auto-x`，后者缺少应用市场入口参数。

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

## 三、推荐安装顺序

推荐先安装 `tc-2`，再安装 `tc-1`：

1. `tc-2` 运行 backend、auth-center、Worker、frontend，并作为默认数据服务节点。
2. `tc-1` 运行小红书 Worker、monitor-center、monitor-agent，并从 Nacos 读取共享配置。

两台机器使用同一个 Nacos Data ID、namespace 和 group。第一次安装会生成数据库密码、Redis 密码、管理员密码、JWT/X 密钥、认证中心密钥和服务客户端凭据并发布到 Nacos；第二台安装时会复用 Nacos 中已有值。

## 四、在 tc-2 安装核心服务

登录 tc-2：

```bash
ssh tc-2
cd ~
bash kejilion.sh app auto-x
```

在菜单中选择 `1` 安装。

服务选择输入：

```text
backend,worker,ai-worker,qq-worker,auth-center,monitor-agent,frontend
```

如果安装器提示 `monitor-agent` 或依赖服务会自动加入，直接确认即可。

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

## 五、在 tc-1 安装监控和小红书服务

登录 tc-1：

```bash
ssh tc-1
cd ~
bash kejilion.sh app auto-x
```

选择 `1` 安装。

服务选择输入：

```text
xhs-worker,monitor-center,monitor-agent
```

不要在 tc-1 手工添加 `auth-center`。认证中心由 tc-2 提供，tc-1 会通过 Nacos 服务发现和共享服务凭据访问它。

Nacos 信息填写为与 tc-2 完全相同的值：

```text
Nacos 地址：      http://118.25.197.211:9999/nacos
Nacos 命名空间：  public
Nacos 用户名：    nacos
Nacos 密码：      输入实际 Nacos 密码
```

安装器会自动探测本机注册地址，并把 tc-1 的 monitor-agent、monitor-center 和 xhs-worker 注册到 Nacos。通常不需要手工填写 `NACOS_ADVERTISE_IP`。

安装结束后检查：

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker compose ls
```

预期包含：

```text
x-sentinel-xhs-worker-1
x-sentinel-monitor-center-1
x-sentinel-monitor-agent-1
```

tc-1 的 Nacos 容器应始终存在：

```bash
docker ps --filter name=^nacos$
```

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
curl -fsS http://127.0.0.1:8200/health/ready
curl -fsS http://127.0.0.1:9100/health/ready
curl -fsS http://127.0.0.1:9101/health/live
curl -fsS http://127.0.0.1:9102/health/live
curl -fsS http://127.0.0.1:8006/health/live
```

不存在的服务端口可以跳过。返回 HTTP 200 即表示对应服务已就绪。

### 3. Nacos 服务注册

在 Nacos 控制台检查 `X_SENTINEL` group 下是否出现类似实例：

```text
xsentinel-backend
xsentinel-worker
xsentinel-ai-worker
xsentinel-qq-worker
xsentinel-auth-center
xsentinel-frontend
xsentinel-xhs-worker
xsentinel-monitor-center
xsentinel-monitor-agent-<节点名>
```

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

## 七、常见问题处理

### Nacos 验证失败

确认地址、用户名和密码：

```bash
curl -I http://118.25.197.211:9999/nacos
```

如果 Nacos 使用防火墙，确保 9999/TCP 可访问；Nacos 2.x 服务发现还建议放行 9848/TCP。

### 应用列表下载失败

确认服务器可以访问 GitHub 或配置的代理。应用列表目录已在本次清理中删除，正常情况下重新执行命令会自动重新克隆，不需要手工上传 `auto-x.conf`。

### 提示安装目录已经存在

检查：

```bash
ls -ld /home/docker/auto-x
```

确认确实要重新安装时，先停止并删除该目录对应的 Auto-X 容器和目录，再重新执行安装入口。不要删除 `/home/docker/nacos`，tc-1 的 Nacos 数据就在这里。

### 服务启动后反复重启

查看对应服务日志：

```bash
docker logs --tail 200 x-sentinel-backend-1
docker logs --tail 200 x-sentinel-auth-center-1
docker logs --tail 200 x-sentinel-monitor-agent-1
```

优先检查 Nacos 是否可达、Nacos 配置是否存在、两台机器的时间是否同步，以及需要的端口是否放行。不要直接手工 `docker run` 替代安装器；修复配置后重新执行同一个安装入口。

### 想重新安装但不想复用旧 Nacos 配置

在 Nacos 控制台删除或清空 `x-sentinel-config.json` 后，再执行安装。这样安装器会重新生成缺失配置；如果保留该 Data ID，安装器会按照“远端优先”继续复用其中已有值。

## 八、最短操作清单

### tc-2

```bash
ssh tc-2
cd ~
bash kejilion.sh app auto-x
# 选择 1
# 选择 backend,worker,ai-worker,qq-worker,auth-center,monitor-agent,frontend
# 填写 Nacos 地址、public、nacos、Nacos 密码
```

### tc-1

```bash
ssh tc-1
cd ~
bash kejilion.sh app auto-x
# 选择 1
# 选择 xhs-worker,monitor-center,monitor-agent
# 填写与 tc-2 相同的 Nacos 地址、命名空间、账号和密码
```

完成后只需要在页面验证服务状态，不需要手工创建数据库、Redis、账号、密钥或复制控制面文件。


## 公网多节点配置自动化（源码更新说明）

安装器自动通过多个 HTTPS 地址探测当前主机公网 IPv4，检测失败停止安装，不回退到内网地址。每台主机独立保存 `NACOS_ADVERTISE_IP`，不会将某台机器的注册地址覆盖到其他节点。

先安装数据节点（当前安装器以包含 auth-center 的首个节点为默认数据节点）。它将 PostgreSQL、Redis 的公网地址、实际宿主机映射端口及已有账号密码同步至 Nacos。后续业务节点只填写相同的 Nacos 连接信息，安装器会在 Compose 创建容器、执行迁移之前同步这些值。若配置仍只有 postgres/redis 容器名，安装器停止并要求先安装数据节点。

数据节点本机使用 Docker 容器名及容器端口，跨节点使用 Nacos 中的公网端点，避免本机公网回环。新数据节点不会静默覆盖已有另一数据节点的地址。

本次为源码修改及本地测试通过，尚未完成两台服务器重新部署验收。云安全组放行不能仅凭服务器脚本保证；需有云 API 凭据才能自动管理云侧规则。
