// Rapier world built from the GLB `collider` extras (contract: docs/glb_contract.md) and the player capsule.
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';

export const RADIUS = 0.25, HEIGHT = 1.7, EYE = 1.6;
const HALF = (HEIGHT - 2 * RADIUS) / 2;
export const STEP = 1 / 60;
// R9: the eye follows the feet with this rate (1/s), so a stair riser climbed by the capsule's autostep reads as a smooth glide
export const EYE_RATE = 7;
const GRAVITY = 9.81, ACCEL = 14, BRAKE = 22; // m/s^2: reaches walking speed in ~0.14 s, stops in ~0.09 s
export const WALK = 2.0, RUN = 3.5; // m/s
// R10-7 fixed ladders (GLB role ladder, extras ladder_top and ladder_front): moving into the ladder climbs, moving away
// descends, at the top the climber steps onto the deck, and walking off the deck over the ladder climbs down. 0.30 m from the face.
const CLIMB = 0.8, CLIMB_OFF = RADIUS + 0.05;

export async function createPhysics(root) {
  await RAPIER.init();
  const world = new RAPIER.World({ x: 0, y: 0, z: 0 });
  world.timestep = STEP;
  root.updateMatrixWorld(true);
  const stats = { mesh: 0, box: 0, none: 0 };
  const names = new Map(); // collider handle -> node name, for diagnostics
  // Room door leaves are exported swung open (docs/glb_contract.md), so an open leaf can lie across a corridor narrower than
  // leaf + capsule. A leaf standing in its open pose is not solid for the player; the moment it moves (closing) it is.
  const leaves = []; // { c: open-pose centre (Vector3), h: half extents }
  const ladders = []; // { n: outward normal (x, z), face: centre of the climbing face, t: across unit, half: half width, bottom, top }
  const inv = new THREE.Matrix4(), rel = new THREE.Matrix4(), v = new THREE.Vector3();

  root.traverse((o) => {
    if (o.userData.role === 'ladder' && o.userData.ladder_top !== undefined) {
      const b = new THREE.Box3().setFromObject(o), c = b.getCenter(new THREE.Vector3());
      const [fx, fy] = String(o.userData.ladder_front ?? '1,0').split(',').map(Number);
      const n = new THREE.Vector2(fx, -fy).normalize(), t = new THREE.Vector2(-n.y, n.x); // IFC (x, y) -> glTF (x, -z)
      const halfT = Math.abs(n.x) * (b.max.x - b.min.x) / 2 + Math.abs(n.y) * (b.max.z - b.min.z) / 2;
      const half = Math.abs(t.x) * (b.max.x - b.min.x) / 2 + Math.abs(t.y) * (b.max.z - b.min.z) / 2;
      ladders.push({ n, t, half, face: new THREE.Vector2(c.x + n.x * halfT, c.z + n.y * halfT), bottom: b.min.y, top: Number(o.userData.ladder_top) });
    }
    const kind = o.userData.collider;
    if (!kind) return;
    stats[kind]++;
    if (kind === 'none') return;
    if (kind === 'mesh') {
      o.traverse((m) => {
        if (!m.isMesh) return;
        const pos = m.geometry.attributes.position, idx = m.geometry.index;
        const verts = new Float32Array(pos.count * 3);
        for (let i = 0; i < pos.count; i++) {
          v.fromBufferAttribute(pos, i).applyMatrix4(m.matrixWorld);
          verts.set([v.x, v.y, v.z], i * 3);
        }
        const ids = idx ? Uint32Array.from(idx.array) : Uint32Array.from({ length: pos.count }, (_, i) => i);
        names.set(world.createCollider(RAPIER.ColliderDesc.trimesh(verts, ids)).handle, o.name);
      });
    } else {
      // box: cuboid of the node's bounds in its own frame, so rotated furniture keeps its true footprint
      inv.copy(o.matrixWorld).invert();
      const b = new THREE.Box3();
      o.traverse((m) => {
        if (!m.isMesh) return;
        rel.multiplyMatrices(inv, m.matrixWorld);
        if (!m.geometry.boundingBox) m.geometry.computeBoundingBox();
        b.union(m.geometry.boundingBox.clone().applyMatrix4(rel));
      });
      if (b.isEmpty()) return;
      const c = b.getCenter(new THREE.Vector3()), s = b.getSize(new THREE.Vector3());
      const p = new THREE.Vector3(), q = new THREE.Quaternion(), sc = new THREE.Vector3();
      o.matrixWorld.decompose(p, q, sc);
      const wc = c.clone().multiply(sc).applyQuaternion(q).add(p);
      const hx = Math.max(0.005, Math.abs(s.x * sc.x) / 2), hy = Math.max(0.005, Math.abs(s.y * sc.y) / 2), hz = Math.max(0.005, Math.abs(s.z * sc.z) / 2);
      const col = world.createCollider(RAPIER.ColliderDesc.cuboid(hx, hy, hz).setTranslation(wc.x, wc.y, wc.z).setRotation({ x: q.x, y: q.y, z: q.z, w: q.w }));
      names.set(col.handle, o.name);
      if (o.userData.openable === 'door') leaves.push({ c: wc.clone(), h: [hx, hy, hz] });
    }
  });

  const body = world.createRigidBody(RAPIER.RigidBodyDesc.kinematicPositionBased().setTranslation(0, 100, 0));
  const collider = world.createCollider(RAPIER.ColliderDesc.capsule(HALF, RADIUS), body);
  const kcc = world.createCharacterController(0.01);
  kcc.enableAutostep(0.22, 0.12, false); // measured: a 0.18 m step is climbed, a 0.20 m one is not (Rapier capsule autostep), whatever maxHeight is
  kcc.enableSnapToGround(0.3);
  kcc.setMaxSlopeClimbAngle(Math.PI * 40 / 180);
  kcc.setMinSlopeSlideAngle(Math.PI * 50 / 180);
  kcc.setSlideEnabled(true);
  kcc.setUp({ x: 0, y: 1, z: 0 });
  // Measured: with Rapier's default nudge (1e-4) the capsule snags on the seams between ground triangles about one step in
  // fifteen and moves ~0 that step, so walking stutters at any frame rate. 1e-3 pushes it off the seam: even steps.
  kcc.setNormalNudgeFactor(0.001);
  world.step();

  // Colliders that count for the player: everything except a door leaf still in its open pose.
  // A leaf is found by pose, not by handle, because interact.js swaps a leaf's collider for one that follows the animation.
  // Disabled colliders leave the queries, so an open leaf is neither collided with nor hit by a probe ray.
  let leafSync = 0;
  function syncLeaves() {
    let changed = false;
    world.forEachCollider((c) => {
      if (c.parent() || c.shapeType() !== RAPIER.ShapeType.Cuboid) return;
      const t = c.translation(), h = c.halfExtents();
      // Room-door leaves share a handful of size classes, so decide against every same-size leaf, never the first one:
      // a different leaf of the same size must not re-enable an open one.
      let same = false, open = false;
      for (const l of leaves) {
        if (Math.abs(h.x - l.h[0]) + Math.abs(h.y - l.h[1]) + Math.abs(h.z - l.h[2]) > 0.03) continue;
        same = true;
        if (Math.abs(t.x - l.c.x) + Math.abs(t.y - l.c.y) + Math.abs(t.z - l.c.z) < 0.03) open = true;
      }
      if (!same) return;
      if (open !== !c.isEnabled()) { c.setEnabled(!open); changed = true; }
    });
    if (changed) { player.leafVersion++; world.step(); } // queries see the change only after a step
  }
  const feet = new THREE.Vector3(0, 100, 0);
  const player = {
    feet, yaw: 0, vy: 0, grounded: false,
    // Horizontal distance from (x, z) to the nearest door leaf standing open (its collider is off, so rays do not see it but the eye does).
    leafDist(x, z) { let d = 9; for (const l of leaves) d = Math.min(d, Math.hypot(Math.max(Math.abs(x - l.c.x) - l.h[0], 0), Math.max(Math.abs(z - l.c.z) - l.h[2], 0))); return d; },
    leafVersion: 0, // bumps whenever a door leaf changes pose; the route planner drops its cache when it moves
    safe: [], lastSafeAt: -1, vx: 0, vz: 0, blockedBy: [], // blockedBy: colliders the last step touched (diagnosis)
    setFeet(x, y, z) {
      feet.set(x, y, z);
      body.setTranslation({ x, y: y + HEIGHT / 2, z }, true);
      collider.setTranslation({ x, y: y + HEIGHT / 2, z });
      player.vy = 0; player.vx = 0; player.vz = 0; player.safe.length = 0; player.climbing = null;
      world.step();
    },
    climbing: null, // the ladder being climbed, or null
    // One fixed step. input: forward/right in [-1,1], run flag. Returns metres moved horizontally.
    step(input) {
      const r = input.run === true ? 1 : Math.min(1, Math.max(0, +input.run || 0)); // true/false from keys, 0..1 from the stick
      const sp = WALK + (RUN - WALK) * r;
      const fx = -Math.sin(player.yaw), fz = -Math.cos(player.yaw);
      const rx = Math.cos(player.yaw), rz = -Math.sin(player.yaw);
      let dx = fx * input.forward + rx * input.right, dz = fz * input.forward + rz * input.right;
      const n = Math.hypot(dx, dz);
      if (n > 1) { dx /= n; dz /= n; }
      if (ladders.length && player.ladderStep(dx, dz)) return 0;
      // velocity eases toward the wanted one: no snap to full speed, no ice-rink slide
      const tx = dx * sp, tz = dz * sp, moving = n > 0.001;
      const rate = (moving ? ACCEL : BRAKE) * STEP * (player.grounded ? 1 : 0.35);
      const ex = tx - player.vx, ez = tz - player.vz, el = Math.hypot(ex, ez);
      if (el <= rate) { player.vx = tx; player.vz = tz; } else { player.vx += ex / el * rate; player.vz += ez / el * rate; }
      player.vy = player.grounded ? -1 : player.vy - GRAVITY * STEP;
      const want = { x: player.vx * STEP, y: player.vy * STEP, z: player.vz * STEP };
      if (leaves.length && leafSync++ % 15 === 0) syncLeaves();
      kcc.computeColliderMovement(collider, want);
      let m = kcc.computedMovement();
      // Seam snag: on flat ground the capsule sometimes stops on the edge between two floor triangles and moves ~0 for a step,
      // which reads as a stutter at any frame rate. When every contact is floor (normal up) and the move came out under half
      // the wanted one, retry once from 1 cm higher; snap-to-ground puts the capsule back on the floor.
      const wantH = Math.hypot(want.x, want.z);
      if (player.grounded && wantH > 1e-4 && Math.hypot(m.x, m.z) < 0.5 * wantH) {
        let floorOnly = kcc.numComputedCollisions() > 0;
        for (let i = 0, k = kcc.numComputedCollisions(); i < k && floorOnly; i++) { const c = kcc.computedCollision(i); if (!c || c.normal1.y < 0.7) floorOnly = false; }
        if (floorOnly) { kcc.computeColliderMovement(collider, { x: want.x, y: 0.01, z: want.z }); m = kcc.computedMovement(); }
      }
      player.blockedBy.length = 0;
      for (let i = 0, n = kcc.numComputedCollisions(); i < n; i++) {
        const c = kcc.computedCollision(i)?.collider;
        if (c) { const t = c.translation(); player.blockedBy.push(`${names.get(c.handle) ?? ''}@${t.x.toFixed(2)},${t.y.toFixed(2)},${t.z.toFixed(2)}`); }
      }
      feet.x += m.x; feet.y += m.y; feet.z += m.z;
      player.grounded = kcc.computedGrounded();
      if (player.grounded) player.vy = 0;
      body.setNextKinematicTranslation({ x: feet.x, y: feet.y + HEIGHT / 2, z: feet.z });
      world.step();
      return Math.hypot(m.x, m.z);
    },
    // Ladder mode (R10-7). Returns true when this step was a ladder step (the walking controller is skipped).
    ladderStep(dx, dz) {
      let L = player.climbing;
      const rel = (l) => { const px = feet.x - l.face.x, pz = feet.z - l.face.y; return { off: px * l.n.x + pz * l.n.y, lat: px * l.t.x + pz * l.t.y }; };
      if (!L) {
        for (const l of ladders) {
          const { off, lat } = rel(l), into = -(dx * l.n.x + dz * l.n.y);
          if (Math.abs(lat) > l.half + 0.10 || feet.y < l.bottom - 0.3 || feet.y > l.top + 0.3) continue;
          const below = feet.y < l.top - 0.3;
          if ((below && off > 0 && off < 0.65 && into > 0.5) || (!below && off > -0.45 && off < 0.65 && into < -0.3)) { L = l; break; }
        }
        if (!L) return false;
        player.climbing = L;
      }
      const { off, lat } = rel(L), into = -(dx * L.n.x + dz * L.n.y);
      let mx = 0, my = 0, mz = 0;
      const toward = (dOff, speed) => { const k = THREE.MathUtils.clamp(dOff, -speed * STEP, speed * STEP); mx += L.n.x * k; mz += L.n.y * k; };
      if (feet.y < L.top - 0.005) {
        if (into < -0.1 && feet.y <= L.bottom + 0.02) { player.climbing = null; return false; } // off the bottom rung: walk away
        toward(CLIMB_OFF - off, 1.0);
        const k = THREE.MathUtils.clamp(-lat, -STEP, STEP); mx += L.t.x * k; mz += L.t.y * k;
        my = THREE.MathUtils.clamp(into, -1, 1) * CLIMB * STEP;
        my = Math.min(my, L.top + 0.02 - feet.y);
        if (feet.y + my < L.bottom) my = L.bottom - feet.y;
      } else if (into > 0.3) { // at the deck: step over the edge onto it
        toward(-RADIUS - 0.10 - off, WALK * into);
        my = L.top + 0.02 - feet.y;
        if (off < -RADIUS - 0.05) { player.climbing = null; }
      } else if (into < -0.3) { // from the deck: out over the edge to the ladder line, then down
        if (off < CLIMB_OFF - 0.01) toward(CLIMB_OFF - off, WALK * -into);
        else my = into * CLIMB * STEP;
      }
      feet.x += mx; feet.y += my; feet.z += mz;
      player.vx = 0; player.vz = 0; player.vy = 0; player.grounded = false;
      body.setNextKinematicTranslation({ x: feet.x, y: feet.y + HEIGHT / 2, z: feet.z });
      collider.setTranslation({ x: feet.x, y: feet.y + HEIGHT / 2, z: feet.z });
      world.step();
      return true;
    },
    // Remember a grounded spot with free radius so `unstick` and the fall guard have somewhere to return to.
    remember(t) {
      if (!player.grounded || t - player.lastSafeAt < 0.5) return;
      const shape = new RAPIER.Ball(RADIUS + 0.35);
      const hit = world.intersectionWithShape({ x: feet.x, y: feet.y + 1.0, z: feet.z }, { x: 0, y: 0, z: 0, w: 1 }, shape, undefined, undefined, collider);
      if (hit) return;
      player.lastSafeAt = t;
      player.safe.push(feet.clone());
      if (player.safe.length > 12) player.safe.shift();
    },
    // Names of the colliders within r metres of the capsule (diagnostics for the test).
    syncLeaves,
    near(r = 0.05) {
      const out = [];
      const c = { x: feet.x, y: feet.y + HEIGHT / 2, z: feet.z };
      world.intersectionsWithShape(c, { x: 0, y: 0, z: 0, w: 1 }, new RAPIER.Capsule(HALF, RADIUS + r), (co) => { if (co.handle !== collider.handle) { const t = co.translation(); out.push(`${names.get(co.handle) ?? ''}@${t.x.toFixed(2)},${t.y.toFixed(2)},${t.z.toFixed(2)}`); } return true; });
      return out;
    },
    // Ground height at (x, z) when a capsule fattened by margin fits there 0.22 m above it (the autostep height), else null.
    clearAt(x, z, fromY, margin = 0) {
      const g = player.groundAt(x, z, fromY);
      if (!g) return null;
      const c = { x, y: g.y + 0.22 + HEIGHT / 2, z };
      const hit = world.intersectionWithShape(c, { x: 0, y: 0, z: 0, w: 1 }, new RAPIER.Capsule(HALF, RADIUS + margin), undefined, undefined, collider);
      return hit ? null : g.y;
    },
    // Every standable surface at (x, z), top to bottom: floors, stair treads, landings where the capsule (fattened by margin) fits
    // with its feet 0.2 m above the surface (so a step or threshold up to 0.19 m never blocks). Used by the route planner of the tests.
    surfaces(x, z, margin = 0, top = 9) {
      const out = [];
      let y = top, above = Infinity; // above: height of the surface hit just before, so a floor shell right over another hit is no floor
      for (let k = 0; k < 120; k++) {
        const hit = world.castRay(new RAPIER.Ray({ x, y, z }, { x: 0, y: -1, z: 0 }), y + 2, true, undefined, undefined, collider);
        if (!hit) break;
        const gy = y - hit.timeOfImpact;
        if (hit.timeOfImpact > 0.001 && above - gy >= 0.5) { // under a 0.2 m slab the ground below is not standable (the ray passes the thin shell)
          const c = { x, y: gy + 0.2 + margin + HEIGHT / 2, z }; // feet 0.2 m up: a kerb or threshold the controller can climb never blocks, even with the margin-fattened capsule
          if (!world.intersectionWithShape(c, { x: 0, y: 0, z: 0, w: 1 }, new RAPIER.Capsule(HALF, RADIUS + margin), undefined, undefined, collider)) out.push(gy);
        }
        above = gy;
        y = gy - (hit.timeOfImpact > 0.001 ? 0.03 : 0.1); // a ray that starts inside a box (furniture stacked over the floor) must not use up the loop
      }
      return out;
    },
    groundAt(x, z, fromY) {
      const hit = world.castRay(new RAPIER.Ray({ x, y: fromY, z }, { x: 0, y: -1, z: 0 }), 10, true, undefined, undefined, collider);
      return hit ? { y: fromY - hit.timeOfImpact, name: names.get(hit.collider.handle) } : null;
    },
    floorY() {
      const hit = world.castRay(new RAPIER.Ray({ x: feet.x, y: feet.y + 0.5, z: feet.z }, { x: 0, y: -1, z: 0 }), 6, true, undefined, undefined, collider);
      return hit ? feet.y + 0.5 - hit.timeOfImpact : -99;
    },
    // Distance to the first solid thing along a horizontal direction at waist height, with its plane.
    ray(yaw, maxDist = 6, h = 0.9) {
      const dir = { x: -Math.sin(yaw), y: 0, z: -Math.cos(yaw) };
      const r = new RAPIER.Ray({ x: feet.x, y: feet.y + h, z: feet.z }, dir);
      const hit = world.castRayAndGetNormal(r, maxDist, true, undefined, undefined, collider);
      if (!hit) return null;
      const pt = { x: feet.x + dir.x * hit.timeOfImpact, y: feet.y + h, z: feet.z + dir.z * hit.timeOfImpact };
      return { dist: hit.timeOfImpact, normal: hit.normal, point: pt, name: names.get(hit.collider.handle) };
    },
  };
  return { world, player, stats, ladders: ladders.length };
}
