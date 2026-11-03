import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { BotActivity, BotStatus, CallLine, State, TraceItem } from "./types";

const fmt = (n: number) => Math.round(n).toLocaleString();

type Tab = "plan" | "calls" | "agent" | "bots";

type Props = {
  state: State;
  trace: TraceItem[];
  botLog: BotActivity[];
  calls: CallLine[];
  agentBusy: boolean;
  callStatus: string | null;
  onAsk: (q: string) => void;
  onStartCall: () => void;
  onEndCall: () => void;
  onPlayTrucks: () => void;
  focusSite: string | null;
};

export default function Panel(p: Props) {
  const [tab, setTab] = useState<Tab>("plan");
  return (
    <aside className="panel">
      <header className="panel-head">
        <button className="reset-demo" onClick={() => api.resetDemo()} title="Back to the starting flood, clean plan, empty Bots log">
          Reset demo
        </button>
        <h1>Starlink Drop</h1>
        <p className="panel-scenario">{p.state.scenario.name}</p>
        {p.state.scenario.forecast && (
          <p className="panel-forecast">
            From BDBI Aquatic Sustainability's flood model · {p.state.scenario.forecast.risk} risk, peak{" "}
            {Math.round(p.state.scenario.forecast.max_prob * 100)}%
            {p.state.scenario.forecast.run_date === "SAMPLE" && " · sample data"}
          </p>
        )}
      </header>
      <nav className="tabs" role="tablist">
        {(["plan", "calls", "agent", "bots"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "tab on" : "tab"} onClick={() => setTab(t)}>
            {t === "plan" ? "Plan" : t === "calls" ? `Calls${p.calls.length ? ` (${p.calls.length})` : ""}` : t === "agent" ? "Agent" : "Bots"}
          </button>
        ))}
      </nav>
      <div className="panel-body">
        {tab === "plan" && <PlanTab {...p} />}
        {tab === "calls" && <CallsTab {...p} />}
        {tab === "agent" && <AgentTab {...p} />}
        {tab === "bots" && <BotsTab {...p} />}
      </div>
    </aside>
  );
}

function PlanTab({ state, onPlayTrucks, focusSite }: Props) {
  const { plan, settings, routes } = state;
  const m = plan.metrics;
  const nx = plan.vs_nearest;
  const [briefing, setBriefing] = useState<string | null>(null);
  // idle -> loading -> playing <-> paused -> idle
  const [voice, setVoice] = useState<"idle" | "loading" | "playing" | "paused">("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const usingBrowserVoice = useRef(false);

  const set = (s: Parameters<typeof api.plan>[0]) => api.plan(s);

  function stopBriefing() {
    audioRef.current?.pause();
    audioRef.current = null;
    speechSynthesis.cancel();
    usingBrowserVoice.current = false;
    setVoice("idle");
  }

  // stop the voice if this tab is closed mid-briefing (otherwise there'd be no Pause button)
  useEffect(() => () => {
    audioRef.current?.pause();
    speechSynthesis.cancel();
  }, []);

  function togglePause() {
    if (voice === "playing") {
      usingBrowserVoice.current ? speechSynthesis.pause() : audioRef.current?.pause();
      setVoice("paused");
    } else if (voice === "paused") {
      usingBrowserVoice.current ? speechSynthesis.resume() : audioRef.current?.play();
      setVoice("playing");
    }
  }

  async function readBriefing() {
    stopBriefing();
    setVoice("loading");
    // Create the audio element inside the click so Safari lets it play later.
    const audio = new Audio();
    audioRef.current = audio;
    const browserVoice = (text: string) => {
      if (usingBrowserVoice.current || audioRef.current !== audio) return;  // one voice only, and not after Stop
      usingBrowserVoice.current = true;
      audio.pause();
      const u = new SpeechSynthesisUtterance(text);
      u.onend = () => setVoice("idle");
      speechSynthesis.speak(u);
      setVoice("playing");
    };
    try {
      const b = await api.briefing();
      setBriefing(b.text);
      // Download Grok's audio first; only use the browser voice if that fails.
      const r = await fetch("/api/briefing/audio");
      if (audioRef.current !== audio) return;          // stopped while loading
      if (!r.ok) return browserVoice(b.text);
      audio.src = URL.createObjectURL(await r.blob());
      audio.onplaying = () => speechSynthesis.cancel();
      audio.onended = () => setVoice("idle");
      await audio.play().then(() => setVoice("playing")).catch(() => browserVoice(b.text));
    } catch {
      setVoice("idle");
    }
  }

  return (
    <>
      <section className="compare" aria-label="Plan comparison">
        <div className="compare-main">
          <span className="num">{fmt(m.vulnerable_reached)}</span>
          <span className="cap">vulnerable residents reconnected</span>
        </div>
        <div className="compare-main">
          <span className="num">{fmt(m.people_reached)}</span>
          <span className="cap">people reconnected, of {fmt(m.people_without_service)} without service</span>
        </div>
        <table className="compare-table">
          <thead>
            <tr><th>Placement</th><th>People</th><th>Vulnerable</th></tr>
          </thead>
          <tbody>
            <tr className="us"><td>This plan</td><td>{fmt(m.people_reached)}</td><td>{fmt(m.vulnerable_reached)}</td></tr>
            <tr><td>Closest to the flooding</td><td>{fmt(plan.nearest_metrics.people_reached)}</td><td>{fmt(plan.nearest_metrics.vulnerable_reached)}</td></tr>
            <tr><td>One per county office</td><td>{fmt(plan.baseline_metrics.people_reached)}</td><td>{fmt(plan.baseline_metrics.vulnerable_reached)}</td></tr>
          </tbody>
        </table>
        <p className="compare-note">Planned for hour {plan.hour} of the flood. Dragging the timeline changes the map, not the plan.</p>
        {nx.people_x && (
          <p className="compare-note">
            {nx.people_x.toFixed(1)}× the people and {(nx.vulnerable_x ?? 0).toFixed(1)}× the vulnerable residents of placing the
            same terminals closest to the flooding.
            {plan.optimality && ` Within ${(100 - plan.optimality.greedy_share_of_optimal * 100).toFixed(1)}% of the best possible plan.`}
          </p>
        )}
      </section>

      <section className="controls" aria-label="Plan settings">
        <div className="seg" role="group" aria-label="Objective">
          <button className={settings.alpha === 0 ? "on" : ""} onClick={() => set({ alpha: 0 })}>Most people</button>
          <button className={settings.alpha > 0 ? "on" : ""} onClick={() => set({ alpha: 2 })}>Most vulnerable</button>
        </div>
        <Stepper label="Terminals" value={settings.k} min={1} max={40} onChange={(k) => set({ k })} />
        <Stepper label="Trucks" value={settings.n_trucks} min={1} max={6} onChange={(n) => set({ n_trucks: n })} />
      </section>

      {routes && (
        <section className="trucks">
          <p>
            {routes.n_trucks} trucks from {state.depot.name.replace(" Agency", "")}: all terminals running in{" "}
            <strong>{Math.round(routes.done_min)} min</strong>.
            {plan.cut_off_sites && plan.cut_off_sites.length > 0 &&
              ` Skipped ${plan.cut_off_sites.join(", ")}: cut off by flooded roads.`}
          </p>
          {routes.prepositioned && routes.prepositioned.done_min < routes.done_min && (
            <p className="prepos">
              Leave now, before the water rises: done in <strong>{Math.round(routes.prepositioned.done_min)} min</strong>,{" "}
              {Math.round(routes.done_min - routes.prepositioned.done_min)} min sooner.
            </p>
          )}
          <button className="btn" onClick={onPlayTrucks}>Play deliveries</button>
        </section>
      )}

      <ol className="sites">
        {plan.sites.map((s) => {
          const arr = routes?.arrivals[s.site_id];
          const st = state.site_status[s.site_id];
          return (
            <li key={s.site_id} className={`site ${s.status} ${focusSite === s.site_id ? "focus" : ""}`}>
              <span className="site-rank">{s.rank}</span>
              <div className="site-body">
                <span className="site-name">{s.name}</span>
                <span className="site-meta">
                  {s.type} · +{fmt(s.people_reached)} people
                  {arr ? ` · arrives ${Math.round(arr.arrive_min)} min` : ""}
                  {st?.headcount ? ` · ${st.headcount} on site` : ""}
                </span>
              </div>
              <span className={`site-status ${s.status}`}>{s.status === "planned" ? "Planned" : s.status === "confirmed" ? "Open" : "Closed"}</span>
              {s.status !== "closed" && (
                <button className="site-close" onClick={() => api.report(s.site_id, false)} title="Mark this site closed">
                  Mark closed
                </button>
              )}
            </li>
          );
        })}
        {state.closed_sites.map((c) => (
          <li key={c.site_id} className="site closed">
            <span className="site-rank">×</span>
            <div className="site-body">
              <span className="site-name">{c.name}</span>
              <span className="site-meta">{c.type} · replaced in the plan{c.notes ? ` · ${c.notes}` : ""}</span>
            </div>
            <span className="site-status closed">Closed</span>
          </li>
        ))}
      </ol>

      <section className="briefing">
        <div className="briefing-controls">
          {voice === "idle" && (
            <button className="btn primary" onClick={readBriefing}>Read briefing</button>
          )}
          {voice === "loading" && (
            <button className="btn primary" disabled>Preparing briefing…</button>
          )}
          {(voice === "playing" || voice === "paused") && (
            <button className="btn primary" onClick={togglePause}>{voice === "playing" ? "Pause" : "Resume"}</button>
          )}
          {voice !== "idle" && (
            <button className="btn" onClick={stopBriefing}>Stop</button>
          )}
        </div>
        {briefing && <p className="briefing-text">{briefing}</p>}
      </section>
    </>
  );
}

function Stepper({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void }) {
  return (
    <div className="stepper">
      <span>{label}</span>
      <button aria-label={`Fewer ${label.toLowerCase()}`} disabled={value <= min} onClick={() => onChange(value - 1)}>−</button>
      <output>{value}</output>
      <button aria-label={`More ${label.toLowerCase()}`} disabled={value >= max} onClick={() => onChange(value + 1)}>+</button>
    </div>
  );
}

function CallsTab({ calls, callStatus, onStartCall, onEndCall }: Props) {
  return (
    <>
      <section className="call-actions">
        <p>
          The dispatcher asks each site if it is open, how many people are there, and whether it has power. Answers update the
          plan while the call is running.
        </p>
        {callStatus && callStatus !== "ended" ? (
          <button className="btn danger" onClick={onEndCall}>End call ({callStatus})</button>
        ) : (
          <button className="btn primary" onClick={onStartCall}>Start call from this laptop</button>
        )}
        <p className="hint">For a phone call, dial the dispatcher's number; it reports here the same way.</p>
      </section>
      <ol className="transcript" aria-live="polite">
        {calls.length === 0 && <li className="empty">No calls yet.</li>}
        {calls.map((c, i) => (
          <li key={i} className={`line ${c.speaker}`}>
            <span className="who">{c.speaker === "agent" ? "Dispatcher" : c.speaker === "site" ? "Site" : "Update"}</span>
            <span>{c.text}</span>
          </li>
        ))}
      </ol>
    </>
  );
}

function AgentTab({ trace, agentBusy, onAsk }: Props) {
  const [q, setQ] = useState("We have 12 terminals and 2 trucks at the DeKalb office. Plan the next 6 hours.");
  return (
    <>
      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault();
          if (q.trim()) onAsk(q.trim());
        }}
      >
        <label htmlFor="ask">Ask the planning agent</label>
        <textarea id="ask" rows={3} value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn primary" disabled={agentBusy}>{agentBusy ? "Working…" : "Ask"}</button>
      </form>
      <ol className="trace" aria-live="polite">
        {trace.map((t, i) => (
          <li key={i} className={`trace-${t.kind}`}>
            {t.kind === "user" && <q>{t.text}</q>}
            {t.kind === "tool_call" && (
              <span>
                <b>{t.step}</b> {t.tool.replace(/_/g, " ")}
                {Object.keys(t.args).length > 0 && <code>{JSON.stringify(t.args).slice(0, 60)}</code>}
              </span>
            )}
            {t.kind === "tool_result" && <span className="result">{t.summary}</span>}
            {t.kind === "answer" && <p>{t.text}</p>}
          </li>
        ))}
      </ol>
    </>
  );
}

const BOTS = [
  { role: "forecast", step: "Predict", job: "Runs BDBI's flood model every morning" },
  { role: "scout", step: "Check", job: "Warnings, gauges, X posts, roads and sites" },
  { role: "dispatch", step: "Act", job: "Drafts outreach and the delivery sheet, with your approval" },
];

function BotsTab({ botLog, state }: Props) {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => {
    const load = () => api.botStatus().then((b) => setStatus(b.bots)).catch(() => {});
    load();
    const t = window.setInterval(load, 10000);
    return () => window.clearInterval(t);
  }, []);

  async function send(role: string, body: Parameters<typeof api.sendToBot>[1], label: string) {
    setResult(null);
    const r = await api.sendToBot(role, body).catch((e) => ({ ok: false, error: String(e) }));
    setResult(r.ok ? `${label}: sent.` : `${label}: ${r.error ?? "failed"}`);
  }

  const ago = (t: number | null) => {
    if (!t) return "not heard from yet";
    const s = Math.round(Date.now() / 1000 - t);
    return s < 60 ? "active just now" : s < 3600 ? `active ${Math.round(s / 60)} min ago` : "active earlier";
  };

  const obs = state.observations;
  const checked = obs.filter((o) => o.model_p != null);
  const agreed = checked.filter((o) => o.model_agrees).length;

  return (
    <>
      <ol className="bots loop">
        {BOTS.map(({ role, step, job }) => {
          const b = status?.[role];
          const live = b && (b.has_routine || b.last_seen);
          return (
            <li key={role} className={live ? "bot live" : "bot"}>
              <span className="bot-step">{step}</span>
              <span className="bot-dot" aria-hidden />
              <div>
                <span className="bot-name">{b?.name ?? role}</span>
                <span className="bot-job">{job} · {b ? (b.has_routine ? "connected" : ago(b.last_seen)) : "…"}</span>
              </div>
            </li>
          );
        })}
      </ol>

      {checked.length > 0 && (
        <p className="truth">
          <strong>{obs.length}</strong> field reports · the model agreed with <strong>{agreed}</strong> of {checked.length}.
          Reports it missed corrected the plan.
        </p>
      )}

      <section className="bot-actions">
        <button className="btn" onClick={() => send("forecast", { reason: "run the flood model now" }, "Forecast Bot")}>
          Run the flood forecast
        </button>
        <button className="btn" onClick={() => send("scout", { reason: "flood drill", drill: true }, "Flood drill")}>
          Run a flood drill with Scout
        </button>
        <button className="btn" onClick={() => send("scout", { reason: "check the current plan" }, "Scout Bot")}>
          Ask Scout to check the plan
        </button>
        <button className="btn primary" onClick={() => send("dispatch", { reason: "plan is final" }, "Dispatch Bot")}>
          Send plan to Dispatch Bot
        </button>
        {result && <p className="hint">{result}</p>}
      </section>

      <ol className="botlog" aria-live="polite">
        {botLog.length === 0 && <li className="empty">No Bot activity yet.</li>}
        {[...botLog].reverse().map((a, i) => (
          <li key={i} className={`botline ${a.kind}`}>
            <span className="who">{a.bot}</span>
            <span>
              {a.text}
              {a.url && (
                <> · <a href={a.url} target="_blank" rel="noreferrer">source</a></>
              )}
            </span>
          </li>
        ))}
      </ol>
    </>
  );
}
