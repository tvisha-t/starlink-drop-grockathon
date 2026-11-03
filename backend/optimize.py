"""Where should the terminals go? Maximal covering location problem.

A terminal at a site serves the tracts whose center is within COVER_KM.
Weight per tract = people without service x (1 + alpha x vulnerability).
Greedy picks the site that covers the most remaining weight, repeatedly.
Greedy is guaranteed at least (1 - 1/e) ~ 63% of the best possible score;
an exact integer program (PuLP) reports the real gap when it is installed.
"""
import numpy as np

from . import hazard
from .geo import pairwise_km

COVER_KM = 2.0
VULNERABLE_SVI = 0.75


class Optimizer:
    def __init__(self, data):
        self.data = data
        t, s, e = data.tracts, data.sites, data.eocs
        self.C = pairwise_km(t.lat.values, t.lon.values, s.lat.values, s.lon.values) <= COVER_KM
        self.Cf = self.C.astype(np.float32)
        self.C_eoc = pairwise_km(t.lat.values, t.lon.values, e.lat.values, e.lon.values) <= COVER_KM
        self.d_eoc_site = pairwise_km(e.lat.values, e.lon.values, s.lat.values, s.lon.values)

    # ---------- inputs ----------
    def weights(self, p_out_h, alpha):
        t = self.data.tracts
        return t["pop"].values * p_out_h * (1.0 + alpha * t["svi"].values)

    def sites_in_track(self, sc):
        """Sites that are likely flooded (or inside a tornado track) themselves."""
        s = self.data.sites
        return set(np.flatnonzero(hazard.site_unusable(sc, s.lat.values, s.lon.values)).tolist())

    def hospitals_to_pin(self, p_out_h, banned, limit):
        """Hospitals whose surroundings are mostly without service get a terminal first."""
        s = self.data.sites
        idx = [j for j in np.flatnonzero(s["is_hospital"].values) if j not in banned]
        scored = []
        for j in idx:
            near = self.C[:, j]
            if near.any() and p_out_h[near].mean() >= 0.5:
                scored.append((p_out_h[near].mean(), j))
        return [j for _, j in sorted(scored, reverse=True)[:limit]]

    # ---------- solvers ----------
    def greedy(self, w, k, pinned=(), banned=(), bonus=None):
        covered = np.zeros(self.C.shape[0], dtype=bool)
        picks = []
        for j in pinned:
            if len(picks) < k:
                picks.append(int(j))
                covered |= self.C[:, j]
        blocked = np.zeros(self.C.shape[1], dtype=bool)
        blocked[list(banned)] = True
        while len(picks) < k:
            gain = (w * ~covered).astype(np.float32) @ self.Cf
            if bonus is not None:
                gain = gain + bonus
            gain[blocked] = -1
            gain[picks] = -1
            j = int(gain.argmax())
            if gain[j] <= 0:
                break
            picks.append(j)
            covered |= self.C[:, j]
        return picks, covered

    def exact(self, w, k, pinned=(), banned=(), time_limit=20):
        """Exact integer program. Returns (picks, covered) or None if PuLP is missing."""
        try:
            import pulp
            pulp.LpVariable("probe", cat="Binary")
        except (ImportError, TypeError):   # missing, or PuLP 4.x (different API): skip the check
            return None
        rows = np.flatnonzero(w > 0)
        cand = [j for j in np.flatnonzero(self.C[rows].any(0)) if j not in set(banned)]
        prob = pulp.LpProblem("terminals", pulp.LpMaximize)
        x = {j: pulp.LpVariable(f"x{j}", cat="Binary") for j in cand}
        y = {i: pulp.LpVariable(f"y{i}", lowBound=0, upBound=1) for i in rows}
        prob += pulp.lpSum(float(w[i]) * y[i] for i in rows)
        for i in rows:
            prob += y[i] <= pulp.lpSum(x[j] for j in np.flatnonzero(self.C[i]) if j in x)
        prob += pulp.lpSum(x.values()) <= k
        for j in pinned:
            if j in x:
                prob += x[j] == 1
        prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))
        picks = [j for j in cand if (x[j].value() or 0) > 0.5]
        covered = self.C[:, picks].any(1) if picks else np.zeros(self.C.shape[0], dtype=bool)
        return picks, covered

    def eoc_baseline(self, k, banned=()):
        """What happened after Helene: one terminal per county emergency office,
        extra terminals at the sites nearest each office in turn."""
        covered = self.C_eoc.any(1).copy()
        n_eoc = len(self.data.eocs)
        extra, used = [], set(banned)
        for r in range(max(0, k - n_eoc)):
            e = r % n_eoc
            for j in np.argsort(self.d_eoc_site[e]):
                if j not in used:
                    extra.append(int(j))
                    used.add(int(j))
                    break
        if extra:
            covered |= self.C[:, extra].any(1)
        return extra, covered

    def nearest_baseline(self, sc, k, banned=()):
        """A responder's instinct: put terminals at the sites closest to the damage."""
        s = self.data.sites
        close = hazard.closeness_to_damage(sc, s.lat.values, s.lon.values)
        picks = [int(j) for j in np.argsort(-close, kind="stable") if j not in set(banned)][:k]
        return picks, self.C[:, picks].any(1)

    # ---------- reporting ----------
    def metrics(self, covered, p_out_h):
        t = self.data.tracts
        out = t["pop"].values * p_out_h
        vuln = t["svi"].values >= VULNERABLE_SVI
        total = float(out.sum())
        return {
            "people_reached": float(out[covered].sum()),
            "vulnerable_reached": float(out[covered & vuln].sum()),
            "people_without_service": total,
            "vulnerable_without_service": float(out[vuln].sum()),
            "share_reached": float(out[covered].sum() / total) if total else 0.0,
        }

    def describe_sites(self, picks, p_out_h):
        """Per-site detail in pick order, with the marginal people each one adds."""
        s, t = self.data.sites, self.data.tracts
        out = t["pop"].values * p_out_h
        vuln = t["svi"].values >= VULNERABLE_SVI
        covered = np.zeros(len(t), dtype=bool)
        rows = []
        for rank, j in enumerate(picks, 1):
            new = self.C[:, j] & ~covered
            covered |= self.C[:, j]
            r = s.iloc[j]
            rows.append({
                "rank": rank, "site_id": r.site_id, "name": r["name"], "type": r.type_label,
                "lat": r.lat, "lon": r.lon, "is_hospital": bool(r.is_hospital),
                "people_reached": round(float(out[new].sum())),
                "vulnerable_reached": round(float(out[new & vuln].sum())),
            })
        return rows

    def plan(self, sim, sc, k=None, alpha=2.0, hour=12, closed=(), site_bonus=None, check_exact=False, keep=()):
        """Full plan for one objective, plus the EOC baseline for comparison."""
        k = int(k or sc.get("terminals", 12))
        h = sim["hours"].index(hour)
        p_h = sim["p_out"][:, h]
        w = self.weights(p_h, alpha)
        banned = self.sites_in_track(sc) | set(closed)
        pinned = [j for j in keep if j not in banned]          # sites already committed stay in the plan
        pinned += [j for j in self.hospitals_to_pin(p_h, banned, limit=max(1, k // 4)) if j not in pinned]
        bonus = None
        if site_bonus:
            bonus = np.zeros(self.C.shape[1], dtype=np.float32)
            for j, people in site_bonus.items():
                bonus[j] = people
        picks, covered = self.greedy(w, k, pinned, banned, bonus)
        _, base_cov = self.eoc_baseline(k, banned)
        _, near_cov = self.nearest_baseline(sc, k, banned)

        result = {
            "k": k, "alpha": alpha, "hour": hour,
            "sites": self.describe_sites(picks, p_h),
            "metrics": self.metrics(covered, p_h),
            "baseline_metrics": self.metrics(base_cov, p_h),        # one per county EOC
            "nearest_metrics": self.metrics(near_cov, p_h),         # closest to the damage
            "pinned_hospitals": len(pinned),
            "picks": picks,
        }
        m = result["metrics"]

        def ratio(b):
            return {
                "people_x": m["people_reached"] / b["people_reached"] if b["people_reached"] else None,
                "vulnerable_x": m["vulnerable_reached"] / b["vulnerable_reached"] if b["vulnerable_reached"] else None,
            }
        result["vs_baseline"] = ratio(result["baseline_metrics"])
        result["vs_nearest"] = ratio(result["nearest_metrics"])
        if check_exact:
            ex = self.exact(w, k, pinned, banned)
            if ex is not None:
                g_score = float(w[covered].sum())
                e_score = float(w[ex[1]].sum())
                result["optimality"] = {"greedy": g_score, "exact": e_score,
                                        "greedy_share_of_optimal": g_score / e_score if e_score else 1.0}
        return result
