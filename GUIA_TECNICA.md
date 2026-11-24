# Guia tecnica del proyecto

## Resumen corto

El proyecto es un observatorio municipal para visualizar instituciones, zonas y cortes de servicio sobre un mapa interactivo de Ciudad. Combina una pagina web con mapa, un panel administrativo con login, una base local para registrar cortes y un conector de WhatsApp que recibe imagenes desde grupos autorizados.

La idea principal es transformar informacion dispersa, como listados de instituciones, zonas, grupos de WhatsApp y avisos de cortes, en una herramienta operativa para consultar, cargar, priorizar y seguir eventos.

## Tecnologias usadas

### Frontend

- HTML: estructura de las pantallas web.
- CSS: diseno visual, layout responsive, colores, paneles y estilos del mapa.
- JavaScript: interaccion del mapa, filtros, carga de datos, eventos y comportamiento de la interfaz.
- Leaflet: libreria principal para mostrar mapas interactivos.
- Leaflet MarkerCluster: agrupacion visual de marcadores cuando hay muchas instituciones cercanas.
- OpenStreetMap: mapa base usado por Leaflet.

Archivos principales:

- `index.html`: portada operativa de cortes activos.
- `mapa.html`: mapa interactivo completo.
- `static/app.css`: estilos del panel con login y vistas administrativas.

### Backend

- Python 3: servidor principal del panel administrativo.
- WSGI simple server: servidor HTTP liviano incluido en Python para ejecutar la app local.
- SQLite: base de datos local para guardar cortes, estados, evidencias y datos operativos.
- Cookies firmadas con HMAC: mecanismo simple de sesion para el login administrativo.

Archivo principal:

- `app.py`: contiene el servidor, login, rutas, panel, carga de cortes, exportacion CSV, API interna y procesamiento de imagenes.

### Automatizacion y procesamiento de datos

- Python: scripts para convertir, limpiar y preparar datos geograficos.
- GeoJSON: formato usado para guardar zonas, barrios, perimetros y capas geograficas.
- CSV / JSON / XLSX: formatos de entrada y salida para datos tabulares.
- KML / SHP / GPKG: formatos geograficos procesados por herramientas del proyecto.
- OpenStreetMap / Nominatim: geocodificacion de direcciones cuando se generan datos del mapa.

Carpeta principal:

- `tools/`: scripts de preparacion de datos, conversion geografica y armado de capas.

Ejemplos:

- `tools/build_map.py`: genera `data/instituciones.json`.
- `tools/build_whatsapp_groups.py`: prepara informacion de grupos.
- `tools/build_municipal_zones.py`: arma zonas municipales.
- `tools/shp_to_geojson.py`, `tools/kml_to_geojson.py`, `tools/gpkg_to_geojson.py`: convierten formatos geograficos a GeoJSON.

### WhatsApp

- Node.js: entorno usado para correr el conector.
- whatsapp-web.js: libreria que se conecta a WhatsApp Web.
- Puppeteer / Chromium: navegador automatizado que usa WhatsApp Web por detras.
- QR de WhatsApp Web: autenticacion inicial del conector.
- JSON de configuracion: lista de grupos permitidos.

Archivos principales:

- `connectors/whatsapp_group_connector.js`: escucha mensajes de grupos autorizados.
- `config/grupos_wsp_permitidos.json`: define que grupos puede escuchar.
- `data/group-connector.log`: historial tecnico del conector.
- `data/group-connector-state.json`: ultimo estado del conector.
- `data/whatsapp-qr.png`: QR generado si WhatsApp necesita reconectar.

Flujo:

1. El conector se autentica con WhatsApp Web.
2. Escucha grupos permitidos.
3. Si llega una imagen, la descarga.
4. Envia la imagen al backend local.
5. El backend la guarda como evidencia y crea o actualiza un corte.

### OCR e interpretacion de imagenes

- pytesseract: puente de Python para usar OCR.
- Tesseract OCR: motor que intenta leer texto dentro de imagenes.
- PIL / Pillow: procesamiento basico de imagenes antes de leer texto.

Uso dentro del proyecto:

- Detectar datos del aviso recibido por imagen.
- Ayudar a clasificar si el mensaje corresponde a corte imprevisto, programado o restablecimiento.
- Asociar zonas o grupos cuando el texto de la imagen lo permite.

### Publicacion temporal

- Cloudflare Tunnel: crea una URL publica temporal que apunta al servidor local.

Uso:

- El servidor corre localmente en `http://localhost:8091`.
- Cloudflare Tunnel expone ese servidor con una URL `trycloudflare.com`.
- La URL sirve mientras el tunel y la computadora sigan encendidos.

## Lenguajes y formatos

Lenguajes de programacion:

- Python: backend, base de datos, OCR, procesamiento y scripts de datos.
- JavaScript: interaccion web y conector de WhatsApp.

Lenguajes de interfaz:

- HTML: estructura de paginas.
- CSS: estilos visuales.

Formatos de datos:

- JSON: instituciones, configuraciones, cache y capas procesadas.
- GeoJSON: datos geograficos para el mapa.
- CSV: exportaciones y datos tabulares.
- SQLite DB: base local `data/cortes.db`.
- PNG / JPG / JPEG: logos, evidencias e imagenes de WhatsApp.

## Componentes del sistema

### 1. Mapa publico

Muestra instituciones y capas geograficas sobre el mapa. Permite buscar, filtrar, visualizar zonas, ver puntos y consultar informacion territorial.

Archivo:

- `mapa.html`

### 2. Portada de cortes activos

Pantalla inicial orientada a operacion. Resume eventos activos, metricas y acceso al mapa.

Archivo:

- `index.html`

### 3. Panel administrativo con login

Permite entrar con usuario y contrasena para cargar, corregir, revisar y exportar cortes.

Archivo:

- `app.py`

Credenciales por defecto:

- Usuario: `admin`
- Contrasena: `admin123`

Estas credenciales pueden cambiarse con variables de entorno:

- `CORTES_USER`
- `CORTES_PASS`

### 4. Base de datos local

Guarda cortes, evidencias, estado, grupo de WhatsApp, remitente, zona, fechas y notas.

Archivo:

- `data/cortes.db`

### 5. Conector de WhatsApp

Escucha mensajes con imagenes en grupos autorizados y los manda al backend.

Archivo:

- `connectors/whatsapp_group_connector.js`

Comando:

```bash
npm run connector:wsp
```

### 6. Herramientas de datos

Preparan la informacion que alimenta el mapa. Sirven para convertir formatos, geocodificar direcciones y construir capas.

Carpeta:

- `tools/`

## Como se arranca

### Panel con login

```bash
cd /home/agustin/municipio-ciudad
python3 app.py
```

URL local:

```text
http://localhost:8091/login
```

### Conector de WhatsApp

En otra terminal:

```bash
cd /home/agustin/municipio-ciudad
npm run connector:wsp
```

### Tunel publico de Cloudflare

```bash
/home/agustin/bin/cloudflared tunnel --url http://localhost:8091
```

Cloudflare entrega una URL publica temporal del tipo:

```text
https://algo.trycloudflare.com/login
```

## Como explicarlo en una presentacion

Una forma simple de contarlo:

> Este sistema integra datos municipales, mapa interactivo y avisos recibidos por WhatsApp. El mapa permite visualizar instituciones y zonas de Ciudad. El panel administrativo registra cortes de servicio, guarda evidencias y permite exportar informacion. Ademas, un conector escucha grupos de WhatsApp autorizados, recibe imagenes, las procesa y las transforma en eventos operativos dentro del panel.

Otra forma mas tecnica:

> La solucion usa HTML, CSS y JavaScript en el frontend, Leaflet como motor de mapas, Python como backend, SQLite como base local y Node.js para integrar WhatsApp Web. Los datos geograficos se preparan en GeoJSON mediante scripts de Python. Para compartir el sistema desde una computadora local se usa Cloudflare Tunnel.

## Puntos fuertes del proyecto

- Integra mapa, base de datos y WhatsApp en una sola herramienta.
- Funciona localmente y puede publicarse temporalmente con Cloudflare.
- Usa formatos abiertos como JSON, CSV y GeoJSON.
- Permite trabajar con datos reales del municipio.
- Tiene una base operativa para registrar, revisar y exportar cortes.
- Puede seguir creciendo sin depender de una plataforma cerrada.

## Limitaciones actuales

- La URL de Cloudflare es temporal si se usa un tunel rapido.
- El conector de WhatsApp depende de una sesion activa de WhatsApp Web.
- El OCR puede fallar si la imagen tiene baja calidad o texto poco legible.
- La base SQLite es practica para uso local, pero para muchos usuarios simultaneos convendria migrar a una base servidor.
- Las credenciales por defecto deben cambiarse antes de usarlo en un entorno real.

## Posibles mejoras

- Crear un tunel Cloudflare permanente con dominio fijo.
- Mejorar roles de usuario y permisos.
- Agregar historial visual de cambios por corte.
- Agregar notificaciones automaticas.
- Mejorar la lectura OCR con validacion asistida.
- Crear reportes PDF automaticos.
- Migrar a PostgreSQL/PostGIS si el proyecto escala en volumen de datos geograficos.

## Frase corta para resumir

> Es una plataforma municipal de monitoreo territorial que conecta mapa, datos, cortes de servicio y avisos de WhatsApp en una herramienta operativa.
