"""Grok Bot agent partners: three Bots in one loop around the planner.

  Forecast Bot  (predict)  Runs BDBI's Aquatic Sustainability flood model every morning on its
                           cloud computer and sends the forecast. Starlink Drop plans terminals
                           before the water rises.
  Scout Bot     (check)    During the flood, checks the real world against the plan: flood
                           warnings, river gauges, X posts about lost service or flooded streets,
                           road closures, and whether planned sites are open.
  Dispatch Bot  (act)      Drafts the outreach to each site and the truck delivery sheet.
                           Nothing is sent without your approval.

They talk to the app over plain HTTPS in both directions:

  Bot -> app   (curl from the Bot's cloud computer, "Authorization: Bearer <BOT_API_KEY>")
    POST /api/bots/forecast      Forecast Bot: flood-model output -> pre-positioning plan
    POST /api/bots/alert         Scout Bot: an official flood warning -> sharpen the plan
    POST /api/bots/observation   Scout Bot: no service / flooding / road closed at a place
    POST /api/bots/site_report   Scout Bot: a planned site is open or closed (with evidence)
    POST /api/bots/activity      any Bot: a one-line update for the Bots tab
    GET  /api/bots/plan          any Bot: the current plan with site contact details
  App -> Bot   (Grok Bot routine webhooks: POST + "Authorization: Bearer <key>")
    send("forecast", ...)        run the flood model now
    send("scout", ...)           new plan -> watch these rivers and check these sites
    send("dispatch", ...)        plan is final -> draft outreach + delivery sheet

Routine URLs and keys come from .env: GROKBOT_<ROLE>_URL and GROKBOT_<ROLE>_KEY.
See bots/*.md for the text to paste into Grok Bot.
"""
import os
import threading
import time
from collections import deque
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

ROLES = {
    "forecast": "Forecast Bot",
    "scout": "Scout Bot",
    "dispatch": "Dispatch Bot",
}
FORECASTS = Path(__file__).resolve().parent.parent / "data" / "forecasts"


def routine(role):
    """(url, key) for a role's Grok Bot routine webhook, or (None, None) if not set up."""
    r = role.upper()
    return os.getenv(f"GROKBOT_{r}_URL"), os.getenv(f"GROKBOT_{r}_KEY")


class BotHub:
    def __init__(self, planner, emit):
        self.p = planner
        self.emit = emit
        self.log = deque(maxlen=200)
        self.lock = threading.Lock()
        self.auto_scout = os.getenv("AUTO_SCOUT", "1") == "1"     # new plan from a Bot -> Scout checks it
        self.last_seen = {}                                        # bot name -> time it last called the app

    # ---------- activity log ----------
    def note(self, bot, text, kind="info", url=None, data=None):
        item = {"t": time.time(), "bot": bot, "text": text, "kind": kind, "url": url, "data": data}
        with self.lock:
            self.log.append(item)
            if bot in ROLES.values():
                self.last_seen[bot] = item["t"]
        self.emit({"type": "bot_activity", **item})
        return item

    def recent(self, n=50):
        with self.lock:
            return list(self.log)[-n:]

    # ---------- what the Bots read ----------
    def plan_for_bots(self):
        """Current plan in a shape a Bot can act on: names, addresses, contacts, arrival times."""
        p = self.p
        arrivals = (p.routes or {}).get("arrivals", {})
        sites = []
        for s in p.plan["sites"]:
            r = p.data.sites.iloc[p.site_index[s["site_id"]]]
            sites.append({
                "rank": s["rank"], "site_id": s["site_id"], "name": s["name"], "type": s["type"],
                "address": r.address, "lat": round(s["lat"], 6), "lon": round(s["lon"], 6),
                "website": r.website, "phone": r.phone, "email": r.email, "facebook": r.facebook,
                "people_reached": s["people_reached"], "status": s.get("status", "planned"),
                "truck_arrives_min": arrivals.get(s["site_id"], {}).get("arrive_min"),
            })
        m = p.plan["metrics"]
        return {
            "scenario": p.scenario.get("name"),
            "terminals": p.plan["k"],
            "people_reconnected": round(m["people_reached"]),
            "people_without_service": round(m["people_without_service"]),
            "depot": p.depot,
            "sites": sites,
            "closed_sites": [c["name"] for c in p.state()["closed_sites"]],
            "rivers_to_watch": p.scenario.get("rivers") or _rivers_near_sites(p),
            "forecast": p.scenario.get("forecast"),
            "road_closures": p.scenario.get("road_closures", []),
            "trucks": [{"truck": r["truck"] + 1, "stops": [_name(p, sid) for sid in r["stops"]],
                        "done_min": r["duration_min"]} for r in (p.routes or {}).get("routes", [])],
        }

    # ---------- inbound ----------
    def forecast(self, bot, points, model="Aquatic Sustainability flood model", run_date=None,
                 source_url=None, note=""):
        """Forecast Bot ran the flood model. Plan terminals before the water rises."""
        from . import hazard
        if not points:
            self.note(bot, "Forecast had no points", kind="error")
            return {"ok": False, "error": "no points"}
        FORECASTS.mkdir(parents=True, exist_ok=True)
        path = FORECASTS / f"forecast_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        sc = hazard.scenario_from_forecast(points, path, model=model, run_date=run_date,
                                           terminals=self.p.k if hasattr(self.p, "k") else 12)
        fc = sc["forecast"]
        self.note(bot, f"Ran the {model}: {fc['risk']} flood risk, peak {fc['max_prob']:.0%} "
                       f"across {fc['points']} points" + (f". {note}" if note else ""), kind="forecast", url=source_url)
        self.p.reset(sc)
        self.emit({"type": "layers_changed"})
        st = self._plan_line("Pre-positioning plan")
        sent = self.send("scout", reason="forecast plan: watch the rivers and check these sites") if self.auto_scout else None
        return {"ok": True, "scenario": sc, "plan": st, "sent_to_scout": sent}

    def alert(self, bot, headline, description, source_url=None):
        """Scout Bot saw an official flood warning. Sharpen the plan, keeping sites already committed."""
        from . import grok
        self.note(bot, f"Warning: {headline}", kind="alert", url=source_url)
        sc, info = grok.parse_scenario(description or headline)
        had_plan = self.p.scenario.get("forecast") is not None
        kept = self.p.update_scenario(sc, reason=f"flood warning: {headline}", keep_committed=had_plan)
        self.emit({"type": "layers_changed"})
        extra = ""
        if had_plan:
            extra = f"; kept {kept['kept']} sites from the forecast plan"
            if kept["dropped_now_flooded"]:
                extra += f", moved {len(kept['dropped_now_flooded'])} now in the water ({', '.join(kept['dropped_now_flooded'][:2])})"
        st = self._plan_line("Updated plan", extra)
        return {"ok": True, "scenario": sc, "parsed_by": info["used"], "plan": st, **kept}

    def observation(self, bot, kind, text="", lat=None, lon=None, place=None, source_url=None, value=None):
        """Scout Bot's ground truth. Placed on the map and checked against the model."""
        from . import grok
        if lat is None or lon is None:
            where = self._locate(place or "")
            if where is None:
                self.note(bot, f"Couldn't place '{place}' on the map", kind="error")
                return {"ok": False, "error": f"unknown place '{place}'"}
            lat, lon, place = where
        obs = self.p.add_observation({"kind": kind, "lat": float(lat), "lon": float(lon), "place": place,
                                      "text": text, "source_url": source_url, "value": value, "bot": bot})
        label = {"no_service": "No service", "flooding": "Flooding", "road_closed": "Road closed",
                 "gauge": "River gauge"}.get(kind, kind)
        line = f"{label}" + (f" at {place}" if place else "") + (f": {text}" if text else "")
        if obs.get("model_p") is not None:
            line += (f" — model agrees ({obs['model_p']:.0%})" if obs.get("model_agrees")
                     else f" — model missed this ({obs['model_p']:.0%})")
        if kind == "no_service" and obs.get("covered_by_plan"):
            line += ", already covered by a planned terminal"
        ch = obs.get("plan_changed")
        if ch and (ch["added"] or ch["removed"]):
            line += f". Plan updated: +{', '.join(ch['added'])}" + (f", −{', '.join(ch['removed'])}" if ch["removed"] else "")
        elif kind == "road_closed":
            line += ". Trucks re-routed"
        elif ch is not None:
            line += ". Re-planned with it: the current sites still cover it best"
        self.note(bot, line, kind="ground_truth", url=source_url, data={"kind": kind, "lat": lat, "lon": lon})
        return {"ok": True, **obs}

    def _locate(self, place):
        from . import grok
        r = grok.geocode_flood(place)
        if "error" not in r:
            return r["lat"], r["lon"], place
        sid = self._match(place)
        if sid:
            row = self.p.data.sites.iloc[self.p.site_index[sid]]
            return float(row.lat), float(row.lon), row["name"]
        return None

    def _plan_line(self, label, extra=""):
        m, pl = self.p.plan["metrics"], self.p.plan
        routes = self.p.routes or {}
        pre = routes.get("prepositioned")
        when = (f"; trucks done in {pre['done_min']:.0f} min if they leave now"
                if pre and self.p.scenario.get("forecast") else "")
        self.note("Starlink Drop", f"{label}: {pl['k']} terminals reconnect {m['people_reached']:,.0f} of "
                                   f"{m['people_without_service']:,.0f} people{when}{extra}", kind="plan")
        return {"people_reached": m["people_reached"], "people_without_service": m["people_without_service"]}

    def site_report(self, bot, site, open_, headcount=None, has_power=None, evidence_url=None, notes=""):
        """Scout Bot (or any Bot) found evidence a planned site is open or closed."""
        site_id = site if site in self.p.site_index else self._match(site)
        if site_id is None:
            self.note(bot, f"Couldn't match a site named '{site}'", kind="error")
            return {"ok": False, "error": f"no site matching '{site}'"}
        why = f"{bot}: {notes}" if notes else f"reported by {bot}"
        result = self.p.report_site(site_id, open_, headcount, has_power, why)
        verdict = "open" if open_ else "closed"
        self.note(bot, f"{result['site']} is {verdict}" + (f" ({notes})" if notes else ""),
                  kind="closed" if not open_ else "confirmed", url=evidence_url)
        return result

    def _match(self, name):
        import difflib
        planned = {s["name"]: s["site_id"] for s in self.p.plan["sites"]}
        best = difflib.get_close_matches(name, list(planned), n=1, cutoff=0.5)
        if best:
            return planned[best[0]]
        hits = self.p.find_site(name)
        return hits[0]["site_id"] if hits else None

    # ---------- outbound ----------
    def send(self, role, reason="", extra=None):
        """POST the current plan (plus extra) to a Bot routine's webhook. Returns a short status."""
        url, key = routine(role)
        name = ROLES.get(role, role)
        if not url:
            self.note("Starlink Drop", f"{name} isn't connected (set GROKBOT_{role.upper()}_URL in .env)", kind="error")
            return {"ok": False, "error": "routine not configured"}
        payload = {"from": "Starlink Drop", "reason": reason, "app_url": os.getenv("PUBLIC_BASE_URL", ""),
                   "plan": self.plan_for_bots(), **(extra or {})}
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=20)
        except httpx.HTTPError as e:
            self.note("Starlink Drop", f"Couldn't reach {name}: {e.__class__.__name__}", kind="error")
            return {"ok": False, "error": str(e)}
        if r.status_code == 200:          # Grok Bot: 200 means the run started, not that it finished
            self.note("Starlink Drop", f"Sent the plan to {name}" + (f" ({reason})" if reason else ""), kind="sent")
            return {"ok": True}
        self.note("Starlink Drop", f"{name} refused the run ({r.status_code}). Is the routine paused or the key old?",
                  kind="error")
        return {"ok": False, "status": r.status_code, "body": r.text[:300]}

    def status(self):
        """has_routine: the app can send this Bot work. last_seen: when the Bot last called the app."""
        return {role: {"name": name, "has_routine": bool(routine(role)[0]),
                       "last_seen": self.last_seen.get(name)} for role, name in ROLES.items()}


def _name(p, site_id):
    return p.data.sites.iloc[p.site_index[site_id]]["name"]


def _rivers_near_sites(p, n=5):
    """Named waterways closest to the planned sites (what Scout should watch)."""
    from . import grok
    seen = []
    for s in p.plan["sites"][:6]:
        for w in grok.nearby_waterways(s["lat"], s["lon"], km=3.0, limit=2):
            if w["name"] not in seen:
                seen.append(w["name"])
    return seen[:n]
