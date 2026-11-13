// Route planner and headless walker used by the tests (viewer/test_viewer.py). Not part of the game loop.
// The grid is built lazily from the real Rapier world: a cell is standable when the real capsule (plus a margin) fits there,
// so a doorway the capsule cannot pass is a doorway the planner cannot use. Levels are separate surfaces per cell, so a stair
// is just a chain of cells whose heights differ by one riser.
import { STEP, EYE_RATE } from './physics.js';

const S = 0.1, X0 = -0.6, Z0 = -20.6, NX = 134, NZ = 290, STEP_UP = 0.19; // the controller climbs 0.18 m, not 0.20 m

export function createNav(player) {
  const memo = new Map();
  let memoVersion = 0;
  let lastQueue = [];
  const surf = (i, j, margin) => {
    const k = (i * 1000 + j) * 64 + margin;
    if (!memo.has(k)) {
      // The grid centres sit on multiples of 0.1 m, exactly where slabs butt against each other: a ray down a crack misses both.
      // A second sample 1.3 cm away closes the crack; both lists are merged (heights within 2 cm are one surface).
      const a = player.surfaces(X0 + i * S, Z0 + j * S, margin * 0.01), b = player.surfaces(X0 + i * S + 0.013, Z0 + j * S + 0.011, margin * 0.01);
      memo.set(k, [...a, ...b.filter((y) => !a.some((v) => Math.abs(v - y) < 0.02))].sort((p, q) => q - p));
    }
    return memo.get(k);
  };
  const near = (list, y) => {
    let best = -1, d = STEP_UP + 1e-6;
    for (let n = 0; n < list.length; n++) if (Math.abs(list[n] - y) < d) { d = Math.abs(list[n] - y); best = n; }
    return best;
  };
  const cellOf = (x, z) => [Math.round((x - X0) / S), Math.round((z - Z0) / S)];
  const DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]];

  // plan(..., limit): with a limit the search only asks whether the start opens into at least `limit` cells ({ escaped: true }).
  // Shortest route from (fx, fy, fz) to the first of the targets [x, z, y?] reached. margin in cm added to the capsule radius.
  function plan(fx, fy, fz, targets, margin = 2, limit = 0) {
    player.syncLeaves(); // open door leaves are not obstacles
    if (memoVersion !== player.leafVersion) { memo.clear(); memoVersion = player.leafVersion; } // cells measured with a leaf in another pose are stale
    let [si, sj] = cellOf(fx, fz);
    let sk = near(surf(si, sj, margin), fy);
    if (sk < 0) { // the player stands closer to furniture than the margin: start from the nearest standable cell
      let best = null;
      for (let a = -5; a <= 5; a++) for (let b = -5; b <= 5; b++) {
        const k = near(surf(si + a, sj + b, margin), fy);
        if (k >= 0 && (!best || a * a + b * b < best[0])) best = [a * a + b * b, si + a, sj + b, k];
      }
      if (!best) return { error: 'start blocked' };
      [, si, sj, sk] = best;
    }
    const want = targets.map((t) => [...cellOf(t[0], t[1]), t[2]]);
    const key = (i, j, k) => (i * 1000 + j) * 8 + k;
    const prev = new Map([[key(si, sj, sk), null]]);
    const q = [[si, sj, sk]];
    for (let h = 0; h < q.length; h++) {
      if (limit && q.length >= limit) return { escaped: true };
      const [i, j, k] = q[h];
      const y = surf(i, j, margin)[k];
      const hit = want.findIndex((w) => Math.abs(w[0] - i) <= 3 && Math.abs(w[1] - j) <= 3 && (w[2] === undefined || Math.abs(w[2] - y) < 0.4));
      if (hit >= 0) return { target: hit, ...trace(prev, key, i, j, k, margin) };
      for (const [di, dj] of DIRS) {
        const ni = i + di, nj = j + dj;
        if (ni < 0 || nj < 0 || ni >= NX || nj >= NZ) continue;
        const nk = near(surf(ni, nj, margin), y);
        if (nk < 0 || prev.has(key(ni, nj, nk))) continue;
        if (di && dj && (near(surf(i + di, j, margin), y) < 0 || near(surf(i, j + dj, margin), y) < 0)) continue;
        prev.set(key(ni, nj, nk), [i, j, k]);
        q.push([ni, nj, nk]);
      }
    }
    lastQueue = q.map(([i, j, k]) => [X0 + i * S, Z0 + j * S, surf(i, j, margin)[k]]);
    return null;
  }

  function trace(prev, key, i, j, k, margin) {
    const cells = [];
    for (let c = [i, j, k]; c; c = prev.get(key(...c))) cells.push(c);
    cells.reverse();
    const pts = cells.map(([a, b, c]) => [X0 + a * S, Z0 + b * S, surf(a, b, margin)[c]]);
    // line-of-sight simplification: keep a waypoint only when the straight line to the next one leaves the standable cells
    const los = (a, b) => {
      const n = Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / (S * 0.5));
      let y = a[2];
      for (let t = 1; t <= n; t++) {
        const [ci, cj] = cellOf(a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n);
        const kk = near(surf(ci, cj, margin), y);
        if (kk < 0) return false;
        y = surf(ci, cj, margin)[kk];
      }
      return Math.abs(y - b[2]) < 0.05;
    };
    const out = [];
    let anchor = 0;
    for (let n = 2; n < pts.length; n++) if (!los(pts[anchor], pts[n])) { out.push(pts[n - 1]); anchor = n - 1; }
    out.push(pts[pts.length - 1]);
    return { waypoints: out, cells: cells.length };
  }

  // Walk with the real controller (player.step) toward each waypoint, facing it, as if W were held. No rendering, no real time.
  function walk(waypoints, { tol = 0.25, maxSeconds = 60, run = false } = {}) {
    const f = player.feet;
    let steps = 0, ok = true, minFloorGap = 0, maxDy = 0, eye = f.y;
    for (const [x, z] of waypoints) {
      let n = 0;
      while (Math.hypot(x - f.x, z - f.z) > tol) {
        if (n++ > maxSeconds / STEP) { ok = false; break; }
        player.yaw = Math.atan2(-(x - f.x), -(z - f.z));
        player.step({ forward: 1, right: 0, run });
        steps++;
        // R9: the biggest per-frame move of the eye, filtered exactly as main.js does (what the player feels on a stair)
        const ne = Math.abs(f.y - eye) > 0.5 ? f.y : eye + (f.y - eye) * Math.min(1, STEP * EYE_RATE);
        maxDy = Math.max(maxDy, Math.abs(ne - eye)); eye = ne;
        const fy = player.floorY();
        if (fy > -90) minFloorGap = Math.min(minFloorGap, f.y - fy);
      }
      if (!ok) break;
    }
    return { ok, seconds: steps * STEP, feet: [f.x, f.y, f.z], minFloorGap, maxDy, grounded: player.grounded };
  }


  // Walk like a person: the aim is corrected only every `reaction` seconds and carries a random error of up to `err` radians,
  // with the run of forward steps never turning faster than a mouse hand does. Deterministic (seeded). Used to prove a flight
  // is easy, not just possible: a stair that only a perfect steerer can climb fails here.
  function walkSloppy(waypoints, { tol = 0.3, maxSeconds = 60, err = 0.35, reaction = 0.4, seed = 1 } = {}) {
    const f = player.feet;
    let s = seed >>> 0;
    const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296 * 2 - 1; };
    let steps = 0, ok = true, minFloorGap = 0, aim = player.yaw, since = 1e9;
    for (const [x, z] of waypoints) {
      let n = 0;
      while (Math.hypot(x - f.x, z - f.z) > tol) {
        if (n++ > maxSeconds / STEP) { ok = false; break; }
        if ((since += STEP) >= reaction) { since = 0; aim = Math.atan2(-(x - f.x), -(z - f.z)) + rnd() * err; }
        const d = Math.atan2(Math.sin(aim - player.yaw), Math.cos(aim - player.yaw));
        player.yaw += Math.max(-0.12, Math.min(0.12, d)); // at most ~7 degrees per step
        player.step({ forward: 1, right: 0, run: false });
        steps++;
        const fy = player.floorY();
        if (fy > -90) minFloorGap = Math.min(minFloorGap, f.y - fy);
      }
      if (!ok) break;
    }
    return { ok, seconds: steps * STEP, feet: [f.x, f.y, f.z], minFloorGap, grounded: player.grounded };
  }

  // Teleport safety net: a spawn point can sit in a pocket that furniture has walled off. Returns the point itself when it opens
  // into the rest of the house, else the nearest point inside the box [min, max] that does, else null.
  function openSpot(x, y, z, box, limit = 4000) {
    const ok = (px, pz) => { const r = plan(px, y, pz, [], 2, limit); return r && r.escaped; };
    if (ok(x, z)) return [x, z];
    const cand = [];
    for (let cx = box.min[0] + 0.3; cx <= box.max[0] - 0.3; cx += 0.2) for (let cz = box.min[2] + 0.3; cz <= box.max[2] - 0.3; cz += 0.2) cand.push([cx, cz]);
    cand.sort((a, b) => Math.hypot(a[0] - x, a[1] - z) - Math.hypot(b[0] - x, b[1] - z));
    for (const [cx, cz] of cand.slice(0, 600)) {
      const [i, j] = cellOf(cx, cz);
      if (near(surf(i, j, 2), y) >= 0 && ok(cx, cz)) return [cx, cz];
    }
    return null;
  }

  return { plan, walk, walkSloppy, surf, cellOf, openSpot, visited: () => lastQueue };
}
