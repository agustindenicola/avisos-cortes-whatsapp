# Municipio Ciudad

Sistema operativo municipal para recibir avisos de cortes por WhatsApp, clasificarlos, registrarlos y mostrarlos en panel, dashboard, mapa e historial.

Estado actual: MVP operativo para demo y validacion. Para alcance y limites ver `docs/MVP_PRELIMINAR.md`.

## Que resuelve

- Recibe avisos desde grupos de WhatsApp autorizados.
- Interpreta texto o imagen para detectar tipo de aviso: imprevisto, programado o restablecido.
- Detecta grupo/zona por texto explicito o por calles/perimetro cuando hay reglas cargadas.
- Registra el aviso en base local SQLite.
- Muestra cortes activos, movimientos recientes, historico y mapa operativo.
- Permite exportar registros en CSV/XLSX.

## Levantar demo local

```bash
cd /home/agustin/municipio-ciudad
./deploy/start_local_stack.sh
```

El script levanta:

- panel web Python;
- conector WhatsApp;
- tunel Cloudflare temporal.

Para ver estado y URL publica actual:

```bash
./deploy/status_local_stack.sh
```

Para apagar todo:

```bash
./deploy/stop_local_stack.sh
```

Acceso local:

```text
http://127.0.0.1:8091/login
```

Credenciales de demo:

```text
admin / admin123
```

Nota: la URL `trycloudflare.com` cambia cada vez que se reinicia el tunel rapido.

## Pantallas principales

- `/`: panel operativo inicial.
- `/dashboard-cortes`: dashboard de cortes, movimientos recientes e historico plegado.
- `/mapa.html`: mapa operativo con rubros, zonas, cortes activos e historico real.
- `/historial`: registros completos, filtros y detalle.
- `/export/cortes.csv`: exportacion CSV.
- `/export/cortes.xlsx`: exportacion Excel.

La carga manual existe por URL interna (`/cortes/nuevo`), pero esta oculta en la navegacion porque el flujo buscado es automatico por WhatsApp.

## Estructura de archivos

```text
app.py                          Backend web, panel, APIs y logica de cortes
mapa.html                       Mapa Leaflet, filtros, simulacion e historico real
static/app.css                  Estilos del panel y dashboard
connectors/whatsapp_group_connector.js
                                Conector local de WhatsApp Web
config/grupos_wsp_permitidos.json
                                Grupos habilitados para escuchar
data/cortes.db                  Base SQLite de cortes registrados
data/instituciones.json         Instituciones y rubros del mapa
data/zonas_municipales.geojson  Zonas/bloques operativos
data/poblacion_por_zona_2022.json
                                Estimacion poblacional por zona
uploads/                        Evidencias recibidas
deploy/                         Scripts de arranque, estado, servicio y backup
tools/                          Scripts para generar y validar datos
docs/                           Documentacion operativa y tecnica
```

## Documentacion

- `docs/INDICE_PROYECTO.md`: guia ordenada de archivos, carpetas y documentacion.
- `docs/OPERACION.md`: uso diario del panel.
- `docs/MVP_PRELIMINAR.md`: alcance, limites y estado del MVP.
- `docs/ENTREGA_CHECKLIST.md`: checklist antes de entregar.
- `docs/INSTALACION_SERVICIO.md`: instalacion como servicio permanente.
- `docs/DESPLIEGUE_VPS.md`: despliegue en servidor cloud.

## Validaciones utiles

Validar integracion de mapa, grupos WSP, zonas municipales y datos INDEC:

```bash
python3 tools/validate_integrations.py
```

Validar sintaxis Python:

```bash
python3 -m py_compile app.py
```

Ver salud del panel:

```text
http://127.0.0.1:8091/health
```

## Regenerar datos del mapa

Generar instituciones:

```bash
python3 tools/build_map.py
```

Con geocodificacion:

```bash
python3 tools/build_map.py --geocode
```

Regenerar zonas municipales y poblacion:

```bash
python3 tools/build_municipal_zones.py
python3 tools/build_population_by_zone.py
```

## Ejemplos de prueba para demo

Texto por calles, sin grupo explicito. Esperado: imprevisto, Grupo F.

```text
ATENCION VECINOS

Corte imprevisto en suministro electrico afecta a perimetro:
Ituzaingo, Alferez Pacheco, Costanera norte y sur y Avdas principal y Mitre.

Reposicion en torno a los 30 minutos.
```

Programado con grupo explicito:

```text
CORTE PROGRAMADO

Grupo H
Dia y hora: 09/07 de 09 hs a 12 hs.
Zona afectada: Grupo H.
Mantenimiento preventivo en linea de media tension.
```

Restablecimiento:

```text
SERVICIO RESTABLECIDO

Grupo H
El suministro electrico se encuentra normalizado.
Horario de reposicion: 12:05 hs.
```

## Ideas para proxima etapa

- URL fija con tunel Cloudflare nombrado o dominio propio.
- Pantalla visible de estado de WhatsApp.
- Backup visible en panel: ultimo backup, estado, archivo y descarga.
- Usuarios y permisos por rol.
- Modo demo con datos congelados para presentaciones.
