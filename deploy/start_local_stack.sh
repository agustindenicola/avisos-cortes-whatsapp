#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/agustin/municipio-ciudad}"
RUNTIME_DIR="${PROJECT_DIR}/data/runtime"
PANEL_URL="${PANEL_URL:-http://127.0.0.1:8091}"
PANEL_PORT="${PANEL_URL##*:}"
PANEL_PORT="${PANEL_PORT%%/*}"

mkdir -p "${RUNTIME_DIR}"
cd "${PROJECT_DIR}"

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

start_process() {
  local name="$1"
  local pid_file="${RUNTIME_DIR}/${name}.pid"
  local log_file="${RUNTIME_DIR}/${name}.log"
  shift

  if is_running "${pid_file}"; then
    echo "${name}: ya esta corriendo (pid $(cat "${pid_file}"))"
    return
  fi

  local existing
  existing="$(existing_pid "${name}")"
  if [[ -n "${existing}" ]]; then
    echo "${existing}" > "${pid_file}"
    echo "${name}: ya estaba corriendo (pid ${existing})"
    return
  fi

  if [[ "${name}" == "cloudflare" ]]; then
    rm -f "${RUNTIME_DIR}/cloudflare.url"
  fi
  : > "${log_file}"
  setsid bash -lc "cd '${PROJECT_DIR}' && exec $*" >> "${log_file}" 2>&1 &
  echo "$!" > "${pid_file}"
  echo "${name}: iniciado (pid $(cat "${pid_file}"), log ${log_file})"
}

cloudflare_public_url() {
  local log_file="${RUNTIME_DIR}/cloudflare.log"
  [[ -f "${log_file}" ]] || return 0
  grep -Eo 'https://[^ |]+\.trycloudflare\.com' "${log_file}" | tail -n 1 || true
}

wait_for_panel() {
  local tries=30
  while (( tries > 0 )); do
    if curl -fsS "${PANEL_URL}/health" >/dev/null 2>&1; then
      echo "panel: responde en ${PANEL_URL}/login"
      return 0
    fi
    tries=$((tries - 1))
    sleep 1
  done

  echo "ERROR: el panel no respondio en ${PANEL_URL}/health"
  tail -n 40 "${RUNTIME_DIR}/panel.log" || true
  exit 1
}

wait_for_cloudflare() {
  local tries=45
  local public_url=""
  while (( tries > 0 )); do
    public_url="$(cloudflare_public_url)"
    if [[ -n "${public_url}" ]]; then
      echo "${public_url}" > "${RUNTIME_DIR}/cloudflare.url"
      echo "cloudflare: responde en ${public_url}/login"
      return 0
    fi
    if ! is_running "${RUNTIME_DIR}/cloudflare.pid" && [[ -z "$(existing_pid cloudflare)" ]]; then
      echo "ERROR: cloudflare no esta corriendo"
      tail -n 40 "${RUNTIME_DIR}/cloudflare.log" || true
      exit 1
    fi
    tries=$((tries - 1))
    sleep 1
  done

  echo "AVISO: cloudflare todavia no publico URL. Revisa con:"
  echo "  ${PROJECT_DIR}/deploy/status_local_stack.sh"
}

start_process panel python3 app.py
wait_for_panel
start_process wsp "npm run connector:wsp"
start_process cloudflare "bash deploy/run_cloudflare.sh"
wait_for_cloudflare

echo
"${PROJECT_DIR}/deploy/status_local_stack.sh"
