// Recorrido 3D de la casa. Modes only change through send() (machine.js, chart 1 of docs/statecharts.md).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { HDRLoader } from 'three/addons/loaders/HDRLoader.js';
import { createMachine, MACHINE } from './machine.js';
import { createPhysics, STEP, EYE, EYE_RATE } from './physics.js';
import { createNav } from './nav.js';
import { initTouch } from './touch.js';
import { createMinimap, FLOORS, floorOf } from './minimap.js';
import { EffectComposer, RenderPass, EffectPass, SMAAEffect, ToneMappingEffect, ToneMappingMode } from 'postprocessing';
import { N8AOPostPass } from 'n8ao';

const $ = (id) => document.getElementById(id);
const LEVELS = ['Planta baja', 'Planta alta', 'Terraza'];
// feet heights: the terrace floor is at 6.40 m, the laundry roof deck at 9.02 m (build/casa.ifc)
const TERRACE_Y = 6.0, DECK_Y = 8.5;
const LOOK = 0.0022, LOOK_TOUCH = 0.0042, FOV_MIN = 35, FOV_MAX = 75;
const RENDER = new URLSearchParams(location.search).get('render') !== '0'; // ?render=0: input and physics only, so software GL keeps 60 Hz
const AO = new URLSearchParams(location.search).get('ao') !== '0'; // the mechanics test runs without the post stack (software GL)
const OPEN_AIR = /patio|sendero|terraza|calle|cochera/i;

// ---------- renderer, light ----------
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = AO ? THREE.NoToneMapping : THREE.ACESFilmicToneMapping; // with AO the ToneMappingEffect does it
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x9fc4e8);
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.05, 600);
const hemi = new THREE.HemisphereLight(0xfff8f0, 0x8a8478, 0.2);
// Four warm bulbs follow the player to the nearest room centres: a fixed light count keeps shaders from recompiling.
const bulbs = Array.from({ length: 4 }, () => new THREE.PointLight(0xffe9d0, 0, 8, 2));
scene.add(...bulbs);
const sun = new THREE.DirectionalLight(0xfff0d8, 3.0);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.03;
scene.add(hemi, sun, sun.target);
renderer.toneMappingExposure = 0.7;
let composer = null;

new HDRLoader().load('../assets/hdri/kloofendal_48d_partly_cloudy_puresky_2k.hdr', (tex) => {
  tex.mapping = THREE.EquirectangularReflectionMapping;
  scene.environment = tex;
  scene.background = tex;
  scene.environmentIntensity = 0.9;
});

// ---------- world state ----------
const world = { rooms: [], spaces: [], bulbSpots: [], roleBoxes: [], walls: [], doors: [], spawns: new Map(), center: new THREE.Vector3(), radius: 12, phys: null, nav: null };
const view = { pitch: 0, fov: 60, locked: false };
const keys = new Set();
let orbit = null, hasLocked = false, lastRoom = 'Calle', minimap = null, planLevel = 0, planHover = null;
// Touch mode: a coarse primary pointer, or the first finger that touches the screen (hybrid laptops start in mouse mode).
let touchMode = matchMedia('(pointer: coarse)').matches || ('ontouchstart' in window && !matchMedia('(pointer: fine)').matches);
const setTouchMode = (on) => { touchMode = on; document.body.classList.toggle('touch', on); touch.setEnabled(machine.state === 'walk', on); };
const touch = initTouch({
  surface: renderer.domElement,
  onLook: (dx, dy) => { if (machine.state === 'walk' && world.phys) { world.phys.player.yaw -= dx * LOOK_TOUCH; view.pitch = THREE.MathUtils.clamp(view.pitch - dy * LOOK_TOUCH, -1.45, 1.45); } },
  onInteract: () => window.casaInteract?.interact(),
});
document.body.classList.toggle('touch', touchMode);
addEventListener('pointerdown', (e) => { if (e.pointerType === 'touch' && !touchMode) setTouchMode(true); }, { capture: true });

const machine = createMachine(onChange);
const send = (e, a) => machine.send(e, a);

// ---------- rooms ----------
function roomAt(p) {
  let best = null, bestVol = Infinity;
  for (const s of world.spaces) {
    const b = s.box;
    if (p.x < b.min.x || p.x > b.max.x || p.z < b.min.z || p.z > b.max.z) continue;
    if (p.y + 0.9 < b.min.y || p.y + 0.9 > Math.max(b.max.y, b.min.y + 2.5)) continue; // flat outdoor spaces count as 2.5 m tall
    const vol = (b.max.x - b.min.x) * (b.max.z - b.min.z) * (b.max.y - b.min.y);
    if (vol < bestVol) { bestVol = vol; best = s; }
  }
  if (best) return best.name;
  // no IfcSpace covers every metre of the terrace or the laundry roof deck: name them by height, never as the street
  if (p.y > DECK_Y) return 'Terraza: cubierta del lavado';
  if (p.y > TERRACE_Y) return 'Terraza';
  return 'Calle';
}

function facingYaw(from, to) {
  return Math.atan2(-(to.x - from.x), -(to.z - from.z));
}

function placeAt(name, why) {
  const sp = world.spawns.get(name);
  if (!sp) return false;
  const space = world.spaces.find((s) => s.name === name);
  const target = space ? space.box.getCenter(new THREE.Vector3()) : null;
  const toIfc = (deg) => { const a = THREE.MathUtils.degToRad(deg); return Math.atan2(-Math.cos(a), Math.sin(a)); };
  const yaw = name === 'Calle' || !target || target.distanceTo(sp.pos) < 0.5 ? toIfc(sp.yawDeg) : facingYaw(sp.pos, target);
  lastRoom = name;
  world.phys.player.setFeet(sp.pos.x, sp.pos.y, sp.pos.z);
  const pl = world.phys.player;
  if (sp.look) {  // a room with a hero wall (the kitchen sink): stand where the GLB says and face it, no search, no turning away
    pl.yaw = facingYaw(sp.pos, sp.look);
    view.pitch = Math.atan2(sp.look.y - (sp.pos.y + EYE), Math.hypot(sp.look.x - sp.pos.x, sp.look.z - sp.pos.z));
    return true;
  }
  // a spawn that furniture has walled into a pocket would trap the player: land at the nearest point of the room that opens out
  if (world.nav && space && name !== 'Calle') {
    const spot = world.nav.openSpot(sp.pos.x, sp.pos.y, sp.pos.z, space);
    if (spot && Math.hypot(spot[0] - sp.pos.x, spot[1] - sp.pos.z) > 0.01) { pl.setFeet(spot[0], sp.pos.y, spot[1]); }
  }
  // land where there is room to look around: of the free points within 2 m of the spawn, the one farthest from any wall or door leaf
  if (space && name !== 'Calle') {
    const home = pl.feet.clone(), around = (a) => pl.ray(a * Math.PI / 4, 3, 1.0)?.dist ?? 3;
    const room = (x, z) => { pl.setFeet(x, home.y, z); let m = Math.min(3, pl.leafDist(x, z) + 0.3); for (let a = 0; a < 8; a++) m = Math.min(m, around(a)); return Math.min(m, 1.5); };
    let best = [home.x, home.z], bestScore = room(home.x, home.z);
    for (let dx = -2; dx <= 2; dx += 0.25) for (let dz = -2; dz <= 2; dz += 0.25) {
      const x = home.x + dx, z = home.z + dz, d = Math.hypot(dx, dz);
      if (d > 2 || x < space.box.min.x + 0.3 || x > space.box.max.x - 0.3 || z < space.box.min.z + 0.3 || z > space.box.max.z - 0.3) continue;
      const g = pl.clearAt(x, z, home.y + 0.5, 0.08);
      if (g === null || Math.abs(g - home.y) > 0.15) continue;
      const score = room(x, z) - 0.1 * d;
      if (score > bestScore + 0.02) { bestScore = score; best = [x, z]; }
    }
    const spot = world.nav ? world.nav.openSpot(best[0], home.y, best[1], space) : best;
    if (spot) pl.setFeet(spot[0], home.y, spot[1]); else pl.setFeet(home.x, home.y, home.z);
  }
  pl.yaw = yaw;
  // never arrive with the nose against a wall or wardrobe: if the view is shorter than 2 m, turn to the most open direction
  const open = (y) => pl.ray(y, 6, 1.0)?.dist ?? 6;
  if (open(yaw) < 2) {
    let best = yaw, far = open(yaw);
    for (let i = 0; i < 16; i++) { const y = yaw + i * Math.PI / 8, d = open(y); if (d > far + 0.3) { far = d; best = y; } }
    pl.yaw = best;
  }
  view.pitch = 0;
  return true;
}

// ---------- state changes ----------
function onChange(next, prev, event, arg) {
  const walking = next === 'walk';
  if (orbit) orbit.enabled = next === 'orbit';
  $('cross').style.display = walking ? 'block' : 'none';
  $('menu').style.display = next === 'paused' ? 'grid' : 'none';
  $('modebtn').textContent = next === 'orbit' ? 'Caminar (Tab)' : touchMode ? 'Salir' : 'Ver desde afuera (Tab)';
  $('modebtn').style.display = next === 'loading' || next === 'error' ? 'none' : '';
  $('planbtn').style.display = next === 'walk' || next === 'paused' ? '' : 'none';
  $('minimap').style.display = next === 'walk' ? 'block' : 'none';
  document.body.classList.toggle('walking', next === 'walk');
  // every transition drops held keys and fingers: nothing can stay pressed behind a menu or a lost pointer lock
  if (prev !== next || event === 'teleport') release();
  touch.setEnabled(next === 'walk', touchMode);
  if (next === 'paused') openPlan();
  $('loading').style.display = next === 'loading' ? '' : 'none';
  if (next === 'error') $('error').style.display = 'grid';

  if (next === 'orbit') {
    document.exitPointerLock?.();
    view.fov = 45;
    camera.fov = 45; camera.updateProjectionMatrix();
    if (prev !== 'loading') {
      camera.position.copy(world.center).add(new THREE.Vector3(10, 11, 24));
      orbit.target.copy(world.center);
      orbit.update();
    }
  }
  if (next === 'walk') {
    if (event === 'enterWalk') placeAt('Calle');
    if (event === 'teleport') placeAt(arg);
    if (event === 'unstick') {
      const p = world.phys.player, s = p.safe.length > 1 ? p.safe[p.safe.length - 2] : p.safe[p.safe.length - 1];
      if (s) { const yaw = p.yaw; p.setFeet(s.x, s.y, s.z); p.yaw = yaw; } else placeAt(lastRoom);
    }
    if (prev !== 'walk') { view.fov = 60; camera.fov = 60; camera.updateProjectionMatrix(); }
    if (event !== 'unstick' && !touchMode) renderer.domElement.requestPointerLock?.()?.catch?.(() => {});
  }
}

// ---------- input ----------
// Losing the keyboard stops the player at once: keys, touch sticks and the walking velocity (it eases out otherwise).
function release() {
  keys.clear();
  touch.reset();
  const p = world.phys?.player;
  if (p) { p.vx = 0; p.vz = 0; }
}
document.addEventListener('pointerlockchange', () => {
  const was = view.locked;
  view.locked = document.pointerLockElement === renderer.domElement;
  if (view.locked) hasLocked = true;
  keys.clear();
  if (was && !view.locked) send('pause');
});
// Anything that takes focus away from the page must release the keys, or a W held at that moment walks forever.
addEventListener('blur', release);
document.addEventListener('visibilitychange', release);
document.addEventListener('mousemove', (e) => {
  if (machine.state !== 'walk' || Math.abs(e.movementX) > 250 || Math.abs(e.movementY) > 250) return; // ignore browser spikes
  world.phys.player.yaw -= e.movementX * LOOK;
  view.pitch = THREE.MathUtils.clamp(view.pitch - e.movementY * LOOK, -1.45, 1.45);
});
renderer.domElement.addEventListener('click', () => {
  if (machine.state === 'walk' && !view.locked && !touchMode) renderer.domElement.requestPointerLock?.()?.catch?.(() => {});
});
addEventListener('wheel', (e) => {
  if (machine.state !== 'walk') return;
  view.fov = THREE.MathUtils.clamp(view.fov + e.deltaY * 0.02, FOV_MIN, FOV_MAX);
  camera.fov = view.fov; camera.updateProjectionMatrix();
}, { passive: true });
addEventListener('keydown', (e) => {
  if (e.code === 'Tab') {
    e.preventDefault();
    if (machine.state === 'orbit') send('enterWalk'); else send('exitWalk');
    return;
  }
  if (e.metaKey) keys.clear(); // macOS sends no keyup for keys released while Cmd is down
  if (e.repeat) return;
  keys.add(e.code);
  if (e.code === 'KeyM') send(machine.state === 'paused' ? 'resume' : 'pause');
  else if (e.code === 'KeyR') send('unstick');
  else if (e.code === 'KeyH') $('help').classList.toggle('mini');
});
addEventListener('keyup', (e) => { keys.delete(e.code); if (e.key === 'Shift') { keys.delete('ShiftLeft'); keys.delete('ShiftRight'); } });
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
  composer?.setSize(innerWidth, innerHeight);
});
$('modebtn').addEventListener('click', () => { $('modebtn').blur(); send(machine.state === 'orbit' ? 'enterWalk' : 'exitWalk'); });
$('planbtn').addEventListener('click', () => { $('planbtn').blur(); send(machine.state === 'paused' ? 'resume' : 'pause'); });
$('minimap').addEventListener('click', () => send('pause'));
$('resume').addEventListener('click', () => send('resume'));
$('outside').addEventListener('click', () => send('exitWalk'));
renderer.domElement.addEventListener('click', () => { if (machine.state === 'paused') send('resume'); });

// ---------- plan: floor tabs, a large drawing of the floor, a tap on a room goes there ----------
function drawPlan() {
  if (!minimap) return;
  const p = world.phys.player;
  minimap.draw($('plan'), planLevel, { feet: [p.feet.x, p.feet.y, p.feet.z], yaw: p.yaw, room: curRoom }, { labels: true, pad: 8, hover: planHover });
  for (const b of $('floors').children) b.classList.toggle('on', Number(b.dataset.level) === planLevel);
}
function openPlan() { if (!minimap) return; planLevel = Math.max(0, floorOf(world.phys.player.feet.y)); drawPlan(); }
function buildPlan() {
  minimap = createMinimap({ spaces: world.spaces, walls: world.walls, doors: world.doors, bounds: [-0.2, 12.4, -20.4, 0.6] });
  FLOORS.forEach((f, i) => {
    const b = document.createElement('button');
    b.textContent = f.name; b.dataset.level = i;
    b.addEventListener('click', () => { planLevel = i; drawPlan(); });
    $('floors').appendChild(b);
  });
  const roomUnder = (e) => { const n = minimap.roomAtPoint($('plan'), planLevel, e.clientX, e.clientY); return world.spawns.has(n) ? n : null; };
  $('plan').addEventListener('pointermove', (e) => { const n = roomUnder(e); if (n !== planHover) { planHover = n; drawPlan(); } });
  $('plan').addEventListener('click', (e) => { const n = roomUnder(e); if (n) send('teleport', n); });
}

function buildMenu() {
  for (const r of world.rooms) {
    const b = document.createElement('button');
    b.textContent = r.name;
    b.className = 'room';
    b.dataset.room = r.name;
    b.addEventListener('click', () => send('teleport', r.name));
    $('rooms' + Math.min(r.level, 2)).appendChild(b);
  }
}

// ---------- loop ----------
let acc = 0, last = performance.now(), clock = 0, stuckT = 0, anchor = new THREE.Vector3(), curRoom = 'Calle';
let frames = 0, mapT = 0, eyeY = null;
function frame(now) {
  requestAnimationFrame(frame);
  frames++;
  const dt = Math.min((now - last) / 1000, 0.25);
  last = now;
  const st = machine.state;
  if (st === 'orbit') orbit.update();
  if (st === 'walk' && world.phys) {
    const p = world.phys.player;
    const forward = THREE.MathUtils.clamp((keys.has('KeyW') || keys.has('ArrowUp') ? 1 : 0) - (keys.has('KeyS') || keys.has('ArrowDown') ? 1 : 0) + touch.axes.forward, -1, 1);
    const right = THREE.MathUtils.clamp((keys.has('KeyD') || keys.has('ArrowRight') ? 1 : 0) - (keys.has('KeyA') || keys.has('ArrowLeft') ? 1 : 0) + touch.axes.right, -1, 1);
    const run = keys.has('ShiftLeft') || keys.has('ShiftRight') || touch.axes.run;
    acc += dt;
    while (acc >= STEP) {
      acc -= STEP; clock += STEP;
      p.step({ forward, right, run });
      p.remember(clock);
      // stuck detector: input held for 2 s with less than 1 cm of movement
      if (forward || right) {
        stuckT += STEP;
        if (stuckT >= 2) {
          if (p.feet.distanceTo(anchor) < 0.01) { stuckT = 0; send('unstick'); } else { anchor.copy(p.feet); stuckT = 0; }
        }
      } else { stuckT = 0; anchor.copy(p.feet); }
      // fall guard
      if (p.feet.y < -1) send('unstick');
    }
    // The body climbs a tread in one step, the eye eases up to it: a stair reads as a ramp, not as sixteen small jolts.
    // A jump over half a metre (teleport, unstick) snaps.
    if (eyeY === null || Math.abs(p.feet.y - eyeY) > 0.5) eyeY = p.feet.y;
    else eyeY += (p.feet.y - eyeY) * Math.min(1, dt * EYE_RATE);
    camera.position.set(p.feet.x, eyeY + EYE, p.feet.z);
    camera.rotation.set(view.pitch, p.yaw, 0, 'YXZ');
    curRoom = roomAt(p.feet);
    const sp = world.rooms.find((r) => r.name === curRoom);
    $('roomName').textContent = curRoom;
    const fl = Math.max(0, floorOf(p.feet.y));
    $('levelName').textContent = LEVELS[fl];
    $('floorChip').textContent = FLOORS[fl].short;
    if (minimap && now - mapT > 100) {
      mapT = now;
      minimap.draw($('minimap'), fl, { feet: [p.feet.x, p.feet.y, p.feet.z], yaw: p.yaw, room: curRoom }, { labels: false, pad: 4 });
    }
  } else if (st === 'orbit') {
    $('roomName').textContent = 'Vista exterior';
    $('levelName').textContent = touchMode ? 'Arrastra para girar, pellizca para acercar' : 'Arrastra para girar, rueda para acercar, Tab para entrar';
    $('floorChip').textContent = '';
  }
  updateLight();
  if (!RENDER) return;
  if (composer) composer.render(dt); else renderer.render(scene, camera);
}

// Indoors the blue sky probe must not tint the walls, and the nearest rooms get a warm ceiling bulb.
function updateLight() {
  const eye = camera.position;
  const inside = machine.state === 'walk' && !OPEN_AIR.test(curRoom);
  scene.environmentIntensity = inside ? 0.2 : machine.state === 'walk' ? 0.6 : 0.9;
  sun.intensity = inside ? 2.2 : 3.0;
  const lit = world.bulbSpots.map((s) => ({ s, d: s.pos.distanceTo(eye) })).sort((a, b) => a.d - b.d).slice(0, bulbs.length);
  bulbs.forEach((b, i) => {
    const l = lit[i];
    b.intensity = l && machine.state !== "orbit" ? 14 * l.s.k : 0;
    if (l) b.position.copy(l.s.pos);
  });
}

// ---------- hook for the test (read-only plus the machine) ----------
window.casa = {
  get state() { return machine.state; },
  send,
  machine: MACHINE,
  trace: machine.trace,
  get player() {
    const p = world.phys?.player;
    return p ? { feet: [p.feet.x, p.feet.y, p.feet.z], yaw: p.yaw, pitch: view.pitch, room: roomAt(p.feet), grounded: p.grounded, climbing: !!p.climbing } : null;
  },
  rooms: world.rooms,
  get camera() { return { distance: camera.position.distanceTo(orbit?.target ?? new THREE.Vector3()), fov: camera.fov, position: camera.position.toArray() }; },
  get bounds() { return { center: world.center.toArray(), radius: world.radius }; },
  get spaces() { return world.spaces.map((s) => ({ name: s.name, min: s.min, max: s.max })); },
  clearAt: (x, z, fromY, margin) => world.phys.player.clearAt(x, z, fromY, margin),
  groundAt: (x, z, y) => world.phys.player.groundAt(x, z, y),
  place: (x, y, z) => world.phys.player.setFeet(x, y, z),
  near: (r) => world.phys.player.near(r),
  setYaw: (y) => { world.phys.player.yaw = y; },
  setPitch: (v) => { view.pitch = THREE.MathUtils.clamp(v, -1.45, 1.45); },
  floorY: () => world.phys.player.floorY(),
  ray: (yawOffset = 0, h = 0.9) => world.phys.player.ray(world.phys.player.yaw + yawOffset, 6, h),
  /** World bounding boxes of the GLB nodes whose extras.role equals role. */
  byRole: (role) => world.roleBoxes.filter((b) => b.role === role),
  /** True when the centre of the box named name is inside the camera frustum. */
  inView: (name) => {
    const b = world.roleBoxes.find((x) => x.name === name);
    if (!b) return false;
    camera.updateMatrixWorld(true);
    const v = new THREE.Vector3(...b.center).project(camera);
    return Math.abs(v.x) <= 1 && Math.abs(v.y) <= 1 && v.z < 1;
  },
  get frames() { return frames; },
  /** Physics seconds simulated while walking (tests time flights with it, not with the wall clock). */
  get clock() { return clock; },
  /** Route over the real collision world from the current feet to the first reachable target [x, z, y?] (glTF metres). Null = none. */
  plan: (targets, margin = 2) => { const f = world.phys.player.feet; return world.nav.plan(f.x, f.y, f.z, targets, margin); },
  /** Walk the waypoints with the real controller, no rendering (fixed 60 Hz steps, as if W were held and the view faced each point). */
  visited: () => world.nav.visited(),
  /** Planner cell at (x, z): the standable heights the route planner sees there. */
  cell: (x, z, margin = 2) => { const [i, j] = world.nav.cellOf(x, z); return world.nav.surf(i, j, margin); },
  /** Hold forward/right for `seconds` of physics (no rendering). Returns the feet at the end and the lowest gap to the floor below. */
  simHold: (forward, right, seconds, run = false) => {
    const p = world.phys.player;
    let gap = 0;
    for (let n = 0; n < seconds / STEP; n++) { p.step({ forward, right, run }); const fy = p.floorY(); if (fy > -90) gap = Math.min(gap, p.feet.y - fy); }
    return { feet: [p.feet.x, p.feet.y, p.feet.z], minFloorGap: gap, grounded: p.grounded };
  },
  /** Why a route ends: from the reachable cell nearest to (x, z) push toward it and report what the capsule touches. */
  diagnose: (x, y, z) => {
    const p = world.phys.player;
    let best = null;
    for (const c of world.nav.visited()) { if (Math.abs(c[2] - y) > 0.4) continue; const d = Math.hypot(c[0] - x, c[1] - z); if (!best || d < best[0]) best = [d, c]; }
    if (!best) return null;
    p.setFeet(best[1][0], best[1][2], best[1][1]);
    p.yaw = Math.atan2(-(x - p.feet.x), -(z - p.feet.z));
    for (let n = 0; n < 90; n++) p.step({ forward: 1, right: 0, run: false });
    return { stoppedAt: [p.feet.x, p.feet.y, p.feet.z], gap: best[0], blockedBy: [...p.blockedBy] };
  },
  /** What the capsule touches when standing at (x, y, z): colliders within 2 cm, for walk diagnostics. */
  touching: (x, y, z) => { const p = world.phys.player; p.setFeet(x, y, z); return p.near(0.02); },
  surfaces: (x, z, cm = 2, top = 9) => world.phys.player.surfaces(x, z, cm * 0.01, top),
  blocked: () => world.phys.player.blockedBy,
  walkRoute: (waypoints, opts) => world.nav.walk(waypoints, opts),
  /** Same route walked with human aim error (seeded); proves a flight is easy, not merely possible. */
  walkSloppy: (waypoints, opts) => world.nav.walkSloppy(waypoints, opts),
  /** Press a key / set the touch stick without hardware (the frame loop reads both). Test hook for the stuck-key cases. */
  get held() { return [...keys]; },
  get touchAxes() { return { ...touch.axes }; },
  get touchMode() { return touchMode; },
  info: {},
  get ready() { return machine.state !== 'loading'; },
};

// ---------- load ----------
async function load() {
  const gltf = await new Promise((res, rej) => {
    // the site serves a meshopt-compressed copy (tools/web/optimize.mjs); ?glb= picks another file for side-by-side proofs
    const url = new URLSearchParams(location.search).get('glb') || '../build/casa.glb';
    new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).load(url, res, (e) => {
      if (e.total) $('bar').firstElementChild.style.width = `${(100 * e.loaded / e.total).toFixed(0)}%`;
    }, rej);
  });
  const root = gltf.scene;
  scene.add(root);
  root.updateMatrixWorld(true);
  const house = new THREE.Box3();
  let tris = 0;
  const spaceNodes = [];
  root.traverse((o) => {
    const u = o.userData;
    if (u.spawn) {
      const pos = o.getWorldPosition(new THREE.Vector3());
      world.spawns.set(u.room, { pos, yawDeg: u.yaw, level: u.level, look: u.look_x === undefined ? null : new THREE.Vector3(u.look_x, u.look_z, -u.look_y) });
    }
    if (u.type === 'IfcSpace') spaceNodes.push(o);
    if (u.ifc === 'IfcWall' || u.ifc === 'IfcDoor') {
      const bx = new THREE.Box3().setFromObject(o);
      (u.ifc === 'IfcWall' ? world.walls : world.doors).push({ min: bx.min.toArray(), max: bx.max.toArray() });
    }
    if (u.role) { const bx = new THREE.Box3().setFromObject(o); world.roleBoxes.push({ role: u.role, name: o.name, min: bx.min.toArray(), max: bx.max.toArray(), center: bx.getCenter(new THREE.Vector3()).toArray() }); }
    if (o.isMesh) {
      // in the web copy each mesh sits on an unnamed child (extras mesh_of) of its IFC node, which holds the extras
      const own = o.userData.mesh_of ? o.parent.userData : u;
      o.castShadow = !(own.collider === 'none');
      o.receiveShadow = true;
      tris += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3;
    }
  });
  for (const o of spaceNodes) {
    const box = new THREE.Box3().setFromObject(o);
    world.spaces.push({ name: o.userData.name, box });
    house.union(box);
    o.visible = false;
  }
  root.traverse((o) => { if (o.name.startsWith('Limite')) o.visible = false; });
  for (const [name, s] of world.spawns) world.rooms.push({ name, level: s.level, spawn: s.pos.toArray() });
  world.rooms.sort((a, b) => a.level - b.level || a.name.localeCompare(b.name, 'es'));
  world.center.copy(house.getCenter(new THREE.Vector3()));
  world.radius = house.getSize(new THREE.Vector3()).length() / 2;
  world.spaces = world.spaces.map((s) => Object.assign(s, { min: s.box.min.toArray(), max: s.box.max.toArray() }));

  // sun fitted to the house
  const r = world.radius + 6;
  sun.position.copy(world.center).add(new THREE.Vector3(0.5, 1, 0.6).normalize().multiplyScalar(60));
  sun.target.position.copy(world.center);
  Object.assign(sun.shadow.camera, { left: -r, right: r, top: r, bottom: -r, near: 1, far: 140 });
  sun.shadow.camera.updateProjectionMatrix();

  for (const s of world.spaces) {
    if (OPEN_AIR.test(s.name) || s.max[1] - s.min[1] < 2) continue;
    const c = s.box.getCenter(new THREE.Vector3());
    world.bulbSpots.push({ name: s.name, pos: new THREE.Vector3(c.x, s.min[1] + 2.1, c.z), k: THREE.MathUtils.clamp((s.max[0] - s.min[0]) * (s.max[2] - s.min[2]) / 14, 0.3, 1) });
  }
  if (AO) {
    composer = new EffectComposer(renderer, { multisampling: 0 });
    composer.addPass(new RenderPass(scene, camera));
    const ao = new N8AOPostPass(scene, camera, innerWidth, innerHeight);
    Object.assign(ao.configuration, { aoRadius: 0.5, distanceFalloff: 1.0, intensity: 2.5, color: new THREE.Color(0x000000) });
    composer.addPass(ao);
    composer.addPass(new EffectPass(camera, new SMAAEffect(), new ToneMappingEffect({ mode: ToneMappingMode.ACES_FILMIC })));
  }
  world.phys = await createPhysics(root);
  world.nav = createNav(world.phys.player);
  buildPlan();
  buildMenu();
  import('./interact.js').then((m) => { m.initInteract(scene, camera, renderer, { world: world.phys.world }); world.phys.player.syncLeaves(); }).catch((err) => console.warn('interact unavailable', err));

  orbit = new OrbitControls(camera, renderer.domElement);
  orbit.enableDamping = true;
  orbit.target.copy(world.center);
  orbit.minDistance = world.radius + 2;
  orbit.maxDistance = 70;
  orbit.maxPolarAngle = THREE.MathUtils.degToRad(85);
  orbit.enablePan = false;
  camera.fov = 45; camera.updateProjectionMatrix();
  camera.position.copy(world.center).add(new THREE.Vector3(10, 11, 24));
  orbit.update();
  window.casa.info = { triangles: Math.round(tris), colliders: world.phys.stats, ladders: world.phys.ladders, toneMapping: 'ACESFilmic', shadows: true, ao: AO, bulbs: world.bulbSpots.length };
  send('loaded');
}

requestAnimationFrame(frame);
load().catch((err) => {
  console.warn('load failed', err);
  $('errorDetail').textContent = String(err?.message ?? err);
  send('failed');
});
