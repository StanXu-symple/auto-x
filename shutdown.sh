#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/tmp/run"

process_matches() {
  local pid="$1"
  local expected="$2"
  local command_line

  kill -0 "$pid" 2>/dev/null || return 1
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"$expected"* ]]
}

signal_tree() {
  local pid="$1"
  local signal="$2"
  local children

  children="$(pgrep -P "$pid" 2>/dev/null || true)"
  for child in $children; do
    signal_tree "$child" "$signal"
  done
  kill "-$signal" "$pid" 2>/dev/null || true
}

stop_process() {
  local name="$1"
  local expected="$2"
  local pidfile="$RUN_DIR/$name.pid"
  local pid

  if [[ ! -f "$pidfile" ]]; then
    echo "[${name}] 未运行（没有 PID 文件）"
    return 0
  fi

  pid="$(tr -d '[:space:]' < "$pidfile")"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    echo "[${name}] PID 文件无效，已清理"
    rm -f "$pidfile"
    return 0
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    echo "[${name}] 已停止，清理失效的 PID 文件"
    rm -f "$pidfile"
    return 0
  fi

  if ! process_matches "$pid" "$expected"; then
    echo "[${name}] PID ${pid} 不属于本项目，拒绝终止并清理 PID 文件" >&2
    rm -f "$pidfile"
    return 1
  fi

  echo "[${name}] 正在停止，PID: ${pid}"
  signal_tree "$pid" TERM

  for ((i = 1; i <= 50; i++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pidfile"
      echo "[${name}] 已停止"
      return 0
    fi
    sleep 0.2
  done

  echo "[${name}] 未在 10 秒内退出，执行强制停止" >&2
  signal_tree "$pid" KILL
  rm -f "$pidfile"
}

if [[ ! -d "$RUN_DIR" ]]; then
  echo "没有发现运行中的项目服务。"
  exit 0
fi

# 与启动顺序相反，先停止入口服务，再停止后台任务和 API。
shutdown_status=0
stop_process "frontend" "npm run dev" || shutdown_status=1
stop_process "xhs-worker" "app.xhs_worker" || shutdown_status=1
stop_process "ai-worker" "app.ai_worker" || shutdown_status=1
stop_process "poll-worker" "app.worker" || shutdown_status=1
stop_process "api" "uvicorn app.main:app" || shutdown_status=1

rmdir "$RUN_DIR" 2>/dev/null || true
if [[ "$shutdown_status" -eq 0 ]]; then
  echo "X Sentinel 前后端服务已全部停止。"
else
  echo "关闭流程已完成，但存在需要人工检查的 PID 文件异常。" >&2
fi
exit "$shutdown_status"
