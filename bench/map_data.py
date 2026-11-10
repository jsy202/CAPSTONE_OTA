"""Turn an OSRM route and Overpass road geometry into one compact local-metres map file.

Data © OpenStreetMap contributors (ODbL). Coordinates become metres east/north of an origin so the
IVI can draw them without any tile server (equirectangular projection; fine for a few kilometres).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

METRES_PER_DEG_LAT = 110574.0
METRES_PER_DEG_LON_AT_EQUATOR = 111320.0


def project(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    return ((lon - lon0) * METRES_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(lat0)),
            (lat - lat0) * METRES_PER_DEG_LAT)


def flat(points, origin) -> list[float]:
    out: list[float] = []
    for lon, lat in points:
        x, y = project(lon, lat, *origin)
        out += [round(x, 1), round(y, 1)]
    return out


def build_map(route: dict, roads: dict, origin: tuple[float, float]) -> dict:
    ways = [{'c': way['class'], 'p': flat(way['geometry'], origin)} for way in roads['ways'] if len(way['geometry']) >= 2]
    path = flat(route['coordinates'], origin)
    length = sum(math.dist(path[i:i + 2], path[i + 2:i + 4]) for i in range(0, len(path) - 2, 2))
    return {'origin': list(origin), 'roads': ways, 'route': {'p': path, 'length': length},
            'attribution': roads.get('attribution', '© OpenStreetMap contributors (ODbL)')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('route', type=Path)
    parser.add_argument('roads', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--origin', default='126.9779,37.5663', help='lon,lat (default: Seoul City Hall)')
    args = parser.parse_args()
    lon, lat = (float(v) for v in args.origin.split(','))
    result = build_map(json.loads(args.route.read_text()), json.loads(args.roads.read_text()), (lon, lat))
    args.output.write_text(json.dumps(result, separators=(',', ':')))
    print({'roads': len(result['roads']), 'route_m': round(result['route']['length']), 'bytes': args.output.stat().st_size})
