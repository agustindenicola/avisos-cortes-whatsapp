#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/agustin/municipio-ciudad"
CRON_FILE="/etc/cron.d/municipio-ciudad-backup"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Este script debe ejecutarse con sudo."
  exit 1
fi

cat > "${CRON_FILE}" <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
15 3 * * * agustin PROJECT_DIR=${PROJECT_DIR} BACKUP_DIR=/home/agustin/backups/municipio-ciudad ${PROJECT_DIR}/deploy/backup.sh >> /home/agustin/backups/municipio-ciudad/backup.log 2>&1
CRON

chmod 644 "${CRON_FILE}"
echo "Backup diario instalado en ${CRON_FILE}."

