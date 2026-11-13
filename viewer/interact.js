// Openable parts: drawers slide, doors swing, the fridge opens. Contract: docs/glb_contract.md ("Openable parts").
// Wiring (one line in main.js, after createPhysics): initInteract(scene, camera, renderer, { world: world.phys.world });
// Triggers: E key, mouse click while walking, a tap on the canvas, or window.houseInteract.interact() (the touch button).
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';

const NAME = /^(.*)__(drawer|door|fridge_door|window)_(\d+)$/;
const NOUN = { drawer: 'drawer', door: 'door', fridge_door: 'fridge door', window: 'window' };
const REACH = 2.4, ASSIST_REACH = 1.8, ASSIST_COS = Math.cos(THREE.MathUtils.degToRad(25));
const SECONDS = { drawer: 0.4, door: 0.7, fridge_door: 0.7, window: 0.7 };
const PLAYER_R = 0.3, PLAYER_H = 1.7;
const Y = new THREE.Vector3(0, 1, 0);
const ease = (x) => x * x * (3 - 2 * x);

/** Facade service leaves are louvre panels, not room parts. They follow the door convention in the GLB (exported open,
 *  close_deg back to closed), but nobody stands at 3.4 m to walk through one: leave them closed and out of the hints. */
function closeServiceLeaf(node, u) {
  const P = new THREE.Vector3(u.pivot_x, 0, -u.pivot_y);
  const q = new THREE.Quaternion().setFromAxisAngle(Y, THREE.MathUtils.degToRad(u.close_deg ?? 90));
  const p0 = node.position.clone(), q0 = node.quaternion.clone();
  node.quaternion.copy(q).multiply(q0);
  node.position.copy(p0).sub(P).applyQuaternion(q).add(P);
  node.updateMatrixWorld(true);
}

export function initInteract(scene, camera, renderer, opts = {}) {
  const world = opts.world ?? null;
  const entries = [];
  const byMesh = new Map();
  const meshes = [];

  scene.updateMatrixWorld(true);
  scene.traverse((o) => {
    const u = o.userData;
    const m = NAME.exec(o.name);
    const kind = m ? m[2] : u.openable;
    if (!kind || !NOUN[kind]) return;
    if (u.role === 'service_leaf') { closeServiceLeaf(o, u); return; }
    const e = makeEntry(o, kind, m ? m[1] : o.name);
    entries.push(e);
    o.traverse((c) => { if (c.isMesh) { byMesh.set(c.uuid, e); meshes.push(c); } }); // parents come first, so deeper entries win
  });

  function makeEntry(node, kind, group) {
    const u = node.userData;
    const p0 = node.position.clone(), q0 = node.quaternion.clone();
    const e = { node, kind, group, name: node.name, t: 1, target: 1, from: 1, started: 0, collider: null, local: null, hint: null };
    if (u.pivot_x !== undefined) {
      // World-space vertices about a vertical pivot in IFC (x, y). A room door is exported OPEN and turns closed;
      // a hinged window is exported CLOSED and turns open by open_deg.
      const P = new THREE.Vector3(u.pivot_x, 0, -u.pivot_y), win = kind === 'window';
      const rad = THREE.MathUtils.degToRad(win ? (u.open_deg ?? 75) : (u.close_deg ?? 90));
      e.t = e.target = e.from = win ? 0 : 1;
      e.apply = (t) => {
        const q = new THREE.Quaternion().setFromAxisAngle(Y, rad * (win ? t : 1 - t));
        node.quaternion.copy(q).multiply(q0);
        node.position.copy(p0).sub(P).applyQuaternion(q).add(P);
      };
    } else if (kind === 'drawer') {
      const dir = new THREE.Vector3(0, 0, 1).applyQuaternion(q0), slide = u.slide ?? 0.3; // Blender local -Y is glTF +Z
      e.t = e.target = e.from = 0;
      e.apply = (t) => node.position.copy(p0).addScaledVector(dir, slide * t);
    } else if (kind === 'window') {
      // Sliding sash: slide metres along the IFC (slide_x, slide_y) direction, exported closed (t = 0).
      const dir = new THREE.Vector3(u.slide_x ?? 0, 0, -(u.slide_y ?? 0)).normalize(), slide = u.slide ?? 0.3;
      e.t = e.target = e.from = 0;
      e.apply = (t) => node.position.copy(p0).addScaledVector(dir, slide * t);
    } else {
      // hinge at the node origin; "L" hinges on the left seen from the front and swings outward (+Z), so it turns by -swing
      const swing = THREE.MathUtils.degToRad(u.swing ?? (kind === 'fridge_door' ? 115 : 100)), sign = (u.hinge ?? 'L') === 'L' ? -1 : 1;
      e.t = e.target = e.from = 0;
      e.apply = (t) => node.quaternion.copy(q0).multiply(new THREE.Quaternion().setFromAxisAngle(Y, sign * swing * t));
    }
    e.apply(e.t);
    node.updateMatrixWorld(true);
    if (world && u.collider === 'box') adoptCollider(e);
    return e;
  }

  // ---------- collider: the static cuboid built by physics.js is replaced by one that follows the leaf ----------
  function localBox(node) {
    const inv = new THREE.Matrix4().copy(node.matrixWorld).invert(), rel = new THREE.Matrix4(), b = new THREE.Box3();
    node.traverse((m) => {
      if (!m.isMesh) return;
      rel.multiplyMatrices(inv, m.matrixWorld);
      if (!m.geometry.boundingBox) m.geometry.computeBoundingBox();
      b.union(m.geometry.boundingBox.clone().applyMatrix4(rel));
    });
    return b;
  }
  function colliderPose(e, t) {
    e.apply(t);
    e.node.updateMatrixWorld(true);
    const p = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
    e.node.matrixWorld.decompose(p, q, s);
    const c = e.local.getCenter(new THREE.Vector3()).multiply(s).applyQuaternion(q).add(p);
    const half = e.local.getSize(new THREE.Vector3()).multiply(s).multiplyScalar(0.5);
    e.apply(e.t);
    e.node.updateMatrixWorld(true);
    return { c, q, half: new THREE.Vector3(Math.abs(half.x), Math.abs(half.y), Math.abs(half.z)) };
  }
  function adoptCollider(e) {
    e.local = localBox(e.node);
    if (e.local.isEmpty()) return;
    const pose = colliderPose(e, e.t);
    world.forEachCollider((c) => {
      if (e.stale || c.parent() || c.shapeType() !== RAPIER.ShapeType.Cuboid) return;
      const tr = c.translation(), h = c.halfExtents();
      if (Math.abs(tr.x - pose.c.x) + Math.abs(tr.y - pose.c.y) + Math.abs(tr.z - pose.c.z) + Math.abs(h.x - pose.half.x) + Math.abs(h.y - pose.half.y) + Math.abs(h.z - pose.half.z) < 0.01) e.stale = c;
    });
    if (e.stale) world.removeCollider(e.stale, true);
    e.collider = makeCollider(pose);
  }
  function makeCollider(pose) {
    return world.createCollider(RAPIER.ColliderDesc.cuboid(pose.half.x, pose.half.y, pose.half.z).setTranslation(pose.c.x, pose.c.y, pose.c.z).setRotation({ x: pose.q.x, y: pose.q.y, z: pose.q.z, w: pose.q.w }));
  }
  function syncCollider(e) {
    if (!e.collider) return;
    const pose = colliderPose(e, e.target);
    world.removeCollider(e.collider, true); // rebuilt rather than moved: a moved parentless collider is not seen by queries until the next broad phase refresh
    e.collider = makeCollider(pose);
  }
  /** True when the player stands where the leaf would be at state t (so it must not move there). A window never traps:
   * its sash swings above the sill and a person opening it stands in front, where an outward sash passes. */
  function wouldTrap(e, t) {
    const f = window.house?.player?.feet;
    if (e.kind === 'window' || !e.collider || !f) return false;
    const pose = colliderPose(e, t);
    const inv = new THREE.Matrix4().compose(pose.c, pose.q, new THREE.Vector3(1, 1, 1)).invert();
    const p = new THREE.Vector3(f[0], f[1], f[2]).applyMatrix4(inv);
    return Math.abs(p.x) < pose.half.x + PLAYER_R && Math.abs(p.z) < pose.half.z + PLAYER_R && p.y > -pose.half.y - PLAYER_H && p.y < pose.half.y;
  }

  // ---------- picking ----------
  const raycaster = new THREE.Raycaster();
  const fwd = new THREE.Vector3();
  const free = (o, d, dist) => {
    if (!world) return true;
    const hit = world.castRay(new RAPIER.Ray(o, d), dist, true, undefined, undefined, undefined, undefined, (c) => c.parent() === null);
    return !hit || hit.timeOfImpact > dist - 0.08;
  };
  /** The part under the aim ray (origin, direction), else the nearest one the player is plausibly pointing at. */
  function pick(origin, dir) {
    raycaster.set(origin, dir);
    raycaster.far = REACH;
    for (const h of raycaster.intersectObjects(meshes, false)) {
      if (free(origin, dir, h.distance)) return byMesh.get(h.object.uuid);
    }
    let best = null, bestCos = ASSIST_COS;
    const c = new THREE.Vector3(), to = new THREE.Vector3(), box = new THREE.Box3();
    for (const e of entries) {
      box.setFromObject(e.node).getCenter(c);
      to.copy(c).sub(origin);
      const dist = to.length();
      if (dist > ASSIST_REACH || dist < 0.05) continue;
      const cos = to.divideScalar(dist).dot(dir);
      if (cos > bestCos && free(origin, to, dist)) { best = e; bestCos = cos; }
    }
    return best;
  }
  function aim(ndc) {
    camera.updateMatrixWorld(true);
    const origin = camera.getWorldPosition(new THREE.Vector3());
    const dir = ndc ? new THREE.Vector3(ndc.x, ndc.y, 0.5).unproject(camera).sub(origin).normalize() : camera.getWorldDirection(fwd).clone();
    return pick(origin, dir);
  }

  // ---------- animation ----------
  const clock = () => performance.now() / 1000;
  function toggle(e) {
    if (!e) return false;
    const next = e.target ? 0 : 1;
    if (wouldTrap(e, next)) { flash('You are in the way'); return false; }
    e.from = e.t;
    e.target = next;
    e.started = clock();
    return true;
  }
  function tick() {
    requestAnimationFrame(tick);
    const now = clock();
    for (const e of entries) {
      if (e.t === e.target) continue;
      const k = Math.min(1, (now - e.started) / SECONDS[e.kind]);
      e.t = e.from + (e.target - e.from) * ease(k);
      if (k === 1) e.t = e.target;
      e.apply(e.t);
      if (e.t === e.target) {
        e.node.updateMatrixWorld(true);
        if (e.collider && wouldTrap(e, e.target)) { e.target = e.from; e.from = e.t; e.started = now; flash('Something is in the way'); continue; }
        syncCollider(e);
      }
    }
  }
  requestAnimationFrame(tick);

  // ---------- hint ----------
  const touch = matchMedia('(pointer: coarse)').matches;
  const hint = document.createElement('div');
  Object.assign(hint.style, {
    position: 'fixed', left: '50%', bottom: '18%', transform: 'translateX(-50%)', zIndex: 11, display: 'none', pointerEvents: 'none',
    font: '14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', color: '#e8edf3', padding: '6px 12px',
    background: 'rgba(16,20,26,.78)', border: '1px solid rgba(255,255,255,.14)', borderRadius: '10px',
  });
  hint.id = 'interactHint';
  document.body.appendChild(hint);
  let flashUntil = 0, hovered = null, pointer = null;
  function flash(text) { hint.textContent = text; hint.style.display = 'block'; flashUntil = clock() + 1.2; }
  const walking = () => window.house?.state === 'walk';
  setInterval(() => {
    hovered = walking() ? aim(!touch && pointer ? pointer : null) : null;
    if (clock() < flashUntil) return;
    if (!hovered) { hint.style.display = 'none'; return; }
    const verb = hovered.target ? 'Close' : 'Open';
    hint.textContent = `${touch ? '' : '[E] '}${verb} ${NOUN[hovered.kind]}`;
    hint.style.display = 'block';
  }, 120);

  // ---------- input ----------
  function interact(ndc = null) {
    return walking() ? toggle(ndc ? aim(ndc) : hovered ?? aim(null)) : false;
  }
  addEventListener('keydown', (ev) => { if (ev.code === 'KeyE' && !ev.repeat) interact(); });
  const dom = renderer.domElement;
  dom.addEventListener('pointermove', (ev) => {
    if (ev.pointerType === 'mouse' && document.pointerLockElement !== dom) pointer = { x: (ev.clientX / innerWidth) * 2 - 1, y: -(ev.clientY / innerHeight) * 2 + 1 };
    else pointer = null;
  });
  dom.addEventListener('pointerleave', () => { pointer = null; });
  dom.addEventListener('click', (ev) => { // mouse: centre ray when the pointer is locked, else the pointer
    if (ev.pointerType === 'touch' || (ev.detail === 0 && !ev.isTrusted)) return;
    interact(document.pointerLockElement === dom ? null : { x: (ev.clientX / innerWidth) * 2 - 1, y: -(ev.clientY / innerHeight) * 2 + 1 });
  });
  let down = null;
  dom.addEventListener('pointerdown', (ev) => { if (ev.pointerType === 'touch') down = { x: ev.clientX, y: ev.clientY, t: clock() }; });
  dom.addEventListener('pointerup', (ev) => { // a tap (short, still) on a part; drags belong to look and joystick
    if (ev.pointerType !== 'touch' || !down) return;
    const tap = Math.hypot(ev.clientX - down.x, ev.clientY - down.y) < 10 && clock() - down.t < 0.35;
    down = null;
    if (tap) interact({ x: (ev.clientX / innerWidth) * 2 - 1, y: -(ev.clientY / innerHeight) * 2 + 1 });
  });

  // ---------- hook for the touch button and the tests ----------
  const worldBox = (e) => new THREE.Box3().setFromObject(e.node);
  const find = (name) => entries.find((e) => e.name === name);
  window.houseInteract = {
    interact: () => interact(),
    get target() { return hovered?.name ?? null; },
    /** True when E would refuse this part: its next pose would stand where the player is. The tests use it to reject
     *  a hint-named spot the game itself would not act on (a drawer whose slide reaches the player's feet). */
    blocked: (name) => { const e = find(name); return !!(e && wouldTrap(e, e.target ? 0 : 1)); },
    get settled() { return entries.every((e) => e.t === e.target); },
    list: () => entries.map((e) => ({ name: e.name, kind: e.kind, group: e.group, open: e.target === 1, t: e.t, hasCollider: !!e.collider, slide: e.node.userData.slide ?? null, swing: e.node.userData.swing ?? null, hinge: e.node.userData.hinge ?? null, closeDeg: e.node.userData.close_deg ?? null, slideX: e.node.userData.slide_x ?? null, slideY: e.node.userData.slide_y ?? null, openDeg: e.node.userData.open_deg ?? null, opening: e.node.userData.opening ?? null, level: e.node.userData.level ?? null })),
    /** World-space centre of the part's bounds and its yaw about the vertical axis (radians). */
    pose: (name) => {
      const e = find(name);
      e.node.updateMatrixWorld(true);
      const q = e.node.getWorldQuaternion(new THREE.Quaternion());
      return { centre: worldBox(e).getCenter(new THREE.Vector3()).toArray(), size: worldBox(e).getSize(new THREE.Vector3()).toArray(), yaw: new THREE.Euler().setFromQuaternion(q, 'YXZ').y, t: e.t, target: e.target };
    },
    /** First mesh hit by a world ray, with the openable it belongs to (null = none): proves interiors are modelled. */
    shoot: (o, d, maxDist = 3) => {
      raycaster.set(new THREE.Vector3(...o), new THREE.Vector3(...d).normalize());
      raycaster.far = maxDist;
      const h = raycaster.intersectObjects(scene.children, true).find((x) => x.object.isMesh && x.object.visible);
      return h ? { dist: h.distance, name: h.object.name, part: byMesh.get(h.object.uuid)?.name ?? null } : null;
    },
    /** Frame of the part in world space: node origin, outward front normal (glTF +Z of the node), bounds of the part and of its parent. */
    frame: (name) => {
      const e = find(name);
      e.node.updateMatrixWorld(true);
      const q = e.node.getWorldQuaternion(new THREE.Quaternion());
      const own = worldBox(e), par = new THREE.Box3().setFromObject(e.node.parent);
      return { origin: e.node.getWorldPosition(new THREE.Vector3()).toArray(), front: new THREE.Vector3(0, 0, 1).applyQuaternion(q).toArray(), min: own.min.toArray(), max: own.max.toArray(), parentMin: par.min.toArray(), parentMax: par.max.toArray() };
    },
    /** Pixel position of the centre of the part's bounds (null when behind the camera). */
    screenOf: (name) => {
      camera.updateMatrixWorld(true);
      const v = worldBox(find(name)).getCenter(new THREE.Vector3()).project(camera);
      return v.z > 1 ? null : [(v.x + 1) / 2 * innerWidth, (1 - v.y) / 2 * innerHeight];
    },
    colliders: () => entries.filter((e) => e.collider).map((e) => ({ name: e.name, t: e.collider.translation(), q: e.collider.rotation(), half: e.collider.halfExtents(), stale: !!e.stale })),
    meshCount: (name) => { let n = 0; find(name).node.traverse((c) => { if (c.isMesh) n++; }); return n; },
  };
  return window.houseInteract;
}
