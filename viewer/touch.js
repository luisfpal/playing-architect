// Touch controls, League of Legends style: the joystick lives in the bottom-left corner zone (it rests there, visible, and
// follows the thumb only inside that zone); a drag anywhere else looks around; an interact button.
// Pointer events with a pointerId per finger, so the joystick and the look drag work at the same time.
// Returns the analog axes the loop reads each frame; look deltas go to onLook (pixels).
const LOOK_BASE = 1; // pixels of drag per pixel of look; main.js scales it to radians
const RADIUS = 56; // joystick travel in CSS px
const MARGIN = 24; // the resting stick sits this far from the left and bottom edges
// The corner zone that grabs the joystick: left side, lower part of the screen. Touches outside it look around.
// The zone is a fixed disc around the resting stick in the bottom-left corner (portrait and landscape alike); the stick never
// moves to the thumb. A touch outside the disc, even just above it, looks around.
const ZONE = RADIUS * 1.6;
const home = () => ({ x: MARGIN + RADIUS, y: innerHeight - MARGIN - RADIUS });
const inZone = (x, y) => { const h = home(); return Math.hypot(x - h.x, y - h.y) <= ZONE; };

export function initTouch({ surface, onLook, onInteract }) {
  const axes = { forward: 0, right: 0, run: 0 }; // run 0..1
  const root = document.createElement('div');
  root.id = 'touch';
  root.innerHTML = '<div id="stickBase"><div id="stickKnob"></div></div><button id="actBtn" aria-label="Interact">Open</button>';
  document.body.appendChild(root);
  const base = root.querySelector('#stickBase'), knob = root.querySelector('#stickKnob'), act = root.querySelector('#actBtn');
  let stick = null, look = null, enabled = false;

  // At rest the stick shows, faded, in its corner, so the thumb knows where to go.
  const rest = () => {
    base.style.cssText = `display:block;opacity:.55;left:${MARGIN}px;top:${innerHeight - MARGIN - 2 * RADIUS}px`;
    knob.style.transform = 'translate(0,0)';
  };
  const reset = () => {
    stick = null; look = null;
    axes.forward = axes.right = 0; axes.run = 0;
    rest();
  };
  addEventListener('resize', () => { if (!stick) rest(); });

  surface.addEventListener('pointerdown', (e) => {
    if (e.pointerType !== 'touch' || !enabled) return;
    if (inZone(e.clientX, e.clientY) && !stick) {
      // the stick centres under the thumb (an offset centre would push the player sideways from the first frame)
      const h = home(); // the stick stays pinned in its corner; the knob measures from the fixed centre
      stick = { id: e.pointerId, x: h.x, y: h.y };
      base.style.cssText = `display:block;opacity:.8;left:${h.x - RADIUS}px;top:${h.y - RADIUS}px`;
    } else if (!look && !(stick && inZone(e.clientX, e.clientY))) {
      look = { id: e.pointerId, x: e.clientX, y: e.clientY };
    }
  });
  addEventListener('pointermove', (e) => {
    if (e.pointerType !== 'touch') return;
    if (stick && e.pointerId === stick.id) {
      let dx = e.clientX - stick.x, dy = e.clientY - stick.y;
      const len = Math.hypot(dx, dy);
      if (len > RADIUS) { dx *= RADIUS / len; dy *= RADIUS / len; }
      knob.style.transform = `translate(${dx}px,${dy}px)`;
      const mag = Math.min(1, len / RADIUS), dead = 0.12;
      const k = mag < dead ? 0 : (mag - dead) / (1 - dead);
      axes.right = len ? dx / Math.max(len, 1e-6) * k : 0;
      axes.forward = len ? -dy / Math.max(len, 1e-6) * k : 0;
      axes.run = Math.min(1, Math.max(0, (k - 0.8) / 0.2)); // the last fifth of the stick blends walk into run, no speed cliff
    } else if (look && e.pointerId === look.id) {
      onLook((e.clientX - look.x) * LOOK_BASE, (e.clientY - look.y) * LOOK_BASE);
      look.x = e.clientX; look.y = e.clientY;
    }
  });
  const release = (e) => {
    if (stick && e.pointerId === stick.id) { stick = null; axes.forward = axes.right = 0; axes.run = 0; rest(); }
    if (look && e.pointerId === look.id) look = null;
  };
  addEventListener('pointerup', release);
  addEventListener('pointercancel', release);
  addEventListener('blur', reset);

  act.addEventListener('pointerdown', (e) => { e.stopPropagation(); e.preventDefault(); onInteract(); });

  return {
    axes,
    // shown only while walking on a touch device; leaving walk mode drops any finger state
    setEnabled(on, touchDevice) {
      enabled = on && touchDevice;
      root.style.display = enabled ? 'block' : 'none';
      reset(); // drops finger state and parks the stick in its corner
    },
    reset,
    get active() { return !!(stick || look); },
  };
}
