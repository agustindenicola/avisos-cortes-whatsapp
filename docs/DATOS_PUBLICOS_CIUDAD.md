# Datos publicos Ciudad

Este borrador usa datos publicos y provisorios porque todavia no hay capas oficiales entregadas por la Municipalidad Demo.

Capas generadas:

- Calles: OpenStreetMap, consultadas dentro del area administrativa `Partido de Ciudad`.
- Instituciones y servicios: OpenStreetMap, rubros normalizados para la demo.
- Barrios/localidades: localidades base cargadas manualmente y barrios/localidades publicados en OpenStreetMap dentro del partido.
- Area de trabajo: area administrativa OpenStreetMap de `Partido de Ciudad`; debe validarse contra GIS municipal antes de uso oficial.
- Logo: marca provisoria local, no oficial.

Comando para regenerar:

```bash
cd /home/agustin/municipio-ciudad
python3 tools/build_ciudad_public_data.py
```

Pendiente para version oficial:

- Validar el area administrativa OSM contra el limite oficial del partido.
- Validar barrios/localidades con Catastro o GIS municipal.
- Reemplazar el logo provisorio por identidad oficial.
- Validar instituciones criticas con fuente municipal.

## Demo de cortes

Para cargar cortes simulados en el panel y mapa de calor:

```bash
cd /home/agustin/municipio-ciudad
python3 tools/seed_demo_cortes_ciudad.py
```

Para borrar solo los cortes simulados:

```bash
cd /home/agustin/municipio-ciudad
python3 tools/seed_demo_cortes_ciudad.py --clear
```

Los registros demo usan `source=demo_ciudad` y no representan eventos reales.
