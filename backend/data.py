"""Load the prepared data files once and keep them in memory."""
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

SITE_TYPES = {
    "school": "School",
    "place_of_worship": "Place of worship",
    "fire_station": "Fire station",
    "community_centre": "Community center",
    "hospital": "Hospital",
}


@dataclass
class Data:
    tracts: pd.DataFrame   # geoid, county, lat, lon, pop, svi
    towers: pd.DataFrame   # tower_id, lon, lat, n_cells, radios, max_range_m
    sites: pd.DataFrame    # site_id, name, type, type_label, lat, lon, is_hospital
    eocs: pd.DataFrame     # county, name, address, lat, lon


def load_sites(path=DATA / "sites.geojson") -> pd.DataFrame:
    feats = json.loads(Path(path).read_text())["features"]
    rows = []
    for f in feats:
        p = f["properties"]
        kind = p.get("amenity")
        if kind not in SITE_TYPES:
            continue
        if not p.get("name") and kind not in ("fire_station", "hospital"):
            continue          # unnamed sites can't be called or found on the ground
        lon, lat = f["geometry"]["coordinates"]
        street = " ".join(x for x in (p.get("addr:housenumber"), p.get("addr:street")) if x)
        city = ", ".join(x for x in (p.get("addr:city"), p.get("addr:postcode")) if x)
        rows.append({
            "osm_id": f.get("id", p.get("@id")),
            "name": p.get("name") or f"Unnamed {SITE_TYPES[kind].lower()}",
            "type": kind,
            "type_label": SITE_TYPES[kind],
            "lat": float(lat),
            "lon": float(lon),
            # contact details from OpenStreetMap, used by the Grok Bots (often missing)
            "address": ", ".join(x for x in (street, city) if x),
            "website": p.get("website") or p.get("contact:website") or "",
            "phone": p.get("phone") or p.get("contact:phone") or "",
            "email": p.get("email") or p.get("contact:email") or "",
            "facebook": p.get("contact:facebook") or "",
        })
    df = pd.DataFrame(rows).reset_index(drop=True)
    df.insert(0, "site_id", [f"s_{i}" for i in range(len(df))])
    df["is_hospital"] = df["type"].eq("hospital")
    return df


def load_tracts(path=DATA / "tracts.csv") -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"geoid": str})
    df["pop"] = df["pop"].clip(lower=0)
    df["svi"] = df["svi"].fillna(df["svi"].median())   # tracts without a score get the median
    return df.reset_index(drop=True)


_cache = None


def load_all(tracts_path=None) -> Data:
    """Load everything once; later calls return the cached copy."""
    global _cache
    if _cache is None:
        _cache = Data(
            tracts=load_tracts(tracts_path or DATA / "tracts.csv"),
            towers=pd.read_csv(DATA / "towers.csv"),
            sites=load_sites(),
            eocs=pd.read_csv(DATA / "eocs.csv"),
        )
    return _cache
