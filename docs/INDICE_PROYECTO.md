# Indice del proyecto

Este documento ordena los archivos principales del sistema para entrega, soporte y futuras mejoras.

## Lectura recomendada

1. `README.md`: resumen general, arranque rapido y ejemplos de demo.
2. `docs/OPERACION.md`: uso diario del sistema.
3. `docs/CIERRE_OPERATIVO_2026-07-13.md`: estado validado, backups y QA final.
4. `docs/MVP_PRELIMINAR.md`: alcance actual y limites.
5. `docs/ENTREGA_CHECKLIST.md`: puntos antes de entregar.
6. `docs/INSTALACION_SERVICIO.md`: instalacion permanente.
7. `docs/DESPLIEGUE_VPS.md`: despliegue en servidor.

## Codigo principal

| Archivo | Uso |
| --- | --- |
| `app.py` | Backend web, login, panel, historial, dashboard, APIs, base SQLite y reglas de interpretacion de avisos. |
| `mapa.html` | Frontend del mapa operativo con Leaflet, filtros por rubro, cortes activos, historico real y simulacion. |
| `static/app.css` | Estilos del panel, dashboard, tablas, tarjetas y vistas administrativas. |
| `index.html` | Portada estatica previa del mapa/sistema. |
| `revisar_wsp.html` | Herramienta auxiliar para revisar grupos o datos WSP. |
| `revisar_zonas.html` | Herramienta auxiliar para revisar zonas. |
| `whatsapp_demo.html` | Demo auxiliar de carga tipo WhatsApp. |

## Conector WhatsApp

| Archivo | Uso |
| --- | --- |
| `connectors/whatsapp_group_connector.js` | Escucha WhatsApp Web, filtra grupos permitidos, descarga imagenes y envia avisos al panel. |
| `config/grupos_wsp_permitidos.json` | Lista de grupos habilitados para el conector. |
| `data/group-connector-state.json` | Estado reciente del conector. |
| `data/group-connector.log` | Log operativo del conector. |
| `data/whatsapp-qr.png` | QR generado cuando hay que vincular WhatsApp Web. |

## Datos operativos

| Archivo/carpeta | Uso |
| --- | --- |
| `data/cortes.db` | Base SQLite con avisos, estados, mensajes, grupos, horarios y evidencias. |
| `uploads/` | Archivos recibidos como evidencia de corte o restablecimiento. |
| `data/runtime/` | PIDs y logs de la pila local: panel, WSP y Cloudflare. |
| `data/instituciones.json` | Instituciones, rubros, coordenadas y datos que consume el mapa. |
| `data/zonas_municipales.geojson` | Zonas o bloques municipales del mapa. |
| `data/barrios_2026.geojson` | Barrios/loteos de referencia. |
| `data/calles_ciudad.geojson` | Calles usadas como referencia territorial. |
| `data/poblacion_por_zona_2022.json` | Estimacion INDEC 2022 por zona. |
| `data/grupos_wsp_zonas.json` | Relacion entre grupos WSP y zonas. |
| `data/grupos_wsp_calles.json` | Calles asociadas a grupos WSP. |
| `data/zone_aliases.json` | Alias de zonas para mejorar deteccion por texto. |

## Scripts de operacion

| Script | Uso |
| --- | --- |
| `deploy/start_local_stack.sh` | Levanta panel, WhatsApp y Cloudflare local. |
| `deploy/status_local_stack.sh` | Muestra estado de servicios y URL publica actual. |
| `deploy/stop_local_stack.sh` | Detiene la pila local. |
| `deploy/run_cloudflare.sh` | Ejecuta el tunel Cloudflare. |
| `deploy/check_status.sh` | Chequeos de servicio para instalacion permanente. |
| `deploy/backup.sh` | Genera backup de base, evidencias y configuracion. |
| `deploy/install_backup_cron.sh` | Instala backup diario por cron. |
| `deploy/install_services.sh` | Instala servicios systemd. |

## Scripts de datos

| Script | Uso |
| --- | --- |
| `tools/build_map.py` | Genera `data/instituciones.json`. |
| `tools/build_municipal_zones.py` | Genera zonas municipales. |
| `tools/build_population_by_zone.py` | Calcula poblacion por zona. |
| `tools/validate_integrations.py` | Valida integracion entre mapa, WSP, zonas y poblacion. |
| `tools/build_whatsapp_groups.py` | Construye datos base de grupos WhatsApp. |
| `tools/build_wsp_zone_mapping.py` | Relaciona grupos WSP con zonas. |
| `tools/match_wsp_streets.py` | Cruza calles con grupos WSP. |
| `tools/evaluate_ocr_photos.py` | Evalua OCR sobre fotos de referencia. |
| `tools/export_wsp_editable_xlsx.py` | Exporta planilla editable de grupos/bloques. |

## Documentacion existente

| Documento | Contenido |
| --- | --- |
| `docs/OPERACION.md` | Acceso, uso diario, WhatsApp, chequeos y backup minimo. |
| `docs/CIERRE_OPERATIVO_2026-07-13.md` | Estado validado, link actual, backup creado, QA y pendientes recomendados. |
| `docs/MVP_PRELIMINAR.md` | Estado del MVP, alcance, limites y criterios de entrega. |
| `docs/ENTREGA_CHECKLIST.md` | Lista de control para entrega profesional. |
| `docs/INSTALACION_SERVICIO.md` | Instalacion como servicio permanente en Linux. |
| `docs/DESPLIEGUE_VPS.md` | Guia para servidor cloud y dominio. |

## Archivos que conviene respaldar

- `data/cortes.db`
- `uploads/`
- `config/grupos_wsp_permitidos.json`
- `.env`
- `.wwebjs_auth/` si se quiere conservar sesion de WhatsApp Web
- `data/whatsapp-qr.png` solo como referencia temporal

## Flujo actual recomendado

1. El aviso llega por WhatsApp.
2. El conector valida si el grupo esta permitido.
3. El panel recibe texto/imagen.
4. El sistema detecta tipo, grupo o zona.
5. Se registra en `data/cortes.db`.
6. El dashboard y el mapa se actualizan.
7. Un aviso de restablecimiento cierra el corte activo si coincide el grupo.

## Mejoras pendientes sugeridas

- Dominio fijo o tunel Cloudflare nombrado.
- Backup visible desde panel.
- Estado visible del conector WhatsApp.
- Usuarios por rol.
- Modo demo con datos congelados.
