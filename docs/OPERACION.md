# Manual operativo

## Acceso

URL local:

```text
http://localhost:8091/login
```

URL publica:

```text
https://dominio-configurado/login
```

El usuario y la contrasena se configuran en el archivo de entorno del servicio:

- `CORTES_USER`
- `CORTES_PASS`

## Uso diario

1. Entrar al panel.
2. Revisar cortes activos y movimientos recientes.
3. Abrir el mapa para ver zona, rubro, impacto e historico real.
4. Abrir el detalle de cada corte para ver evidencia, zona, estado y notas.
5. Corregir manualmente solo si un aviso automatico quedo pendiente o mal clasificado.
6. Exportar CSV/XLSX cuando se necesite reporte operativo.

## WhatsApp

El conector escucha solo los grupos habilitados en:

```text
config/grupos_wsp_permitidos.json
```

Cuando llega texto o imagen desde un grupo permitido:

1. El conector recibe el mensaje.
2. Si hay imagen, la descarga.
3. Envia texto e imagen al panel.
4. El panel guarda evidencia en `uploads/` cuando corresponde.
5. El panel detecta tipo de aviso, grupo/zona y estado.
6. El panel crea, actualiza o cierra el corte.

Si WhatsApp se desconecta, el conector puede generar un QR en:

```text
data/whatsapp-qr.png
```

Ese QR se escanea desde WhatsApp en "Dispositivos vinculados".

## Chequeos rapidos

Panel:

```text
http://localhost:8091/health
```

Debe responder un JSON con:

```json
{"ok": true}
```

Log del conector:

```bash
tail -n 80 data/group-connector.log
```

Base de datos:

```text
data/cortes.db
```

Evidencias:

```text
uploads/
```

## Backup minimo

Respaldar todos los dias:

- `data/cortes.db`
- `uploads/`
- `config/grupos_wsp_permitidos.json`
- `.wwebjs_auth/` si se quiere conservar la sesion de WhatsApp Web

## Reinicio esperado

En una instalacion final, estos procesos deben iniciar solos:

- Panel Python.
- Conector de WhatsApp.
- Tunel Cloudflare.

Si alguno no esta activo, el sistema puede abrir pero no recibir WhatsApp, o puede funcionar localmente pero no tener URL publica.
