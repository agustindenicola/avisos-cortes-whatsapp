#!/usr/bin/env python3
import json
import math
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

CIUDAD_CENTER = (-34.1634, -58.9592)
CIUDAD_BBOX = (-34.31, -59.02, -34.10, -58.84)

LOCALITY_FALLBACK = [
    ("Ciudad", -34.1634, -58.9592, "localidad"),
    ("Alto Los Cardales", -34.3270, -58.9870, "localidad"),
    ("Los Pioneros", -34.3180, -58.9520, "localidad"),
    ("Lomas del Rio Lujan", -34.2750, -58.9000, "localidad"),
    ("Ingeniero Romulo Otamendi", -34.2280, -58.8950, "localidad"),
    ("El Morejon", -34.2770, -59.0530, "localidad"),
]

POI_RUBROS = {
    "hospital": "SALUD",
    "clinic": "SALUD",
    "doctors": "SALUD",
    "dentist": "SALUD",
    "pharmacy": "SALUD",
    "school": "EDUCACION",
    "kindergarten": "EDUCACION",
    "college": "EDUCACION",
    "university": "EDUCACION",
    "fire_station": "BOMBEROS",
    "police": "SEGURIDAD",
    "townhall": "SERVICIOS VARIOS",
    "community_centre": "ASISTENCIA",
    "social_facility": "ASISTENCIA",
    "place_of_worship": "RELIGIOSA",
    "sports_centre": "DEPORTE",
    "library": "CULTURA",
}


def overpass(query):
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    request = urllib.request.Request(
        OVERPASS_URL,
        data=data,
        headers={"User-Agent": "municipio-ciudad-borrador/1.0"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def feature_collection(features):
    return {"type": "FeatureCollection", "features": features}


def element_center(element):
    if "lat" in element and "lon" in element:
        return element["lat"], element["lon"]
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        return center["lat"], center["lon"]
    geometry = element.get("geometry") or []
    if geometry:
        return (
            sum(point["lat"] for point in geometry) / len(geometry),
            sum(point["lon"] for point in geometry) / len(geometry),
        )
    return None, None


def circle_polygon(lat, lon, radius_km=1.0, points=28):
    coords = []
    lat_rad = math.radians(lat)
    for index in range(points):
        angle = 2 * math.pi * index / points
        dlat = (radius_km / 111.32) * math.sin(angle)
        dlon = (radius_km / (111.32 * max(math.cos(lat_rad), 0.2))) * math.cos(angle)
        coords.append([lon + dlon, lat + dlat])
    coords.append(coords[0])
    return {"type": "Polygon", "coordinates": [coords]}


def line_geometry(element):
    geometry = element.get("geometry") or []
    coords = [[point["lon"], point["lat"]] for point in geometry if "lat" in point and "lon" in point]
    if len(coords) < 2:
        return None
    return {"type": "LineString", "coordinates": coords}


def polygon_from_relation(relation):
    rings = []
    for member in relation.get("members", []):
        if member.get("role") not in {"outer", ""}:
            continue
        geometry = member.get("geometry") or []
        ring = [[point["lon"], point["lat"]] for point in geometry if "lat" in point and "lon" in point]
        if len(ring) >= 4:
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            rings.append(ring)
    if not rings:
        return None
    return {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}


def point_in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i, point in enumerate(ring):
        xi, yi = point
        xj, yj = ring[j]
        crosses = (yi > lat) != (yj > lat)
        if crosses:
            x_intersect = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            if lon < x_intersect:
                inside = not inside
        j = i
    return inside


def point_in_geometry(lat, lon, geometry):
    if not geometry:
        return False
    if geometry["type"] == "Polygon":
        rings = geometry.get("coordinates") or []
        return bool(rings) and point_in_ring(lon, lat, rings[0])
    if geometry["type"] == "MultiPolygon":
        for polygon in geometry.get("coordinates") or []:
            if polygon and point_in_ring(lon, lat, polygon[0]):
                return True
    return False


def element_in_boundary(element, boundary_geometry):
    lat, lon = element_center(element)
    if lat is not None and lon is not None:
        return point_in_geometry(lat, lon, boundary_geometry)
    geometry = element.get("geometry") or []
    return any(point_in_geometry(point["lat"], point["lon"], boundary_geometry) for point in geometry)


def clean_name(value):
    value = str(value or "").strip()
    value = re.sub(r"\s+", " ", value)
    return value


def valid_place_name(name):
    lowered = name.lower()
    if "," in name:
        return False
    if "rio negro" in lowered or "río negro" in lowered:
        return False
    return True


def valid_institution_name(name):
    lowered = name.lower()
    if "ciudad" in lowered or "ciudad" in lowered:
        return False
    return True


def rubro_for(tags):
    amenity = tags.get("amenity") or tags.get("emergency") or tags.get("healthcare") or tags.get("leisure")
    if amenity in POI_RUBROS:
        return POI_RUBROS[amenity]
    if tags.get("office") == "government":
        return "SERVICIOS VARIOS"
    return "SERVICIOS VARIOS"


def build_boundary():
    return None


def build_osm_layers():
    query = """
    [out:json][timeout:90];
    area["boundary"="administrative"]["name"="Partido de Ciudad"]->.searchArea;
    (
      way(area.searchArea)["highway"];
      node(area.searchArea)["place"~"city|town|village|hamlet|suburb|neighbourhood"];
      way(area.searchArea)["place"~"city|town|village|hamlet|suburb|neighbourhood"];
      node(area.searchArea)["amenity"~"hospital|clinic|doctors|dentist|pharmacy|school|kindergarten|college|university|fire_station|police|townhall|community_centre|social_facility|place_of_worship|library"];
      way(area.searchArea)["amenity"~"hospital|clinic|doctors|dentist|pharmacy|school|kindergarten|college|university|fire_station|police|townhall|community_centre|social_facility|place_of_worship|library"];
      node(area.searchArea)["emergency"="fire_station"];
      way(area.searchArea)["emergency"="fire_station"];
      node(area.searchArea)["healthcare"];
      way(area.searchArea)["healthcare"];
      node(area.searchArea)["office"="government"];
      way(area.searchArea)["office"="government"];
      node(area.searchArea)["leisure"="sports_centre"];
      way(area.searchArea)["leisure"="sports_centre"];
    );
    out center tags geom;
    """
    return overpass(query).get("elements", [])


def build_streets(elements, boundary_geometry=None):
    features = []
    for element in elements:
        tags = element.get("tags") or {}
        if element.get("type") != "way" or "highway" not in tags:
            continue
        if boundary_geometry and not element_in_boundary(element, boundary_geometry):
            continue
        geometry = line_geometry(element)
        if not geometry:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "@id": f"way/{element.get('id')}",
                "name": tags.get("name", ""),
                "highway": tags.get("highway", ""),
                "source": "OpenStreetMap",
            },
            "geometry": geometry,
        })
    return feature_collection(features)


def build_zones(elements, boundary):
    seen = set()
    features = []
    for name, lat, lon, kind in LOCALITY_FALLBACK:
        seen.add(name.lower())
        features.append({
            "type": "Feature",
            "properties": {
                "id": f"ciudad-{len(features)}",
                "label": name,
                "name": name,
                "kind": kind,
                "source": "referencia_publica",
            },
            "geometry": circle_polygon(lat, lon, 1.3 if name != "Ciudad" else 2.4),
        })

    for element in elements:
        tags = element.get("tags") or {}
        if "place" not in tags:
            continue
        name = clean_name(tags.get("name"))
        if not name or not valid_place_name(name) or name.lower() in seen:
            continue
        lat, lon = element_center(element)
        if lat is None or lon is None:
            continue
        if boundary and not point_in_geometry(lat, lon, boundary["geometry"]):
            continue
        seen.add(name.lower())
        features.append({
            "type": "Feature",
            "properties": {
                "id": f"ciudad-{len(features)}",
                "label": name,
                "name": name,
                "kind": tags.get("place", "barrio"),
                "source": "OpenStreetMap",
            },
            "geometry": circle_polygon(lat, lon, 0.9),
        })
    return feature_collection(features)


def build_institutions(elements, boundary_geometry=None):
    records = []
    seen = set()
    for element in elements:
        tags = element.get("tags") or {}
        if not any(key in tags for key in ("amenity", "emergency", "healthcare", "office", "leisure")):
            continue
        name = clean_name(tags.get("name") or tags.get("operator"))
        lat, lon = element_center(element)
        if not name or lat is None or lon is None:
            continue
        if not valid_institution_name(name):
            continue
        if boundary_geometry and not point_in_geometry(lat, lon, boundary_geometry):
            continue
        key = (name.lower(), round(lat, 5), round(lon, 5))
        if key in seen:
            continue
        seen.add(key)
        rubro = rubro_for(tags)
        street = clean_name(tags.get("addr:street"))
        number = clean_name(tags.get("addr:housenumber"))
        address = " ".join(part for part in (street, number) if part) or "Sin direccion OSM"
        records.append({
            "id": len(records) + 1,
            "grupo": "",
            "numero": str(len(records) + 1),
            "nombre": name,
            "rubro": rubro,
            "direccion": address,
            "ciudad": "Ciudad",
            "telefono": clean_name(tags.get("phone")),
            "observaciones": "Dato publico provisorio desde OpenStreetMap",
            "direccion_busqueda": f"{address}, Ciudad, Buenos Aires, Argentina",
            "lat": lat,
            "lon": lon,
            "geocode_estado": "osm",
            "geocode_nombre": name,
        })
    rubros = sorted({record["rubro"] for record in records})
    return {
        "meta": {
            "total": len(records),
            "con_coordenadas": len(records),
            "rubros": rubros,
            "source": "OpenStreetMap",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "instituciones": records,
    }


def main():
    print("Descargando datos publicos de Ciudad desde OpenStreetMap...")
    boundary = build_boundary()
    elements = build_osm_layers()

    boundary_geometry = boundary["geometry"] if boundary else None
    write_json(DATA / "calles_ciudad.geojson", build_streets(elements, boundary_geometry))
    zones = build_zones(elements, boundary)
    write_json(DATA / "barrios_2026.geojson", zones)
    write_json(DATA / "zonas_municipales.geojson", zones)
    write_json(DATA / "instituciones.json", build_institutions(elements, boundary_geometry))

    empty_fc = feature_collection([])
    write_json(DATA / "renabap_zte.geojson", empty_fc)
    write_json(DATA / "manzanas_zte.geojson", empty_fc)
    write_json(DATA / "grupos_wsp_perimetros.geojson", empty_fc)
    write_json(DATA / "grupos_wsp_perimetros_ruta.geojson", empty_fc)
    write_json(DATA / "grupos_wsp_calles_detectadas.geojson", empty_fc)
    write_json(DATA / "grupos_whatsapp_manual.geojson", empty_fc)
    write_json(DATA / "grupos_wsp_zonas.json", {"groups": []})
    write_json(DATA / "grupos_wsp_calles.json", {"source_streets": "data/calles_ciudad.geojson", "groups": []})
    write_json(DATA / "grupos_whatsapp_cooperativa.json", [])
    write_json(DATA / "poblacion_por_zona_2022.json", {})

    print("Listo:")
    print(f"  calles: {len(json.loads((DATA / 'calles_ciudad.geojson').read_text())['features'])}")
    print(f"  zonas/localidades: {len(zones['features'])}")
    print(f"  instituciones: {len(json.loads((DATA / 'instituciones.json').read_text())['instituciones'])}")


if __name__ == "__main__":
    main()
