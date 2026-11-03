"""Starlink Drop API server.

Run from the repo root:
    uvicorn backend.app:app --reload --port 8000

REST endpoints are under /api, live updates go out on the /events WebSocket,
and the built map app (frontend/dist) is served at /.
"""
import asyncio
import json
import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import grok
from .agent import Agent
from .bots import BotHub
from .planner import Planner, to_jsonable
from .scenario import PRESETS
from .voice import VoiceBridge

ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="Starlink Drop")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

planner = Planner()
clients: set[WebSocket] = set()
loop: asyncio.AbstractEventLoop | None = None
agent_lock = threading.Lock()


# ---------- live updates ----------
async def _broadcast(event):
    data = json.dumps(to_jsonable(event), default=float)
    for ws in list(clients):
        try:
            await ws.send_text(data)
        except Exception:
            clients.discard(ws)


def emit(event):
    """Thread-safe: schedule a broadcast on the server's event loop."""
    if loop is not None:
        asyncio.run_coroutine_threadsafe(_broadcast(event), loop)


planner.listeners.append(emit)
voice = VoiceBridge(planner, emit)
bots = BotHub(planner, emit)


@app.on_event("startup")
async def _startup():
    global loop
    loop = asyncio.get_running_loop()


@app.websocket("/events")
async def events(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()        # keep-alive pings from the client
    except WebSocketDisconnect:
        clients.discard(ws)


# ---------- reads ----------
@app.get("/api/state")
def get_state():
    return to_jsonable(planner.state())


@app.get("/api/layers")
def get_layers():
    return to_jsonable(planner.layers())


@app.get("/api/tracts.geojson")
def get_tract_shapes():
    path = ROOT / "data" / "tracts.geojson"
    if not path.exists():
        raise HTTPException(404, "Run prep/export_tracts.py first")
    return FileResponse(path, media_type="application/geo+json")


@app.get("/api/presets")
def get_presets():
    return {k: v["name"] for k, v in PRESETS.items()}


@app.get("/api/sites/{site_id}")
def get_site(site_id: str):
    info = planner.site(site_id)
    if info is None:
        raise HTTPException(404, "unknown site")
    return to_jsonable(info)


@app.get("/api/health")
def health():
    return {"ok": True, "grok": grok.has_key(), "roads": planner.roads is not None,
            "voice_agent": bool(os.getenv("VOICE_AGENT_ID"))}


# ---------- writes ----------
class ScenarioText(BaseModel):
    text: str


@app.post("/api/scenario")
def set_scenario(body: ScenarioText):
    sc, info = grok.parse_scenario(body.text)
    planner.reset(sc)
    emit({"type": "layers_changed"})
    return {"scenario": sc, "parsed_by": info["used"], "trace": info.get("trace", [])}


@app.post("/api/scenario/preset/{name}")
def set_preset(name: str):
    if name not in PRESETS:
        raise HTTPException(404, "unknown preset")
    planner.reset(PRESETS[name])
    emit({"type": "layers_changed"})
    return {"scenario": PRESETS[name]}


class PlanSettings(BaseModel):
    k: int | None = None
    alpha: float | None = None
    hour: float | None = None
    n_trucks: int | None = None


@app.post("/api/plan")
def replan(body: PlanSettings):
    return to_jsonable(planner.replan(reason="settings changed", **body.model_dump()))


class SiteReport(BaseModel):
    open: bool = True
    headcount: int | None = None
    has_power: bool | None = None
    notes: str = ""


@app.post("/api/sites/{site_id}/status")
def report_site(site_id: str, body: SiteReport):
    """Same path the voice agent uses; also handy as a manual demo fallback."""
    return to_jsonable(planner.report_site(site_id, body.open, body.headcount, body.has_power, body.notes))


@app.post("/api/reset_demo")
def reset_demo():
    """Back to the starting flood with a clean plan and an empty Bots log (no backend restart)."""
    from .scenario import DEFAULT_PRESET, PRESETS as P
    planner.reset(P[DEFAULT_PRESET])
    bots.log.clear()
    emit({"type": "reset"})
    emit({"type": "layers_changed"})
    return {"ok": True}


@app.post("/api/reset_sites")
def reset_sites():
    planner.reset(planner.scenario)
    return {"ok": True}


class AgentMessage(BaseModel):
    message: str


@app.post("/api/agent")
def run_agent(body: AgentMessage):
    if not agent_lock.acquire(blocking=False):
        raise HTTPException(409, "The agent is already working")
    try:
        answer = Agent(planner, on_trace=emit).run(body.message)
        emit({"type": "layers_changed"})
        return {"answer": answer}
    finally:
        agent_lock.release()


@app.post("/api/briefing")
def make_briefing():
    text, source = grok.briefing(to_jsonable(planner.summary()))
    planner.last_briefing = text
    return {"text": text, "source": source}


@app.get("/api/briefing/audio")
def briefing_audio():
    text = getattr(planner, "last_briefing", None)
    if not text:
        text, _ = grok.briefing(to_jsonable(planner.summary()))
    audio = grok.tts(text)
    if audio is None:
        raise HTTPException(503, "No XAI_API_KEY; the browser will read the briefing instead")
    return Response(audio, media_type="audio/mpeg")


# ---------- Grok Bot agent partners ----------
def bot_auth(request: Request):
    """Bots send the BOT_API_KEY from .env as a Bearer token (open if no key is set, for local testing)."""
    key = os.getenv("BOT_API_KEY")
    if key and request.headers.get("authorization", "") != f"Bearer {key}":
        raise HTTPException(401, "Send Authorization: Bearer <BOT_API_KEY>")


class ForecastPoint(BaseModel):
    lat: float
    lon: float
    prob: float


class BotForecast(BaseModel):
    bot: str = "Forecast Bot"
    points: list[ForecastPoint]                 # run_flood_inference(...)["points"] from Aquatic Sustainability
    model: str = "Aquatic Sustainability flood model"
    run_date: str | None = None
    source_url: str | None = None
    note: str = ""


class BotAlert(BaseModel):
    bot: str = "Scout Bot"
    headline: str
    description: str = ""
    source_url: str | None = None


class BotObservation(BaseModel):
    bot: str = "Scout Bot"
    kind: str                       # no_service | flooding | road_closed | gauge
    text: str = ""
    lat: float | None = None        # give lat/lon, or a place name the app can look up
    lon: float | None = None
    place: str | None = None
    source_url: str | None = None
    value: float | None = None      # gauge: river stage in feet


class BotSiteReport(BaseModel):
    bot: str = "Scout Bot"
    site: str                      # site_id (e.g. "s_847") or the site's name
    open: bool
    headcount: int | None = None
    has_power: bool | None = None
    evidence_url: str | None = None
    notes: str = ""


class BotActivity(BaseModel):
    bot: str
    text: str
    url: str | None = None


@app.post("/api/bots/forecast")
def bots_forecast(body: BotForecast, request: Request):
    bot_auth(request)
    return to_jsonable(bots.forecast(body.bot, [pt.model_dump() for pt in body.points], body.model,
                                     body.run_date, body.source_url, body.note))


@app.post("/api/bots/alert")
def bots_alert(body: BotAlert, request: Request):
    bot_auth(request)
    return to_jsonable(bots.alert(body.bot, body.headline, body.description, body.source_url))


@app.post("/api/bots/observation")
def bots_observation(body: BotObservation, request: Request):
    bot_auth(request)
    if body.kind not in ("no_service", "flooding", "road_closed", "gauge"):
        raise HTTPException(422, "kind must be no_service, flooding, road_closed or gauge")
    return to_jsonable(bots.observation(body.bot, body.kind, body.text, body.lat, body.lon, body.place,
                                        body.source_url, body.value))


@app.post("/api/bots/site_report")
def bots_site_report(body: BotSiteReport, request: Request):
    bot_auth(request)
    return to_jsonable(bots.site_report(body.bot, body.site, body.open, body.headcount, body.has_power,
                                        body.evidence_url, body.notes))


@app.post("/api/bots/activity")
def bots_activity(body: BotActivity, request: Request):
    bot_auth(request)
    return bots.note(body.bot, body.text, url=body.url)


@app.get("/api/bots/plan")
def bots_plan(request: Request):
    bot_auth(request)
    return to_jsonable(bots.plan_for_bots())


@app.get("/api/bots/status")
def bots_status():
    return {"bots": bots.status(), "activity": bots.recent()}


SAMPLE_WARNING = ("Flash Flood Warning for central and southern DeKalb County until 11:00 PM EST. At 6:40 PM EST, "
                  "Doppler radar indicated 4 to 6 inches of rain has fallen, with 2 more inches possible. Major flooding "
                  "is ongoing along the South River and Snapfinger Creek. Low water crossings near Panthersville and "
                  "Lithonia are flooded.")


class BotSend(BaseModel):
    reason: str = ""
    alert_text: str | None = None  # Scout Bot: a test warning to act on
    drill: bool = False            # Scout Bot: run a flood drill with scripted, labeled injects


def drill_packet():
    """A practice flood for Scout Bot, like the 'injects' in an emergency-management exercise.
    Every item is made up and labeled DRILL. Scout applies its normal rules to them: some
    items should be reported, and some should be held back (too few posts to trust)."""
    plan = bots.plan_for_bots()
    site = plan["sites"][1] if len(plan["sites"]) > 1 else plan["sites"][0]
    return {
        "note": "DRILL. Every item below is made up for practice. Use only these items as sources, "
                "do not search the web, and start every headline and text you send with 'DRILL:'.",
        "warning": {"headline": "Flash Flood Warning for central and southern DeKalb County", "text": SAMPLE_WARNING},
        "gauges": [{"site": "South River at Klondike Rd", "stage_ft": 14.2, "flood_stage_ft": 12.0, "trend": "rising"}],
        "posts": [
            {"id": "p1", "place": "Gresham Park", "text": "no bars at all near the Gresham Park rec center, can't call anyone"},
            {"id": "p2", "place": "Gresham Park", "text": "Verizon AND T-Mobile both dead in Gresham Park?? texts won't send"},
            {"id": "p3", "place": "Gresham Park", "text": "anyone near Bouldercrest & Gresham have signal? mine's been SOS for an hour"},
            {"id": "p4", "place": "Lithonia", "text": "Main St in Lithonia has water over the road, turned around"},
            {"id": "p5", "place": "Lithonia", "text": "lithonia main street is a river rn"},
            {"id": "p6", "place": "Buckhead", "text": "my wifi is slow tonight ugh"},
        ],
        "road_reports": [
            {"source": "DeKalb County Police (drill)", "place": "Panthersville",
             "text": "Columbia Dr closed between Panthersville Rd and Snapfinger Rd, water over the roadway"},
        ],
        "site_notices": [
            {"site_id": site["site_id"], "site": site["name"], "source": "the site's Facebook page (drill)",
             "text": f"{site['name']} is closed today. Our parking lot and first floor are flooded."},
        ],
    }


@app.get("/api/bots/drill")
def bots_drill(request: Request):
    """The drill packet, for Scout Bot to fetch (the webhook body also carries it)."""
    bot_auth(request)
    return to_jsonable(drill_packet())


@app.post("/api/bots/send/{role}")
def bots_send(role: str, body: BotSend):
    """Called from the map app: hand work to a Bot (forecast, scout, dispatch)."""
    extra = {}
    if role == "forecast":
        extra["forecast_center"] = {"lat": 33.70, "lon": -84.25}
    if role == "scout" and body.drill:
        extra["drill"] = drill_packet()
    elif role == "scout" and body.alert_text is not None:
        extra["test_alert"] = body.alert_text or SAMPLE_WARNING
    return bots.send(role, reason=body.reason, extra=extra or None)


# ---------- voice ----------
@app.post("/voice/incoming")
async def voice_incoming(request: Request):
    """Webhook for phone numbers routed to us: SpaceXAI sends realtime.call.incoming."""
    secret = os.getenv("VOICE_WEBHOOK_SECRET")
    if secret and request.query_params.get("token") != secret:
        raise HTTPException(401, "bad token")
    ev = await request.json()
    if ev.get("type") == "realtime.call.incoming":
        call_id = ev["data"]["call_id"]
        asyncio.create_task(voice.join_phone_call(call_id))
    return JSONResponse({"ok": True})


@app.websocket("/voice/browser")
async def voice_browser(ws: WebSocket):
    await ws.accept()
    try:
        await voice.browser_call(ws)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        emit({"type": "call_error", "call": "browser", "error": str(e)})


# ---------- frontend ----------
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
