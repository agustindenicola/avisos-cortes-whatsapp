# MVP preliminar

## Alcance

Este proyecto es una version preliminar operativa para visualizar cortes, zonas, instituciones sensibles y estimaciones de poblacion sobre mapa de Ciudad.

Incluye:

- Mapa interactivo con instituciones, barrios/loteos, RENABAP, calles y bloques operativos.
- Panel local con login para registrar, revisar y exportar cortes.
- Estimacion de poblacion y hogares con radios INDEC 2022.
- Priorizacion operativa por instituciones sensibles, densidad urbana y zona afectada.
- Base para recepcion de imagenes por grupos de WhatsApp mediante conector local.

## Como correrlo

```bash
cd /home/agustin/municipio-ciudad
python3 app.py
```

Abrir:

```text
http://localhost:8091/login
```

Credenciales de demo:

```text
admin / admin123
```

Para uso real, cambiar `CORTES_USER`, `CORTES_PASS` y `CORTES_SECRET` usando variables de entorno o un archivo `.env` local.

## Datos usados

- Instituciones y servicios: `data/instituciones.json`
- Zonas municipales: `data/zonas_municipales.geojson`
- Barrios base 2026: `data/barrios_2026.geojson`
- Grupos WSP operativos: `data/grupos_wsp_zonas.json` y `data/grupos_whatsapp_manual.geojson`
- Poblacion INDEC 2022: `data/poblacion_por_zona_2022.json`

## Criterio INDEC

La poblacion mostrada es una estimacion operativa por radios censales 2022. No reemplaza un dato oficial por barrio o bloque.

En la pantalla se diferencia:

- `Cruce operativo`: asignacion razonable para lectura territorial.
- `Cruce puntual`: estimacion basada en pocos radios.
- `Zona extensa / baja densidad`: zonas grandes donde el numero bajo puede ser coherente por ruralidad.
- `Bloque operativo aproximado`: bloque WSP no oficial, armado para operacion.
- `Estimado por barrios asociados`: fallback cuando el bloque no tiene cruce propio pero si barrios relacionados.

La consistencia tecnica se controla con:

```bash
python3 tools/validate_integrations.py
```

Estado actual de la validacion: la integracion es consistente, sin zonas fuera de rango en el mapa operativo. Quedan como advertencias esperadas los grupos WSP `ISLA`, `J` y `N` sin estimacion INDEC y 36 zonas omitidas porque sus geometrias fuente caen fuera del area esperada de Ciudad.

## Limitaciones conocidas

- Algunos bloques WSP son aproximados y requieren validacion municipal/cooperativa.
- Algunas zonas extensas pueden verse raras si se interpretan solo por poblacion total; conviene mirar densidad y superficie.
- Las zonas omitidas por geometria fuera de rango no se muestran ni se usan para cruces hasta corregir la fuente GIS correspondiente.
- El conector WhatsApp depende de una sesion activa de WhatsApp Web.
- El OCR ayuda, pero no debe reemplazar la revision humana para avisos criticos.
- La base SQLite es practica para MVP local; para produccion conviene planificar una base servidor.

## Entrega prolija

El repositorio debe excluir:

- Sesiones de WhatsApp (`.wwebjs_auth`, `.wwebjs_cache`)
- Evidencias runtime (`uploads`)
- Base local con cortes (`data/cortes.db`)
- QR, logs y estado del conector
- Dependencias instaladas (`node_modules`)
- Intermedios pesados del plano PDF (`data/plano_pdf`)
