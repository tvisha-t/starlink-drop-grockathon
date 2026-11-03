import { useCallback, useEffect, useRef, useState } from "react";
import { api, wsUrl } from "./api";
import MapView from "./MapView";
import Timeline from "./Timeline";
import Panel from "./Panel";
import { startBrowserCall, type CallHandle } from "./browserCall";
import type { BotActivity, CallLine, Layers, State, TraceItem } from "./types";

const PLAYBACK_SECONDS = 25; // how long the delivery animation takes on screen

export default function App() {
  const [state, setState] = useState<State | null>(null);
  const [layers, setLayers] = useState<Layers | null>(null);
  const [shapes, setShapes] = useState<GeoJSON.FeatureCollection | null>(null);
  const [hourIdx, setHourIdx] = useState(4);
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const [botLog, setBotLog] = useState<BotActivity[]>([]);
  const [calls, setCalls] = useState<CallLine[]>([]);
  const [agentBusy, setAgentBusy] = useState(false);
  const [scenarioText, setScenarioText] = useState("Major flooding along the South River after 7 inches of rain");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [tripTime, setTripTime] = useState<number | null>(null);
  const [focusSite, setFocusSite] = useState<string | null>(null);
  const [callStatus, setCallStatus] = useState<string | null>(null);
  const call = useRef<CallHandle | null>(null);

  const loadLayers = useCallback(() => api.layers().then(setLayers), []);

  // First load. The backend may be restarting (it reloads when files change), so keep
  // retrying instead of giving up and leaving "Loading the planner…" on screen.
  useEffect(() => {
    let stop = false;
    const retry = (fn: () => Promise<unknown>) => {
      const go = () => fn().catch(() => !stop && window.setTimeout(go, 1500));
      go();
    };
    retry(() => api.state().then((s) => {
      setState(s);
      setHourIdx(s.hours.indexOf(s.settings.hour));
    }));
    retry(loadLayers);
    retry(() => api.botStatus().then((b) => setBotLog(b.activity)));
    retry(() => api.tractShapes().then(setShapes));
    return () => { stop = true; };
  }, [loadLayers]);

  // live updates from the server
  useEffect(() => {
    let ws: WebSocket;
    let timer: number;
    const connect = () => {
      ws = new WebSocket(wsUrl("/events"));
      ws.onmessage = (e) => {
        const ev = JSON.parse(e.data);
        if (ev.type === "plan_updated") setState(ev.state);
        else if (ev.type === "layers_changed") loadLayers();
        else if (ev.type === "reset") {
          setTrace([]);
          setCalls([]);
          setBotLog([]);
          setTripTime(null);
          setFocusSite(null);
          setNotice("Demo reset.");
        }
        else if (ev.type === "observation")
          setState((s) => (s ? { ...s, observations: [...s.observations, ev.observation] } : s));
        else if (ev.type === "trace") setTrace((t) => [...t, ev]);
        else if (ev.type === "bot_activity") setBotLog((b) => [...b.slice(-199), ev]);
        else if (ev.type === "call_transcript") setCalls((c) => [...c, { call: ev.call, speaker: ev.speaker, text: ev.text }]);
        else if (ev.type === "site_status") {
          const msg = ev.open
            ? `${ev.name} is open${ev.headcount ? ` with about ${ev.headcount} people` : ""}.`
            : `${ev.name} is closed. Replacing it in the plan.`;
          setCalls((c) => [...c, { call: "update", speaker: "system", text: msg }]);
          setNotice(msg);
          setFocusSite(ev.site_id);
        } else if (ev.type === "call_error") setNotice(`Call problem: ${JSON.stringify(ev.error)}`);
      };
      ws.onclose = () => (timer = window.setTimeout(connect, 1500));
    };
    connect();
    const ping = window.setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send("ping"), 20000);
    return () => {
      window.clearInterval(ping);
      window.clearTimeout(timer);
      ws.onclose = null;
      ws.close();
    };
  }, [loadLayers]);

  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 6000);
    return () => window.clearTimeout(t);
  }, [notice]);

  async function simulate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api.scenario(scenarioText);
      setNotice(
        r.parsed_by === "grok"
          ? "Grok set the scenario."
          : r.parsed_by.includes("grok error")
            ? "Grok didn't respond, so the offline reader set the scenario."
            : "Scenario set (offline reader).",
      );
      setTripTime(null);
      setCalls([]);
    } catch (err) {
      setNotice(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function ask(q: string) {
    setAgentBusy(true);
    setTrace([]);
    try {
      await api.agent(q);
    } catch (err) {
      setNotice(String(err));
    } finally {
      setAgentBusy(false);
    }
  }

  function playTrucks() {
    if (!state?.routes) return;
    const end = state.routes.done_min * 60;
    const t0 = performance.now();
    const tick = (now: number) => {
      const t = ((now - t0) / 1000 / PLAYBACK_SECONDS) * end;
      setTripTime(Math.min(t, end));
      if (t < end) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  async function startCall() {
    try {
      setCallStatus("connecting");
      call.current = await startBrowserCall(setCallStatus);
    } catch (err) {
      setCallStatus(null);
      setNotice(`Couldn't start the call: ${err}`);
    }
  }

  function endCall() {
    call.current?.hangup();
    call.current = null;
    setCallStatus(null);
  }

  if (!state) return <div className="loading">Loading the planner…</div>;

  return (
    <div className="app">
      <main className="stage">
        <MapView state={state} layers={layers} shapes={shapes} hourIdx={hourIdx} tripTime={tripTime} onPickSite={setFocusSite} />
        <form className="command" onSubmit={simulate}>
          <label htmlFor="scenario" className="sr-only">Describe a flood</label>
          <input id="scenario" value={scenarioText} onChange={(e) => setScenarioText(e.target.value)} placeholder="Describe a flood, e.g. flash flooding on Peachtree Creek, 5 inches of rain" />
          <button className="btn primary" disabled={busy}>{busy ? "Simulating…" : "Simulate"}</button>
        </form>
        <div className="legend" aria-hidden>
          <span><i className="sw outage" /> Likely no service</span>
          <span><i className="sw tower-down" /> Tower dark</span>
          {layers?.hazard.type === "flood" && <span><i className="sw water" /> Flooding</span>}
          {layers?.hazard.type === "flood" && layers.hazard.forecast_points.length > 0 && (
            <span><i className="sw forecast" /> BDBI model forecast (% flood chance)</span>
          )}
          <span><i className="sw road" /> Road under water or closed</span>
          <span><i className="sw site" /> Terminal site, 2 km reach</span>
          {state.observations.length > 0 && <span><i className="sw report" /> Report from Scout Bot</span>}
        </div>
        {tripTime !== null && state.routes && (
          <div className="clock">
            {Math.floor(tripTime / 60)} min · {fmtReconnected(state, tripTime)} people reconnected
          </div>
        )}
        <Timeline
          hours={state.hours}
          peopleOut={state.people_out_by_hour}
          hourIdx={hourIdx}
          onChange={setHourIdx}
        />
        {notice && <div className="notice" role="status">{notice}</div>}
      </main>
      <Panel
        state={state}
        trace={trace}
        botLog={botLog}
        calls={calls}
        agentBusy={agentBusy}
        callStatus={callStatus}
        onAsk={ask}
        onStartCall={startCall}
        onEndCall={endCall}
        onPlayTrucks={playTrucks}
        focusSite={focusSite}
      />
    </div>
  );
}

function fmtReconnected(state: State, t: number) {
  const r = state.routes!;
  let total = 0;
  for (const s of state.plan.sites) {
    const a = r.arrivals[s.site_id];
    if (a && a.arrive_min * 60 <= t) total += s.people_reached;
  }
  return total.toLocaleString();
}
