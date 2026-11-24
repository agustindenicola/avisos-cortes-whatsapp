#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/agustin/municipio-ciudad}"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-/home/agustin/bin/cloudflared}"
LOCAL_URL="${CLOUDFLARE_LOCAL_URL:-http://localhost:8091}"
CLOUDFLARE_PROTOCOL="${CLOUDFLARE_PROTOCOL:-http2}"
ENV_FILE="${PROJECT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

if [[ ! -x "${CLOUDFLARED_BIN}" ]]; then
  echo "No encuentro cloudflared ejecutable en ${CLOUDFLARED_BIN}" >&2
  exit 1
fi

if [[ -n "${CLOUDFLARE_TUNNEL_NAME:-}" ]]; then
  exec "${CLOUDFLARED_BIN}" tunnel --protocol "${CLOUDFLARE_PROTOCOL}" run "${CLOUDFLARE_TUNNEL_NAME}"
fi

if [[ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]]; then
  exec "${CLOUDFLARED_BIN}" tunnel --protocol "${CLOUDFLARE_PROTOCOL}" run --token "${CLOUDFLARE_TUNNEL_TOKEN}"
fi

exec "${CLOUDFLARED_BIN}" tunnel --protocol "${CLOUDFLARE_PROTOCOL}" --url "${LOCAL_URL}"
