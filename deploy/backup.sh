#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/agustin/municipio-ciudad}"
BACKUP_DIR="${BACKUP_DIR:-/home/agustin/backups/municipio-ciudad}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="${BACKUP_DIR}/${STAMP}"

mkdir -p "${DEST}"

if [[ -f "${PROJECT_DIR}/data/cortes.db" ]]; then
  sqlite3 "${PROJECT_DIR}/data/cortes.db" ".backup '${DEST}/cortes.db'"
fi

if [[ -d "${PROJECT_DIR}/uploads" ]]; then
  tar -C "${PROJECT_DIR}" -czf "${DEST}/uploads.tar.gz" uploads
fi

if [[ -f "${PROJECT_DIR}/config/grupos_wsp_permitidos.json" ]]; then
  mkdir -p "${DEST}/config"
  cp "${PROJECT_DIR}/config/grupos_wsp_permitidos.json" "${DEST}/config/"
fi

if [[ -f "${PROJECT_DIR}/.env" ]]; then
  cp "${PROJECT_DIR}/.env" "${DEST}/env.backup"
  chmod 600 "${DEST}/env.backup"
fi

find "${BACKUP_DIR}" -mindepth 1 -maxdepth 1 -type d -mtime +14 -print -exec rm -rf {} \;

echo "Backup creado en ${DEST}"

