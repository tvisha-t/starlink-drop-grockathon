"""Voice confirmation calls with the Grok Voice Agent API.

The agent asks each site three questions (open? how many people? power?) and
then calls the `report_site_status` tool. We handle that tool ourselves: the
planner marks the site, re-plans, and the map updates while the call goes on.

Two ways to run a call, both over SpaceXAI's realtime WebSocket:

1. Phone call (inbound SIP). Create a phone number whose calls are sent to our
   webhook (see scripts/setup_voice_number.py). When someone dials it, SpaceXAI
   POSTs `realtime.call.incoming` to /voice/incoming; we join the call at
   wss://api.x.ai/v1/realtime?call_id=... and configure it with session.update.
   For the demo, the teammate playing a site manager dials the number.

2. Browser call. The map app streams the laptop microphone to /voice/browser;
   we relay audio to wss://api.x.ai/v1/realtime and play the agent's replies.
   This needs no phone and is the fallback if the venue has bad signal.

Event names follow SpaceXAI's realtime reference (OpenAI Realtime compatible):
`response.function_call_arguments.done` -> reply with a `function_call_output`
item and `response.create`.
"""
import asyncio
import difflib
import json
import os

import websockets
from dotenv import load_dotenv

load_dotenv()
REALTIME_URL = "wss://api.x.ai/v1/realtime"

REPORT_TOOL = {
    "type": "function",
    "name": "report_site_status",
    "description": "Report the site manager's answers. Call once you know whether the site is open; "
                   "include headcount and power if they said them.",
    "parameters": {
        "type": "object",
        "properties": {
            "site_name": {"type": "string", "description": "Name of the site the caller is at"},
            "open": {"type": "boolean", "description": "Is the site open and safe to use right now"},
            "headcount": {"type": "integer", "description": "About how many people are there"},
            "has_power": {"type": "boolean", "description": "Does the site have electrical power"},
            "notes": {"type": "string"},
        },
        "required": ["site_name", "open"],
    },
}


def instructions(site_names):
    sites = "; ".join(site_names[:12])
    return (
        "You are the DeKalb County emergency coordination line during a flood. You are confirming sites that may "
        "receive a Starlink internet terminal. Be brief, calm and polite. If you don't know which site you are "
        f"talking to, ask. The sites in the current plan are: {sites}. Ask three questions, one at a time: "
        "(1) Is your site open, dry and safe to use right now? (2) About how many people are there? "
        "(3) Do you have electrical power? Then call report_site_status with the answers, repeat them back in one "
        "sentence, thank them, and end the call. Keep the call under one minute. If they say the site is closed or "
        "unsafe, call report_site_status with open=false straight away, thank them, and end the call."
    )


class VoiceBridge:
    def __init__(self, planner, emit):
        self.p = planner
        self.emit = emit                      # emit(event_dict) -> broadcast to the map app
        self.key = os.getenv("XAI_API_KEY")
        self.agent_id = os.getenv("VOICE_AGENT_ID")

    # ---------- shared ----------
    def match_site(self, name):
        planned = {s["name"]: s["site_id"] for s in self.p.plan["sites"]}
        best = difflib.get_close_matches(name, list(planned), n=1, cutoff=0.4)
        if best:
            return planned[best[0]]
        hits = self.p.find_site(name)
        return hits[0]["site_id"] if hits else None

    def session_update(self):
        return {"type": "session.update", "session": {
            "instructions": instructions([s["name"] for s in self.p.plan["sites"]]),
            "turn_detection": {"type": "server_vad"},
            "tools": [REPORT_TOOL],
        }}

    async def handle_server_event(self, ws, ev, call_label):
        """Shared handling for transcripts and tool calls. Returns nothing."""
        kind = ev.get("type", "")
        if kind == "response.output_audio_transcript.done":
            self.emit({"type": "call_transcript", "call": call_label, "speaker": "agent", "text": ev.get("transcript", "")})
        elif kind == "conversation.item.input_audio_transcription.completed":
            self.emit({"type": "call_transcript", "call": call_label, "speaker": "site", "text": ev.get("transcript", "")})
        elif kind == "response.function_call_arguments.done" and ev.get("name") == "report_site_status":
            args = json.loads(ev.get("arguments") or "{}")
            site_id = self.match_site(args.get("site_name", ""))
            if site_id is None:
                result = {"ok": False, "error": f"no site named {args.get('site_name')}"}
            else:
                result = await asyncio.to_thread(self.p.report_site, site_id, bool(args.get("open", True)),
                                                 args.get("headcount"), args.get("has_power"), args.get("notes", ""))
            self.emit({"type": "call_tool", "call": call_label, "args": args, "result": result})
            await ws.send(json.dumps({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": ev.get("call_id"), "output": json.dumps(result)}}))
            await ws.send(json.dumps({"type": "response.create"}))
        elif kind == "error":
            self.emit({"type": "call_error", "call": call_label, "error": ev.get("error")})

    # ---------- 1. phone calls (inbound SIP webhook) ----------
    async def join_phone_call(self, call_id):
        headers = {"Authorization": f"Bearer {self.key}"}
        label = f"phone {call_id[-6:]}"
        self.emit({"type": "call_started", "call": label})
        try:
            async with websockets.connect(f"{REALTIME_URL}?call_id={call_id}", additional_headers=headers) as ws:
                await ws.send(json.dumps(self.session_update()))
                await ws.send(json.dumps({"type": "response.create"}))   # agent speaks first
                async for raw in ws:
                    await self.handle_server_event(ws, json.loads(raw), label)
        except Exception as e:
            self.emit({"type": "call_error", "call": label, "error": str(e)})
        self.emit({"type": "call_ended", "call": label})

    # ---------- 2. browser calls (mic <-> backend <-> SpaceXAI) ----------
    async def browser_call(self, client_ws):
        """client_ws: FastAPI WebSocket. Client sends {"audio": base64 pcm16 24kHz} chunks;
        we send back {"audio": ...} chunks and transcript events."""
        if not self.key:
            await client_ws.send_json({"type": "call_error", "error": "XAI_API_KEY is not set"})
            return
        url = f"{REALTIME_URL}?agent_id={self.agent_id}" if self.agent_id else REALTIME_URL
        headers = {"Authorization": f"Bearer {self.key}"}
        label = "browser"
        self.emit({"type": "call_started", "call": label})
        async with websockets.connect(url, additional_headers=headers) as xai:
            await xai.send(json.dumps(self.session_update()))
            await xai.send(json.dumps({"type": "response.create"}))

            async def upstream():
                while True:
                    msg = await client_ws.receive_json()
                    if msg.get("type") == "hangup":
                        return
                    if "audio" in msg:
                        await xai.send(json.dumps({"type": "input_audio_buffer.append", "audio": msg["audio"]}))

            async def downstream():
                async for raw in xai:
                    ev = json.loads(raw)
                    if ev.get("type") == "response.output_audio.delta":
                        await client_ws.send_json({"type": "audio", "audio": ev["delta"]})
                    elif ev.get("type") == "input_audio_buffer.speech_started":
                        await client_ws.send_json({"type": "interrupt"})
                    await self.handle_server_event(xai, ev, label)

            tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
        self.emit({"type": "call_ended", "call": label})
