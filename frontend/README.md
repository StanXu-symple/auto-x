# X Sentinel Console

X Sentinel 的 Vue 3 管理控制台，用于管理 X/Twitter 监听账号、查看采集内容与轮询记录，并监控服务器、MySQL、Redis 和 Worker 状态。

## 技术栈

- Vue 3 + TypeScript + Vite
- Element Plus（深色主题定制）
- Pinia + Vue Router
- Axios + lucide-vue-next

## 本地运行

```bash
cp .env.example .env
npm ci
npm run dev
```

默认开发地址为 `http://localhost:5173`，`/api` 请求会代理到 `VITE_PROXY_TARGET`（默认 `http://localhost:8000`）。

## 环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `/api/v1` | 浏览器访问的 API 前缀 |
| `VITE_PROXY_TARGET` | `http://localhost:8000` | Vite 开发代理目标 |
| `VITE_APP_NAME` | `X Sentinel` | 应用名称 |

## 常用命令

```bash
npm run dev         # 启动开发服务器
npm run type-check  # TypeScript / Vue 类型检查
npm run build       # 生产构建
npm run preview     # 预览生产构建
```

## Docker

`Dockerfile` 使用 Node 22 构建静态资源，并通过 Nginx 80 端口提供服务。Nginx 将 `/api/` 反向代理到 `backend:8000`，并为 Vue Router 配置了 SPA fallback。

```bash
docker build -t x-sentinel-frontend .
docker run --rm -p 8080:80 x-sentinel-frontend
```
