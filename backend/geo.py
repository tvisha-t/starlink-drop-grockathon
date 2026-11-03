"""Small geometry helpers that need only NumPy (no GIS libraries).

Everything is projected to a flat local x/y grid in kilometres centred on
Atlanta. Over a 60 km area the error is far below anything that matters here.
"""
import numpy as np

R_KM = 6371.0
LAT0, LON0 = 33.75, -84.39
_COS0 = np.cos(np.radians(LAT0))


def to_xy(lat, lon):
    """Project lat/lon in degrees to local x/y in km."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    x = np.radians(lon - LON0) * R_KM * _COS0
    y = np.radians(lat - LAT0) * R_KM
    return x, y


def to_latlon(x, y):
    """Inverse of to_xy."""
    lat = LAT0 + np.degrees(np.asarray(y) / R_KM)
    lon = LON0 + np.degrees(np.asarray(x) / (R_KM * _COS0))
    return lat, lon


def pairwise_km(lat1, lon1, lat2, lon2):
    """Distance matrix of shape (len1, len2) in km."""
    x1, y1 = to_xy(lat1, lon1)
    x2, y2 = to_xy(lat2, lon2)
    return np.hypot(x1[:, None] - x2[None, :], y1[:, None] - y2[None, :])


def dist_to_segment_km(lat, lon, a, b):
    """Distance from each point to the segment a->b (each [lat, lon]), in km."""
    px, py = to_xy(lat, lon)
    ax, ay = to_xy(a[0], a[1])
    bx, by = to_xy(b[0], b[1])
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0:
        return np.hypot(px - ax, py - ay)
    t = np.clip(((px - ax) * dx + (py - ay) * dy) / seg2, 0.0, 1.0)
    return np.hypot(px - (ax + t * dx), py - (ay + t * dy))


def endpoint(start, bearing_deg, length_km):
    """End point [lat, lon] given a start point, compass bearing and length."""
    x0, y0 = to_xy(start[0], start[1])
    b = np.radians(bearing_deg)
    lat, lon = to_latlon(x0 + length_km * np.sin(b), y0 + length_km * np.cos(b))
    return [round(float(lat), 6), round(float(lon), 6)]
