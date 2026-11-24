#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/agustin/municipio-ciudad"
SYSTEMD_DIR="/etc/systemd/system"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Este script debe ejecutarse con sudo."
  exit 1
fi

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
  echo "Falta ${PROJECT_DIR}/.env."
  echo "Copiar .env.example a .env y configurar credenciales reales antes de instalar."
  exit 1
fi

cp "${PROJECT_DIR}/deploy/systemd/municipio-ciudad.service" "${SYSTEMD_DIR}/"
cp "${PROJECT_DIR}/deploy/systemd/municipio-ciudad-wsp.service" "${SYSTEMD_DIR}/"
cp "${PROJECT_DIR}/deploy/systemd/municipio-ciudad-cloudflare.service" "${SYSTEMD_DIR}/"

systemctl daemon-reload
systemctl enable municipio-ciudad.service
systemctl enable municipio-ciudad-wsp.service
systemctl enable municipio-ciudad-cloudflare.service

systemctl restart municipio-ciudad.service
systemctl restart municipio-ciudad-wsp.service
systemctl restart municipio-ciudad-cloudflare.service

echo "Servicios instalados y reiniciados."
echo "Revisar con:"
echo "  systemctl status municipio-ciudad"
echo "  systemctl status municipio-ciudad-wsp"
echo "  systemctl status municipio-ciudad-cloudflare"

