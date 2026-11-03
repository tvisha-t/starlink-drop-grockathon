"""List the Grok models your API key can use (run from the repo root).

    python3 scripts/list_models.py

Set XAI_FAST_MODEL / XAI_MODEL in .env to names from this list.
"""
import os

import httpx
from dotenv import load_dotenv

load_dotenv()
r = httpx.get("https://api.x.ai/v1/models",
              headers={"Authorization": f"Bearer {os.environ['XAI_API_KEY']}"}, timeout=30)
r.raise_for_status()
for m in sorted(x["id"] for x in r.json().get("data", [])):
    print(m)
