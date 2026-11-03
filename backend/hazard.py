"""What the disaster does, wherever it is: towers, roads and sites.

Two hazard types share one interface, so the simulation, optimizer and routing
don't care which one is active:

  flood    (default) water spreads out from Atlanta's rivers and creeks. How bad it
           gets comes from the scenario's severity, or from a flood forecast in the
           format of BDBI Aquatic Sustainability's flood model (lat, lon, prob).
  tornado  the original wind-track model, kept for comparison.

For a flood, the chance a place is under water at the peak is

    p_flood = severity_at_nearest_water x exp(-distance_to_water / reach)

where reach is wider for rivers than creeks and grows with severity. Water
rises between 20% and 100% of the peak hour, so towers fail over time, and
backup batteries start draining only once a tower loses power.
"""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .geo import dist_to_segment_km, to_xy

DATA = Path(__file__).resolve().parent.parent / "data"

# how far flooding spreads from each kind of waterway at severity 1 (km)
REACH_KM = {"river": 0.9, "lake": 0.45, "stream": 0.35}
SEARCH_KM = 3.0

FLOOD = dict(
    damage_share=0.35,      # share of flooded towers whose ground equipment is destroyed
    power_gain=1.2,         # flooded areas lose grid power almost surely
    regional_power=0.15,    # heavy rain also cuts power across the wider area
    road_block=0.45,        # p_flood above this: road under water
    road_slow=0.20,         # above this: passable but slow
    site_flooded=0.40,      # above this: the site itself is likely flooded
)

SEVERITY_WORDS = {"minor": 0.35, "moderate": 0.55, "major": 0.75, "record": 0.95, "catastrophic": 0.95}


# ---------------------------------------------------------------------------
# Waterways and flood field
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def waterways():
    df = pd.read_csv(DATA / "waterways.csv").fillna({"name": ""})
    x, y = to_xy(df.lat.values, df.lon.values)
    tree = cKDTree(np.c_[x, y])
    reach = df.kind.map(REACH_KM).fillna(0.35).values
    return df, tree, reach


@lru_cache(maxsize=1)
def waterway_names():
    """Named waterways and their center point, for lookups like 'Peachtree Creek'."""
    df, _, _ = waterways()
    named = df[df.name != ""]
    g = named.groupby("name").agg(lat=("lat", "mean"), lon=("lon", "mean"), n=("lat", "size"))
    return g[g.n >= 5].sort_values("n", ascending=False)


def load_forecast(path):
    """Read a flood forecast in Aquatic Sustainability's output format: lat, lon, prob[, prediction]."""
    f = pd.read_csv(path)
    return f[["lat", "lon", "prob"]].dropna()


def _point_severity(sc):
    """Severity (0..1) at every waterway point for this scenario."""
    df, _, _ = waterways()
    if sc.get("forecast_file"):
        # Inverse-distance weighting of the forecast's flood probabilities onto each waterway point
        fc = load_forecast(sc["forecast_file"])
        fx, fy = to_xy(fc.lat.values, fc.lon.values)
        wx, wy = to_xy(df.lat.values, df.lon.values)
        d = np.hypot(wx[:, None] - fx[None, :], wy[:, None] - fy[None, :]) + 0.5
        w = 1.0 / d ** 2
        return (w @ fc.prob.values) / w.sum(1)
    s = float(sc.get("severity", 0.75))
    # waterways named in the scenario flood fully; others by distance from the focus point
    named = set(n.lower() for n in sc.get("rivers", []))
    fx, fy = to_xy(*sc["focus"])
    wx, wy = to_xy(df.lat.values, df.lon.values)
    r = float(sc.get("radius_km", 10.0))
    weight = np.exp(-((wx - fx) ** 2 + (wy - fy) ** 2) / (2 * (r / 1.5) ** 2))
    if named:
        on_named = df.name.str.lower().isin(named).values
        weight = np.where(on_named, 1.0, weight * 0.6)
    return s * weight


@lru_cache(maxsize=8)
def _cached_severity(key):
    import json
    return _point_severity(json.loads(key))


def scenario_from_forecast(points, path, model="Aquatic Sustainability flood model", run_date=None, terminals=12):
    """Turn a flood-model forecast (list of {lat, lon, prob}) into a flood scenario.

    The model gives flood probability on a coarse grid (0.1 degree rainfall cells).
    Starlink Drop spreads that onto the river and creek network, so the forecast says
    how likely flooding is and the waterways say which streets go under."""
    import pandas as pd
    from .geo import pairwise_km
    f = pd.DataFrame(points)[["lat", "lon", "prob"]].astype(float)
    f.to_csv(path, index=False)
    # points inside the study area (about 40 km around the city)
    d = pairwise_km(np.array([33.75]), np.array([-84.39]), f.lat.values, f.lon.values)[0]
    near = f[d <= 40] if (d <= 40).any() else f
    w = near.prob.values ** 2 + 1e-9
    focus = [float((near.lat * w).sum() / w.sum()), float((near.lon * w).sum() / w.sum())]
    peak = float(near.prob.max())
    risk = "high" if peak >= 0.75 else "elevated" if peak >= 0.5 else "moderate" if peak >= 0.3 else "low"
    return {
        "name": f"Forecast: {risk} flood risk ({model}{', ' + run_date if run_date else ''})",
        "type": "flood", "focus": focus, "radius_km": 12.0, "severity": round(peak, 3), "rivers": [],
        "peak_hour": 8.0, "terminals": int(terminals), "forecast_file": str(path),
        "forecast": {"model": model, "run_date": run_date, "points": len(f), "max_prob": round(peak, 3),
                     "risk": risk},
        # the model's own output, drawn on the map as-is
        "forecast_points": [[round(float(o), 4), round(float(a), 4), round(float(p), 3)]
                            for a, o, p in zip(f.lat, f.lon, f.prob)],
    }


def _key(sc):
    """Cache key: only the fields that change where the water goes."""
    import json
    keep = ("type", "focus", "radius_km", "severity", "rivers", "forecast_file")
    return json.dumps({k: sc.get(k) for k in keep}, sort_keys=True)


def p_flood(sc, lat, lon):
    """Chance each location is under water at the flood's peak."""
    df, tree, reach = waterways()
    sev = _cached_severity(_key(sc))
    x, y = to_xy(np.atleast_1d(lat), np.atleast_1d(lon))
    dist, idx = tree.query(np.c_[x, y], k=12, distance_upper_bound=SEARCH_KM)
    valid = np.isfinite(dist)
    idx = np.where(valid, idx, 0)
    s = sev[idx]
    grow = 0.5 + np.clip(s, 0, 1)                            # worse floods spread further
    val = np.where(valid, s * np.exp(-np.where(valid, dist, 99) / (reach[idx] * grow)), 0.0).max(1)
    for r in sc.get("flood_reports") or []:          # flooding seen on the ground beats the model
        rx, ry = to_xy(np.array([r["lat"]]), np.array([r["lon"]]))
        val = np.maximum(val, 0.9 * np.exp(-np.hypot(x - rx[0], y - ry[0]) / 0.5))
    return np.clip(val, 0, 1)


# ---------------------------------------------------------------------------
# The interface used by the simulation, optimizer and routing
# ---------------------------------------------------------------------------
def tower_effects(sc, lat, lon, wind_params):
    """Per-tower (p_damage, p_power, onset_lo_h, onset_hi_h)."""
    if sc.get("type") == "tornado":
        from .sim import fragility, wind_field
        v, d = wind_field(lat, lon, sc, wind_params)
        half = sc["width_m"] / 2000.0
        p_damage = fragility(v, wind_params["theta_damage"], wind_params["beta_damage"])
        p_power = np.maximum(fragility(v, wind_params["theta_power"], wind_params["beta_power"]),
                             np.exp(-np.maximum(d - half, 0.0) / wind_params["lambda_power_km"]))
        return p_damage, p_power, 0.0, 0.0
    pf = p_flood(sc, lat, lon)
    s = float(sc.get("severity", 0.75))
    p_damage = FLOOD["damage_share"] * pf
    p_power = np.clip(np.maximum(FLOOD["power_gain"] * pf, FLOOD["regional_power"] * s), 0, 1)
    peak = float(sc.get("peak_hour", 6))
    return p_damage, p_power, 0.2 * peak, peak


def road_effects(sc, lat, lon):
    """(blocked, slowed) boolean arrays for road segment midpoints."""
    if sc.get("type") == "tornado":
        d = dist_to_segment_km(lat, lon, sc["start"], sc["end"])
        half = sc["width_m"] / 2000.0
        return d <= half, d <= half * 3.0
    pf = p_flood(sc, lat, lon)
    return pf >= FLOOD["road_block"], pf >= FLOOD["road_slow"]


def site_unusable(sc, lat, lon):
    """Sites that are likely damaged or under water themselves."""
    if sc.get("type") == "tornado":
        return dist_to_segment_km(lat, lon, sc["start"], sc["end"]) <= sc["width_m"] / 2000.0
    return p_flood(sc, lat, lon) >= FLOOD["site_flooded"]


def closeness_to_damage(sc, lat, lon):
    """Higher = closer to the worst of the disaster (for the 'closest to the damage' baseline)."""
    if sc.get("type") == "tornado":
        return -dist_to_segment_km(lat, lon, sc["start"], sc["end"])
    return p_flood(sc, lat, lon)


def map_layer(sc, min_p=0.12):
    """What to draw: the tornado track, or flooded water corridors as [lon, lat, radius_m, intensity]."""
    if sc.get("type") == "tornado":
        return {"type": "tornado", "path": [[sc["start"][1], sc["start"][0]], [sc["end"][1], sc["end"][0]]],
                "width_m": sc["width_m"]}
    df, _, reach = waterways()
    sev = _cached_severity(_key(sc))
    radius = np.minimum(reach * (0.5 + np.clip(sev, 0, 1)) * 1000 * np.clip(sev, 0, 1), 650)
    work = pd.DataFrame({"way": df.way_id.values, "lon": df.lon.values.round(5), "lat": df.lat.values.round(5),
                         "w": radius, "s": sev})
    work = work[work.s >= min_p]
    paths = []
    for _, g in work.groupby("way", sort=False):
        if len(g) < 2:
            continue
        paths.append({"path": g[["lon", "lat"]].values.tolist(), "width_m": round(float(2 * g.w.mean())),
                      "intensity": round(float(g.s.mean()), 2)})
    pts = [[r["lon"], r["lat"], 500, 0.9] for r in sc.get("flood_reports") or []]
    return {"type": "flood", "paths": paths, "points": pts, "forecast_points": sc.get("forecast_points", [])}
