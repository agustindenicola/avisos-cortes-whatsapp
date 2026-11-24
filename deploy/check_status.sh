#!/usr/bin/env bash
set -euo pipefail

HEALTH_URL="${HEALTH_URL:-http://localhost:8091/health}"

echo "== Panel =="
if command -v curl >/dev/null 2>&1; then
  curl -fsS "${HEALTH_URL}" || {
    echo
    echo "ERROR: el panel no responde en ${HEALTH_URL}"
    exit 1
  }
  echo
else
  echo "curl no esta instalado."
fi

echo "== Servicios systemd =="
for service in municipio-ciudad municipio-ciudad-wsp municipio-ciudad-cloudflare; do
  if systemctl list-unit-files "${service}.service" >/dev/null 2>&1; then
    systemctl is-active --quiet "${service}.service" && echo "${service}: activo" || echo "${service}: no activo"
  else
    echo "${service}: no instalado"
  fi
done

echo "== Log WhatsApp =="
if [[ -f "data/group-connector.log" ]]; then
  tail -n 10 data/group-connector.log
else
  echo "No existe data/group-connector.log"
fi

