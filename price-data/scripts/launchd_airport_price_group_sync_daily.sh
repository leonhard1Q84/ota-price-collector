#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/admin/PycharmProjects/price-data"
cd "$PROJECT_DIR"

source "$PROJECT_DIR/scripts/load_feishu_env.sh"
load_feishu_env_from_bat "$PROJECT_DIR/run_airport_price_group_sync.bat"

mkdir -p "$PROJECT_DIR/src/feishu_schedule_runtime"
exec "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/src/airport_price_group_sync.py"
