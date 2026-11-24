# Cierre operativo 2026-07-13

Estado: version local validada y congelada para uso/demo.

## URL actual

```text
https://<tunel>.trycloudflare.com/login
```

El tunel actual es rapido de Cloudflare. Si se reinicia `cloudflared`, la URL puede cambiar salvo que se configure un tunel nombrado o dominio fijo.

## Acceso

Credenciales actuales de demo:

```text
admin / admin123
```

Recomendacion futura: cambiar `CORTES_USER`, `CORTES_PASS` y `CORTES_SECRET` si el link se comparte fuera de un entorno controlado. El backend dejo de imprimir la contraseña en `panel.log`.

## Comandos operativos

Levantar stack completo:

```bash
cd /home/agustin/municipio-ciudad
./deploy/start_local_stack.sh
```

Ver estado y URL publica:

```bash
./deploy/status_local_stack.sh
```

Apagar panel, WhatsApp y Cloudflare:

```bash
./deploy/stop_local_stack.sh
```

Reiniciar solo panel sin tocar el tunel:

```bash
cd /home/agustin/municipio-ciudad
pid="$(pgrep -f 'python3 app.py' | head -n 1 || true)"
if [ -n "$pid" ]; then kill "$pid"; sleep 1; fi
mkdir -p data/runtime
setsid bash -lc 'cd /home/agustin/municipio-ciudad && exec python3 app.py' >> data/runtime/panel.log 2>&1 &
echo $! > data/runtime/panel.pid
```

## Backups creados

Backup operativo final de base, uploads, configuracion y `.env`:

```text
/home/agustin/backups/municipio-ciudad/20260713-141705
```

Snapshot final de codigo fuente sin `node_modules`, sesiones de WhatsApp ni uploads:

```text
/home/agustin/backups/municipio-ciudad/source/municipio-ciudad-source-20260713-141705.tar.gz
```

## QA realizado

- Panel, WhatsApp y Cloudflare activos.
- Link publico responde en `/login`.
- Login carga sin descripcion gris secundaria.
- Inicio responde `200` y la estructura HTML queda fuera de la cabecera.
- Mapa responde `200` e incluye acceso a `Dashboard` y casa.
- Dashboard responde `200`, sin topbar, con acciones en orden: `Exportar Excel | Ver mapa | casa`.
- Exportacion Excel responde `200` y genera `.xlsx` valido.
- Exportacion CSV responde `200` y genera CSV valido.
- `/health`, `/api/cortes-activos` y `/api/cortes-movimientos` responden OK.
- `UNION VECINAL BARRIO LA CAMPIÑA II` ya no aparece en `data/instituciones.json`.

## Pendientes recomendados

- Configurar tunel Cloudflare nombrado o dominio propio para URL fija.
- Definir politica de rotacion de contraseña.
- Hacer backup antes de cada cambio grande.
- Si se comparte con terceros, evitar usar el tunel rapido como entorno permanente.
