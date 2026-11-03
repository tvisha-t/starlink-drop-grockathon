# Starlink Drop

When a flood knocks out cell service, emergency managers have a handful of Starlink terminals
and need to decide where each one goes. Starlink Drop simulates which cell towers fail as the
water rises, picks the sites that reconnect the most vulnerable people, routes delivery trucks
around flooded roads, and keeps the plan current with Grok: a voice agent confirms sites by
phone, and three Grok Bots forecast the flood, check the plan against the real world, and
draft the outreach.

Built for the BDBI x SpaceXAI Grokathon at Georgia Tech, on top of BDBI's
[Aquatic Sustainability](https://github.com/gt-big-data/aquatic-sustainability) flood model.

## How it works

1. **Flood forecast** (`backend/hazard.py`). The Aquatic Sustainability model (a CNN-LSTM on
   NASA GPM rainfall and SMAP soil moisture) gives the chance of flooding on a coarse grid.
   Starlink Drop spreads it onto Atlanta's river and creek network (2,574 waterways from
   OpenStreetMap) to see which streets, towers and sites go under. Without a forecast, a flood
   can be typed in plain English ("major flooding along the South River after 7 inches of
   rain") and Grok turns it into a scenario with tool calling.
2. **Outage simulation** (`backend/sim.py`). 3,032 real cell towers (OpenCelliD), 300 Monte
   Carlo draws. As the water reaches each tower it can lose its equipment, lose grid power and
   run until its battery (2–8 h) or generator (24–72 h) dies, regain power after the water
   goes down, or go dark because its hub tower failed. A Census tract has service if any tower
   in range is up. Output: chance of no service per tract at 0, 2, 4, 6, 12, 24 and 48 hours.
3. **Site picking** (`backend/optimize.py`). Maximal covering location problem over schools,
   places of worship, fire stations, community centers and hospitals, skipping sites likely to
   be flooded. Each tract is weighted by people without service × (1 + α × CDC Social
   Vulnerability Index). Greedy is guaranteed at least 63% of optimal; an exact integer program
   (PuLP/CBC) reports the real gap. Compared against placing terminals closest to the flooding
   and against one terminal per county emergency office.
4. **Truck routing** (`backend/routing.py`). OpenStreetMap road graph with flooded and
   reported-closed roads removed and roads near water slowed. Dijkstra travel times, dispatch
   by people reached per minute, then 2-opt to minimize people-hours without service. Also
   reports how much sooner trucks finish if they leave on the forecast, before the water rises.
5. **Ground truth beats the model** (`backend/planner.py`). Field reports (no-service posts,
   flooded streets, closed roads, closed sites) are checked against the model. When the model
   missed something, the report corrects it, and the plan changes one site at a time because
   trucks are already loaded.
6. **Grok throughout.** Scenario parsing with tool calling, a planning agent that drives the
   whole planner as tools (every call shows in the Agent tab), a spoken briefing (Grok TTS),
   a Grok voice agent that confirms sites by phone, and three Grok Bots (below).

Everything has an offline fallback, so the app runs without an API key or network: a
rule-based scenario parser, a scripted agent, a template briefing and the browser's own speech.

## Setup

You need Python 3.11 or 3.12 (3.14 is too new for some geo packages) and Node 18+.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in XAI_API_KEY and VOICE_AGENT_ID
```

### Data

The `data/` folder is not in git (it's large). Create it with:

| File | How to get it |
| --- | --- |
| `data/SVI2022_GEORGIA_tract.gdb` | [CDC/ATSDR SVI download](https://www.atsdr.cdc.gov/placeandhealth/svi/data_documentation_download.html): 2022, Georgia, Census Tracts, geodatabase |
| `data/sites.geojson` | [overpass-turbo.eu](https://overpass-turbo.eu) query below, Export → GeoJSON |
| `data/eocs.csv` | County emergency office addresses (`prep/make_eocs.py` geocodes them) |
| `data/towers_raw/310.csv.gz`, `311.csv.gz` | [OpenCelliD](https://opencellid.org) downloads (free token) |
| `data/atlanta_drive.graphml` | `osmium extract` from the Geofabrik Georgia file, then `ox.graph_from_xml` (see below) |
| `data/waterways.csv` | `python3 prep/extract_waterways.py` (needs `pip install osmium` and `data/atl.osm.pbf` from the road steps) |

```
[out:json][timeout:60];
(nwr["amenity"~"school|place_of_worship|fire_station|community_centre|hospital"](33.55,-84.65,33.95,-84.05););
out center;
```

Then run the prep scripts once:

```bash
python3 prep/load_towers.py      # -> data/towers.csv
python3 prep/export_tracts.py    # -> data/tracts.csv, data/tracts.geojson
python3 -m backend.run_core_test # sanity check: simulation + plan in the terminal
```

Road network (the public Overpass servers are often overloaded, so build it from a file):

```bash
brew install osmium-tool
curl -L -o data/georgia-latest.osm.pbf https://download.geofabrik.de/north-america/us/georgia-latest.osm.pbf
osmium extract -b -84.50,33.62,-84.18,33.86 data/georgia-latest.osm.pbf -o data/atl.osm.pbf
osmium tags-filter data/atl.osm.pbf w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link,tertiary,tertiary_link,unclassified,residential,living_street -o data/atl_drive.osm
python3 prep/build_roads.py
python3 prep/extract_waterways.py   # rivers, creeks and lakes -> data/waterways.csv
```

## Run

```bash
# terminal 1: API (from the repo root)
uvicorn backend.app:app --reload --port 8000

# terminal 2: map app
cd frontend && npm install && npm run dev     # open http://localhost:5173
```

For the demo, build once and serve everything from the API: `cd frontend && npm run build`, then open http://localhost:8000.

## Voice calls

- **From the laptop:** Calls tab → "Start call from this laptop". The browser streams the mic to the backend, which talks to the Grok Voice Agent (`VOICE_AGENT_ID`) and handles `report_site_status` itself.
- **By phone:** run `ngrok http 8000`, put the URL in `PUBLIC_BASE_URL`, then `python3 scripts/setup_voice_number.py`. Calls to that number reach `/voice/incoming`, and the backend joins the call over the realtime WebSocket. A teammate playing a site manager dials it.
- **Manual fallback:** hover a site in the Plan tab and click "Mark closed". It goes through the same re-planning path.

Outbound calls placed by the API are not in SpaceXAI's public docs yet, so the phone flow uses inbound calls. Outbound calls can be placed from the console.

## Grok Bot agent partners

Three Grok Bots work around the planner in one loop, **predict → check → act**:

- **Forecast Bot** runs the Aquatic Sustainability flood model every morning on its own cloud
  computer (it can handle the NASA downloads) and sends the forecast to `/api/bots/forecast`.
  It refuses to send a forecast if the NASA downloads failed and the model fell back to
  synthetic data.
- **Scout Bot** checks the plan against the real world: flood warnings, USGS river gauges, X
  posts about no service or flooded streets, Georgia 511 road closures, and whether planned
  sites are open. Each report lands on the map, is checked against the model, and corrects the
  plan when the model missed it.
- **Dispatch Bot** drafts outreach to each site and the truck delivery sheet. Nothing is sent
  without approval.

They connect through `/api/bots/*` (Bots → app) and Grok Bot routine webhooks (app → Bots),
and show up live in the map's **Bots** tab. Setup and the text to paste into each Bot:
[`bots/README.md`](bots/README.md). Rehearse without Grok Bot: `python3 scripts/rehearse_bots.py`.

## Honest limits

- The Aquatic Sustainability model's grid is coarse (0.1°, about 11 km). The river network adds
  street-level detail, but this is not a hydraulic flood model.
- `bots/samples/forecast_sample.json` is sample data in the model's output format, for
  rehearsal. It is labeled as a sample in the app.
- OpenCelliD positions are estimated from phone measurements, not surveyed.
- Failure, battery and restoration parameters are assumptions (listed in `backend/sim.py`), not calibrated to FCC outage reports yet.
- A terminal is modeled as a hub serving tracts whose center is within 2 km of the site.

## Repo map

```
backend/   data.py geo.py hazard.py scenario.py sim.py optimize.py routing.py
           planner.py grok.py agent.py voice.py bots.py app.py run_core_test.py
frontend/  React + deck.gl + MapLibre map app
prep/      one-time data conversion scripts
scripts/   setup_voice_number.py rehearse_bots.py list_models.py
bots/      Grok Bot roles and routines (forecast, scout, dispatch), sample forecast
```
