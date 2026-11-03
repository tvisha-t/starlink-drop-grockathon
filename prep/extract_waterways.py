"""Extract Atlanta's rivers, creeks and lakes from OpenStreetMap -> data/waterways.csv.

Each waterway line is turned into points every ~60 m so the app can quickly find
how far any tower, road or site is from water.

Run once (from the repo root):  python3 prep/extract_waterways.py
Needs:  pip install osmium   and   data/atl.osm.pbf (see README: road network steps)
"""
import csv
import math
import sys

import osmium

SRC = sys.argv[1] if len(sys.argv) > 1 else "data/atl.osm.pbf"
OUT = "data/waterways.csv"
STEP_M = 60.0
KEEP = {"river": "river", "stream": "stream", "canal": "stream"}   # drains/ditches are too small to matter


def dist_m(a, b):
    dy = (b[0] - a[0]) * 111_320.0
    dx = (b[1] - a[1]) * 111_320.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


class Handler(osmium.SimpleHandler):
    def __init__(self, writer):
        super().__init__()
        self.w = writer
        self.n_lines = 0
        self.n_points = 0

    def emit(self, coords, name, kind, way_id):
        """Write points along the line every STEP_M metres (plus the end point)."""
        self.n_lines += 1
        out = [coords[0]]
        carried = 0.0                      # metres walked since the last written point
        for prev, cur in zip(coords, coords[1:]):
            d = dist_m(prev, cur)
            pos = STEP_M - carried         # distance into this segment of the next point
            while pos <= d:
                t = pos / d
                out.append((prev[0] + (cur[0] - prev[0]) * t, prev[1] + (cur[1] - prev[1]) * t))
                pos += STEP_M
            carried = d - (pos - STEP_M)
        out.append(coords[-1])
        for lat, lon in out:
            self.w.writerow([way_id, name, kind, round(lat, 6), round(lon, 6)])
        self.n_points += len(out)

    def way(self, w):
        tags = w.tags
        kind = KEEP.get(tags.get("waterway", ""))
        is_lake = tags.get("natural") == "water" and tags.get("water") in ("lake", "reservoir", "river", "pond")
        if not kind and not is_lake:
            return
        try:
            coords = [(n.lat, n.lon) for n in w.nodes if n.location.valid()]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        self.emit(coords, tags.get("name", ""), kind or "lake", w.id)


with open(OUT, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["way_id", "name", "kind", "lat", "lon"])
    h = Handler(writer)
    h.apply_file(SRC, locations=True)

print(f"{h.n_lines:,} waterways -> {h.n_points:,} points in {OUT}")
