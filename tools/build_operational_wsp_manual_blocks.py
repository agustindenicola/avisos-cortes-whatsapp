#!/usr/bin/env python3
import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTPUT = DATA / "grupos_whatsapp_manual.geojson"


GROUP_COLORS = {
    "A": "#12b8cc",
    "B": "#8ccf59",
    "C": "#ffd437",
    "D": "#ff8c28",
    "E": "#ef4a3c",
    "F": "#0f766e",
    "G": "#2257c7",
    "H": "#7c3aed",
    "I": "#db2777",
    "J": "#0891b2",
    "K": "#16a34a",
    "L": "#ca8a04",
    "M": "#dc2626",
    "Ñ": "#4f46e5",
    "O": "#9333ea",
    "Q": "#475569",
}


# Alias operativos para nombres que el Excel usa de forma informal.
# Son deliberadamente prácticos: buscan cerrar la capa de entrega, no catastro perfecto.
TERM_TO_BARRIOS_2026 = {
    ("D", "Orsi"): ["barrio-57"],
    ("F", "Orsi"): ["barrio-57"],
    ("F", "Casuarinas"): ["barrio-57"],
    ("G", "Malvicino"): ["barrio-52"],
    ("G", "San Luis"): ["barrio-52"],
    ("I", "Solares Paraíso"): ["barrio-132"],
    ("J", "La Merced"): ["barrio-128"],
    ("K", "Las Cuatro Esquinas (parcial)"): ["barrio-83"],
    ("M", "Paraje Atucha"): ["barrio-75", "barrio-76"],
    ("M", "Chacras del Paraná"): ["barrio-62", "barrio-86"],
    ("O", "El Caburé"): ["barrio-144"],
    ("O", "Las Cuatro Esquinas (parcial)"): ["barrio-83"],
}


POINT_APPROX = {
    ("Q", "Edificio El Mirador"): (-59.023556, -34.0900037, 0.0038),
}

PERIMETER_TERMS = {
    ("A", "Perímetro comprendido entre las calles Urquiza, Independencia, Costanera Norte y Av. Norte / Principal"),
    ("B", "Perímetro comprendido entre las calles Independencia, Larrea, Costanera Norte y Sur Av. Lavalle / Principal"),
    ("F", "perímetro comprendido entre las calles Lavalle, Principal, Castelli y Mitre"),
}


def load_json(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def group_props(group, note, method, term="", source="operativo"):
    return {
        "group": group,
        "label": f"Grupo {group}",
        "term": term,
        "method": method,
        "status": "operational_approx",
        "source": source,
        "color": GROUP_COLORS.get(group, "#0f766e"),
        "note": note,
    }


def clone_feature(feature, props):
    cloned = deepcopy(feature)
    cloned["properties"] = {**props, **{
        "source_label": feature.get("properties", {}).get("label", ""),
        "source_id": feature.get("properties", {}).get("id", ""),
    }}
    return cloned


def square_polygon(lon, lat, delta):
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon - delta, lat - delta],
            [lon + delta, lat - delta],
            [lon + delta, lat + delta],
            [lon - delta, lat + delta],
            [lon - delta, lat - delta],
        ]],
    }


def bbox_polygon(coords, padding=0.0028):
    lons = [point[0] for point in coords]
    lats = [point[1] for point in coords]
    if not lons or not lats:
        return None
    min_lon, max_lon = min(lons) - padding, max(lons) + padding
    min_lat, max_lat = min(lats) - padding, max(lats) + padding
    return {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat],
            [max_lon, min_lat],
            [max_lon, max_lat],
            [min_lon, max_lat],
            [min_lon, min_lat],
        ]],
    }


def collect_coords(geometry):
    if not geometry:
        return []
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "LineString":
        return coords
    if gtype == "MultiLineString":
        return [point for line in coords for point in line]
    return []


def perimeter_features():
    features = []
    perimeters = load_json("grupos_wsp_perimetros.geojson").get("features", [])
    route_features = load_json("grupos_wsp_perimetros_ruta.geojson").get("features", [])

    # Preferimos la ruta de A porque sigue mejor las calles que el casco convexo.
    for feature in route_features:
        group = feature.get("properties", {}).get("group")
        if group != "A":
            continue
        props = group_props(
            "A",
            "Perímetro aproximado por calles del canal WSP. Revisado como bloque operativo.",
            "street_route_ring",
            feature.get("properties", {}).get("note", ""),
            "grupos_wsp_perimetros_ruta.geojson",
        )
        features.append(clone_feature(feature, props))

    for feature in perimeters:
        props0 = feature.get("properties", {})
        group = props0.get("group", "")
        if group == "A":
            continue
        props = group_props(
            group,
            "Perímetro aproximado por calles del canal WSP. Se usa para entrega operativa.",
            props0.get("method", "street_perimeter"),
            props0.get("term", ""),
            "grupos_wsp_perimetros.geojson",
        )
        features.append(clone_feature(feature, props))
    return features


def barrio_alias_features(groups):
    barrios = load_json("barrios_2026.geojson").get("features", [])
    by_id = {feature.get("properties", {}).get("id"): feature for feature in barrios}
    features = []
    for group in groups:
        group_key = str(group.get("group", ""))
        for term in group.get("review_terms", []):
            term_text = term.get("term", "")
            for barrio_id in TERM_TO_BARRIOS_2026.get((group_key, term_text), []):
                source = by_id.get(barrio_id)
                if not source:
                    continue
                label = source.get("properties", {}).get("label", barrio_id)
                props = group_props(
                    group_key,
                    f"Alias operativo: '{term_text}' se representa con {label}.",
                    "barrio_2026_alias",
                    term_text,
                    "barrios_2026.geojson",
                )
                features.append(clone_feature(source, props))
    return features


def point_features(groups):
    features = []
    for group in groups:
        group_key = str(group.get("group", ""))
        for term in group.get("review_terms", []):
            term_text = term.get("term", "")
            point = POINT_APPROX.get((group_key, term_text))
            if not point:
                continue
            lon, lat, delta = point
            props = group_props(
                group_key,
                f"Ubicación aproximada tomada como referencia puntual: {term_text}.",
                "point_area_approx",
                term_text,
                "lugares_extra.csv",
            )
            features.append({
                "type": "Feature",
                "properties": props,
                "geometry": square_polygon(lon, lat, delta),
            })
    return features


def street_band_features(groups):
    street_data = load_json("grupos_wsp_calles_detectadas.geojson").get("features", [])
    by_key = {}
    for feature in street_data:
        props = feature.get("properties", {})
        key = (str(props.get("group", "")), props.get("term", ""))
        by_key.setdefault(key, []).extend(collect_coords(feature.get("geometry")))

    features = []
    seen = set()
    for group in groups:
        group_key = str(group.get("group", ""))
        for term in group.get("review_terms", []):
            term_text = term.get("term", "")
            key = (group_key, term_text)
            if key in seen or key in PERIMETER_TERMS or key in TERM_TO_BARRIOS_2026 or key in POINT_APPROX:
                continue
            coords = by_key.get(key, [])
            if len(coords) < 2:
                continue
            geometry = bbox_polygon(coords)
            if not geometry:
                continue
            props = group_props(
                group_key,
                f"Banda aproximada generada sobre calles detectadas para: {term_text}.",
                "street_bbox_approx",
                term_text,
                "grupos_wsp_calles_detectadas.geojson",
            )
            features.append({
                "type": "Feature",
                "properties": props,
                "geometry": geometry,
            })
            seen.add(key)
    return features


def main():
    groups = load_json("grupos_wsp_zonas.json").get("groups", [])
    features = []
    features.extend(perimeter_features())
    features.extend(barrio_alias_features(groups))
    features.extend(point_features(groups))
    features.extend(street_band_features(groups))
    payload = {
        "type": "FeatureCollection",
        "name": "grupos_whatsapp_manual_operativo",
        "features": features,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{OUTPUT} - {len(features)} features")


if __name__ == "__main__":
    main()
