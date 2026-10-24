#!/bin/zsh

set -euo pipefail

PROJECT_ROOT="/Users/admin/PycharmProjects/price-data"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
SCHEDULER="$PROJECT_ROOT/src/bm_weekly_scheduler.py"
RUNTIME_DIR="$PROJECT_ROOT/src/feishu_schedule_runtime"
PID_FILE="$RUNTIME_DIR/bm_weekly_scheduler.pid"
LOG_FILE="$RUNTIME_DIR/bm_weekly_scheduler.log"

mkdir -p "$RUNTIME_DIR"

source "$PROJECT_ROOT/scripts/load_feishu_env.sh"
load_feishu_env_from_bat "$PROJECT_ROOT/run_airport_price_group_sync.bat"

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(<"$PID_FILE")"
  if [[ "$existing_pid" == <-> ]] && kill -0 "$existing_pid" 2>/dev/null; then
    print -r -- "BM 周报调度器已在运行，pid=$existing_pid"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

existing_pid="$(pgrep -f "$SCHEDULER" | head -n 1 || true)"
if [[ -n "$existing_pid" ]]; then
  print -r -- "检测到未由 PID 文件管理的 BM 周报调度器，pid=$existing_pid；请先停止它"
  exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
  print -r -- "Python 虚拟环境不可执行: $PYTHON" >&2
  exit 1
fi

nohup "$PYTHON" "$SCHEDULER" --notify-failure >>"$LOG_FILE" 2>&1 </dev/null &!
scheduler_pid=$!
print -r -- "$scheduler_pid" > "$PID_FILE"

sleep 1
if ! kill -0 "$scheduler_pid" 2>/dev/null; then
  rm -f "$PID_FILE"
  print -r -- "BM 周报调度器启动失败，请检查日志: $LOG_FILE" >&2
  exit 1
fi

print -r -- "BM 周报调度器已从终端后台启动，pid=$scheduler_pid"
print -r -- "日志: $LOG_FILE"
