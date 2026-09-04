#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
RUN_DIR="$ROOT_DIR/tmp/run"
LOG_DIR="$ROOT_DIR/logs"

mkdir -p "$RUN_DIR" "$LOG_DIR"

fail() {
  echo "启动失败：$*" >&2
  exit 1
}

process_matches() {
  local pid="$1"
  local expected="$2"
  local command_line

  kill -0 "$pid" 2>/dev/null || return 1
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"$expected"* ]]
}

start_process() {
  local name="$1"
  local expected="$2"
  local workdir="$3"
  local logfile="$4"
  shift 4

  local pidfile="$RUN_DIR/$name.pid"
  local pid=""

  if [[ -f "$pidfile" ]]; then
    pid="$(tr -d '[:space:]' < "$pidfile")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && process_matches "$pid" "$expected"; then
      echo "[${name}] 已运行，PID: ${pid}"
      return 0
    fi
    echo "[${name}] 清理失效的 PID 文件"
    rm -f "$pidfile"
  fi

  printf '\n===== %s starting at %s =====\n' "$name" "$(date '+%Y-%m-%d %H:%M:%S')" >> "$logfile"
  (
    cd "$workdir"
    nohup "$@" >> "$logfile" 2>&1 &
    echo "$!" > "$pidfile"
  )

  pid="$(tr -d '[:space:]' < "$pidfile")"
  sleep 1
  if ! process_matches "$pid" "$expected"; then
    echo "[${name}] 启动失败，最后 40 行日志：" >&2
    tail -n 40 "$logfile" >&2 || true
    rm -f "$pidfile"
    exit 1
  fi
  echo "[${name}] 已启动，PID: ${pid}，日志: ${logfile}"
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local pidfile="$3"
  local logfile="$4"
  local attempts="${5:-30}"
  local pid

  pid="$(tr -d '[:space:]' < "$pidfile")"
  for ((i = 1; i <= attempts; i++)); do
    if curl --fail --silent --show-error --max-time 2 "$url" >/dev/null 2>&1; then
      echo "[${name}] 健康检查通过：${url}"
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[${name}] 在健康检查完成前退出，最后 40 行日志：" >&2
      tail -n 40 "$logfile" >&2 || true
      rm -f "$pidfile"
      exit 1
    fi
    sleep 1
  done

  echo "[${name}] 健康检查超时：${url}" >&2
  tail -n 40 "$logfile" >&2 || true
  exit 1
}

command -v npm >/dev/null 2>&1 || fail "未找到 npm，请先安装 Node.js。"
command -v curl >/dev/null 2>&1 || fail "未找到 curl。"
[[ -f "$BACKEND_DIR/.env" ]] || fail "缺少 backend/.env。"
[[ -x "$PYTHON_BIN" ]] || fail "缺少 backend/.venv。请先在 backend 目录创建虚拟环境并安装依赖：python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'"
[[ -d "$FRONTEND_DIR/node_modules" ]] || fail "缺少前端依赖。请先执行：cd frontend && npm ci"

echo "[migration] 检查并升级数据库结构"
(
  cd "$BACKEND_DIR"
  "$PYTHON_BIN" -m alembic upgrade head
)

start_process \
  "api" \
  "uvicorn app.main:app" \
  "$BACKEND_DIR" \
  "$LOG_DIR/api.log" \
  "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
wait_for_url "api" "http://127.0.0.1:8000/api/v1/health/ready" "$RUN_DIR/api.pid" "$LOG_DIR/api.log"

start_process \
  "poll-worker" \
  "app.worker" \
  "$BACKEND_DIR" \
  "$LOG_DIR/poll-worker.log" \
  "$PYTHON_BIN" -m app.worker

start_process \
  "ai-worker" \
  "app.ai_worker" \
  "$BACKEND_DIR" \
  "$LOG_DIR/ai-worker.log" \
  "$PYTHON_BIN" -m app.ai_worker

start_process \
  "frontend" \
  "npm run dev" \
  "$FRONTEND_DIR" \
  "$LOG_DIR/frontend.log" \
  npm run dev -- --host 127.0.0.1 --port 5173
wait_for_url "frontend" "http://127.0.0.1:5173/" "$RUN_DIR/frontend.pid" "$LOG_DIR/frontend.log"

echo
echo "X Sentinel 已全部启动："
echo "  前端：    http://127.0.0.1:5173"
echo "  API：     http://127.0.0.1:8000"
echo "  API 文档：http://127.0.0.1:8000/docs"
echo "  日志目录：$LOG_DIR"
echo "关闭服务：./shutdown.sh"
