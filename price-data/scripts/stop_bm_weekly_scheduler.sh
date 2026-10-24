#!/bin/zsh

set -euo pipefail

PROJECT_ROOT="/Users/admin/PycharmProjects/price-data"
SCHEDULER="$PROJECT_ROOT/src/bm_weekly_scheduler.py"
PID_FILE="$PROJECT_ROOT/src/feishu_schedule_runtime/bm_weekly_scheduler.pid"

if [[ ! -f "$PID_FILE" ]]; then
  print -r -- "未找到 BM 周报调度器 PID 文件"
  exit 0
fi

scheduler_pid="$(<"$PID_FILE")"
if [[ "$scheduler_pid" != <-> ]]; then
  rm -f "$PID_FILE"
  print -r -- "PID 文件内容无效，已清理"
  exit 1
fi

command_line="$(ps -p "$scheduler_pid" -o command= 2>/dev/null || true)"
if [[ -z "$command_line" ]]; then
  rm -f "$PID_FILE"
  print -r -- "BM 周报调度器已停止，已清理旧 PID 文件"
  exit 0
fi
if [[ "$command_line" != *"$SCHEDULER"* ]]; then
  print -r -- "PID $scheduler_pid 不是 BM 周报调度器，未发送停止信号" >&2
  exit 1
fi

kill "$scheduler_pid"
for _ in {1..20}; do
  if ! kill -0 "$scheduler_pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    print -r -- "BM 周报调度器已停止，pid=$scheduler_pid"
    exit 0
  fi
  sleep 0.25
done

print -r -- "BM 周报调度器未在 5 秒内退出，pid=$scheduler_pid" >&2
exit 1
