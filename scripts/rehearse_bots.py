"""Rehearse the Grok Bot demo without Grok Bot: plays the three Bots' requests against your local app.

    python3 scripts/rehearse_bots.py            # app on http://localhost:8000
    python3 scripts/rehearse_bots.py --fast     # no pauses
    python3 scripts/rehearse_bots.py --step     # press Enter to go to each stage (for presenting)

The loop, predict -> check -> act:
  1. Forecast Bot sends a flood forecast (a sample in the exact format of BDBI Aquatic
     Sustainability's run_flood_inference output) -> the app makes a pre-positioning plan.
  2. Scout Bot finds a flash flood warning -> the plan sharpens, committed sites stay.
  3. Scout Bot reports what it sees: a closed site, a flooded road, people with no service.
     Each one is checked against the model and updates the plan.
  4. Dispatch Bot drafts the outreach and waits for approval.
Watch the map's Bots tab. Uses BOT_API_KEY from .env, like the real Bots.
Everything it sends is labeled REHEARSAL.
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()
APP = os.getenv("REHEARSE_APP_URL", "http://localhost:8000")
KEY = os.getenv("BOT_API_KEY")
H = {"Authorization": f"Bearer {KEY}"} if KEY else {}
STEP = "--step" in sys.argv
PAUSE = 0 if ("--fast" in sys.argv or STEP) else 3


def stage(label):
    """With --step, wait for Enter before each stage so the presenter controls the pace."""
    if STEP:
        input(f"\n[Enter] {label} ")
SAMPLE = Path(__file__).resolve().parent.parent / "bots" / "samples" / "forecast_sample.json"


def post(path, body):
    r = httpx.post(APP + path, json=body, headers=H, timeout=120)
    r.raise_for_status()
    return r.json()


def say(bot, text):
    post("/api/bots/activity", {"bot": bot, "text": text})
    print(f"{bot}: {text}")
    time.sleep(PAUSE)


# 1. predict
stage("Forecast Bot sends the flood forecast")
say("Forecast Bot", "REHEARSAL: running the Aquatic Sustainability flood model for metro Atlanta…")
fc = json.loads(SAMPLE.read_text())
r = post("/api/bots/forecast", fc)
print("Forecast plan:", r["scenario"]["name"])
time.sleep(PAUSE)

# 2. check: the official warning
stage("Scout Bot finds the flood warning")
say("Scout Bot", "Checking National Weather Service flood warnings and USGS gauges on the South River…")
say("Scout Bot", "REHEARSAL: USGS South River gauge at 14.2 ft and rising (made-up reading)")
r = post("/api/bots/alert", {
    "bot": "Scout Bot",
    "headline": "REHEARSAL: Flash Flood Warning for central and southern DeKalb County",
    "description": "4 to 6 inches of rain has fallen, with 2 more inches possible. Major flooding is ongoing "
                   "along the South River and Snapfinger Creek.",
    "source_url": "https://api.weather.gov/alerts/active?area=GA",
})
print("Warning plan:", r["scenario"]["name"], f"(parsed by {r['parsed_by']}, kept {r['kept']} sites)")
time.sleep(PAUSE)

# 3. check: ground truth
stage("Scout Bot reports what it sees on the ground")
plan = httpx.get(APP + "/api/bots/plan", headers=H, timeout=30).json()
site = plan["sites"][1]
say("Scout Bot", f"Checking planned sites online, starting with {plan['sites'][0]['name']}…")
r = post("/api/bots/site_report", {
    "bot": "Scout Bot", "site": site["site_id"], "open": False,
    "notes": "REHEARSAL: Facebook post says the parking lot is under water and the building is closed",
    "evidence_url": site["facebook"] or site["website"] or None,
})
print("Closed:", r["site"])
time.sleep(PAUSE)

for obs in [
    {"kind": "road_closed", "place": "Panthersville", "text": "REHEARSAL: DeKalb police: Columbia Dr closed, water over the road"},
    {"kind": "no_service", "place": "Gresham Park", "text": "REHEARSAL: 6 posts on X in 20 min: \"no bars at all\", \"can't call out\""},
    {"kind": "flooding", "place": "Lithonia", "text": "REHEARSAL: photo on X of Main St under water"},
]:
    r = post("/api/bots/observation", {"bot": "Scout Bot", **obs})
    agree = "model agrees" if r.get("model_agrees") else "model missed it"
    print(f"{obs['kind']} at {obs['place']}: {agree}", r.get("plan_changed") or "")
    time.sleep(PAUSE)

# 4. act
stage("Dispatch Bot drafts the outreach")
plan = httpx.get(APP + "/api/bots/plan", headers=H, timeout=30).json()
n_email = sum(1 for s in plan["sites"] if s["email"]) or 3
n_call = len(plan["sites"]) - n_email
say("Dispatch Bot", f"REHEARSAL: drafted {n_email} email{'s' if n_email != 1 else ''}, {n_call} call scripts and a "
                    f"delivery sheet for {len(plan['trucks'])} trucks. Waiting for your approval before sending anything.")
print("Done. Check the Bots tab.")
