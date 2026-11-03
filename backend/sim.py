"""Monte Carlo simulation of cell towers failing over the hours after a flood (or tornado).

Each draw decides, per tower:
  * structural damage        flood: chance the site is under water x share of equipment destroyed
                             tornado: fragility curve on wind speed
  * grid power loss          flooded areas lose power; heavy rain cuts some power region-wide
  * onset                    flood water reaches each tower between 20% and 100% of the peak hour
  * backup duration          battery (2-8 h) or, for ~30% of towers, a generator (24-72 h)
  * power restoration        grid power returns after a lognormal delay (median ~20 h)
  * upstream link            each tower depends on a hub tower; a dark hub takes it down

A tower is up at hour h if it is undamaged, its hub is up, and it has grid power
(never lost, or already restored) or its backup has not run out. A tract has service if any tower in range is up.
All parameters are assumptions, shown as such in the app.
"""
import math

import numpy as np

from . import hazard
from .geo import dist_to_segment_km, pairwise_km, to_xy

HOURS = (0, 2, 4, 6, 12, 24, 48)

PARAMS = dict(
    lambda_wind_km=0.4,      # wind decay outside the damage track
    theta_damage=140, beta_damage=0.25,   # structural fragility (median mph, spread)
    theta_power=55, beta_power=0.35,      # power-equipment fragility
    lambda_power_km=3.0,     # grid outages spread wider than wind damage
    gen_share=0.30,          # share of towers with a generator
    battery_h=(2, 8), generator_h=(24, 72),
    restore_median_h=20, restore_sigma=0.6,   # when grid power comes back (lognormal)
    towers_per_hub=15,
    dense_range_km=2.0, sparse_range_km=5.0,
)

_erf = np.vectorize(math.erf)


def norm_cdf(x):
    return 0.5 * (1.0 + _erf(np.asarray(x, dtype=float) / math.sqrt(2.0)))


def fragility(v, theta, beta):
    """Lognormal fragility curve: P(failure | wind v)."""
    return norm_cdf(np.log(np.maximum(v, 1.0) / theta) / beta)


def wind_field(lat, lon, sc, p=PARAMS):
    """Wind (mph) and distance to the track centerline (km) at each point."""
    d = dist_to_segment_km(lat, lon, sc["start"], sc["end"])
    half = sc["width_m"] / 2000.0
    v = np.where(d <= half, sc["max_wind_mph"],
                 sc["max_wind_mph"] * np.exp(-(d - half) / p["lambda_wind_km"]))
    return v, d


def assign_hubs(lat, lon, per_hub, iters=12, seed=0):
    """Mini k-means on tower locations. Returns the index of each tower's hub tower."""
    x, y = to_xy(lat, lon)
    pts = np.c_[x, y]
    k = max(1, len(pts) // per_hub)
    rng = np.random.default_rng(seed)
    centers = pts[rng.choice(len(pts), k, replace=False)].copy()
    for _ in range(iters):
        labels = ((pts[:, None, :] - centers[None]) ** 2).sum(-1).argmin(1)
        for j in range(k):
            members = labels == j
            if members.any():
                centers[j] = pts[members].mean(0)
    labels = ((pts[:, None, :] - centers[None]) ** 2).sum(-1).argmin(1)
    hub_tower = ((centers[:, None, :] - pts[None]) ** 2).sum(-1).argmin(1)  # real tower nearest each center
    return hub_tower[labels]


def coverage_matrix(tracts, towers, p=PARAMS):
    """Boolean matrix (tracts x towers): which towers reach each tract's center."""
    d = pairwise_km(tracts.lat.values, tracts.lon.values, towers.lat.values, towers.lon.values)
    tt = pairwise_km(towers.lat.values, towers.lon.values, towers.lat.values, towers.lon.values)
    dense = (tt <= 3.0).sum(1) >= 6            # 6+ towers within 3 km counts as dense
    reach = np.where(dense, p["dense_range_km"], p["sparse_range_km"])
    A = d <= reach[None, :]
    A[np.arange(len(d)), d.argmin(1)] = True    # every tract keeps at least its nearest tower
    return A, reach


class Simulator:
    """Holds the static pieces (coverage, hubs) so each scenario run is fast."""

    def __init__(self, data, params=None):
        self.p = dict(PARAMS, **(params or {}))
        self.tracts = data.tracts
        self.towers = data.towers
        self.A, self.reach = coverage_matrix(self.tracts, self.towers, self.p)
        self.Af = self.A.astype(np.float32)
        self.hub_idx = assign_hubs(self.towers.lat.values, self.towers.lon.values,
                                   self.p["towers_per_hub"])

    def simulate(self, sc, n=300, seed=7, hours=HOURS):
        p, T = self.p, len(self.towers)
        rng = np.random.default_rng(seed)
        p_damage, p_power, onset_lo, onset_hi = hazard.tower_effects(
            sc, self.towers.lat.values, self.towers.lon.values, p)

        damaged = rng.random((T, n)) < p_damage[:, None]
        no_power = rng.random((T, n)) < p_power[:, None]
        # when the hazard reaches each tower: instantly for a tornado, as water rises for a flood
        onset = rng.uniform(onset_lo, onset_hi, (T, n)) if onset_hi > onset_lo else np.full((T, n), onset_lo)
        generator = rng.random((T, n)) < p["gen_share"]
        backup = np.where(generator,
                          rng.uniform(*p["generator_h"], (T, n)),
                          rng.uniform(*p["battery_h"], (T, n)))
        # grid power comes back only after the peak (water has to go down first)
        restore = onset_hi + rng.lognormal(np.log(p["restore_median_h"]), p["restore_sigma"], (T, n))

        p_out = np.zeros((len(self.tracts), len(hours)), dtype=np.float32)
        tower_up = np.zeros((T, len(hours)), dtype=np.float32)
        for k, h in enumerate(hours):
            hit = onset <= h
            powered = ~(no_power & hit) | (backup > h - onset) | (restore <= h)
            local = ~(damaged & hit) & powered
            up = local & local[self.hub_idx]                 # hub must be up too
            served = (self.Af @ up.astype(np.float32)) > 0   # tracts x draws
            p_out[:, k] = 1.0 - served.mean(1)
            tower_up[:, k] = up.mean(1)

        pop = self.tracts["pop"].values
        return {
            "hours": list(hours),
            "p_out": p_out,                                   # tracts x hours
            "tower_up": tower_up,                             # towers x hours
            "people_out": (p_out * pop[:, None]).sum(0),      # expected people without service
            "towers_damaged_share": float(damaged.mean()),
            "hazard": sc.get("type", "tornado"),
            "towers_dark_share": (1 - tower_up.mean(0)).tolist(),
        }
