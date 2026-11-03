"""Grok calls: chat with tool calling, scenario parsing, briefing, and text-to-speech.

Uses SpaceXAI's OpenAI-compatible REST API (https://api.x.ai/v1). Every function
has an offline fallback so the app still runs when there is no key or no network.
"""
import json
import os
import re

import httpx
from dotenv import load_dotenv

import numpy as np
import pandas as pd

from .scenario import PRESETS, make_flood_scenario

load_dotenv()
BASE_URL = "https://api.x.ai/v1"
MODEL = os.getenv("XAI_MODEL", "grok-4.7")                              # big reasoning model
FAST_MODEL = os.getenv("XAI_FAST_MODEL", "grok-4.20-0309-non-reasoning")  # quick tool calls
_fast_ok = True   # flips to False if the fast model isn't available on this key


def has_key():
    return bool(os.getenv("XAI_API_KEY"))


def _headers():
    return {"Authorization": f"Bearer {os.environ['XAI_API_KEY']}", "Content-Type": "application/json"}


def chat(messages, tools=None, temperature=0.2, timeout=60, fast=True):
    """One chat completion. Returns the assistant message dict.

    fast=True uses FAST_MODEL (seconds per call) and falls back to MODEL once if
    the fast model is rejected (unknown model, no access)."""
    global _fast_ok
    model = FAST_MODEL if (fast and _fast_ok) else MODEL
    body = {"model": model, "messages": messages, "temperature": temperature}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    r = httpx.post(f"{BASE_URL}/chat/completions", headers=_headers(), json=body, timeout=timeout)
    if model == FAST_MODEL and r.status_code in (400, 403, 404):
        print(f"[grok] {FAST_MODEL} rejected ({r.status_code}: {r.text[:200]}); using {MODEL} from now on")
        _fast_ok = False
        return chat(messages, tools, temperature, timeout, fast=False)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def tts(text, voice="eve"):
    """Speak text with Grok's voice. Returns MP3 bytes, or None without a key."""
    if not has_key():
        return None
    r = httpx.post(f"{BASE_URL}/tts", headers=_headers(), timeout=60,
                   json={"text": text, "voice_id": voice, "language": "en"})
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------------------
# Gazetteer: approximate centers of places in the study area. Grok must look
# places up here instead of inventing coordinates.
# ---------------------------------------------------------------------------
GAZETTEER = {
    "downtown atlanta": (33.7537, -84.3863), "midtown": (33.7838, -84.3830),
    "buckhead": (33.8400, -84.3800), "west end": (33.7360, -84.4130),
    "east atlanta": (33.7400, -84.3460), "kirkwood": (33.7530, -84.3220),
    "east point": (33.6796, -84.4394), "college park": (33.6534, -84.4494),
    "hapeville": (33.6601, -84.4102), "forest park": (33.6220, -84.3691),
    "decatur": (33.7748, -84.2963), "avondale estates": (33.7715, -84.2669),
    "scottdale": (33.7901, -84.2638), "clarkston": (33.8095, -84.2394),
    "tucker": (33.8545, -84.2171), "belvedere park": (33.7548, -84.2674),
    "candler-mcafee": (33.7268, -84.2724), "gresham park": (33.7034, -84.3143),
    "panthersville": (33.7076, -84.2716), "south dekalb": (33.6900, -84.2600),
    "redan": (33.7454, -84.1316), "stonecrest": (33.7085, -84.1349),
    "lithonia": (33.7123, -84.1052), "conley": (33.6454, -84.3266),
    "ellenwood": (33.6229, -84.2799), "rex": (33.5876, -84.2741),
    "stone mountain": (33.8081, -84.1702), "emory": (33.7925, -84.3240),
    "georgia tech": (33.7756, -84.3963), "druid hills": (33.7804, -84.3363),
}

DIRECTIONS = {"north": 0, "northeast": 45, "east": 90, "southeast": 135, "south": 180,
              "southwest": 225, "west": 270, "northwest": 315}


def geocode(place):
    """Look up a place. Returns {"lat","lon","matched"} or {"error","candidates"}."""
    key = place.lower().strip()
    if key in GAZETTEER:
        lat, lon = GAZETTEER[key]
        return {"lat": lat, "lon": lon, "matched": key}
    hits = [k for k in GAZETTEER if key in k or k in key]
    if len(hits) == 1:
        lat, lon = GAZETTEER[hits[0]]
        return {"lat": lat, "lon": lon, "matched": hits[0]}
    return {"error": f"'{place}' not found", "candidates": hits or sorted(GAZETTEER)[:12]}


def nearby_waterways(lat, lon, km=6.0, limit=8):
    """Named rivers and creeks near a point, closest first."""
    from . import hazard
    from .geo import pairwise_km
    names = hazard.waterway_names()
    df, _, _ = hazard.waterways()
    named = df[df.name.isin(names.index)]
    d = pairwise_km(np.array([lat]), np.array([lon]), named.lat.values, named.lon.values)[0]
    best = pd.Series(d, index=named.name.values).groupby(level=0).min().sort_values()
    return [{"name": n, "km": round(float(v), 1)} for n, v in best[best <= km].head(limit).items()]


def known_waterway(name):
    """Match a waterway name to the map's spelling ('south river' -> 'South River')."""
    from . import hazard
    names = list(hazard.waterway_names().index)
    key = name.lower().strip()
    exact = [n for n in names if n.lower() == key]
    if exact:
        return exact[0]
    hits = [n for n in names if key in n.lower()]
    return min(hits, key=len) if hits else None


SCENARIO_TOOLS = [
    {"type": "function", "function": {
        "name": "geocode",
        "description": "Look up a neighborhood or city in metro Atlanta. Returns its coordinates and the named rivers "
                       "and creeks near it. Always call this; never invent coordinates.",
        "parameters": {"type": "object", "properties": {"place": {"type": "string"}}, "required": ["place"]}}},
    {"type": "function", "function": {
        "name": "set_flood_scenario",
        "description": "Store the flood scenario.",
        "parameters": {"type": "object", "properties": {
            "focus_lat": {"type": "number"}, "focus_lon": {"type": "number"},
            "severity": {"type": "string", "enum": ["minor", "moderate", "major", "record"]},
            "rivers": {"type": "array", "items": {"type": "string"},
                       "description": "Rivers/creeks that flood, spelled as geocode returned them"},
            "radius_km": {"type": "number", "description": "How wide an area floods; default 9"},
            "peak_hour": {"type": "number", "description": "Hours until water peaks: flash flood 3-4, river flood 6-12"},
            "rain_in": {"type": "number", "description": "Rainfall in inches, if given"},
            "terminals": {"type": "integer", "minimum": 1, "maximum": 50},
            "name": {"type": "string", "description": "Short label, e.g. 'Major flooding along the South River'"}},
            "required": ["focus_lat", "focus_lon", "severity"]}}},
]

SCENARIO_SYSTEM = """You turn a description of a flood in metro Atlanta into a structured scenario.
Rules:
- Call geocode for each place mentioned (a neighborhood, or a river or creek name), then call set_flood_scenario once.
- rivers: use the waterway names geocode returned. If the user names a river, include it. If they only name a place,
  pick the 2-4 closest named waterways.
- Severity: 3-4 inches of rain or "flooding" = moderate; 5-7 inches or "major" = major; 8+ inches or "record" = record.
- Flash flood -> peak_hour 4. River flood -> peak_hour 8. Default terminals: 12.
"""


def parse_scenario(text):
    """Natural language -> scenario dict. Uses Grok tool calling, else a rule-based parser."""
    if not has_key():
        return parse_scenario_offline(text), {"used": "offline parser"}
    messages = [{"role": "system", "content": SCENARIO_SYSTEM}, {"role": "user", "content": text}]
    trace = []
    for _ in range(4):
        try:
            msg = chat(messages, SCENARIO_TOOLS, timeout=30)
        except Exception as e:      # slow or failing API: never leave the demo hanging
            print(f"[grok] scenario parsing failed, using offline parser: {e}")
            return parse_scenario_offline(text), {"used": f"offline parser (grok error: {e.__class__.__name__})", "trace": trace}
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            break
        for c in calls:
            name = c["function"]["name"]
            args = json.loads(c["function"]["arguments"] or "{}")
            trace.append({"tool": name, "args": args})
            if name == "geocode":
                result = geocode_flood(args.get("place", ""))
            elif name == "set_flood_scenario":
                return _scenario_from_args(args), {"used": "grok", "trace": trace}
            else:
                result = {"error": "unknown tool"}
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result)})
    # Grok did not call set_flood_scenario: fall back rather than fail the demo
    return parse_scenario_offline(text), {"used": "offline parser (grok gave no scenario)", "trace": trace}


def geocode_flood(place):
    """geocode() for neighborhoods, plus river/creek names, plus the waterways nearby."""
    r = geocode(place)
    if "error" in r:
        w = known_waterway(place)
        if w is None:
            return r
        from . import hazard
        row = hazard.waterway_names().loc[w]
        r = {"lat": float(row.lat), "lon": float(row.lon), "matched": w, "is_waterway": True}
    r["waterways_nearby"] = nearby_waterways(r["lat"], r["lon"])
    return r


def _scenario_from_args(a):
    rivers = [w for w in (known_waterway(r) for r in a.get("rivers") or []) if w]
    peak = a.get("peak_hour") or 8
    name = (a.get("name") or "").strip()
    if len(name) < 12:            # Grok left the name out (or gave a bare "Flooding"): build one
        name = flood_label(a.get("severity") or "major", rivers, flash=peak <= 4)
    return make_flood_scenario(focus=[a["focus_lat"], a["focus_lon"]], severity=a.get("severity", "major"),
                               rivers=rivers, radius_km=a.get("radius_km") or 9.0,
                               peak_hour=peak, rain_in=a.get("rain_in"),
                               terminals=a.get("terminals") or 12, name=name)


def flood_label(severity, rivers, flash=False, place=None):
    """'Major flash flooding along the South River and Snapfinger Creek'."""
    sev = severity if isinstance(severity, str) else (
        "record" if severity >= 0.9 else "major" if severity >= 0.7 else "moderate" if severity >= 0.5 else "minor")
    kind = f"{sev.title()} {'flash ' if flash else ''}flooding"
    if rivers:
        return f"{kind} along {'the ' if 'River' in rivers[0] else ''}{' and '.join(rivers[:2])}"
    return f"{kind} in {place.title()}" if place else kind


FLOOD_WORDS = ("flood", "rain", "river", "creek", "overflow", "storm")


def mentions_place(text):
    t = text.lower()
    return (any(p in t for p in GAZETTEER) or "river" in t or "creek" in t) and any(w in t for w in FLOOD_WORDS)


def parse_scenario_offline(text):
    """Keyword parser used without a key: place, river/creek names, severity, rainfall, terminal count."""
    t = text.lower()
    terms = re.search(r"(\d+)\s*(?:starlink\s*)?terminals?", t)
    rain = re.search(r"(\d+(?:\.\d+)?)\s*(?:inches|inch|in\b|\")", t)
    rain_in = float(rain.group(1)) if rain else None
    sev = next((w for w in ("record", "catastrophic", "major", "moderate", "minor") if w in t), None)
    if sev is None:
        sev = "record" if (rain_in or 0) >= 8 else "major" if (rain_in or 0) >= 5 or rain_in is None else "moderate"
    sev = "record" if sev == "catastrophic" else sev
    from . import hazard
    rivers = [n for n in hazard.waterway_names().index if n.lower() in t]
    rivers = [r for r in rivers if not any(r != o and r.lower() in o.lower() for o in rivers)]   # 'Peachtree Creek' in 'South Fork …'
    place = next((p for p in sorted(GAZETTEER, key=len, reverse=True) if p in t), None)
    if place:
        focus = list(GAZETTEER[place])
    elif rivers:
        row = hazard.waterway_names().loc[rivers[0]]
        focus = [float(row.lat), float(row.lon)]
    else:
        return dict(PRESETS["south_river_flood"])
    if not rivers:
        rivers = [w["name"] for w in nearby_waterways(*focus)[:3]]
    kind = f"{sev.title()} {'flash ' if 'flash' in t else ''}flooding"
    named = [r for r in rivers if r.lower() in t]
    if named:
        label = f"{kind} along {'the ' if 'River' in named[0] else ''}{' and '.join(named[:2])}"
    elif place:
        label = f"{kind} in {place.title()}"
    else:
        label = f"{kind} along {'the ' if 'River' in rivers[0] else ''}{rivers[0]}"
    return make_flood_scenario(focus=focus, severity=sev, rivers=rivers,
                               peak_hour=4 if "flash" in t else 8, rain_in=rain_in,
                               terminals=int(terms.group(1)) if terms else 12, name=label)


# ---------------------------------------------------------------------------
# Briefing
# ---------------------------------------------------------------------------
BRIEFING_SYSTEM = """You are briefing a county emergency manager about a flood that is knocking out cell service.
Write about 150 words of plain spoken English (it will be read aloud). Use only numbers from the JSON.
Cover: where the terminals go (name the top 3 sites), how many people and vulnerable people they reconnect,
how that compares with placing terminals at the sites closest to the damage (nearest_to_damage_metrics),
truck timing if given (if routes.prepositioned is present, say how much sooner trucks finish when they leave
before the water rises), and who is still unreached. Mention one terminal per county emergency office only briefly. No headings, no bullet points, no markdown."""


def briefing(summary):
    """summary: dict with plan metrics, top sites, routes. Returns text."""
    if has_key():
        try:
            msg = chat([{"role": "system", "content": BRIEFING_SYSTEM},
                        {"role": "user", "content": json.dumps(summary, default=float)}], temperature=0.4)
            return msg.get("content", "").strip(), "grok"
        except httpx.HTTPError:
            pass
    return briefing_offline(summary), "template"


def _lower_first(name):
    """'Major flooding along …' -> 'major flooding along …' (keeps 'Forecast: …' readable)."""
    name = name or "this flood"
    if name.startswith("Forecast:"):
        return "today's forecast flood"
    return name[0].lower() + name[1:]


def briefing_offline(s):
    m, b = s["metrics"], s["baseline_metrics"]
    top = ", ".join(x["name"] for x in s["sites"][:3])
    parts = [
        f"For {_lower_first(s['scenario_name'])}, we recommend {s['k']} Starlink terminals, starting with {top}.",
        f"At hour {s['hour']}, about {m['people_without_service']:,.0f} people are without cell service. "
        f"This plan reconnects about {m['people_reached']:,.0f} of them, including {m['vulnerable_reached']:,.0f} "
        f"in the most vulnerable neighborhoods.",
    ]
    n = s.get("nearest_to_damage_metrics")
    if n and n["people_reached"]:
        parts.append(f"Putting the same terminals at the sites closest to the flooding would reach about "
                     f"{n['people_reached']:,.0f} people and {n['vulnerable_reached']:,.0f} vulnerable residents.")
    parts.append(f"One terminal per county emergency office would reach about {b['people_reached']:,.0f}, "
                 f"because those offices sit outside the worst of the outage.")
    if s.get("routes"):
        r = s["routes"]
        parts.append(f"With {r['n_trucks']} trucks, all terminals are running within {r['done_min']:.0f} minutes"
                     + (f", and {len(r['unreachable'])} sites are cut off by flooded roads." if r["unreachable"] else "."))
        pre = r.get("prepositioned")
        if pre and pre["done_min"] < r["done_min"]:
            parts.append(f"If the trucks leave on today's forecast, before the water rises, they finish in about "
                         f"{pre['done_min']:.0f} minutes instead.")
    unreached = m["people_without_service"] - m["people_reached"]
    parts.append(f"About {unreached:,.0f} people remain without service and need another solution.")
    return " ".join(parts)
