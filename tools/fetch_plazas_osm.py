#!/usr/bin/env python3
import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "lugares_extra.csv"


OVERPASS_QUERY = """
[out:json][timeout:25];
(
  node["leisure"="park"](-34.20,-59.30,-33.95,-58.85);
  way["leisure"="park"](-34.20,-59.30,-33.95,-58.85);
  relation["leisure"="park"](-34.20,-59.30,-33.95,-58.85);
  node["place"="square"](-34.20,-59.30,-33.95,-58.85);
  way["place"="square"](-34.20,-59.30,-33.95,-58.85);
  relation["place"="square"](-34.20,-59.30,-33.95,-58.85);
);
out center tags;
"""


def main():
    url = "https://overpass-api.de/api/interpreter"
    data = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": "municipio-empresa-ciudad/1.0"},
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8"))

    rows = []
    seen = set()
    for element in payload.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name") or tags.get("official_name")
        if not name:
            continue
        lat = element.get("lat") or element.get("center", {}).get("lat")
        lon = element.get("lon") or element.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        lat = float(lat)
        lon = float(lon)
        # Keep Ciudad/Localidad and discard nearby Ciudad results from the broad bbox.
        if lon > -59.0:
            continue
        city = "Localidad" if lon < -59.12 else "Ciudad"
        key = (name.strip().lower(), city)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "nombre": name.strip(),
                "rubro": "PLAZA",
                "direccion": "",
                "ciudad": city,
                "telefono": "",
                "observaciones": "Plaza/parque agregado desde OpenStreetMap",
                "lat": lat,
                "lon": lon,
                "fuente": f"OpenStreetMap {element.get('type')}/{element.get('id')}",
            }
        )

    rows.sort(key=lambda row: row["nombre"].lower())
    OUTPUT.parent.mkdir(exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "nombre",
                "rubro",
                "direccion",
                "ciudad",
                "telefono",
                "observaciones",
                "lat",
                "lon",
                "fuente",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"Escrito: {OUTPUT}")
    print(f"Plazas/parques: {len(rows)}")


if __name__ == "__main__":
    main()
