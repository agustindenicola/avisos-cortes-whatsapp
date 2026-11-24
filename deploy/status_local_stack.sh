#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/agustin/municipio-ciudad}"
RUNTIME_DIR="${PROJECT_DIR}/data/runtime"
PANEL_URL="${PANEL_URL:-http://127.0.0.1:8091}"
PANEL_PORT="${PANEL_URL##*:}"
PANEL_PORT="${PANEL_PORT%%/*}"

is_running() {
  local pid_file="$1"
  [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" >/dev/null 2>&1
}

existing_pid() {
  local name="$1"
  case "${name}" in
    panel)
      if curl -fsS "${PANEL_URL}/health" >/dev/null 2>&1; then
        lsof -tiTCP:"${PANEL_PORT}" -sTCP:LISTEN | head -n 1 || true
      else
        pgrep -f "cd ${PROJECT_DIR}.*python3 app.py|${PROJECT_DIR}/app.py" | head -n 1 || true
      fi
      ;;
    wsp)
      pgrep -f "cd ${PROJECT_DIR}.*connector:wsp|${PROJECT_DIR}/connectors/whatsapp_group_connector.js" | head -n 1 || true
      ;;
    cloudflare)
      pgrep -f "cloudflared tunnel .*localhost:8091|${PROJECT_DIR}/deploy/run_cloudflare.sh" | head -n 1 || true
      ;;
  esac
}

print_process() {
  local name="$1"
  local pid_file="${RUNTIME_DIR}/${name}.pid"
  if is_running "${pid_file}"; then
    echo "${name}: activo (pid $(cat "${pid_file}"))"
  else
    local pid
    pid="$(existing_pid "${name}")"
    if [[ -n "${pid}" ]]; then
      echo "${name}: activo (pid ${pid})"
    else
      echo "${name}: inactivo"
    fi
  fi
}

print_process panel
print_process wsp
print_process cloudflare

cloudflare_active=false
if is_running "${RUNTIME_DIR}/cloudflare.pid" || [[ -n "$(existing_pid cloudflare)" ]]; then
  cloudflare_active=true
fi

echo
if command -v curl >/dev/null 2>&1 && curl -fsS "${PANEL_URL}/health" >/dev/null 2>&1; then
  echo "panel local: ${PANEL_URL}/login"
else
  echo "panel local: no responde"
fi

if [[ -f "${PROJECT_DIR}/data/group-connector-state.json" ]]; then
  echo
  echo "whatsapp:"
  python3 -m json.tool "${PROJECT_DIR}/data/group-connector-state.json" 2>/dev/null || cat "${PROJECT_DIR}/data/group-connector-state.json"
fi

public_url=""
if [[ "${cloudflare_active}" == "true" ]]; then
  if [[ -f "${RUNTIME_DIR}/cloudflare.log" ]]; then
    public_url="$(grep -Eo 'https://[^ |]+\.trycloudflare\.com' "${RUNTIME_DIR}/cloudflare.log" | tail -n 1 || true)"
    if [[ -n "${public_url}" ]]; then
      echo "${public_url}" > "${RUNTIME_DIR}/cloudflare.url"
    fi
  fi
  if [[ -z "${public_url}" && -f "${RUNTIME_DIR}/cloudflare.url" ]]; then
    public_url="$(cat "${RUNTIME_DIR}/cloudflare.url")"
  fi
else
  rm -f "${RUNTIME_DIR}/cloudflare.url"
fi

echo
if [[ -n "${public_url}" ]]; then
  echo "cloudflare: ${public_url}/login"
else
  echo "cloudflare: sin URL publica actual"
  if [[ -f "${RUNTIME_DIR}/cloudflare.log" ]]; then
    echo "ultimas lineas del log:"
    tail -n 12 "${RUNTIME_DIR}/cloudflare.log"
  fi
fi
