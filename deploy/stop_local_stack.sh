#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/agustin/municipio-ciudad}"
RUNTIME_DIR="${PROJECT_DIR}/data/runtime"

existing_pid() {
  local name="$1"
  case "${name}" in
    panel)
      pgrep -f "cd ${PROJECT_DIR}.*python3 app.py|${PROJECT_DIR}/app.py" | head -n 1 || true
      ;;
    wsp)
      pgrep -f "cd ${PROJECT_DIR}.*connector:wsp|${PROJECT_DIR}/connectors/whatsapp_group_connector.js" | head -n 1 || true
      ;;
    cloudflare)
      pgrep -f "cloudflared tunnel .*localhost:8091|${PROJECT_DIR}/deploy/run_cloudflare.sh" | head -n 1 || true
      ;;
  esac
}

stop_process() {
  local name="$1"
  local pid_file="${RUNTIME_DIR}/${name}.pid"

  local pid=""
  if [[ -f "${pid_file}" ]]; then
    pid="$(cat "${pid_file}")"
  else
    pid="$(existing_pid "${name}")"
  fi

  if [[ -z "${pid}" ]]; then
    echo "${name}: sin pid"
    return
  fi

  if kill -0 "${pid}" >/dev/null 2>&1; then
    kill "${pid}" >/dev/null 2>&1 || true
    sleep 1
    if kill -0 "${pid}" >/dev/null 2>&1; then
      kill -TERM "-${pid}" >/dev/null 2>&1 || true
    fi
    echo "${name}: detenido"
  else
    echo "${name}: no estaba corriendo"
  fi
  rm -f "${pid_file}"
}

stop_process cloudflare
stop_process wsp
stop_process panel
