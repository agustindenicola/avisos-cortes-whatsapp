#!/usr/bin/env python3
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INDEC_GPKG = ROOT / "data" / "indec_personas_radios_2022_ciudad.gpkg"
GROUPS_GEOJSON = ROOT / "data" / "grupos_whatsapp_manual.geojson"
GROUPS_ZONES_JSON = ROOT / "data" / "grupos_wsp_zonas.json"
BARRIOS_GEOJSON = ROOT / "data" / "barrios_2026.geojson"
MUNICIPAL_ZONES_GEOJSON = ROOT / "data" / "zonas_municipales.geojson"
OUT_JSON = ROOT / "data" / "poblacion_por_zona_2022.json"
OUT_CSV = ROOT / "data" / "poblacion_por_zona_2022.csv"

SPECIAL_GROUP_ZONE_IDS = {
    # El grupo A se define por perímetro de calles en la fuente WSP y no trae
    # barrios asociados. Usamos la misma aproximación A/B que consume el mapa.
    "A": ["zona-p1-1-12-de-octubre", "zona-p1-20-centro", "zona-p2-3-centro"],
}


def iter_rings(geometry):
    if not geometry:
        return
    if geometry["type"] == "Polygon":
        for ring in geometry["coordinates"]:
            yield ring
    elif geometry["type"] == "MultiPolygon":
        for polygon in geometry["coordinates"]:
            for ring in polygon:
                yield ring


def point_in_ring(lon, lat, ring):
    inside = False
    if not ring:
        return False
    j = len(ring) - 1
    for i, current in enumerate(ring):
        xi, yi = current[0], current[1]
        xj, yj = ring[j][0], ring[j][1]
        crosses = (yi > lat) != (yj > lat)
        if crosses:
            x_intersect = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi
            if lon < x_intersect:
                inside = not inside
        j = i
    return inside


def point_in_geometry(lon, lat, geometry):
    if not geometry:
        return False
    if geometry["type"] == "Polygon":
        rings = geometry["coordinates"]
        if not rings or not point_in_ring(lon, lat, rings[0]):
            return False
        return not any(point_in_ring(lon, lat, hole) for hole in rings[1:])
    if geometry["type"] == "MultiPolygon":
        return any(point_in_geometry(lon, lat, {"type": "Polygon", "coordinates": polygon}) for polygon in geometry["coordinates"])
    return False


def load_geojson(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("features", [])


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_radios():
    with sqlite3.connect(INDEC_GPKG) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select
                fid,
                redcode,
                coalesce(RADIO, redcode) as radio,
                Radios_poblacion_total as population,
                Radios_hogares_total as households,
                Radios_latitud_centroide as lat,
                Radios_longitud_centroide as lon,
                Radios_superficie_km2 as km2
            from features
            order by fid
            """
        ).fetchall()
    return [dict(row) for row in rows]


def new_summary(kind, zone_id, label, extra=None):
    return {
        "kind": kind,
        "id": zone_id,
        "label": label,
        "population": 0,
        "households": 0,
        "area_km2": 0,
        "radio_count": 0,
        "radios": [],
        **(extra or {}),
    }


def add_radio(summary, radio):
    population = int(round(radio["population"] or 0))
    households = int(round(radio["households"] or 0))
    area_km2 = float(radio["km2"] or 0)
    summary["population"] += population
    summary["households"] += households
    summary["area_km2"] += area_km2
    summary["radio_count"] += 1
    summary["radios"].append({
        "fid": radio["fid"],
        "redcode": radio["redcode"],
        "radio": radio["radio"],
        "population": population,
        "households": households,
        "lat": radio["lat"],
        "lon": radio["lon"],
        "area_km2": area_km2,
    })


def summarize_by_features(radios, features, kind):
    summaries = {}
    unmatched = []
    for feature in features:
        props = feature.get("properties", {})
        if kind == "grupo_wsp":
            zone_id = props.get("group") or props.get("label") or str(len(summaries) + 1)
            label = props.get("label") or f"Grupo {zone_id}"
            extra = {
                "group": props.get("group"),
                "source": props.get("source"),
                "source_label": props.get("source_label"),
                "status": props.get("status"),
            }
        else:
            zone_id = props.get("id") or str(props.get("fid"))
            label = props.get("label") or zone_id
            extra = {
                "fid": props.get("fid"),
                "number": props.get("number"),
            }
        summaries.setdefault(zone_id, new_summary(kind, zone_id, label, extra))

    for radio in radios:
        matches = []
        lon = radio["lon"]
        lat = radio["lat"]
        if lon is None or lat is None:
            unmatched.append(radio)
            continue
        for feature in features:
            if point_in_geometry(lon, lat, feature.get("geometry")):
                props = feature.get("properties", {})
                zone_id = props.get("group") if kind == "grupo_wsp" else props.get("id")
                if zone_id:
                    matches.append(zone_id)

        # Los grupos pueden tener varias features para el mismo grupo. Evitar duplicar.
        matches = sorted(set(matches))
        if not matches:
            unmatched.append(radio)
            continue
        share = {
            **radio,
            "population": (radio["population"] or 0) / len(matches),
            "households": (radio["households"] or 0) / len(matches),
            "km2": (radio["km2"] or 0) / len(matches),
        }
        for zone_id in matches:
            add_radio(summaries[zone_id], share)

    return summaries, unmatched


def finalize_summary(summary):
    summary["population"] = int(round(summary["population"]))
    summary["households"] = int(round(summary["households"]))
    summary["area_km2"] = round(summary["area_km2"], 6)
    summary["density_hab_km2"] = round(summary["population"] / summary["area_km2"], 2) if summary["area_km2"] else None
    summary["radios"] = sorted(summary["radios"], key=lambda item: (str(item["redcode"]), item["fid"]))
    return summary


def merge_barrio_summary(target, barrio):
    target["population"] += int(barrio.get("population") or 0)
    target["households"] += int(barrio.get("households") or 0)
    target["area_km2"] += float(barrio.get("area_km2") or 0)
    target["radio_count"] += int(barrio.get("radio_count") or 0)
    target["radios"].extend(barrio.get("radios") or [])


def group_summaries_from_barrios(groups_data, municipal_zones, barrio_by_id, direct_group_by_id):
    source_by_zone_id = {
        feature.get("properties", {}).get("id"): feature.get("properties", {}).get("sourceZoneId")
        for feature in municipal_zones
        if feature.get("properties", {}).get("id")
    }
    groups = []
    for group in groups_data.get("groups", []):
        group_id = str(group.get("group") or "").strip().upper()
        if not group_id:
            continue
        summary = new_summary("grupo_wsp", group_id, group.get("label") or f"Grupo {group_id}", {
            "group": group_id,
            "source": "barrios_asociados",
            "status": "operational_estimate",
            "method": "Suma de barrios/loteos asociados al bloque operativo WSP.",
            "whatsapp": group.get("whatsapp", ""),
        })
        zone_ids = [barrio.get("id") for barrio in group.get("barrios", []) if barrio.get("id")]
        if group_id in SPECIAL_GROUP_ZONE_IDS:
            zone_ids = zone_ids or SPECIAL_GROUP_ZONE_IDS[group_id]

        seen_barrio_ids = set()
        for zone_id in zone_ids:
            source_id = source_by_zone_id.get(zone_id)
            if not source_id or source_id in seen_barrio_ids:
                continue
            barrio_summary = barrio_by_id.get(source_id)
            if not barrio_summary:
                continue
            seen_barrio_ids.add(source_id)
            merge_barrio_summary(summary, barrio_summary)

        if summary["population"]:
            summary["source_zone_ids"] = sorted(seen_barrio_ids)
            groups.append(finalize_summary(summary))
            continue

        direct = direct_group_by_id.get(group_id)
        if direct:
            direct["source"] = direct.get("source") or "bloque_manual"
            direct["method"] = "Cruce por centroide dentro del polígono manual del bloque; usar como respaldo."
            groups.append(direct)

    return groups


def write_csv(sections):
    rows = []
    for section, summaries in sections.items():
        for item in summaries:
            rows.append({
                "section": section,
                "kind": item["kind"],
                "id": item["id"],
                "label": item["label"],
                "population": item["population"],
                "households": item["households"],
                "radio_count": item["radio_count"],
                "area_km2": item["area_km2"],
                "density_hab_km2": item["density_hab_km2"],
            })
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    radios = load_radios()
    direct_group_summaries, group_unmatched = summarize_by_features(radios, load_geojson(GROUPS_GEOJSON), "grupo_wsp")
    barrio_summaries, barrio_unmatched = summarize_by_features(radios, load_geojson(BARRIOS_GEOJSON), "barrio")

    barrios = [finalize_summary(item) for item in barrio_summaries.values() if item["radio_count"] > 0]
    direct_groups = [finalize_summary(item) for item in direct_group_summaries.values() if item["radio_count"] > 0]
    direct_group_by_id = {str(item.get("group") or item["id"]).upper(): item for item in direct_groups}
    barrio_by_id = {item["id"]: item for item in barrios}
    groups = group_summaries_from_barrios(
        load_json(GROUPS_ZONES_JSON),
        load_geojson(MUNICIPAL_ZONES_GEOJSON),
        barrio_by_id,
        direct_group_by_id,
    )
    groups.sort(key=lambda item: str(item.get("group") or item["label"]))
    barrios.sort(key=lambda item: item["label"])

    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source": "INDEC / Censo Nacional de Población, Hogares y Viviendas 2022",
            "dataset": "Indicadores de personas. Radios, 2022 - Ciudad",
            "method": "Barrios: asignación por centroide de radio censal dentro de polígono. Bloques WSP: suma de barrios/loteos asociados; si no hay barrios asociados, respaldo por polígono manual.",
            "limitations": "Estimación territorial operativa; no reemplaza datos oficiales por barrio ni un cruce por intersección fina de superficies.",
            "total_radios": len(radios),
            "total_population_source": int(round(sum(row["population"] or 0 for row in radios))),
            "total_households_source": int(round(sum(row["households"] or 0 for row in radios))),
            "group_unmatched_radios": len(group_unmatched),
            "barrio_unmatched_radios": len(barrio_unmatched),
        },
        "groups": groups,
        "barrios": barrios,
        "unmatched": {
            "groups": [{"fid": row["fid"], "redcode": row["redcode"], "population": row["population"], "lat": row["lat"], "lon": row["lon"]} for row in group_unmatched],
            "barrios": [{"fid": row["fid"], "redcode": row["redcode"], "population": row["population"], "lat": row["lat"], "lon": row["lon"]} for row in barrio_unmatched],
        },
    }

    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv({"groups": groups, "barrios": barrios})
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_CSV}")
    print(f"groups={len(groups)} barrios={len(barrios)} radios={len(radios)}")
    print(f"group_unmatched={len(group_unmatched)} barrio_unmatched={len(barrio_unmatched)}")


if __name__ == "__main__":
    main()
