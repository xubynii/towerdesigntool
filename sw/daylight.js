/* Tower Workspace · daylight for LEED, ported from Sun & Wind Studio (physics.js and the daylight part of study.js).
   Daylight factor: sky component per floor cell from sixteen sight lines to the glass under the CIE overcast sky, plus the BRE
   externally and internally reflected components. Daylight autonomy: a climate-based run over the LM-83 occupied hours
   (8:00–18:00 clock time) of the CWEC typical year, each face lit by its own Perez all-weather sky; direct sun is left to the blinds,
   as LM-83 does for sDA. Annual sunlight exposure: every occupied hour with at least 1,000 lux of direct sun on the cell, counted
   per cell; a cell over 250 hours counts against ASE.
   Differences from the Studio: a floor plate is the outline of a mass (any polygon, courtyards as holes), faces take the compass
   bearing of the edge they sit on (15° steps) instead of four cardinal faces, there are no shading devices, and each mass is
   sampled at up to four levels. Runs in the analysis worker; `inputs.climate` is the bundled CWEC year. */
var TWX = (function () {
  'use strict';
  // ---- Perez 1990 sky and luminous efficacy, NOAA sun position (physics.js) ----
  const PZ = [[-0.008, 0.588, -0.062, -0.060, 0.072, -0.022], [0.130, 0.683, -0.151, -0.019, 0.066, -0.029], [0.330, 0.487, -0.221, 0.055, -0.064, -0.026], [0.568, 0.187, -0.295, 0.109, -0.152, -0.014],
    [0.873, -0.392, -0.362, 0.226, -0.462, 0.001], [1.132, -1.237, -0.412, 0.288, -0.823, 0.056], [1.060, -1.600, -0.359, 0.264, -1.127, 0.131], [0.678, -0.327, -0.250, 0.156, -1.377, 0.251]];
  const EPS = [1.065, 1.23, 1.5, 1.95, 2.8, 4.5, 6.2];
  const DIF = { a: [97.24, 107.22, 104.97, 102.39, 100.71, 106.42, 141.88, 152.23], b: [-0.46, 1.15, 2.96, 5.59, 5.94, 3.83, 1.90, 0.35], c: [12.00, 0.59, -5.53, -13.95, -22.75, -36.15, -53.24, -45.27], d: [-8.91, -3.95, -8.77, -13.90, -23.74, -28.83, -14.03, -7.98] };
  const DIR = { a: [57.20, 98.99, 109.83, 110.34, 106.36, 107.19, 105.75, 101.18], b: [-4.55, -3.46, -4.90, -5.84, -3.97, -1.25, 0.77, 1.58], c: [-2.98, -1.21, -1.71, -1.99, -1.75, -1.51, -1.26, -1.10], d: [117.12, 12.38, -8.81, -4.56, -6.16, -26.73, -34.44, -8.29] };
  const EXT_ILL = [131153, 130613, 128992, 126816, 124731, 123240, 122652, 123120, 124576, 126658, 128814, 130471];
  const ALBEDO = 0.2, DST = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0];   // daylight saving, hours ahead of standard time by month
  let H = null; const faceCache = {};
  function sunAt(lat, lon, doy, hour, tz) {
    const g = 2 * Math.PI / 365 * (doy - 1 + (hour - 12) / 24);
    const eq = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
    const decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g) - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
    const ha = ((hour * 60 + eq + 4 * lon - 60 * tz) / 4 - 180) * Math.PI / 180;
    const cz = Math.sin(lat) * Math.sin(decl) + Math.cos(lat) * Math.cos(decl) * Math.cos(ha), zen = Math.acos(Math.max(-1, Math.min(1, cz)));
    let az = Math.acos(Math.max(-1, Math.min(1, (Math.sin(decl) - Math.sin(lat) * cz) / (Math.cos(lat) * Math.sin(zen) || 1e-9)))); if (ha > 0) az = 2 * Math.PI - az;
    return { alt: Math.PI / 2 - zen, az, cz };
  }
  // every hour of the typical year: sun, Perez sky state, luminous efficacy; and the LM-83 occupied hours
  function prep(C) {
    const h = C && C.hourly; if (!h || !h.dhi || h.dhi.length < 8760) return null; if (H && H.src === h) return H;
    const n = 8760, lat = C.lat * Math.PI / 180, lon = C.lon, days = C.days || [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    const o = { src: h, n, doy: new Int16Array(n), mon: new Int8Array(n), hr: new Int8Array(n), alt: new Float32Array(n), az: new Float32Array(n), cz: new Float32Array(n),
      ghi: Float32Array.from(h.ghi), dni: Float32Array.from(h.dni), dhi: Float32Array.from(h.dhi), T: Float32Array.from(h.t),
      F1: new Float32Array(n), F2: new Float32Array(n), effD: new Float32Array(n), effB: new Float32Array(n), Ed: new Float32Array(n), Eb: new Float32Array(n), occ: null };
    const mStart = []; { let s = 0; for (let m = 0; m < 12; m++) { mStart.push(s); s += days[m]; } }
    for (let k = 0; k < n; k++) {
      const doy = Math.floor(k / 24) + 1, he = k % 24 + 1; let m = 11; for (let q = 0; q < 12; q++) { if (doy <= mStart[q] + days[q]) { m = q; break; } }
      o.doy[k] = doy; o.mon[k] = m; o.hr[k] = he; const p = sunAt(lat, lon, doy, he - 0.5, -8); o.alt[k] = p.alt; o.az[k] = p.az; o.cz[k] = p.cz;
      const dhi = o.dhi[k], dni = o.dni[k]; if (p.alt <= 0.01 || dhi <= 0) continue;
      const z = Math.PI / 2 - p.alt, zeta = 1.041 * z * z * z, eps = ((dhi + dni) / (dhi + 1e-4) + zeta) / (1 + zeta); let b = 0; while (b < 7 && eps > EPS[b]) b++;
      const altD = p.alt * 180 / Math.PI, am = (1 - 0.1 * (C.elev || 4) / 1000) / (Math.sin(p.alt) + 0.15 / Math.pow(altD + 3.885, 1.253));
      const delta = dhi * 93.73 * am / EXT_ILL[m], c = PZ[b];
      o.F1[k] = Math.max(0, c[0] + c[1] * delta + c[2] * z); o.F2[k] = c[3] + c[4] * delta + c[5] * z;
      const W = Math.exp(0.07 * (o.T[k] - 3) - 0.075);
      o.effD[k] = Math.max(0, DIF.a[b] + DIF.b[b] * W + DIF.c[b] * p.cz + DIF.d[b] * Math.log(Math.max(1e-4, delta)));
      o.effB[k] = Math.max(0, DIR.a[b] + DIR.b[b] * W + DIR.c[b] * Math.exp(5.73 * z - 5) + DIR.d[b] * delta);
      o.Ed[k] = dhi * o.effD[k]; o.Eb[k] = dni * o.effB[k];
    }
    const occ = []; for (let k = 0; k < n; k++) { const clockEnd = o.hr[k] + DST[o.mon[k]]; if (clockEnd >= 9 && clockEnd <= 18) occ.push(k); } o.occ = Int32Array.from(occ);
    H = o; for (const k in faceCache) delete faceCache[k]; return H;
  }
  // sky and ground-reflected illuminance (lux) on a vertical face of compass bearing az, for every hour
  function face(P, az) {
    const key = Math.round(((az % 360) + 360) % 360); if (faceCache[key]) return faceCache[key];
    const n = P.n, a0 = key * Math.PI / 180, b85 = Math.cos(85 * Math.PI / 180), skyL = new Float32Array(n), gndL = new Float32Array(n);
    for (let k = 0; k < n; k++) {
      if (P.alt[k] <= 0.01) continue; const cosT = Math.cos(P.alt[k]) * Math.cos(P.az[k] - a0), a = Math.max(0, cosT), b = Math.max(b85, P.cz[k]);
      const s = Math.max(0, P.dhi[k] * ((1 - P.F1[k]) * 0.5 + P.F1[k] * a / b + P.F2[k])); skyL[k] = s * P.effD[k]; gndL[k] = (P.Eb[k] * P.cz[k] + P.Ed[k]) * ALBEDO * 0.5;
    }
    return (faceCache[key] = { az: key, skyL, gndL });
  }

  // ---- the floor plate of a mass, in the engines' frame (x east, y north), 1 m cells ----
  const GAP = 0.45, DL_E = 16000, DL_R = { floor: 0.2, wall: 0.5, ceil: 0.8, erc: 0.2, maint: 0.9, udiLo: 100, udiHi: 3000, rays: 16 };
  const cieBand = (s1, s2) => 3 * ((s2 * s2 - s1 * s1) / 2 + 2 * (s2 * s2 * s2 - s1 * s1 * s1) / 3) / (7 * Math.PI);
  const vertSkyAbove = t => 1 - (t / 2 + Math.sin(2 * t) / 4) / (Math.PI / 4);
  const inPoly = (x, y, p) => { let c = false; for (let i = 0, j = p.length - 1; i < p.length; j = i++) { const a = p[i], b = p[j]; if ((a[1] > y) !== (b[1] > y) && x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]) c = !c; } return c; };
  const ringArea = p => { let a = 0; for (let i = 0; i < p.length; i++) { const q = p[(i + 1) % p.length]; a += p[i][0] * q[1] - q[0] * p[i][1]; } return a / 2; };
  function plate(m) {
    const N = r => r.map(q => [q[0], -q[1]]), outer = N(m.pts), holes = (m.holes || []).map(N), core = m.core ? { x0: m.core.x, x1: m.core.x + m.core.w, y0: -(m.core.z + m.core.d), y1: -m.core.z } : null;
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9; outer.forEach(q => { if (q[0] < x0) x0 = q[0]; if (q[0] > x1) x1 = q[0]; if (q[1] < y0) y0 = q[1]; if (q[1] > y1) y1 = q[1]; });
    x0 = Math.floor(x0); y0 = Math.floor(y0); const nx = Math.max(1, Math.ceil(x1 - x0)), ny = Math.max(1, Math.ceil(y1 - y0)), cells = new Uint8Array(nx * ny); let area = 0;
    const inCore = (x, y) => !!core && x >= core.x0 && x <= core.x1 && y >= core.y0 && y <= core.y1;
    for (let iy = 0; iy < ny; iy++) for (let ix = 0; ix < nx; ix++) { const x = x0 + ix + 0.5, y = y0 + iy + 0.5; if (inPoly(x, y, outer) && !holes.some(h => inPoly(x, y, h))) { cells[iy * nx + ix] = 1; if (!inCore(x, y)) area++; } }
    // edges with the compass bearing of their outward normal, in 15° steps; a courtyard edge faces into the courtyard
    const edges = [], addRing = (r, out) => { const sg = ringArea(r) > 0 ? 1 : -1; for (let i = 0; i < r.length; i++) { const a = r[i], b = r[(i + 1) % r.length], ex = b[0] - a[0], ey = b[1] - a[1], L = Math.hypot(ex, ey); if (L < 0.05) continue; const nxn = ey / L * sg * out, nyn = -ex / L * sg * out, az = (Math.atan2(nxn, nyn) * 180 / Math.PI + 360) % 360; edges.push({ a, b, L, nx: nxn, ny: nyn, bin: Math.round(az / 15) % 24 }); } };
    addRing(outer, 1); holes.forEach(h => addRing(h, -1));
    let cx = 0, cy = 0; outer.forEach(q => { cx += q[0]; cy += q[1]; }); cx /= outer.length; cy /= outer.length;
    const coreP = core ? 2 * (core.x1 - core.x0 + core.y1 - core.y0) : 0;
    return { x0, y0, nx, ny, cells, edges, inCore, area: Math.max(1, area), cx, cy, coreP };
  }
  const solid = (pl, cx, cy) => cx >= 0 && cy >= 0 && cx < pl.nx && cy < pl.ny && !!pl.cells[cy * pl.nx + cx];
  function edgeNear(pl, x, y) { let best = null, bd = 1e9; for (const e of pl.edges) { const ex = e.b[0] - e.a[0], ey = e.b[1] - e.a[1], t = Math.max(0, Math.min(1, ((x - e.a[0]) * ex + (y - e.a[1]) * ey) / (e.L * e.L))), d = Math.hypot(x - e.a[0] - ex * t, y - e.a[1] - ey * t); if (d < bd) { bd = d; best = e; } } return best; }
  // one sight line from a cell: how far to the glass, and which face it is. Null if the core is in the way
  function sight(pl, x0, y0, ix, iy, dx, dy, maxK) {
    let x = x0, y = y0; for (let k = 1; k < maxK; k++) { x += dx * 0.5; y += dy * 0.5; if (pl.inCore(x, y)) return null; if (!solid(pl, Math.floor(x - pl.x0), Math.floor(y - pl.y0))) { const e = edgeNear(pl, x, y); return e ? { D: k * 0.5, bin: e.bin } : null; } } return null;
  }
  // mean obstruction angle in front of each face: buildings 15 to 160 m away within about 53° of the face's normal
  function obstruction(pl, z, blds) {
    const out = {}; for (const e of pl.edges) { if (out[e.bin] != null) continue; const a = e.bin * 15 * Math.PI / 180, n = [Math.sin(a), Math.cos(a)]; let hs = 0, ds = 0, c = 0;
      for (const b of blds) { const dx = b.x - pl.cx, dy = b.y - pl.cy, d = Math.hypot(dx, dy); if (d < 15 || d > 160) continue; if ((dx * n[0] + dy * n[1]) / d < 0.6) continue; hs += b.top; ds += d; c++; }
      const hn = c ? hs / c : 0, dn = c ? Math.max(25, ds / c) : 60; out[e.bin] = Math.atan(Math.max(0, hn - z) / dn); } return out;
  }
  const band = (f2f, wwr) => { const hh = Math.max(1.2, f2f - GAP), head = hh, gh = Math.max(0.3, Math.min(head, hh * wwr)); return { hh, head, gh, sp: Math.max(0, head - gh) }; };

  function daylightFloor(P, pl, m, obs, step, hs, target, vt) {
    const B = band(m.f2f, m.wwr), tau = vt * DL_R.maint, NR = DL_R.rays, dphi = 2 * Math.PI / NR, dirs = []; for (let i = 0; i < NR; i++) { const a = (i + 0.5) * dphi; dirs.push([Math.cos(a), Math.sin(a)]); }
    const bins = [...new Set(pl.edges.map(e => e.bin))], bi = {}; bins.forEach((b, i) => bi[b] = i); const NB = bins.length, nc = pl.nx * pl.ny, sc = new Float32Array(nc * NB), df = new Float32Array(nc).fill(NaN), da = new Float32Array(nc).fill(NaN), cellList = [];
    const sill = Math.max(0, B.sp - 0.8), head = Math.max(sill + 0.1, B.head - 0.8);
    for (let iy = 0; iy < pl.ny; iy += step) for (let ix = 0; ix < pl.nx; ix += step) {
      if (!solid(pl, ix, iy)) continue; const x0 = pl.x0 + ix + 0.5, y0 = pl.y0 + iy + 0.5; if (pl.inCore(x0, y0)) continue; const ci = iy * pl.nx + ix; cellList.push(ci);
      for (const d0 of dirs) { const hit = sight(pl, x0, y0, ix, iy, d0[0], d0[1], 160); if (!hit) continue; const d = Math.max(0.5, hit.D), a0 = Math.atan(sill / d), a2 = Math.atan(head / d); if (a2 <= a0) continue;
        const a1 = Math.max(a0, Math.min(a2, obs[hit.bin] || 0)); sc[ci * NB + bi[hit.bin]] += tau * (dphi * cieBand(Math.sin(a1), Math.sin(a2)) + DL_R.erc * dphi * cieBand(Math.sin(a0), Math.sin(a1))); }
    }
    // internally reflected component (BRE split-flux), uniform over the plate
    let perim = 0; const Wg = new Float64Array(NB); for (const e of pl.edges) { perim += e.L; Wg[bi[e.bin]] += e.L * B.gh * tau; }
    const Af = pl.area, walls = (perim + pl.coreP) * B.hh, Atot = 2 * Af + walls, Rm = (DL_R.floor * Af + DL_R.ceil * Af + DL_R.wall * walls) / Atot;
    const Rfw = (DL_R.floor * Af + DL_R.wall * walls / 2) / (Af + walls / 2), Rcw = (DL_R.ceil * Af + DL_R.wall * walls / 2) / (Af + walls / 2), kI = 0.85 / (Atot * (1 - Rm));
    const fo = bins.map(b => vertSkyAbove(obs[b] || 0)); let ircDF = 0; for (let q = 0; q < NB; q++) ircDF += kI * Wg[q] * ((0.396 * fo[q] + DL_R.erc * 0.396 * (1 - fo[q])) * Rfw + 0.1 * Rcw);
    let sum = 0, n = 0, lit = 0, daSum = 0, uS = 0, uU = 0, uE = 0, df2 = 0;
    for (const ci of cellList) { let v = ircDF; for (let q = 0; q < NB; q++) v += sc[ci * NB + q]; df[ci] = v; sum += v; n++; if (v >= 0.02) df2++; }
    // the climate-based run: each face's own Perez sky over the occupied hours of every hs-th day
    const idx = []; for (const k of P.occ) if ((P.doy[k] - 1) % hs === 0) idx.push(k); const N = idx.length, kS = [], irc = new Float32Array(N);
    for (let q = 0; q < NB; q++) { const F = face(P, bins[q] * 15), ks = new Float32Array(N); for (let j = 0; j < N; j++) { const k = idx[j]; ks[j] = F.skyL[k] / 0.396; irc[j] += kI * Wg[q] * ((F.skyL[k] * fo[q] + DL_R.erc * F.skyL[k] * (1 - fo[q])) * Rfw + F.gndL[k] * Rcw); } kS.push(ks); }
    for (const ci of cellList) { let ok = 0, s = 0, u = 0, e = 0; const base = ci * NB;
      for (let j = 0; j < N; j++) { let E = irc[j]; for (let q = 0; q < NB; q++) E += sc[base + q] * kS[q][j]; if (E >= target) ok++; if (E < DL_R.udiLo) s++; else if (E > DL_R.udiHi) e++; else u++; }
      const daC = N ? ok / N : 0; da[ci] = daC; daSum += daC; if (daC >= 0.5) lit++; uS += s / Math.max(1, N); uU += u / Math.max(1, N); uE += e / Math.max(1, N); }
    return { df, da, mean: n ? sum / n : 0, df2: n ? df2 / n : 0, sda: n ? lit / n : 0, daMean: n ? daSum / n : 0, udiS: n ? uS / n : 0, udiU: n ? uU / n : 0, udiE: n ? uE / n : 0, cells: n, hours: N * hs };
  }
  // IES LM-83 sunlight exposure: occupied hours with direct sun, grouped by 2° of azimuth so each cell walks one sight line per azimuth
  function aseHours(P) {
    if (aseHours.c && aseHours.c.src === P) return aseHours.c.v; const bins = new Map();
    for (const k of P.occ) { if (P.alt[k] <= 0.03) continue; const E = P.Eb[k] * Math.sin(P.alt[k]); if (E < 1000) continue; const b = Math.round(P.az[k] * 90 / Math.PI); let o = bins.get(b); if (!o) { o = { az: b * 2, alt: [], E: [] }; bins.set(b, o); } o.alt.push(P.alt[k]); o.E.push(E); }
    const v = { bins: [...bins.values()].map(o => ({ az: o.az, alt: Float32Array.from(o.alt), E: Float32Array.from(o.E) })) }; aseHours.c = { src: P, v }; return v;
  }
  function aseFloor(P, pl, m, step, vt) {
    const B = band(m.f2f, m.wwr), AH = aseHours(P), hrs = new Float32Array(pl.nx * pl.ny).fill(NaN); let n = 0, over = 0;
    const D = AH.bins.map(b => { const a = b.az * Math.PI / 180; return { b, dx: Math.sin(a), dy: Math.cos(a) }; });
    for (let iy = 0; iy < pl.ny; iy += step) for (let ix = 0; ix < pl.nx; ix += step) {
      if (!solid(pl, ix, iy)) continue; const x0 = pl.x0 + ix + 0.5, y0 = pl.y0 + iy + 0.5; if (pl.inCore(x0, y0)) continue; n++; let Hh = 0;
      for (const q of D) { const hit = sight(pl, x0, y0, ix, iy, q.dx, q.dy, 200); if (!hit) continue; const b = q.b; for (let j = 0; j < b.alt.length; j++) { const hz = hit.D * Math.tan(b.alt[j]); if (hz < B.sp || hz > B.sp + B.gh) continue; if (b.E[j] * vt >= 1000) Hh++; } }
      hrs[iy * pl.nx + ix] = Hh; if (Hh >= 250) over++;
    }
    return { hrs, ase: n ? over / n : 0, cells: n };
  }

  // ---- the node: every mass, sampled at up to four levels; floors take the nearest sample ----
  const BANDS = {
    df: [{ to: 1, label: 'under 1%', color: '#344c6e' }, { to: 2, label: '1–2%', color: '#8497b0' }, { to: 3.5, label: '2–3.5%', color: '#dee4ec' }, { to: 5, label: '3.5–5%', color: '#e3b56f' }, { to: null, label: '5% or more', color: '#d9901f' }],
    da: [{ to: 20, label: 'under 20% of hours', color: '#2c3e60' }, { to: 50, label: '20–50%', color: '#7f93ad' }, { to: 75, label: '50–75% · counts for sDA', color: '#c7b77c' }, { to: null, label: '75% or more', color: '#f0c45c' }],
    ase: [{ to: 125, label: 'under 125 h', color: '#f0e0be' }, { to: 250, label: '125–250 h', color: '#e3b08c' }, { to: 500, label: '250–500 h · counts against ASE', color: '#cc7257' }, { to: null, label: '500 h or more', color: '#b8362b' }]
  };
  function fieldOf(pl, arr, step, k, bands, label, unit, z) {
    const nx = Math.ceil(pl.nx / step), ny = Math.ceil(pl.ny / step), values = new Float32Array(nx * ny).fill(NaN);
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) { const v = arr[(j * step) * pl.nx + i * step]; if (v === v) values[j * nx + i] = v * k; }
    return { kind: 'ground', field: label, label, unit, z, grid: { x0: pl.x0, y0: pl.y0, cell: step, nx, ny }, values, bands };
  }
  const pc = v => Math.round(v * 100) + '%';
  function run(inputs, params) {
    const P = prep(inputs.climate); if (!P) throw new Error('The hourly climate year did not load');
    const target = +params.target || 300, vt = Math.max(0.1, Math.min(0.95, +params.vt || 0.6)), step = +params.grid === 1 ? 1 : 2, hs = step === 1 ? 1 : 2, wwrAll = +params.wwr > 0 ? Math.min(0.95, +params.wwr / 100) : null;
    const src = (inputs.tw && inputs.tw.masses) || [], masses = src.filter(m => m.use !== 'parking' && m.floors > 0).map(m => Object.assign({}, m, { wwr: wwrAll != null ? wwrAll : (m.wwr || 40) / 100 }));
    if (!masses.length) return { state: 'unknown', headline: 'No occupied floors to study', items: [], data: { plates: [] } };
    const cen = r => { let x = 0, y = 0; r.forEach(q => { x += q[0]; y += q[1]; }); return [x / r.length, y / r.length]; };
    const ctx = ((inputs.site && inputs.site.context) || []).map(c => { const q = cen(c.ring); return { x: q[0], y: q[1], top: c.z1 }; });
    const items = [], plates = [], per = []; let A = 0, sSda = 0, sDa = 0, sUdi = 0, sDf = 0, sDf2 = 0, sAse = 0;
    for (const m of masses) {
      const pl = plate(m), others = src.filter(o => o.id !== m.id).map(o => { const q = cen(o.pts); return { x: q[0], y: -q[1], top: o.base + o.floors * o.f2f }; }), blds = ctx.concat(others);
      const F = m.floors, idx = [...new Set([0, Math.round((F - 1) / 3), Math.round(2 * (F - 1) / 3), F - 1])], memo = {}, samp = idx.map(L => { const z = m.base + L * m.f2f + 0.8, obs = obstruction(pl, z, blds), sig = Object.keys(obs).sort().map(k => k + ':' + Math.round(obs[k] * 90)).join(',');
        if (!memo[sig]) memo[sig] = { dl: daylightFloor(P, pl, m, obs, step, hs, target, vt), first: L }; return { L, z, r: memo[sig] }; });
      const ase = aseFloor(P, pl, m, step, vt);   // sun through the glass does not depend on the level here: one run per mass
      let w = 0, sda = 0, da = 0, udi = 0, df = 0, df2 = 0; for (let L = 0; L < F; L++) { let best = samp[0]; for (const q of samp) if (Math.abs(q.L - L) < Math.abs(best.L - L)) best = q; const r = best.r.dl; w++; sda += r.sda; da += r.daMean; udi += r.udiU; df += r.mean; df2 += r.df2; }
      const R = { sda: sda / w, da: da / w, udi: udi / w, df: df / w, df2: df2 / w, ase: ase.ase }, area = pl.area * F; per.push({ m, R, area });
      A += area; sSda += R.sda * area; sDa += R.da * area; sUdi += R.udi * area; sDf += R.df * area; sDf2 += R.df2 * area; sAse += R.ase * area;
      const seen = new Set(); samp.forEach(q => { if (seen.has(q.r.first)) return; seen.add(q.r.first); const r = q.r.dl, zf = m.base + q.r.first * m.f2f + 0.8, nm = m.name + ' · level ' + (q.r.first + 1);
        plates.push({ key: m.id + ':' + q.r.first, name: nm, df: fieldOf(pl, r.df, step, 100, BANDS.df, 'Daylight factor · ' + nm, '%', zf), da: fieldOf(pl, r.da, step, 100, BANDS.da, 'Daylight autonomy · ' + nm, '% of hours', zf), ase: fieldOf(pl, ase.hrs, step, 1, BANDS.ase, 'Hours of direct sun · ' + nm, 'h', zf) }); });
    }
    const sda = sSda / A, ase = sAse / A, df = sDf / A, pts = sda >= 0.75 ? 3 : sda >= 0.55 ? 2 : sda >= 0.40 ? 1 : 0, st = (v, a, b) => v >= a ? 'pass' : v >= b ? 'warn' : 'fail';
    items.push({ id: 'dl-sda', label: 'Spatial daylight autonomy · sDA' + target + '/50%', value: pc(sda) + ' of floor · ' + pts + ' point' + (pts === 1 ? '' : 's'), threshold: 'LEED v4.1 EQ Daylight, option 1: 40 / 55 / 75% for 1 / 2 / 3 points', state: st(sda, 0.55, 0.40) });
    items.push({ id: 'dl-da', label: 'Mean daylight autonomy', value: pc(sDa / A) + ' of occupied hours', threshold: 'at ' + target + ' lux from daylight alone, 8:00–18:00', state: 'unknown' });
    items.push({ id: 'dl-udi', label: 'Useful daylight · 100 to 3,000 lux', value: pc(sUdi / A) + ' of occupied hours', threshold: 'reported, not judged', state: 'unknown' });
    items.push({ id: 'dl-df', label: 'Mean daylight factor', value: (df * 100).toFixed(1) + '%', threshold: '2% or more reads as daylit (BRE, EN 17037 guide)', state: st(df, 0.02, 0.01) });
    items.push({ id: 'dl-df2', label: 'Floor with a daylight factor of 2% or more', value: pc(sDf2 / A), threshold: 'reported, not judged', state: 'unknown' });
    items.push({ id: 'dl-ase', label: 'Annual sunlight exposure · ASE1000/250h', value: pc(ase) + ' of floor', threshold: 'LEED v4.1: 10% or less, or show how glare is controlled', state: ase <= 0.10 ? 'pass' : 'warn' });
    for (const q of per) { const R = q.R, nm = q.m.name;
      items.push({ id: q.m.id + ':sda', label: 'sDA' + target + '/50%', value: pc(R.sda), threshold: '55% for 2 points', state: st(R.sda, 0.55, 0.40), towerName: nm });
      items.push({ id: q.m.id + ':df', label: 'Mean daylight factor', value: (R.df * 100).toFixed(1) + '%', threshold: '2% or more', state: st(R.df, 0.02, 0.01), towerName: nm });
      items.push({ id: q.m.id + ':ase', label: 'ASE1000/250h', value: pc(R.ase), threshold: '10% or less', state: R.ase <= 0.10 ? 'pass' : 'warn', towerName: nm }); }
    const state = sda < 0.40 ? 'fail' : (sda < 0.55 || ase > 0.10) ? 'warn' : 'pass';
    return { state, headline: 'sDA ' + pc(sda) + ' · ' + pts + ' pt · ASE ' + pc(ase) + ' · DF ' + (df * 100).toFixed(1) + '%', items, data: { plates, sda, ase, df, points: pts, hours: P.occ.length } };
  }
  const settings = [
    { id: 'target', label: 'Daylight target on the work plane', choices: [{ value: 200, label: '200 lux · circulation' }, { value: 300, label: '300 lux · offices, LEED sDA' }, { value: 500, label: '500 lux · classrooms, studios' }] },
    { id: 'vt', label: 'Glass visible transmittance', min: 0.2, max: 0.9, step: 0.05 },
    { id: 'wwr', label: 'Window-to-wall, 0 = from each material', min: 0, max: 95, step: 5, unit: '%' },
    { id: 'grid', label: 'Grid', choices: [{ value: 2, label: '2 m, every second day' }, { value: 1, label: '1 m, every day (slower)' }] }
  ];
  const defaults = { target: 300, vt: 0.6, wwr: 0, grid: 2 };
  return { IDS: ['tw.daylight'], RUNNERS: { 'tw.daylight': { id: 'tw.daylight', defaults, settings, run } }, settingsOf: id => settings, defaultsOf: id => defaults };
})();
if (typeof self !== 'undefined') self.TWX = TWX;
