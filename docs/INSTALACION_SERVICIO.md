# Instalacion como servicio permanente

Esta guia deja el proyecto preparado para arrancar solo al iniciar el equipo.

## 1. Crear configuracion real

Copiar el ejemplo:

```bash
cd /home/agustin/municipio-ciudad
cp .env.example .env
```

Editar `.env` y cambiar como minimo:

```text
CORTES_SECRET
CORTES_USER
CORTES_PASS
GROUP_CONNECTOR_TOKEN
```

El mismo `GROUP_CONNECTOR_TOKEN` lo usa el panel y el conector.

## 2. Instalar dependencias

Python:

```bash
python3 -m pip install -r requirements.txt
```

Node:

```bash
npm install
```

OCR:

```bash
sudo apt install tesseract-ocr tesseract-ocr-spa
```

## 3. Chequear conector WhatsApp

Antes de abrir WhatsApp Web, verificar config, panel local, Chromium y grupos habilitados:

```bash
cd /home/agustin/municipio-ciudad
npm run connector:check
```

Para listar los grupos reales disponibles en la sesion vinculada:

```bash
npm run connector:list-groups
```

Ese listado sirve para completar `config/grupos_wsp_permitidos.json` con `name`, `id` y `operationalGroup`.

## 4. Instalar servicios systemd

```bash
sudo ./deploy/install_services.sh
```

## 5. Ver estado

```bash
systemctl status municipio-ciudad
systemctl status municipio-ciudad-wsp
systemctl status municipio-ciudad-cloudflare
```

Logs:

```bash
journalctl -u municipio-ciudad -f
journalctl -u municipio-ciudad-wsp -f
journalctl -u municipio-ciudad-cloudflare -f
```

Chequeo del panel:

```bash
./deploy/check_status.sh
```

## 6. Instalar backup diario

```bash
sudo ./deploy/install_backup_cron.sh
```

Backup manual:

```bash
./deploy/backup.sh
```

## 7. Cloudflare

El servicio usa `deploy/run_cloudflare.sh`.

Si `.env` no tiene tunel nombrado, levanta un tunel rapido:

```text
cloudflared tunnel --url http://localhost:8091
```

Eso sirve para pruebas, pero no garantiza URL fija.

Para entrega con dominio fijo, configurar en `.env` una de estas opciones:

```text
CLOUDFLARE_TUNNEL_NAME=municipio-ciudad
PUBLIC_BASE_URL=https://mapa.tudominio.com
```

o:

```text
CLOUDFLARE_TUNNEL_TOKEN=token-del-tunel
PUBLIC_BASE_URL=https://mapa.tudominio.com
```

`PUBLIC_BASE_URL` solo se usa para que el panel de estado muestre la URL publica correcta.

## 8. Prueba final de entrega

1. Reiniciar el equipo.
2. Verificar que los tres servicios esten activos.
3. Entrar al panel desde la URL publica.
4. Enviar una imagen desde un grupo de WhatsApp permitido.
5. Confirmar que el corte aparece en el panel.
6. Exportar CSV.
7. Confirmar que el backup del dia existe.
