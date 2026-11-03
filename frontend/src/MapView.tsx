import { useMemo } from "react";
import DeckGL from "@deck.gl/react";
import { GeoJsonLayer, LineLayer, PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { TripsLayer } from "@deck.gl/geo-layers";
import { Map } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Layers, State } from "./types";

const BASEMAP = "https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json";
const INITIAL_VIEW = { longitude: -84.29, latitude: 33.73, zoom: 10.6, pitch: 0, bearing: 0 };

type RGBA = [number, number, number, number];

// sequential outage ramp: transparent -> amber -> red -> deep red
const RAMP: [number, RGBA][] = [
  [0.12, [255, 170, 60, 0]],
  [0.3, [255, 170, 60, 115]],
  [0.6, [235, 90, 50, 175]],
  [1.0, [196, 32, 62, 225]],
];

function outageColor(p: number): RGBA {
  if (p <= RAMP[0][0]) return RAMP[0][1];
  for (let i = 1; i < RAMP.length; i++) {
    const [x1, c1] = RAMP[i];
    const [x0, c0] = RAMP[i - 1];
    if (p <= x1) {
      const t = (p - x0) / (x1 - x0);
      return c0.map((v, k) => Math.round(v + (c1[k] - v) * t)) as RGBA;
    }
  }
  return RAMP[RAMP.length - 1][1];
}

const SIGNAL: RGBA = [87, 199, 255, 255];
const CLOSED: RGBA = [154, 163, 169, 255];
const CONFIRMED: RGBA = [92, 214, 151, 255];
// ground-truth reports from Scout Bot
const REPORT = {
  no_service: [232, 121, 249, 255] as RGBA,   // magenta: people say they have no service
  flooding: [125, 200, 255, 255] as RGBA,     // pale blue: flooding seen on the ground
  road_closed: [255, 92, 92, 255] as RGBA,    // red: road closed
};

const KIND_LABEL: Record<string, string> = { no_service: "No service", flooding: "Flooding", road_closed: "Road closed" };

type Props = {
  state: State;
  layers: Layers | null;
  shapes: GeoJSON.FeatureCollection | null;
  hourIdx: number;
  tripTime: number | null;
  onPickSite: (siteId: string) => void;
};

export default function MapView({ state, layers, shapes, hourIdx, tripTime, onPickSite }: Props) {
  const pOut = useMemo(() => {
    const m = new globalThis.Map<string, number>();
    layers?.tracts.forEach((t) => m.set(t.geoid, t.p_out[hourIdx] ?? 0));
    return m;
  }, [layers, hourIdx]);

  const sites = state.plan.sites;
  const unreachable = new Set(state.routes?.unreachable ?? []);

  const deckLayers = [
    shapes &&
      new GeoJsonLayer({
        id: "tracts",
        data: shapes,
        filled: true,
        stroked: true,
        getFillColor: (f: any) => outageColor(pOut.get(f.properties.geoid) ?? 0),
        getLineColor: [255, 255, 255, 18],
        lineWidthMinPixels: 0.5,
        updateTriggers: { getFillColor: [hourIdx, layers] },
        transitions: { getFillColor: 400 },
      }),
    layers?.hazard.type === "flood" &&
      new PathLayer({
        id: "water",
        data: layers.hazard.paths,
        getPath: (d: any) => d.path,
        getWidth: (d: any) => Math.max(d.width_m, 80),
        widthUnits: "meters",
        widthMinPixels: 1.5,
        capRounded: true,
        jointRounded: true,
        getColor: (d: any) => [38, 120, 230, Math.round(70 + 120 * d.intensity)],
      }),
    layers?.hazard.type === "flood" &&
      new ScatterplotLayer({
        id: "water-reported",
        data: layers.hazard.points,
        getPosition: (d: any) => [d[0], d[1]],
        getRadius: (d: any) => d[2],
        radiusUnits: "meters",
        getFillColor: [38, 120, 230, 110],
      }),
    layers?.hazard.type === "tornado" &&
      new PathLayer({
        id: "track",
        data: [{ path: layers.hazard.path }],
        getPath: (d: any) => d.path,
        getWidth: layers.hazard.width_m,
        widthUnits: "meters",
        capRounded: true,
        getColor: [255, 255, 255, 46],
      }),
    layers &&
      new LineLayer({
        id: "blocked-roads",
        data: layers.blocked_roads,
        getSourcePosition: (d: any) => d[0],
        getTargetPosition: (d: any) => d[1],
        getColor: [255, 92, 92, 200],
        getWidth: 1.5,
        widthUnits: "pixels",
      }),
    layers &&
      new ScatterplotLayer({
        id: "towers",
        data: layers.towers,
        getPosition: (d: any) => [d.lon, d.lat],
        getRadius: 2.2,
        radiusUnits: "pixels",
        getFillColor: (d: any) => {
          const up = d.up[hourIdx];
          return up > 0.8 ? [214, 222, 228, 90] : up > 0.4 ? [244, 201, 93, 220] : [255, 80, 70, 255];
        },
        updateTriggers: { getFillColor: [hourIdx] },
      }),
    new ScatterplotLayer({
      id: "reach",
      data: sites.filter((s) => s.status !== "closed"),
      getPosition: (d: any) => [d.lon, d.lat],
      getRadius: 2000,
      radiusUnits: "meters",
      stroked: true,
      filled: true,
      getFillColor: [87, 199, 255, 26],
      getLineColor: [87, 199, 255, 110],
      lineWidthMinPixels: 1,
    }),
    tripTime !== null &&
      state.routes &&
      new TripsLayer({
        id: "trucks",
        data: state.routes.routes,
        getPath: (d: any) => d.path.map((p: number[]) => [p[0], p[1]]),
        getTimestamps: (d: any) => d.path.map((p: number[]) => p[2]),
        getColor: (d: any) => (d.truck === 0 ? [255, 255, 255] : [255, 214, 120]),
        widthMinPixels: 4,
        trailLength: 900,
        currentTime: tripTime,
        capRounded: true,
        jointRounded: true,
      }),
    new ScatterplotLayer({
      id: "sites",
      data: sites,
      pickable: true,
      getPosition: (d: any) => [d.lon, d.lat],
      getRadius: 11,
      radiusUnits: "pixels",
      stroked: true,
      lineWidthMinPixels: 2,
      getFillColor: (d: any) => {
        const arrived = tripTime !== null && state.routes?.arrivals[d.site_id] &&
          state.routes.arrivals[d.site_id].arrive_min * 60 <= tripTime;
        if (d.status === "closed") return CLOSED;
        if (arrived || d.status === "confirmed") return CONFIRMED;
        return [15, 28, 38, 255];
      },
      getLineColor: (d: any) => (unreachable.has(d.site_id) ? [255, 92, 92, 255] : d.status === "closed" ? CLOSED : SIGNAL),
      updateTriggers: { getFillColor: [tripTime, state.site_status] },
      onClick: (info: any) => info.object && onPickSite(info.object.site_id),
    }),
    new TextLayer({
      id: "site-rank",
      data: sites,
      getPosition: (d: any) => [d.lon, d.lat],
      getText: (d: any) => String(d.rank),
      getSize: 12,
      getColor: [238, 240, 234, 255],
      fontFamily: "Barlow Condensed, sans-serif",
      fontWeight: 700,
    }),
    new ScatterplotLayer({
      id: "closed-sites",
      data: state.closed_sites,
      getPosition: (d: any) => [d.lon, d.lat],
      getRadius: 9,
      radiusUnits: "pixels",
      stroked: true,
      lineWidthMinPixels: 2,
      getFillColor: [15, 28, 38, 255],
      getLineColor: CLOSED,
    }),
    new TextLayer({
      id: "closed-x",
      data: state.closed_sites,
      getPosition: (d: any) => [d.lon, d.lat],
      getText: () => "x",
      getSize: 14,
      getColor: CLOSED,
      fontFamily: "Barlow Condensed, sans-serif",
      fontWeight: 700,
    }),
    layers?.hazard.type === "flood" && layers.hazard.forecast_points.length > 0 &&
      new ScatterplotLayer({
        id: "forecast",
        data: layers.hazard.forecast_points,
        pickable: true,
        getPosition: (d: any) => [d[0], d[1]],
        getRadius: 13,
        radiusUnits: "pixels",
        stroked: true,
        lineWidthMinPixels: 1.5,
        getFillColor: (d: any) => [167, 139, 250, Math.round(60 + 170 * d[2])],
        getLineColor: [237, 233, 254, 220],
      }),
    layers?.hazard.type === "flood" && layers.hazard.forecast_points.length > 0 &&
      new TextLayer({
        id: "forecast-pct",
        data: layers.hazard.forecast_points,
        getPosition: (d: any) => [d[0], d[1]],
        getText: (d: any) => `${Math.round(d[2] * 100)}`,
        getSize: 11,
        getColor: [255, 255, 255, 255],
        fontFamily: "Barlow Condensed, sans-serif",
        fontWeight: 700,
      }),
    new ScatterplotLayer({
      id: "reports",
      data: state.observations.filter((o) => o.kind !== "gauge"),
      pickable: true,
      getPosition: (d: any) => [d.lon, d.lat],
      getRadius: 7,
      radiusUnits: "pixels",
      stroked: true,
      lineWidthMinPixels: 2,
      getFillColor: (d: any) => REPORT[d.kind as keyof typeof REPORT] ?? [255, 255, 255, 255],
      getLineColor: [15, 28, 38, 255],
    }),
    new TextLayer({
      id: "report-glyph",
      data: state.observations.filter((o) => o.kind !== "gauge"),
      getPosition: (d: any) => [d.lon, d.lat],
      getText: (d: any) => (d.kind === "no_service" ? "!" : d.kind === "road_closed" ? "–" : "~"),
      getSize: 12,
      getColor: [15, 28, 38, 255],
      fontFamily: "Barlow Condensed, sans-serif",
      fontWeight: 700,
    }),
    new ScatterplotLayer({
      id: "depot",
      data: [state.depot],
      getPosition: (d: any) => [d.lon, d.lat],
      getRadius: 8,
      radiusUnits: "pixels",
      stroked: true,
      getFillColor: [255, 255, 255, 255],
      getLineColor: [15, 28, 38, 255],
      lineWidthMinPixels: 3,
    }),
  ].filter(Boolean);

  return (
    <DeckGL
      initialViewState={INITIAL_VIEW}
      controller
      layers={deckLayers as any}
      getCursor={({ isHovering }) => (isHovering ? "pointer" : "grab")}
      getTooltip={({ object, layer }: any) => {
        if (!object) return null;
        if (layer?.id === "sites")
          return { text: `${object.rank}. ${object.name}\n${object.type} · +${object.people_reached.toLocaleString()} people` };
        if (layer?.id === "forecast")
          return { text: `BDBI Aquatic Sustainability flood model\nFlood probability here: ${Math.round(object[2] * 100)}%` };
        if (layer?.id === "reports") {
          const check = object.model_p == null ? "" : object.model_agrees
            ? `\nModel agrees (${Math.round(object.model_p * 100)}%)` : `\nModel missed this (${Math.round(object.model_p * 100)}%), plan corrected`;
          return { text: `${KIND_LABEL[object.kind] ?? object.kind}${object.place ? ` · ${object.place}` : ""}\n${object.text}${check}\n${object.bot}` };
        }
        return null;
      }}
    >
      <Map mapStyle={BASEMAP} reuseMaps />
    </DeckGL>
  );
}
