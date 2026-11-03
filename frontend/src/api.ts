import type { BotActivity, BotStatus, Layers, State } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.json() as Promise<T>;
}

const post = <T,>(path: string, body: unknown = {}) => req<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  state: () => req<State>("/api/state"),
  layers: () => req<Layers>("/api/layers"),
  tractShapes: () => req<GeoJSON.FeatureCollection>("/api/tracts.geojson"),
  health: () => req<{ grok: boolean; roads: boolean; voice_agent: boolean }>("/api/health"),
  scenario: (text: string) => post<{ parsed_by: string }>("/api/scenario", { text }),
  preset: (name: string) => post("/api/scenario/preset/" + name),
  plan: (settings: { k?: number; alpha?: number; hour?: number; n_trucks?: number }) => post<State>("/api/plan", settings),
  report: (siteId: string, open: boolean, headcount?: number) =>
    post(`/api/sites/${siteId}/status`, { open, headcount: headcount ?? null, notes: open ? "" : "marked closed from the map" }),
  resetSites: () => post("/api/reset_sites"),
  resetDemo: () => post("/api/reset_demo"),
  agent: (message: string) => post<{ answer: string }>("/api/agent", { message }),
  briefing: () => post<{ text: string; source: string }>("/api/briefing"),
  botStatus: () => req<{ bots: BotStatus; activity: BotActivity[] }>("/api/bots/status"),
  sendToBot: (role: string, body: { reason?: string; alert_text?: string; drill?: boolean } = {}) =>
    post<{ ok: boolean; error?: string }>(`/api/bots/send/${role}`, body),
};

export function wsUrl(path: string) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}
