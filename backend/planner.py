"""The planner: one object that holds the current scenario, simulation, plan and
routes, and re-plans when anything changes (a new flood, a closed site, more
terminals). The API, the Grok agent and the voice calls all go through it.
"""
import threading
import time

import numpy as np

from . import hazard
from .data import load_all
from .optimize import Optimizer
from .scenario import DEFAULT_PRESET, PRESETS
from .sim import Simulator


class Planner:
    def __init__(self, with_roads=True):
        t0 = time.time()
        self.data = load_all()
        self.sim_engine = Simulator(self.data)
        self.opt = Optimizer(self.data)
        self.roads = None
        if with_roads:
            try:
                from .routing import RoadNetwork
                self.roads = RoadNetwork()
            except Exception as e:     # routing is optional; the core still works
                print(f"[planner] road network unavailable: {e}")
        self.lock = threading.RLock()
        self.listeners = []            # callables(event: dict) for live updates
        self.site_index = {sid: i for i, sid in enumerate(self.data.sites.site_id)}
        dek = self.data.eocs[self.data.eocs.county == "DeKalb"].iloc[0]
        self.depot = {"name": dek["name"], "lat": float(dek.lat), "lon": float(dek.lon)}
        self.reset(PRESETS[DEFAULT_PRESET])
        print(f"[planner] ready in {time.time() - t0:.1f}s")

    # ---------- events ----------
    def emit(self, event):
        for fn in list(self.listeners):
            try:
                fn(event)
            except Exception as e:
                print(f"[planner] listener error: {e}")

    # ---------- state changes ----------
    def reset(self, scenario):
        with self.lock:
            self.scenario = dict(scenario)
            self.k = int(scenario.get("terminals", 12))
            self.alpha = 2.0
            self.hour = 12
            self.n_trucks = 2
            self.closed = set()             # site indices confirmed closed
            self.cut_off = set()            # site indices trucks can't reach (blocked roads)
            self.site_status = {}           # site_id -> latest report
            self.site_bonus = {}            # site index -> extra people confirmed on site
            self.observations = []          # ground truth from Scout Bot: no-service posts, flooding, closed roads
            self._simulate()
            self.replan(reason="new scenario")

    REPORTED_P = 0.8          # outage chance given to tracts where people report no service
    REPORT_KM = 1.5           # how far around a report the correction reaches

    def _simulate(self):
        """Run the simulation, then apply ground truth the model missed (reports beat the model)."""
        self.sim = self.sim_engine.simulate(self.scenario)
        self._apply_service_reports()

    def _apply_service_reports(self):
        t, pop = self.data.tracts, self.data.tracts["pop"].values
        p_out = self.sim["p_out"]
        for o in self.observations:
            if o["kind"] != "no_service" or o.get("model_agrees"):
                continue
            d = np.hypot((t.lat.values - o["lat"]) * 111.0, (t.lon.values - o["lon"]) * 92.6)
            near = np.flatnonzero(d <= max(self.REPORT_KM, float(d.min())))
            # from the hour of the report on (planning hour as a stand-in), until power is back
            for k, h in enumerate(self.sim["hours"]):
                if 0 < h <= 24:
                    p_out[near, k] = np.maximum(p_out[near, k], self.REPORTED_P)
        self.sim["people_out"] = (p_out * pop[:, None]).sum(0)

    def _committed(self):
        """Sites a person has confirmed open: trucks are heading there, so re-plans keep them."""
        return [self.site_index[sid] for sid, v in self.site_status.items() if v.get("state") == "confirmed"]

    def update_scenario(self, scenario, reason, keep_committed=True):
        """A new or sharper picture of the same flood (e.g. a warning after a forecast).
        Trucks may already be out, so planned sites that are still usable stay in the plan."""
        with self.lock:
            committed = [self.site_index[s["site_id"]] for s in self.plan["sites"]] if keep_committed else []
            if keep_committed:      # keep showing the forecast that started this plan
                scenario = dict(scenario)
                for k in ("forecast", "forecast_points"):
                    if k in self.scenario and k not in scenario:
                        scenario[k] = self.scenario[k]
            status, bonus, closed, obs = self.site_status, self.site_bonus, self.closed, self.observations
            self.reset(scenario)
            self.site_status, self.site_bonus, self.closed, self.observations = status, bonus, closed, obs
            self._apply_service_reports()
            unusable = self.opt.sites_in_track(self.scenario)
            keep = [j for j in committed if j not in unusable and j not in closed]
            dropped = [self.data.sites.iloc[j]["name"] for j in committed if j in unusable]
        self.replan(reason=reason, keep=keep)
        return {"kept": len(keep), "dropped_now_flooded": dropped}

    def add_observation(self, obs):
        """Ground truth from the field. Closed roads go into routing; everything is checked against the model."""
        with self.lock:
            lat, lon = obs["lat"], obs["lon"]
            check = {}
            # places from posts are approximate, so check the area around the spot, not one point
            if obs["kind"] == "no_service":
                t = self.data.tracts
                d = np.hypot((t.lat.values - lat) * 111.0, (t.lon.values - lon) * 92.6)
                near = np.flatnonzero(d <= max(2.0, float(d.min())))
                h = self.sim["hours"].index(self.hour)
                p = float(self.sim["p_out"][near, h].max())
                picks = self.plan["picks"]
                check = {"model_p": round(p, 2), "model_agrees": p >= 0.3,
                         "covered_by_plan": bool(self.opt.C[np.ix_(near, picks)].any()) if picks else False}
            elif obs["kind"] in ("flooding", "road_closed") and self.scenario.get("type") == "flood":
                ring = [(0, 0)] + [(r * np.cos(a), r * np.sin(a)) for r in (0.4, 0.8) for a in np.linspace(0, 2 * np.pi, 8, endpoint=False)]
                lats = np.array([lat + dy / 111.0 for dx, dy in ring])
                lons = np.array([lon + dx / 92.6 for dx, dy in ring])
                p = float(hazard.p_flood(self.scenario, lats, lons).max())
                check = {"model_p": round(p, 2), "model_agrees": p >= hazard.FLOOD["road_slow"]}
            obs = {**obs, **check, "at": time.time()}
            self.observations.append(obs)
            where = obs.get("place") or obs.get("text") or "a reported spot"
            reason = None
            if obs["kind"] == "road_closed":
                self.scenario.setdefault("road_closures", []).append({"lat": lat, "lon": lon, "text": obs.get("text", "")})
                reason = f"road closed near {where}"
                keep = [self.site_index[s["site_id"]] for s in self.plan["sites"]]   # same sites, new routes
            elif obs["kind"] == "flooding" and not obs.get("model_agrees") and self.scenario.get("type") == "flood":
                # the model missed this flooding: add it, re-simulate and re-plan
                self.scenario.setdefault("flood_reports", []).append({"lat": lat, "lon": lon})
                self._simulate()
                reason, keep = f"flooding reported at {where} (model missed it)", self._committed()
            elif obs["kind"] == "no_service" and not obs.get("model_agrees"):
                self._apply_service_reports()
                reason, keep = f"no service reported at {where} (model missed it)", self._committed()
            before = {s["site_id"] for s in self.plan["sites"]}
        if reason is None:
            self.emit({"type": "observation", "observation": obs})
            return obs
        if obs["kind"] != "road_closed":
            keep = self._one_swap_keep()
        self.replan(reason=reason, keep=keep)
        after = {s["site_id"] for s in self.plan["sites"]}
        obs["plan_changed"] = {"added": [self._name(x) for x in after - before],
                               "removed": [self._name(x) for x in before - after]}
        self.emit({"type": "layers_changed"})
        return obs

    def _one_swap_keep(self):
        """Field reports change the plan one site at a time (trucks are already loaded):
        keep every planned site except the one adding the least under the corrected outage.
        The optimizer then fills that slot with the best site, which may be the same one."""
        picks = [self.site_index[s["site_id"]] for s in self.plan["sites"]]
        if len(picks) < 2:
            return picks
        h = self.sim["hours"].index(self.hour)
        w = self.opt.weights(self.sim["p_out"][:, h], self.alpha)
        C = self.opt.C[:, picks]
        count = C.sum(1)
        only = [(float(w[C[:, i] & (count == 1)].sum()), j) for i, j in enumerate(picks)]
        fixed = set(self._committed())
        weakest = min((x for x in only if x[1] not in fixed), default=None)
        return [j for j in picks if weakest is None or j != weakest[1]]

    def _name(self, site_id):
        return self.data.sites.iloc[self.site_index[site_id]]["name"]

    def replan(self, reason="update", k=None, alpha=None, hour=None, n_trucks=None, keep=()):
        with self.lock:
            if k is not None:
                self.k = int(k)
            if alpha is not None:
                self.alpha = float(alpha)
            if hour is not None:
                self.hour = min(self.sim["hours"], key=lambda h: abs(h - float(hour)))
            if n_trucks is not None:
                self.n_trucks = int(n_trucks)
            self.plan = self.opt.plan(self.sim, self.scenario, k=self.k, alpha=self.alpha, hour=self.hour,
                                      closed=self.closed | self.cut_off, site_bonus=self.site_bonus, keep=keep)
            self.alt_plan = self.opt.plan(self.sim, self.scenario, k=self.k,
                                          alpha=0.0 if self.alpha else 2.0, hour=self.hour,
                                          closed=self.closed, site_bonus=self.site_bonus)
            self.routes = self._route()
            if self.routes and self.routes["unreachable"]:
                # sites cut off by blocked roads can't get a terminal: swap them for reachable ones
                cut = {self.site_index[sid] for sid in self.routes["unreachable"]}
                self.cut_off |= cut
                kept = [self.site_index[s["site_id"]] for s in self.plan["sites"] if self.site_index[s["site_id"]] not in cut]
                self.plan = self.opt.plan(self.sim, self.scenario, k=self.k, alpha=self.alpha, hour=self.hour,
                                          closed=self.closed | self.cut_off, site_bonus=self.site_bonus, keep=kept)
                self.routes = self._route()
            for s in self.plan["sites"]:
                s["status"] = self.site_status.get(s["site_id"], {}).get("state", "planned")
            self.plan["cut_off_sites"] = [self.data.sites.iloc[j]["name"] for j in sorted(self.cut_off)]
            event = {"type": "plan_updated", "reason": reason, "state": self.state()}
        self.emit(event)
        return event["state"]

    def _route(self):
        if self.roads is None or not self.plan["sites"]:
            return None
        r = self.roads.plan_routes(self.scenario, self.depot, self.plan["sites"], self.n_trucks)
        r["n_trucks"] = self.n_trucks
        r["done_min"] = max((x["duration_min"] for x in r["routes"]), default=0)
        if self.scenario.get("type") == "flood":
            # the same deliveries made before the water rises (what a forecast buys you)
            dry = self.roads.plan_routes(None, self.depot, self.plan["sites"], self.n_trucks)
            r["prepositioned"] = {
                "done_min": float(max((x["duration_min"] for x in dry["routes"]), default=0)),
                "people_hours": float(dry["people_hours"]),
                "reachable": len(self.plan["sites"]) - len(dry["unreachable"]),
            }
        return r

    def report_site(self, site_id, open_=True, headcount=None, has_power=None, notes=""):
        """Called by the voice agent (or a demo button) with a site's answers."""
        with self.lock:
            j = self.site_index.get(site_id)
            if j is None:
                return {"ok": False, "error": f"unknown site {site_id}"}
            state = "confirmed" if open_ else "closed"
            self.site_status[site_id] = {"state": state, "open": open_, "headcount": headcount,
                                         "has_power": has_power, "notes": notes, "at": time.time()}
            if open_:
                self.closed.discard(j)
                if headcount:
                    self.site_bonus[j] = float(headcount)
            else:
                self.closed.add(j)
                self.site_bonus.pop(j, None)
            name = self.data.sites.iloc[j]["name"]
            # trucks are already loaded: keep every other planned site, only fill the gap
            keep = [self.site_index[s["site_id"]] for s in self.plan["sites"] if self.site_index[s["site_id"]] != j]
        self.emit({"type": "site_status", "site_id": site_id, "name": name, **self.site_status[site_id]})
        self.replan(reason=f"{name} reported {state}", keep=keep)
        in_plan = [s["name"] for s in self.plan["sites"]]
        return {"ok": True, "site": name, "status": state, "plan_now": in_plan}

    # ---------- views ----------
    def state(self):
        hours = self.sim["hours"]
        return {
            "scenario": self.scenario,
            "settings": {"k": self.k, "alpha": self.alpha, "hour": self.hour, "n_trucks": self.n_trucks},
            "depot": self.depot,
            "hours": hours,
            "people_out_by_hour": [round(float(x)) for x in self.sim["people_out"]],
            "towers_dark_by_hour": [round(float(x), 4) for x in self.sim["towers_dark_share"]],
            "plan": _strip(self.plan),
            "alt_plan": _strip(self.alt_plan),
            "routes": self.routes,
            "site_status": self.site_status,
            "closed_sites": [self._site_brief(sid) for sid, v in self.site_status.items() if v["state"] == "closed"],
            "observations": self.observations[-200:],
        }

    def _site_brief(self, site_id):
        r = self.data.sites.iloc[self.site_index[site_id]]
        return {"site_id": site_id, "name": r["name"], "type": r.type_label, "lat": float(r.lat), "lon": float(r.lon),
                "notes": self.site_status[site_id].get("notes", "")}

    def layers(self):
        """Per-tract and per-tower values for the map (sent once per simulation)."""
        t, w = self.data.tracts, self.data.towers
        return {
            "hours": self.sim["hours"],
            "tracts": [{"geoid": g, "pop": int(p), "svi": round(float(v), 3), "p_out": [round(float(x), 3) for x in row]}
                       for g, p, v, row in zip(t.geoid, t["pop"], t.svi, self.sim["p_out"])],
            "towers": [{"id": i, "lat": float(a), "lon": float(o), "up": [round(float(x), 3) for x in row]}
                       for i, a, o, row in zip(w.tower_id, w.lat, w.lon, self.sim["tower_up"])],
            "blocked_roads": self.roads.blocked_segments(self.scenario) if self.roads else [],
            "hazard": hazard.map_layer(self.scenario),
        }

    def site(self, site_id):
        j = self.site_index.get(site_id)
        if j is None:
            return None
        r = self.data.sites.iloc[j]
        h = self.sim["hours"].index(self.hour)
        p_h = self.sim["p_out"][:, h]
        cov = self.opt.C[:, j]
        t = self.data.tracts
        return {"site_id": site_id, "name": r["name"], "type": r.type_label, "lat": float(r.lat), "lon": float(r.lon),
                "in_plan": any(s["site_id"] == site_id for s in self.plan["sites"]),
                "closed": j in self.closed, "status": self.site_status.get(site_id),
                "tracts_in_reach": int(cov.sum()),
                "people_without_service_in_reach": round(float((t["pop"].values * p_h)[cov].sum())),
                "avg_vulnerability_in_reach": round(float(t.svi.values[cov].mean()), 3) if cov.any() else None}

    def find_site(self, name):
        s = self.data.sites
        hits = s[s["name"].str.lower().str.contains(name.lower(), regex=False)]
        return hits.head(5)[["site_id", "name", "type_label"]].to_dict("records")

    def summary(self):
        """Compact summary for the Grok briefing."""
        p = self.plan
        out = {"scenario_name": self.scenario.get("name"), "hazard": self.scenario.get("type", "flood"),
               "k": p["k"], "hour": p["hour"],
               "objective": "most vulnerable" if self.alpha else "most people",
               "metrics": p["metrics"], "baseline_metrics": p["baseline_metrics"],
               "nearest_to_damage_metrics": p["nearest_metrics"],
               "sites": [{k: s[k] for k in ("name", "type", "people_reached", "vulnerable_reached", "status")}
                         for s in p["sites"]],
               "closed_sites": [v for v in self.site_status.values() if v["state"] == "closed"],
               "forecast": self.scenario.get("forecast"),
               "ground_truth": _obs_summary(self.observations)}
        if self.routes:
            out["routes"] = {"n_trucks": self.routes["n_trucks"], "done_min": self.routes["done_min"],
                             "unreachable": self.routes["unreachable"],
                             "reconnected_by_hour": self.routes["reconnected_by_hour"],
                             "prepositioned": self.routes.get("prepositioned")}
        return out


def _obs_summary(obs):
    if not obs:
        return None
    out = {}
    for kind in ("no_service", "flooding", "road_closed"):
        xs = [o for o in obs if o["kind"] == kind]
        if xs:
            out[kind] = {"reports": len(xs), "model_agreed": sum(bool(o.get("model_agrees")) for o in xs)}
    return out


def _strip(plan):
    return {k: v for k, v in plan.items() if k != "picks"}


def to_jsonable(x):
    if isinstance(x, dict):
        return {str(k): to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [to_jsonable(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x
