# Grok Bot agent partners

Starlink Drop plans where Starlink terminals go during a flood. Three Grok Bots work around it
in one loop: **predict → check → act**.

| Bot | Job | How it's triggered | Talks to the app with |
| --- | --- | --- | --- |
| **Forecast Bot** (predict) | Runs BDBI Aquatic Sustainability's flood model on its cloud computer and sends the forecast. The app plans terminals before the water rises. | Schedule (6 AM daily) + webhook ("Run the flood forecast") | `POST /api/bots/forecast` |
| **Scout Bot** (check) | Checks the plan against the real world: flood warnings, river gauges, X posts about no service or flooded streets, road closures, and whether planned sites are open. | Schedule (every 15 min) + webhook (the app sends it each new forecast plan) | `POST /api/bots/alert`, `/observation`, `/site_report` |
| **Dispatch Bot** (act) | Drafts outreach to each site and the truck delivery sheet. Sends nothing without your approval. | Webhook ("Send plan to Dispatch Bot") | `GET /api/bots/plan` |

All Bots also post one-line updates to `POST /api/bots/activity`, which show up live in the
app's **Bots** tab. Every Scout report is checked against the model ("model agrees" / "model
missed this"); reports the model missed correct it and change the plan by one site at a time.

## One-time setup

1. **Public link to your laptop.** The Bots run in the cloud.
   ```
   ngrok http 8000
   ```
   Copy the `https://….ngrok-free.app` address.

2. **Add to `.env`** (then restart the backend):
   ```
   PUBLIC_BASE_URL=https://<your-ngrok-address>
   BOT_API_KEY=<make up a long random string>
   ```
   Make a random string with: `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`

3. **Create each Bot** in the Grok Bot app: open the matching file in this folder
   (`forecast.md`, `scout.md`, `dispatch.md`), paste the **Role** section as the
   Bot's role, then ask the Bot to create the routine described in **Routine**.
   Replace `{APP_URL}` and `{BOT_API_KEY}` with your values from step 2.

4. **Copy each webhook routine's URL and key into `.env`:**
   ```
   GROKBOT_FORECAST_URL=...   GROKBOT_FORECAST_KEY=...
   GROKBOT_SCOUT_URL=...      GROKBOT_SCOUT_KEY=...
   GROKBOT_DISPATCH_URL=...   GROKBOT_DISPATCH_KEY=...
   ```
   (In each routine's Webhook section: "POST to" is the URL, the Bearer value is the key.)

5. Restart the backend. The **Bots** tab shows which Bots are connected.

ngrok gives you a new address each time it restarts. If it changes, update
`PUBLIC_BASE_URL` and the `{APP_URL}` in the Bots' instructions.

## Rehearsing without Grok Bot

`python3 scripts/rehearse_bots.py` plays the whole loop against your local app, so you can
practice the demo before the real Bots are set up: Forecast Bot sends a sample forecast (in the
exact format of the model's output), Scout sends a flood warning, a closed site, a closed road,
no-service posts and a flooded street, and Dispatch posts its drafts. Everything it sends is
labeled REHEARSAL. To start over, restart the backend.

## Demo flow

1. Bots tab → **Run the flood forecast**. Forecast Bot runs the model; the map shows the flood
   risk and the pre-positioning plan ("leave now, done in 126 min").
2. Bots tab → **Run a flood drill with Scout**. On a dry day there is nothing real to find, so
   the app sends Scout a drill packet (a warning, a river gauge, posts, a police road closure and
   a site's closure notice, all made up and labeled DRILL). Scout applies its normal rules:
   it reports the warning, the closure, the closed site and the cluster of "no service" posts,
   and holds back the two Lithonia posts and the off-topic one. Each report shows on the map
   and the Bots tab says whether the model agreed and what changed.
3. Calls tab: the Grok voice agent confirms a site is open.
4. Plan tab: Grok reads the briefing aloud.
5. Bots tab → **Send plan to Dispatch Bot**. Dispatch drafts the emails and delivery sheet
   and asks for approval.
