"""Disaster scenarios: flood (default) and tornado presets, defaults and validation."""
from .geo import endpoint

# Mid-range wind speed (mph) and a typical damage-track width (m) per EF rating
EF_WIND = {0: 75, 1: 98, 2: 123, 3: 150, 4: 183, 5: 210}
EF_WIDTH = {0: 150, 1: 300, 2: 500, 3: 800, 4: 1200, 5: 1600}

SEVERITY = {"minor": 0.35, "moderate": 0.55, "major": 0.75, "record": 0.95}

PRESETS = {
    "south_river_flood": {
        "name": "Major flooding along the South River and Snapfinger Creek",
        "type": "flood",
        "focus": [33.690, -84.230],     # south DeKalb, where the two meet
        "radius_km": 9.0,
        "severity": 0.80,
        "rivers": ["South River", "Snapfinger Creek", "Shoal Creek", "Cobbs Creek"],
        "peak_hour": 8,
        "rain_in": 7.0,
        "terminals": 12,
    },
    "peachtree_creek_flood": {
        "name": "Flash flooding on Peachtree Creek",
        "type": "flood",
        "focus": [33.820, -84.330],
        "radius_km": 7.0,
        "severity": 0.70,
        "rivers": ["Peachtree Creek", "South Fork Peachtree Creek", "North Fork Peachtree Creek", "Burnt Fork Creek"],
        "peak_hour": 4,
        "rain_in": 5.0,
        "terminals": 12,
    },
    "south_dekalb_ef3": {
        "name": "EF3 tornado through south DeKalb",
        "type": "tornado",
        "start": [33.665, -84.330],
        "end": [33.767, -84.207],      # about 10 miles toward the northeast
        "width_m": 800,
        "max_wind_mph": 150,
        "terminals": 12,
    },
}


def make_scenario(start, end=None, bearing_deg=None, length_km=None, ef=3,
                  width_m=None, max_wind_mph=None, terminals=12, name=None) -> dict:
    """Build a validated tornado scenario. Give either an end point or a bearing + length."""
    if end is None:
        if bearing_deg is None or length_km is None:
            raise ValueError("Give an end point, or a bearing and a length.")
        end = endpoint(start, bearing_deg, length_km)
    ef = int(min(max(ef, 0), 5))
    return {
        "name": name or f"EF{ef} tornado",
        "type": "tornado",
        "start": [float(start[0]), float(start[1])],
        "end": [float(end[0]), float(end[1])],
        "width_m": float(width_m or EF_WIDTH[ef]),
        "max_wind_mph": float(max_wind_mph or EF_WIND[ef]),
        "terminals": int(terminals),
    }


def make_flood_scenario(focus, severity=0.75, rivers=(), radius_km=9.0, peak_hour=6,
                        rain_in=None, terminals=12, name=None, forecast_file=None) -> dict:
    """Build a validated flood scenario. severity is 0..1 or a word (minor/moderate/major/record)."""
    if isinstance(severity, str):
        severity = SEVERITY.get(severity.lower(), 0.75)
    sc = {
        "name": name or "Flooding",
        "type": "flood",
        "focus": [float(focus[0]), float(focus[1])],
        "radius_km": float(min(max(radius_km, 2.0), 25.0)),
        "severity": float(min(max(severity, 0.1), 1.0)),
        "rivers": [str(r) for r in rivers],
        "peak_hour": float(min(max(peak_hour, 1), 24)),
        "terminals": int(terminals),
    }
    if rain_in is not None:
        sc["rain_in"] = float(rain_in)
    if forecast_file:
        sc["forecast_file"] = str(forecast_file)
    return sc


DEFAULT_PRESET = "south_river_flood"
