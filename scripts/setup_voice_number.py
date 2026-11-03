"""Create a SpaceXAI phone number whose calls are sent to this app.

When someone dials it, SpaceXAI POSTs `realtime.call.incoming` to
PUBLIC_BASE_URL/voice/incoming and the backend joins the call with the
report_site_status tool, so the plan updates during the call.

Run once (with ngrok running and PUBLIC_BASE_URL set in .env):
    python3 scripts/setup_voice_number.py            # create the number
    python3 scripts/setup_voice_number.py --list     # show your numbers

Endpoint per SpaceXAI's REST reference: POST /v2/phone-numbers
(a number routes either to an agent_id or to a webhook, not both).
"""
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()
KEY = os.environ["XAI_API_KEY"]
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

if "--list" in sys.argv:
    r = httpx.get("https://api.x.ai/v2/phone-numbers", headers=H, timeout=30)
    print(json.dumps(r.json(), indent=2))
    sys.exit()

base = os.environ["PUBLIC_BASE_URL"].rstrip("/")
token = os.getenv("VOICE_WEBHOOK_SECRET", "")
url = f"{base}/voice/incoming" + (f"?token={token}" if token else "")
body = {
    "origin": "xai_provisioned",
    "name": "Starlink Drop dispatcher (webhook)",
    "area_code": os.getenv("AREA_CODE", "404"),
    "webhook": {"name": "starlink-drop", "url": url},
}
r = httpx.post("https://api.x.ai/v2/phone-numbers", headers=H, json=body, timeout=60)
print(r.status_code)
print(json.dumps(r.json(), indent=2))
