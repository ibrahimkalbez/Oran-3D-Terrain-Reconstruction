/**
 * Sun position (NOAA solar calculator equations, accuracy ≈ 0.01° for 1950–2050) and sun
 * vectors in model coordinates. Defaults: Oran (35.697 N, 0.633 W), UTC+1 (Algeria, no DST).
 */

export const ORAN = { latitude: 35.6971, longitude: -0.6308, utc_offset: 1 };

const rad = (d: number) => (d * Math.PI) / 180;
const deg = (r: number) => (r * 180) / Math.PI;

export interface SunPosition {
  /** Degrees clockwise from north. */
  azimuth: number;
  /** Degrees above the horizon (with atmospheric refraction). */
  elevation: number;
}

export function solarPosition(dateUtc: Date, latitude: number, longitude: number): SunPosition {
  const jd = dateUtc.getTime() / 86400000 + 2440587.5;
  const t = (jd - 2451545) / 36525;
  const l0 = (((280.46646 + t * (36000.76983 + t * 0.0003032)) % 360) + 360) % 360;
  const m = 357.52911 + t * (35999.05029 - 0.0001537 * t);
  const e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t);
  const c = Math.sin(rad(m)) * (1.914602 - t * (0.004817 + 0.000014 * t)) + Math.sin(rad(2 * m)) * (0.019993 - 0.000101 * t) + Math.sin(rad(3 * m)) * 0.000289;
  const trueLong = l0 + c;
  const omega = 125.04 - 1934.136 * t;
  const appLong = trueLong - 0.00569 - 0.00478 * Math.sin(rad(omega));
  const meanObliq = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60;
  const obliq = meanObliq + 0.00256 * Math.cos(rad(omega));
  const decl = Math.asin(Math.sin(rad(obliq)) * Math.sin(rad(appLong)));
  const y = Math.tan(rad(obliq / 2)) ** 2;
  const eqTime =
    4 *
    deg(
      y * Math.sin(2 * rad(l0)) -
        2 * e * Math.sin(rad(m)) +
        4 * e * y * Math.sin(rad(m)) * Math.cos(2 * rad(l0)) -
        0.5 * y * y * Math.sin(4 * rad(l0)) -
        1.25 * e * e * Math.sin(2 * rad(m)),
    );
  const minutes = dateUtc.getUTCHours() * 60 + dateUtc.getUTCMinutes() + dateUtc.getUTCSeconds() / 60;
  const tst = (((minutes + eqTime + 4 * longitude) % 1440) + 1440) % 1440;
  const ha = tst / 4 < 0 ? tst / 4 + 180 : tst / 4 - 180;
  const latR = rad(latitude);
  const cosZen = Math.sin(latR) * Math.sin(decl) + Math.cos(latR) * Math.cos(decl) * Math.cos(rad(ha));
  const zen = Math.acos(Math.max(-1, Math.min(1, cosZen)));
  let elevation = 90 - deg(zen);
  // Atmospheric refraction (NOAA approximation).
  if (elevation > -0.575) {
    const te = Math.tan(rad(elevation));
    const refr =
      elevation > 85 ? 0 : elevation > 5 ? 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5 : -20.774 + elevation * (103.4 + elevation * (-12.79 + elevation * 0.711));
    elevation += refr / 3600;
  }
  const azDen = Math.cos(latR) * Math.sin(zen);
  let azimuth: number;
  if (Math.abs(azDen) < 1e-9) azimuth = latitude > 0 ? 180 : 0;
  else {
    const a = deg(Math.acos(Math.max(-1, Math.min(1, (Math.sin(latR) * Math.cos(zen) - Math.sin(decl)) / azDen))));
    azimuth = ha > 0 ? (a + 180) % 360 : (540 - a) % 360;
  }
  return { azimuth, elevation };
}

/** Unit vector towards the sun in model coordinates; `north` is the plan direction of north (default +Y). */
export function sunVector(p: SunPosition, north: [number, number] = [0, 1]): [number, number, number] {
  const len = Math.hypot(north[0], north[1]) || 1;
  const n = [north[0] / len, north[1] / len];
  const east = [n[1], -n[0]];
  const a = rad(p.azimuth);
  const el = rad(p.elevation);
  const h = Math.cos(el);
  return [east[0] * Math.sin(a) * h + n[0] * Math.cos(a) * h, east[1] * Math.sin(a) * h + n[1] * Math.cos(a) * h, Math.sin(el)];
}

export interface SunPathOptions {
  dates: string[];
  latitude?: number;
  longitude?: number;
  utc_offset?: number;
  step_minutes?: number;
  start_hour?: number;
  end_hour?: number;
  min_elevation?: number;
  north?: [number, number];
}

export interface SunSample {
  local_time: string;
  azimuth: number;
  elevation: number;
  vector: [number, number, number];
  weight_hours: number;
}

/** Sun samples over the given days (local times), each weighted by the time step in hours. */
export function sunPath(o: SunPathOptions): SunSample[] {
  const lat = o.latitude ?? ORAN.latitude;
  const lon = o.longitude ?? ORAN.longitude;
  const offset = o.utc_offset ?? ORAN.utc_offset;
  const step = o.step_minutes ?? 30;
  const out: SunSample[] = [];
  for (const day of o.dates) {
    const [y, mo, d] = day.split("-").map(Number);
    if (!y || !mo || !d) throw new Error(`Date '${day}' must be YYYY-MM-DD.`);
    for (let minute = (o.start_hour ?? 0) * 60 + step / 2; minute < (o.end_hour ?? 24) * 60; minute += step) {
      const utc = new Date(Date.UTC(y, mo - 1, d, 0, 0, 0) + (minute - offset * 60) * 60000);
      const pos = solarPosition(utc, lat, lon);
      if (pos.elevation <= (o.min_elevation ?? 0)) continue;
      const hh = Math.floor(minute / 60);
      const mm = Math.round(minute % 60);
      out.push({
        local_time: `${day} ${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`,
        azimuth: Math.round(pos.azimuth * 100) / 100,
        elevation: Math.round(pos.elevation * 100) / 100,
        vector: sunVector(pos, o.north),
        weight_hours: step / 60,
      });
    }
  }
  return out;
}
