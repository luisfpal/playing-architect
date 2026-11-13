// Floor plan drawn from the GLB itself (room volumes, walls, doors), north up: the street is at the bottom of the plan.
// Two uses: a small live minimap of the player's floor, and a large plan in the menu where a room tap teleports there.
// glTF frame: x east, z = -IFC y, so the street (z > 0) maps to the bottom.
export const FLOORS = [{ name: 'Ground floor', short: 'GF', y0: -1, y1: 1.6 }, { name: 'Upper floor', short: '1F', y0: 1.6, y1: 4.8 }, { name: 'Terrace', short: 'T', y0: 4.8, y1: 12 }];
export const floorOf = (y) => FLOORS.findIndex((f) => y >= f.y0 && y < f.y1);

export function createMinimap({ spaces, walls, doors, bounds }) {
  const [X0, X1, Z0, Z1] = bounds; // metres, glTF
  const draw = (cv, level, player, { labels, pad, hover }) => {
    const g = cv.getContext('2d');
    const W = cv.width, H = cv.height;
    const s = Math.min((W - 2 * pad) / (X1 - X0), (H - 2 * pad) / (Z1 - Z0));
    const px = (x) => pad + (x - X0) * s, py = (z) => pad + (z - Z0) * s;
    const f = FLOORS[level];
    g.clearRect(0, 0, W, H);
    for (const r of spaces) {
      const y = r.min[1] + 0.5;
      if (y < f.y0 || y >= f.y1) continue;
      const cur = player && r.name === player.room;
      g.fillStyle = r.name === hover ? 'rgba(47,124,246,.55)' : cur ? 'rgba(47,124,246,.35)' : 'rgba(255,255,255,.10)';
      g.fillRect(px(r.min[0]), py(r.min[2]), (r.max[0] - r.min[0]) * s, (r.max[2] - r.min[2]) * s);
    }
    g.fillStyle = 'rgba(230,236,243,.85)';
    for (const w of walls) if (w.min[1] >= f.y0 - 0.2 && w.min[1] < f.y1 - 0.5) g.fillRect(px(w.min[0]), py(w.min[2]), Math.max(1.2, (w.max[0] - w.min[0]) * s), Math.max(1.2, (w.max[2] - w.min[2]) * s));
    g.fillStyle = 'rgba(16,20,26,.9)';
    for (const d of doors) if (d.min[1] >= f.y0 - 0.2 && d.min[1] < f.y1 - 0.5) g.fillRect(px(d.min[0]) - 1, py(d.min[2]) - 1, (d.max[0] - d.min[0]) * s + 2, (d.max[2] - d.min[2]) * s + 2);
    if (labels) {
      g.font = '11px -apple-system, Segoe UI, sans-serif';
      g.textAlign = 'center'; g.textBaseline = 'middle';
      for (const r of spaces) {
        const y = r.min[1] + 0.5;
        if (y < f.y0 || y >= f.y1 || (r.max[0] - r.min[0]) * s < 34) continue;
        g.fillStyle = 'rgba(232,237,243,.9)';
        g.fillText(r.name.replace(/\s*\(.*\)/, ''), px((r.min[0] + r.max[0]) / 2), py((r.min[2] + r.max[2]) / 2), (r.max[0] - r.min[0]) * s - 4);
      }
    }
    if (player && floorOf(player.feet[1]) === level) {
      const x = px(player.feet[0]), y = py(player.feet[2]);
      g.save(); g.translate(x, y); g.rotate(-player.yaw); // yaw 0 faces -z (up the page)
      g.fillStyle = '#ff8a3d'; g.strokeStyle = '#111'; g.lineWidth = 1.5;
      g.beginPath(); g.moveTo(0, -8); g.lineTo(5.5, 6); g.lineTo(0, 3); g.lineTo(-5.5, 6); g.closePath(); g.fill(); g.stroke();
      g.restore();
    }
    cv._map = { s, pad, X0, Z0 };
  };
  // Room under a canvas point (for taps on the large plan), smallest volume first.
  const roomAtPoint = (cv, level, cx, cy) => {
    const m = cv._map; if (!m) return null;
    const r = cv.getBoundingClientRect();
    const x = ((cx - r.left) * cv.width / r.width - m.pad) / m.s + m.X0, z = ((cy - r.top) * cv.height / r.height - m.pad) / m.s + m.Z0;
    const f = FLOORS[level];
    let best = null, area = Infinity;
    for (const sp of spaces) {
      const y = sp.min[1] + 0.5;
      if (y < f.y0 || y >= f.y1 || x < sp.min[0] || x > sp.max[0] || z < sp.min[2] || z > sp.max[2]) continue;
      const a = (sp.max[0] - sp.min[0]) * (sp.max[2] - sp.min[2]);
      if (a < area) { area = a; best = sp.name; }
    }
    return best;
  };
  return { draw, roomAtPoint };
}
