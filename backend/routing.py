"""Truck routing on the real road network, with flooded and closed roads removed.

Pipeline
  1. Load the OSMnx road graph once and convert it to plain arrays (cached in
     data/roads_cache.npz so later startups take well under a second).
  2. For a scenario, drop every road segment that is under water (or inside a
     tornado track) or reported closed, and slow down segments near the water.
  3. Dijkstra (SciPy) from the depot and from every planned site gives a
     travel-time matrix. Sites with no path are unreachable.
  4. Dispatch: the truck that is free soonest drives to the site with the most
     people reached per minute of driving; then 2-opt swaps reduce total
     people-hours without service.
  5. Each route is returned as [lon, lat, seconds] points for deck.gl's TripsLayer.
"""
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from . import hazard
from .geo import to_xy

DATA = Path(__file__).resolve().parent.parent / "data"
SETUP_MIN = 10.0          # minutes to unload and set up a terminal at a site
DEBRIS_FACTOR = 2.0       # travel-time multiplier on slowed roads (standing water, debris)


class RoadNetwork:
    def __init__(self, graphml=DATA / "atlanta_drive.graphml", cache=DATA / "roads_cache.npz"):
        if Path(cache).exists() and Path(cache).stat().st_mtime >= Path(graphml).stat().st_mtime:
            z = np.load(cache)
            self.node_lat, self.node_lon = z["node_lat"], z["node_lon"]
            self.u, self.v, self.t = z["u"], z["v"], z["t"]
        else:
            self._build(graphml)
            np.savez_compressed(cache, node_lat=self.node_lat, node_lon=self.node_lon,
                                u=self.u, v=self.v, t=self.t)
        x, y = to_xy(self.node_lat, self.node_lon)
        self.tree = cKDTree(np.c_[x, y])
        self.mid_lat = (self.node_lat[self.u] + self.node_lat[self.v]) / 2
        self.mid_lon = (self.node_lon[self.u] + self.node_lon[self.v]) / 2

    def _build(self, graphml):
        import osmnx as ox   # only needed the first time
        G = ox.load_graphml(graphml)
        ids = list(G.nodes)
        index = {n: i for i, n in enumerate(ids)}
        self.node_lat = np.array([G.nodes[n]["y"] for n in ids])
        self.node_lon = np.array([G.nodes[n]["x"] for n in ids])
        best = {}
        for a, b, d in G.edges(data=True):          # keep the fastest of parallel edges
            key = (index[a], index[b])
            tt = float(d.get("travel_time") or 60.0)
            if key not in best or tt < best[key]:
                best[key] = tt
        keys = np.array(list(best.keys()), dtype=np.int64)
        self.u, self.v = keys[:, 0], keys[:, 1]
        self.t = np.array(list(best.values()), dtype=np.float64)

    # ---------- helpers ----------
    def nearest_node(self, lat, lon):
        x, y = to_xy(np.atleast_1d(lat), np.atleast_1d(lon))
        dist, idx = self.tree.query(np.c_[x, y])
        return idx, dist   # dist in km

    def damaged_graph(self, sc):
        """Travel-time matrix with the disaster's effect applied. Returns (csr, n_blocked).
        sc=None gives the dry road network: trucks that leave before the water rises."""
        if sc is None:
            blocked, t = np.zeros(len(self.t), dtype=bool), self.t
        else:
            blocked, slowed = hazard.road_effects(sc, self.mid_lat, self.mid_lon)
            blocked = blocked | self._extra_blocked(sc)
            t = np.where(slowed, self.t * DEBRIS_FACTOR, self.t)
        keep = ~blocked
        n = len(self.node_lat)
        g = csr_matrix((t[keep], (self.u[keep], self.v[keep])), shape=(n, n))
        return g, int(blocked.sum())

    def _extra_blocked(self, sc, radius_km=0.15):
        """Road closures reported from the field (Scout Bot, calls): block segments near each point."""
        pts = sc.get("road_closures") or []
        out = np.zeros(len(self.t), dtype=bool)
        if not pts:
            return out
        mx, my = to_xy(self.mid_lat, self.mid_lon)
        for c in pts:
            cx, cy = to_xy(np.array([c["lat"]]), np.array([c["lon"]]))
            out |= np.hypot(mx - cx[0], my - cy[0]) <= radius_km
        return out

    def blocked_segments(self, sc, limit=4000):
        """Line segments of blocked roads, for drawing on the map."""
        blocked, _ = hazard.road_effects(sc, self.mid_lat, self.mid_lon)
        idx = np.flatnonzero(blocked | self._extra_blocked(sc))[:limit]
        return [[[float(self.node_lon[self.u[i]]), float(self.node_lat[self.u[i]])],
                 [float(self.node_lon[self.v[i]]), float(self.node_lat[self.v[i]])]] for i in idx]

    # ---------- main entry ----------
    def plan_routes(self, sc, depot, sites, n_trucks=2):
        """depot: dict(lat, lon); sites: list of dicts with lat, lon, people_reached, site_id, name."""
        g, n_blocked = self.damaged_graph(sc)
        pts_lat = np.array([depot["lat"]] + [s["lat"] for s in sites])
        pts_lon = np.array([depot["lon"]] + [s["lon"] for s in sites])
        nodes, snap_km = self.nearest_node(pts_lat, pts_lon)

        dist_s, pred = dijkstra(g, directed=True, indices=nodes, return_predecessors=True)
        tt_min = dist_s[:, nodes] / 60.0                      # stops x stops, minutes
        reachable = np.isfinite(tt_min[0])
        reachable[0] = True

        people = np.array([0.0] + [float(s.get("people_reached", 0)) for s in sites])
        stops = [i for i in range(1, len(nodes)) if reachable[i]]
        routes = dispatch(tt_min, people, stops, n_trucks)

        out_routes, arrivals = [], {}
        for k, route in enumerate(routes):
            path, clock, at = [], 0.0, 0
            for j in route:
                seg = self._path_coords(pred[at], nodes[at], nodes[j])
                times = self._seg_times(seg, clock, tt_min[at, j] * 60)
                path.extend([[lon, lat, ts] for (lon, lat), ts in zip(seg, times)])
                clock += tt_min[at, j] * 60
                arrivals[sites[j - 1]["site_id"]] = {"truck": k, "arrive_min": round(clock / 60, 1)}
                clock += SETUP_MIN * 60
                at = j
            out_routes.append({"truck": k, "stops": [sites[j - 1]["site_id"] for j in route],
                               "path": path, "duration_min": round(clock / 60, 1)})

        unreachable = [sites[i - 1]["site_id"] for i in range(1, len(nodes)) if not reachable[i]]
        # comparison: same trucks, sites delivered in plan-rank order, round-robin
        naive_routes = [stops[k::max(1, n_trucks)] for k in range(max(1, n_trucks))]
        naive = sum(people_hours(r, tt_min, people) for r in naive_routes)
        best = sum(people_hours(r, tt_min, people) for r in routes)
        return {
            "routes": out_routes,
            "arrivals": arrivals,
            "unreachable": unreachable,
            "blocked_road_segments": n_blocked,
            "people_hours": round(best / 60, 1),
            "people_hours_rank_order": round(naive / 60, 1),
            "reconnected_by_hour": reconnected_by_hour(arrivals, sites),
        }

    def _path_coords(self, pred_row, src, dst):
        nodes = []
        cur = dst
        while cur != src and cur >= 0:
            nodes.append(cur)
            cur = pred_row[cur]
        nodes.append(src)
        nodes.reverse()
        return [(float(self.node_lon[n]), float(self.node_lat[n])) for n in nodes]

    @staticmethod
    def _seg_times(seg, start_s, total_s):
        """Spread total_s along the path in proportion to segment length."""
        if len(seg) < 2:
            return [start_s] * len(seg)
        lon = np.array([p[0] for p in seg])
        lat = np.array([p[1] for p in seg])
        x, y = to_xy(lat, lon)
        step = np.r_[0, np.hypot(np.diff(x), np.diff(y))]
        frac = np.cumsum(step) / max(step.sum(), 1e-9)
        return (start_s + frac * total_s).round(1).tolist()


# ---------- dispatch heuristics (pure functions, easy to test) ----------
def people_hours(route, tt_min, people, start=0):
    """Sum over stops of people x minutes until their terminal is running."""
    t, at, cost = 0.0, start, 0.0
    for j in route:
        t += tt_min[at, j] + SETUP_MIN
        cost += people[j] * t
        at = j
    return cost


def dispatch(tt_min, people, stops, n_trucks):
    trucks = [{"at": 0, "t": 0.0, "route": []} for _ in range(max(1, n_trucks))]
    todo = set(stops)
    while todo:
        tr = min(trucks, key=lambda x: x["t"])
        j = max(todo, key=lambda s: people[s] / (tt_min[tr["at"], s] + SETUP_MIN))
        tr["t"] += tt_min[tr["at"], j] + SETUP_MIN
        tr["at"] = j
        tr["route"].append(j)
        todo.remove(j)
    return [two_opt(tr["route"], tt_min, people) for tr in trucks]


def two_opt(route, tt_min, people, max_rounds=50):
    best = list(route)
    best_cost = people_hours(best, tt_min, people)
    for _ in range(max_rounds):
        improved = False
        for i in range(len(best) - 1):
            for k in range(i + 1, len(best)):
                cand = best[:i] + best[i:k + 1][::-1] + best[k + 1:]
                c = people_hours(cand, tt_min, people)
                if c < best_cost - 1e-9:
                    best, best_cost, improved = cand, c, True
        if not improved:
            break
    return best


def reconnected_by_hour(arrivals, sites, hours=(1, 2, 3, 4, 6)):
    people = {s["site_id"]: s.get("people_reached", 0) for s in sites}
    return {h: int(sum(people[sid] for sid, a in arrivals.items() if a["arrive_min"] <= h * 60))
            for h in hours}
