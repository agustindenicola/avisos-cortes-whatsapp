# Despliegue en VPS

Objetivo: dejar el sistema corriendo en una maquina encendida 24/7 para no depender de la computadora local.

## Servidor recomendado para empezar

- Ubuntu 22.04 o 24.04 LTS.
- 1 o 2 vCPU.
- 2 GB de RAM minimo.
- 20 GB de disco minimo.
- Acceso SSH.

Para pruebas y primera entrega, SQLite alcanza. Si el uso crece mucho, se evalua migrar base de datos.

## Orden de trabajo

1. Crear VPS.
2. Entrar por SSH.
3. Instalar dependencias del sistema.
4. Copiar el proyecto.
5. Crear `.env` real.
6. Instalar dependencias Python y Node.
7. Iniciar panel local.
8. Vincular WhatsApp con QR.
9. Configurar Cloudflare Tunnel permanente.
10. Instalar servicios systemd.
11. Instalar backup diario.
12. Probar reinicio completo.

## Dependencias de sistema

```bash
sudo apt update
sudo apt install -y python3 python3-pip nodejs npm sqlite3 curl tar tesseract-ocr tesseract-ocr-spa
```

Instalar `cloudflared` segun la documentacion oficial de Cloudflare o copiar el binario ya usado si corresponde.

## Copia del proyecto

Desde la computadora local:

```bash
rsync -av --exclude node_modules --exclude .wwebjs_cache /home/agustin/municipio-ciudad/ usuario@IP_DEL_SERVIDOR:/home/agustin/municipio-ciudad/
```

Si se quiere conservar la sesion actual de WhatsApp, no excluir `.wwebjs_auth`. Si se prefiere vincular de cero en el servidor, se puede no copiarla.

Nota: las plantillas de `deploy/systemd/` asumen que el proyecto vive en `/home/agustin/municipio-ciudad`. Si el VPS usa otro usuario o ruta, hay que reemplazar esa ruta en:

- `deploy/systemd/municipio-ciudad.service`
- `deploy/systemd/municipio-ciudad-wsp.service`
- `deploy/systemd/municipio-ciudad-cloudflare.service`
- `deploy/install_services.sh`
- `deploy/install_backup_cron.sh`

## Configuracion

En el servidor:

```bash
cd /home/agustin/municipio-ciudad
cp .env.example .env
nano .env
```

Cambiar minimo:

- `CORTES_SECRET`
- `CORTES_USER`
- `CORTES_PASS`
- `GROUP_CONNECTOR_TOKEN`

## Instalacion de dependencias del proyecto

```bash
cd /home/agustin/municipio-ciudad
python3 -m pip install -r requirements.txt
npm install
```

## Prueba manual

Panel:

```bash
python3 app.py
```

En otra terminal:

```bash
curl http://localhost:8091/health
```

Conector WhatsApp:

```bash
npm run connector:wsp
```

Si aparece QR, escanearlo desde WhatsApp.

## Servicios permanentes

```bash
sudo ./deploy/install_services.sh
sudo ./deploy/install_backup_cron.sh
./deploy/check_status.sh
```

## Cloudflare permanente

El servicio `municipio-ciudad-cloudflare` ejecuta `deploy/run_cloudflare.sh`.

Para prueba rapida, alcanza con no configurar tunel nombrado. El script ejecuta:

```bash
cloudflared tunnel --url http://localhost:8091
```

Para entrega, usar tunel nombrado o token de tunel y dominio fijo.

Pendiente de configurar cuando se tenga:

- cuenta de Cloudflare;
- dominio o subdominio;
- acceso para crear el tunel.

Variables relevantes en `.env`:

```text
CLOUDFLARED_BIN=/home/agustin/bin/cloudflared
CLOUDFLARE_LOCAL_URL=http://localhost:8091
CLOUDFLARE_TUNNEL_NAME=municipio-ciudad
CLOUDFLARE_TUNNEL_TOKEN=
PUBLIC_BASE_URL=https://mapa.tudominio.com
```

Si se usa `CLOUDFLARE_TUNNEL_TOKEN`, no hace falta que el servidor tenga credenciales locales del tunel.

## Prueba de aceptacion

- Reiniciar el VPS.
- Ver que el panel abre desde dominio publico.
- Ver que `/health` responde.
- Enviar imagen desde grupo permitido.
- Confirmar que aparece el corte.
- Exportar CSV.
- Confirmar que se genero backup.
