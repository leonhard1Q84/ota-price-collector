#!/bin/zsh
set -euo pipefail

load_feishu_env_from_bat() {
  local bat_file="$1"
  local line

  if [[ ! -f "$bat_file" ]]; then
    echo "Missing FEISHU env source: $bat_file" >&2
    return 1
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    case "$line" in
      "set FEISHU_APP_ID="*)
        export FEISHU_APP_ID="${line#set FEISHU_APP_ID=}"
        ;;
      "set FEISHU_APP_SECRET="*)
        export FEISHU_APP_SECRET="${line#set FEISHU_APP_SECRET=}"
        ;;
    esac
  done < "$bat_file"

  if [[ -z "${FEISHU_APP_ID:-}" || -z "${FEISHU_APP_SECRET:-}" ]]; then
    echo "Missing FEISHU_APP_ID or FEISHU_APP_SECRET" >&2
    return 1
  fi
}
