import type { Vec2 } from "../geometry/polygon.js";

/**
 * Computational domain for an urban wind simulation following the usual best-practice
 * guidelines (COST Action 732, AIJ): inlet 5 H upstream, lateral and top boundaries 5 H from
 * the buildings, outlet 15 H downstream, blockage ratio below 3 %. H = tallest building.
 * Wind direction is meteorological: the direction the wind comes FROM, clockwise from north.
 */
export function windDomain(
  points: Vec2[],
  hMax: number,
  groundZ: number,
  directionDeg: number,
  north: [number, number] = [0, 1],
  factors: { upstream?: number; downstream?: number; lateral?: number; top?: number } = {},
) {
  if (points.length === 0 || !(hMax > 0)) throw new Error("Need building footprints and a positive height.");
  const len = Math.hypot(north[0], north[1]) || 1;
  const n: Vec2 = [north[0] / len, north[1] / len];
  const e: Vec2 = [n[1], -n[0]];
  const a = (directionDeg * Math.PI) / 180;
  // Unit vector pointing where the wind blows TO (opposite of where it comes from).
  const from: Vec2 = [e[0] * Math.sin(a) + n[0] * Math.cos(a), e[1] * Math.sin(a) + n[1] * Math.cos(a)];
  const flow: Vec2 = [-from[0], -from[1]];
  const side: Vec2 = [-flow[1], flow[0]];

  const along = points.map((p) => p[0] * flow[0] + p[1] * flow[1]);
  const across = points.map((p) => p[0] * side[0] + p[1] * side[1]);
  const [aMin, aMax] = [Math.min(...along), Math.max(...along)];
  const [cMin, cMax] = [Math.min(...across), Math.max(...across)];
  const up = (factors.upstream ?? 5) * hMax;
  const down = (factors.downstream ?? 15) * hMax;
  const lat = (factors.lateral ?? 5) * hMax;
  const top = Math.max((factors.top ?? 5) * hMax + hMax, 6 * hMax);

  const x0 = aMin - up;
  const x1 = aMax + down;
  const y0 = cMin - lat;
  const y1 = cMax + lat;
  const corner = (s: number, t: number): [number, number, number] => [flow[0] * s + side[0] * t, flow[1] * s + side[1] * t, groundZ];
  const width = y1 - y0;
  const frontal = (cMax - cMin) * hMax; // upper bound of the frontal area
  const blockage = frontal / (width * top);
  return {
    wind_from_deg: directionDeg,
    flow_direction: [round(flow[0]), round(flow[1]), 0],
    h_max: round(hMax),
    upstream: round(up),
    downstream: round(down),
    lateral: round(lat),
    height: round(top),
    length: round(x1 - x0),
    width: round(width),
    blockage_ratio_pct: round(blockage * 100, 2),
    blockage_ok: blockage < 0.03,
    corners: [corner(x0, y0), corner(x1, y0), corner(x1, y1), corner(x0, y1)].map((c) => c.map((v) => round(v))),
    inlet_edge: [corner(x0, y0), corner(x0, y1)].map((c) => c.map((v) => round(v))),
    ground_z: groundZ,
  };
}

function round(v: number, d = 3): number {
  return Math.round(v * 10 ** d) / 10 ** d;
}
