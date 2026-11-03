export type Forecast = { model: string; run_date: string | null; points: number; max_prob: number; risk: string };

export type Scenario = {
  name: string;
  type: "flood" | "tornado";
  terminals: number;
  // flood
  focus?: [number, number];
  severity?: number;
  rivers?: string[];
  peak_hour?: number;
  rain_in?: number;
  forecast?: Forecast;
  road_closures?: { lat: number; lon: number; text: string }[];
  // tornado
  start?: [number, number];
  end?: [number, number];
  width_m?: number;
  max_wind_mph?: number;
};

export type Observation = {
  kind: "no_service" | "flooding" | "road_closed" | "gauge";
  lat: number;
  lon: number;
  place: string | null;
  text: string;
  source_url: string | null;
  bot: string;
  model_p?: number | null;
  model_agrees?: boolean;
  covered_by_plan?: boolean;
  plan_changed?: { added: string[]; removed: string[] };
};

export type Metrics = {
  people_reached: number;
  vulnerable_reached: number;
  people_without_service: number;
  vulnerable_without_service: number;
  share_reached: number;
};

export type PlanSite = {
  rank: number;
  site_id: string;
  name: string;
  type: string;
  lat: number;
  lon: number;
  is_hospital: boolean;
  people_reached: number;
  vulnerable_reached: number;
  status: "planned" | "confirmed" | "closed";
};

export type Plan = {
  k: number;
  alpha: number;
  hour: number;
  sites: PlanSite[];
  metrics: Metrics;
  baseline_metrics: Metrics;
  nearest_metrics: Metrics;
  vs_baseline: { people_x: number | null; vulnerable_x: number | null };
  vs_nearest: { people_x: number | null; vulnerable_x: number | null };
  optimality?: { greedy_share_of_optimal: number };
  cut_off_sites?: string[];
};

export type Route = { truck: number; stops: string[]; path: [number, number, number][]; duration_min: number };

export type Routes = {
  routes: Route[];
  arrivals: Record<string, { truck: number; arrive_min: number }>;
  unreachable: string[];
  blocked_road_segments: number;
  people_hours: number;
  people_hours_rank_order: number;
  reconnected_by_hour: Record<string, number>;
  n_trucks: number;
  done_min: number;
  prepositioned?: { done_min: number; people_hours: number; reachable: number };
};

export type SiteStatus = { state: string; open: boolean; headcount: number | null; has_power: boolean | null; notes: string };

export type State = {
  scenario: Scenario;
  settings: { k: number; alpha: number; hour: number; n_trucks: number };
  depot: { name: string; lat: number; lon: number };
  hours: number[];
  people_out_by_hour: number[];
  towers_dark_by_hour: number[];
  plan: Plan;
  alt_plan: Plan;
  routes: Routes | null;
  site_status: Record<string, SiteStatus>;
  closed_sites: { site_id: string; name: string; type: string; lat: number; lon: number; notes: string }[];
  observations: Observation[];
};

export type Layers = {
  hours: number[];
  tracts: { geoid: string; pop: number; svi: number; p_out: number[] }[];
  towers: { id: string; lat: number; lon: number; up: number[] }[];
  blocked_roads: [[number, number], [number, number]][];
  // flood: water points [lon, lat, radius_m, intensity]; tornado: the track
  hazard: { type: "flood"; paths: { path: [number, number][]; width_m: number; intensity: number }[]; points: [number, number, number, number][]; forecast_points: [number, number, number][] } | { type: "tornado"; path: [number, number][]; width_m: number };
};

export type TraceItem =
  | { kind: "user"; text: string }
  | { kind: "tool_call"; tool: string; args: Record<string, unknown>; step: number }
  | { kind: "tool_result"; tool: string; summary: string }
  | { kind: "answer"; text: string };

export type CallLine = { call: string; speaker: "agent" | "site" | "system"; text: string };

export type BotActivity = { t: number; bot: string; text: string; kind: string; url: string | null };

export type BotStatus = Record<string, { name: string; has_routine: boolean; last_seen: number | null }>;
