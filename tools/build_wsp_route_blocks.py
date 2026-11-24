#!/usr/bin/env python3
import json
import math
from pathlib import Path

import networkx as nx


ROOT = Path(__file__).resolve().parents[1]
STREETS_PATH = ROOT / "data" / "calles_ciudad.geojson"
OUTPUT_PATH = ROOT / "data" / "grupos_wsp_perimetros_ruta.geojson"


RULES = [
    {
        "group": "A",
        "label": "Grupo A",
        "start_streets": ("Avenida Norte", "Urquiza"),
        "end_streets": ("Avenida Principal", "Independencia"),
        "main_side_prefer": ("Avenida Norte", "Avenida Principal"),
        "alternate_exclude": ("Avenida Norte", "Avenida Principal"),
        "note": "Borde principal por Norte/Principal y cierre por calles conectadas entre Urquiza e Independencia.",
    }
]


def distance(a, b):
    return math.hypot((a[0] - b[0]) * 92300, (a[1] - b[1]) * 111000)


def key(point):
    return (round(point[0], 7), round(point[1], 7))


def load_streets():
    return json.loads(STREETS_PATH.read_text(encoding="utf-8")).get("features", [])


def street_points(streets, name):
    points = []
    for feature in streets:
        if feature.get("properties", {}).get("name") != name:
            continue
        if feature.get("geometry", {}).get("type") != "LineString":
            continue
        points.extend(feature["geometry"]["coordinates"])
    return points


def nearest_between(streets, first_name, second_name):
    first = street_points(streets, first_name)
    second = street_points(streets, second_name)
    best = None
    for a in first:
        for b in second:
            candidate = (distance(a, b), key(a), key(b))
            if best is None or candidate[0] < best[0]:
                best = candidate
    if not best:
        raise ValueError(f"No pude cruzar {first_name} con {second_name}")
    return best[1]


def build_graph(streets, include=None, exclude=()):
    include = set(include or [])
    exclude = set(exclude or [])
    graph = nx.Graph()
    for feature in streets:
        name = feature.get("properties", {}).get("name", "")
        if include and name not in include:
            continue
        if name in exclude:
            continue
        if feature.get("geometry", {}).get("type") != "LineString":
            continue
        coords = feature.get("geometry", {}).get("coordinates", [])
        for a, b in zip(coords, coords[1:]):
            ka, kb = key(a), key(b)
            graph.add_edge(ka, kb, weight=distance(ka, kb), name=name)
    return graph


def nearest_node(graph, point):
    return min(graph.nodes, key=lambda node: distance(node, point))


def shortest_path(graph, start, end):
    start_node = nearest_node(graph, start)
    end_node = nearest_node(graph, end)
    return nx.shortest_path(graph, start_node, end_node, weight="weight")


def path_names(graph, path):
    names = []
    for a, b in zip(path, path[1:]):
        name = graph[a][b]["name"]
        if not names or names[-1] != name:
            names.append(name)
    return names


def main():
    streets = load_streets()
    features = []
    for rule in RULES:
        start = nearest_between(streets, *rule["start_streets"])
        end = nearest_between(streets, *rule["end_streets"])
        main_graph = build_graph(streets)
        alt_graph = build_graph(streets, exclude=rule["alternate_exclude"])
        main_path = shortest_path(main_graph, start, end)
        alt_path = shortest_path(alt_graph, start, end)
        ring = [[lon, lat] for lon, lat in main_path + list(reversed(alt_path))[1:]]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        features.append({
            "type": "Feature",
            "properties": {
                "group": rule["group"],
                "label": rule["label"],
                "method": "street_route_ring",
                "status": "draft_review_required",
                "note": rule["note"],
                "main_path_streets": path_names(main_graph, main_path),
                "alternate_path_streets": path_names(alt_graph, alt_path),
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [ring],
            },
        })
        print(rule["label"])
        print("  main:", " -> ".join(path_names(main_graph, main_path)))
        print("  cierre:", " -> ".join(path_names(alt_graph, alt_path)))

    OUTPUT_PATH.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": "grupos_wsp_perimetros_ruta",
        "features": features,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
