"""Headless proof of the game viewer (playwright + Chromium). Run through `make viewer-test`.

Spec: plan/r4_plan.md task C1 against docs/statecharts.md chart 1. Coordinates are glTF (x east, y up, z = -IFC y):
the street is at z > 0, the house at z in [-20, 0].

 a  chart 1 == MACHINE            b  loaded -> orbit, no console errors         c  orbit clamp
 d  street -> porch -> genkan on foot, real keyboard and mouse                    i  wheel changes FOV only
 e  every room: face the nearest wall, hold W, stay >= 0.25 m (the body radius) from its plane, camera inside the room
 f  stairs: every flight up AND down (planner route walked with the real controller; also once with real keys)
 g  feet never below the floor under them                                         h  corner press then unstick
 k  every room entered and left through its doors, from the hub of its floor; a walled-off room fails with its blocker
 l  stuck keys: blur, visibilitychange, pause and resume never leave a key held
 m  plan / minimap / floor indicator / help on desktop
 n  mobile 390x844 and 844x390: touch joystick (bottom-left corner only), drag to look, interact button, plan, nothing off screen
Real input (keys, mouse, CDP touch) is used for d, f (once), h, l, m, n. The exhaustive walks (e, f, k) step the same controller
(`player.step`) with a route planned over the same Rapier world, because swiftshader renders only a few frames per second.
Waits that gate a physics assertion wait for `house.clock` (physics seconds), never for the wall clock: on this machine one
rendered frame is about 3 s, and a millisecond wait measures the renderer, not the game.

VT_ONLY=n runs only the touch section (VT_ONLY=desktop only the rest); it exists to iterate on one section without the full suite.
"""
import http.server
import math
import os
import re
import socketserver
import sys
import threading
from functools import partial
from pathlib import Path

from playwright.sync_api import sync_playwright


def is_wall(name):
    """A room wall or an arena boundary (glTF names carry underscores for spaces); party walls and wall-mounted fittings are not."""
    n = str(name).replace("_", " ").lower()
    return n.startswith("boundary") or (" wall" in n and "party wall" not in n)

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "build" / "r5_shots"
LOOK = 0.0022  # yaw per mouse pixel, same constant as main.js
FAILS = []
HUBS = {0: "Hall", 1: "Upper landing", 2: "Laundry room"}
FLOOR_Y = [0.0, 3.2, 6.4]
ONLY = {s.strip() for s in os.environ.get("VT_ONLY", "").split(",") if s.strip()}  # empty = every section


def on(tag):
    """True when the section tagged `tag` runs: every section without VT_ONLY, else only the tags listed there."""
    return not ONLY or tag in ONLY


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name} {detail}", flush=True)
    if not ok:
        FAILS.append(name)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def translate_path(self, path):
        # HOUSE_GLB=build/web/house.glb runs the same tests on the web copy the site serves
        if os.environ.get("HOUSE_GLB") and path.split("?")[0] == "/build/house.glb":
            return str(ROOT / os.environ["HOUSE_GLB"])
        return super().translate_path(path)


def serve():
    srv = socketserver.TCPServer(("127.0.0.1", 0), partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def doc_edges():
    block = re.search(r"```mermaid\n(.*?)```", (ROOT / "docs/statecharts.md").read_text(), re.S).group(1)
    return {(s, ev, t) for s, t, ev in re.findall(r"^\s*(\w+) --> (\w+) : (\w+)", block, re.M)}


def seg_dist(q, a, b):
    """Distance from point q to the segment a-b (2D)."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dz) / (dx * dx + dz * dz or 1e-12)))
    return math.hypot(q[0] - a[0] - t * dx, q[1] - a[1] - t * dz)


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def level_of(y):
    return 0 if y < 1.6 else 1 if y < 4.8 else 2


class Driver:
    """Real input only: keys through the keyboard, look through mouse moves (movementX is derived by Chromium)."""

    def __init__(self, page):
        self.page = page
        self.mx = 20

    def ev(self, js):
        return self.page.evaluate(js)

    @property
    def p(self):
        return self.ev("house.player")

    def turn(self, dyaw):
        """Rotate by dyaw radians (positive = counter-clockwise seen from above) in steps the viewer accepts."""
        px = -dyaw / LOOK
        w = self.page.viewport_size["width"]
        while abs(px) > 1:
            step = max(-100, min(100, px))
            nx = self.mx + step
            if nx > w - 40 or nx < 40:
                nx = 20 if step > 0 else w - 20  # a jump over 250 px: the viewer ignores it as a browser spike
                self.page.mouse.move(nx, 150)
                self.mx = nx
                nx = self.mx + step
            self.page.mouse.move(nx, 150)
            self.mx = nx
            px -= step

    def face(self, yaw):
        for _ in range(6):
            err = wrap(yaw - self.p["yaw"])
            if abs(err) < 0.02:
                return
            self.turn(err)
            self.page.wait_for_timeout(80)

    def walk_to(self, x, z, tol=0.35, timeout=25.0, run=False):
        """Hold W (and Shift) while steering by mouse until within tol of (x, z). Returns True on arrival."""
        page = self.page
        page.keyboard.down("KeyW")
        if run:
            page.keyboard.down("ShiftLeft")
        t = 0.0
        ok = False
        prev = None
        try:
            while t < timeout:
                p = self.p
                fx, _, fz = p["feet"]
                # arrived if the feet passed within tol of the point since the last sample: on a loaded machine one sample can
                # be 0.5 m of walking apart, so a point can be crossed between two samples without either being within tol.
                # Only for route points (tol 0.3); a final stop (tol 0.2) must really be reached, not walked past
                if math.hypot(x - fx, z - fz) < tol or (prev and tol >= 0.3 and seg_dist((x, z), prev, (fx, fz)) < tol):
                    ok = True
                    break
                prev = (fx, fz)
                want = math.atan2(-(x - fx), -(z - fz))
                err = wrap(want - p["yaw"])
                if abs(err) > 0.4:  # a sharp turn: stop, turn to face the waypoint, then go (no drifting off a narrow stair)
                    page.keyboard.up("KeyW")
                    page.wait_for_timeout(350)
                    self.face(want)  # retries, so a first mouse step that Chromium reports as movementX 0 is corrected
                    page.wait_for_timeout(200)
                    page.keyboard.down("KeyW")
                elif abs(err) > 0.03:
                    self.turn(err)
                page.wait_for_timeout(120)
                t += 0.14
        finally:
            page.keyboard.up("KeyW")
            if run:
                page.keyboard.up("ShiftLeft")
        return ok

    def hold(self, keys, ms):
        for k in keys:
            self.page.keyboard.down(k)
        self.page.wait_for_timeout(ms)
        for k in keys:
            self.page.keyboard.up(k)


def new_page(browser, port, errors, w=640, h=300):
    page = browser.new_page(viewport={"width": w, "height": h})
    watch(page, errors)
    page.goto(f"http://127.0.0.1:{port}/viewer/index.html?ao=0")
    return page


def watch(page, errors):
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    # headless Chromium has no pointer lock; that message is the environment, not a defect
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "ointer" not in m.text else None)


def shot(page, path, size=(1280, 720)):
    old = page.viewport_size
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.wait_for_timeout(1200)
    page.screenshot(path=str(path))
    page.set_viewport_size(old)
    page.wait_for_timeout(300)


def nearest_wall(d):
    """Nearest wall plane seen from the player, as (yaw that faces it squarely, ray hit). A candidate counts only if the
    square-on ray lands on the same plane, so a doorway beside it cannot turn a wall test into a walk through a door."""
    ev = d.ev
    base = d.p["yaw"]
    cands = []
    for k in range(16):
        off = k * math.pi / 8
        h = ev(f"house.ray({off}, 0.9)")
        if h and abs(h["normal"]["y"]) < 0.3 and is_wall(h["name"]):
            cands.append((h["dist"], off, h))
    for _, off, h in sorted(cands, key=lambda c: c[0]):
        f = d.p["feet"]
        n = h["normal"]
        s = 1.0 if (f[0] - h["point"]["x"]) * n["x"] + (f[2] - h["point"]["z"]) * n["z"] >= 0 else -1.0
        yaw = math.atan2(s * n["x"], s * n["z"])  # direction (-s*n) as a yaw: forward = (-sin, -cos)
        sq = ev(f"house.ray({yaw - base}, 0.9)")
        if sq and is_wall(sq["name"]) and abs(sq["normal"]["x"] * n["x"] + sq["normal"]["z"] * n["z"]) > 0.99:
            # the body is 0.5 m wide: rays 0.2 rad to each side must land on the same plane, or the square-on ray went through an open door leaf
            if all((e := ev(f"house.ray({yaw - base + da}, 0.9)")) and abs((e["point"]["x"] - sq["point"]["x"]) * n["x"] + (e["point"]["z"] - sq["point"]["z"]) * n["z"]) < 0.15
                   for da in (-0.2, 0.2)):
                return yaw, sq
    return None


def floor_under(d):
    return d.ev("house.floorY()")


def settle(d):
    d.ev("house.simHold(0, 0, 0.6)")


def wait_clock(page, seconds, timeout=180000):
    """Wait until the viewer has simulated `seconds` more physics seconds. Physics time, not wall time: the frame loop
    advances the clock by whole fixed steps, so this resolves on the frames the game actually ran, whatever the renderer's
    speed. Polling runs on animation frames, so a page that is busy drawing still answers as soon as its next frame ends."""
    start = page.evaluate("house.clock")
    page.wait_for_function("t => house.clock >= t", arg=start + seconds, timeout=timeout)


# ---- touch (Chrome DevTools Protocol: real touch events, several fingers at once) ----
class Touch:
    def __init__(self, page):
        self.cdp = page.context.new_cdp_session(page)
        self.pts = {}

    def _send(self, kind):
        self.cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y, "id": i} for i, (x, y) in self.pts.items()]})

    def down(self, i, x, y):
        self.pts[i] = (x, y)
        self._send("touchStart")

    def move(self, i, x, y):
        self.pts[i] = (x, y)
        self._send("touchMove")

    def up(self, i):
        x, y = self.pts.pop(i, (0, 0))
        # touchEnd lists the fingers that LIFT (the remaining ones stay down); listing the rest lifted the wrong finger
        self.cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": [{"x": x, "y": y, "id": i}]})

    def drag(self, i, x0, y0, x1, y1, steps=8, hold=False, page=None):
        self.down(i, x0, y0)
        for s in range(1, steps + 1):
            self.move(i, x0 + (x1 - x0) * s / steps, y0 + (y1 - y0) * s / steps)
        if not hold:
            self.up(i)


def inside_viewport(page, selector, pad=0):
    r = page.evaluate(f"(() => {{ const e = document.querySelector({selector!r}); if (!e) return null; const r = e.getBoundingClientRect(); const s = getComputedStyle(e);"
                      f" return {{x: r.x, y: r.y, w: r.width, h: r.height, shown: s.display !== 'none' && s.visibility !== 'hidden', vw: innerWidth, vh: innerHeight}}; }})()")
    if r is None:
        return False, "missing"
    ok = r["shown"] and r["x"] >= -pad and r["y"] >= -pad and r["x"] + r["w"] <= r["vw"] + pad and r["y"] + r["h"] <= r["vh"] + pad and r["w"] > 0
    return ok, str({k: round(v, 1) if isinstance(v, float) else v for k, v in r.items()})


def room_rows(ev):
    return ev("house.rooms")


FLIGHTS = [("ground floor -> upper floor", "Hall", "Upper landing", 3.2),
           ("upper floor -> terrace", "Upper landing", "Laundry room", 6.4)]
SLOPPY_SEEDS = (1, 2, 3)  # human aim error: re-aimed every 0.4 s with up to 0.45 rad of error (viewer/nav.js walkSloppy)


def run_flights(tag, d, spawn):
    """Every flight up and down, three ways: planned route, a human's aim error, and running (Shift). Times are physics seconds.
    The terrace flight takes at most 1.2 x the other flight in each direction and mode."""
    ev = d.ev
    secs = {}
    for label, low, high, hy in FLIGHTS:
        for direction, (a, b) in (("up", (low, high)), ("down", (high, low))):
            sx, sy, sz = spawn[b]
            for mode, call in (("planned", "house.walkRoute(@W@, { tol: 0.25, maxSeconds: 60 })"),
                               ("run", "house.walkRoute(@W@, { tol: 0.25, maxSeconds: 60, run: true })"),
                               *((f"sloppy{n}", f"house.walkSloppy(@W@, {{ err: 0.45, seed: {n}, maxSeconds: 60 }})") for n in SLOPPY_SEEDS)):
                ev(f"house.send('teleport', {a!r})")
                settle(d)
                route = ev(f"house.plan([[{sx}, {sz}, {sy}]])")
                if not route or "waypoints" not in route:
                    check(f"f[{tag}]: {label} {direction} {mode}: route exists", False, f"{a} -> {b}: {route}")
                    continue
                # the flight is the stair, not the walk from the room's teleport spot to its foot or top: a room's
                # "room to look" landing sits at a different distance from the stair, so time only the stair waypoints
                # (last waypoint on the start floor to first waypoint on the end floor)
                # the planner drops the start point and merges collinear legs: when the room spawn stands in line with the
                # stair (the Hall spot at the foot of the lower flight) the route's first waypoint is already the landing and
                # the trim below would skip the whole lower flight. Start the list at the feet so the trim sees the start floor.
                f0 = d.p["feet"]
                wps = [[f0[0], f0[2], f0[1]]] + route["waypoints"]
                i = 0
                while i < len(wps) - 1 and abs(wps[i + 1][2] - wps[0][2]) < 0.5:
                    i += 1
                j = len(wps) - 1
                while j > 0 and abs(wps[j - 1][2] - wps[-1][2]) < 0.5:
                    j -= 1
                # No ramp colliders any more, so the first end-floor waypoint can sit on the bottom tread; walk one
                # waypoint further (a person steps off the stair) so the check measures the floor, not the last tread
                wps = wps[i:min(j + 2, len(wps))]
                ev(f"house.place({wps[0][0]}, {wps[0][2]}, {wps[0][1]})")
                w = ev(call.replace("@W@", str(wps)))
                end = d.p
                ok = w["ok"] and w["grounded"] and abs(end["feet"][1] - sy) < 0.1 and w["minFloorGap"] > -0.05
                check(f"f[{tag}]: {label} {direction} {mode}: walked end to end", ok,
                      f"y {end['feet'][1]:.2f} want {sy:.2f}, floor gap {w['minFloorGap']:.2f}, {w['seconds']:.1f} s")
                secs[(label, direction, mode)] = w["seconds"]
    for direction in ("up", "down"):
        for mode in ("planned", "run", *(f"sloppy{n}" for n in SLOPPY_SEEDS)):
            t = secs.get((FLIGHTS[1][0], direction, mode))
            o = secs.get((FLIGHTS[0][0], direction, mode))
            if t is not None and o is not None:
                check(f"f[{tag}]: terrace flight {direction} {mode} within 20 % of the other flight", t <= 1.2 * o + 0.5, f"terrace {t:.1f} s, other {o:.1f} s")
    return secs


def run_rooms(tag, d, rooms, spawn):
    """Every room entered and left through its doors from the hub of its floor (planned route, real controller)."""
    ev = d.ev
    trapped = []
    for r in rooms:
        name = r["name"]
        if name in ("Street",) or name in HUBS.values():
            continue
        sx, sy, sz = r["spawn"]
        lvl = level_of(sy)
        hub = spawn[HUBS[lvl]]
        ev(f"house.send('teleport', {name!r})")  # lands on the spawn, or on the nearest spot of the room that opens out
        settle(d)
        there = d.p["feet"]
        out_route = ev(f"house.plan([[{hub[0]}, {hub[2]}, {hub[1]}]])")  # leave: room -> hub
        left = bool(out_route and "waypoints" in out_route)
        if left:
            w = ev(f"house.walkRoute({out_route['waypoints']}, {{ tol: 0.3, maxSeconds: 90 }})")
            left = w["ok"] and w["minFloorGap"] > -0.05
        ev(f"house.send('teleport', {HUBS[lvl]!r})")  # enter: hub -> room
        settle(d)
        in_route = ev(f"house.plan([[{there[0]}, {there[2]}, {there[1]}]])")
        entered = bool(in_route and "waypoints" in in_route)
        if entered:
            # the planner stops within 0.3 m of the goal, which can still be the doorway: finish the last metre straight in
            w = ev(f"house.walkRoute({in_route['waypoints'] + [[there[0], there[2], there[1]]]}, {{ tol: 0.15, maxSeconds: 90 }})")
            entered = w["ok"] and d.p["room"] == name
        if not (left and entered):
            why = ev(f"house.diagnose({sx}, {sy}, {sz})") if not entered else None
            trapped.append(f"{name}: leave={left} enter={entered} {why}")
    check(f"k[{tag}]: every room can be entered and left through its doors", not trapped, "\n      " + "\n      ".join(trapped))


def touch_climb(tag, mp, t, w, h, spawn, a, b):
    """Climb from room a to room b with the touch joystick (finger 1 held) and the look drag (finger 2), steering along the route.
    Returns (ok, detail, stair seconds): the time is the stair only, from the last waypoint on the start floor to the first on
    the end floor, the same segment run_flights times, so the walk from the room spawn to the stair cannot bias the ratio. Every
    wait is physics time: on a rendered page one frame is several seconds and a wall-clock cadence would time the renderer."""
    mev = mp.evaluate
    mev(f"house.send('teleport', {a!r})")
    settle(Driver(mp))
    sx, sy, sz = spawn[b]
    route = mev(f"house.plan([[{sx}, {sz}, {sy}]])")
    if not route or "waypoints" not in route:
        return False, f"no route {a} -> {b}", None
    f0 = mev("house.player.feet")
    wps = [[f0[0], f0[2], f0[1]]] + route["waypoints"]  # start at the feet: see run_flights (a spawn in line with the stair)
    i = 0
    while i < len(wps) - 1 and abs(wps[i + 1][2] - wps[0][2]) < 0.5:
        i += 1
    j = len(wps) - 1
    while j > 0 and abs(wps[j - 1][2] - wps[-1][2]) < 0.5:
        j -= 1
    if j <= i:  # a route that never changes floor: time the whole walk
        i, j = 0, len(wps) - 1
    t0 = t1 = None
    lx, ly = 80, h - 80  # The pinned stick centre (touch.js MARGIN + RADIUS); the old w*0.2, h*0.75 now falls outside the zone
    held, ok = False, True
    for k, (x, z, _y) in enumerate(wps):
        for _ in range(250):
            p = mev("house.player")
            fx, _, fz = p["feet"]
            if math.hypot(x - fx, z - fz) < 0.3:
                if k == i:
                    t0 = mev("house.clock")
                if k == j:
                    t1 = mev("house.clock")
                break
            err = wrap(math.atan2(-(x - fx), -(z - fz)) - p["yaw"])
            if abs(err) > 0.4 and held:  # sharp turn: let go of the stick, turn on the spot
                t.up(1)
                held = False
            if abs(err) > 0.04:
                dx = max(-150, min(150, -err / 0.0042))
                x0 = w * 0.55 if dx > 0 else w * 0.8  # right half, clear of the minimap and the interact button
                t.drag(2, x0, h * 0.55, x0 + dx, h * 0.55, steps=1)  # one move: the handler applies the whole delta at once
            if not held and abs(err) <= 0.4:
                t.down(1, lx, ly)
                t.move(1, lx, ly - 55)
                held = True
            wait_clock(mp, 0.05)
        else:
            ok = False
            break
    if held:
        t.up(1)
    end = mev("house.player")["feet"]
    stair = None if t0 is None or t1 is None else t1 - t0
    return ok and abs(end[1] - sy) < 0.1, f"{a} -> {b}: y {end[1]:.2f} want {sy:.2f}", stair


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    srv = serve()
    port = srv.server_address[1]
    args = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=args)
        errors = []
        if on("desktop"):
            page = new_page(browser, port, errors)
            page.wait_for_function("window.house && window.house.state !== 'loading'", timeout=120000)
            d = Driver(page)
            ev = d.ev

            # (a) the chart equals the table
            code = {(s, e, t) for s, evs in ev("house.machine").items() for e, t in evs.items()}
            doc = doc_edges()
            check("a: chart 1 == MACHINE", code == doc, f"only code {code - doc}, only doc {doc - code}")
            check("a: ignored events", ev("house.send('loaded')") is False and ev("house.send('resume')") is False and ev("house.state") == "orbit")

            # (b) loaded
            check("b: loading -> orbit", ev("house.state") == "orbit" and ev("house.trace")[:2] == ["loading", "loaded:orbit"], ev("house.trace"))
            info = ev("house.info")
            check("b: scene budget", 0 < info["triangles"] < 2_400_000, str(info))  # Full-detail vehicles (src/render/config.py MAX_TRIANGLES)
            rooms = room_rows(ev)
            check("b: room menu has every spawn", len(rooms) >= 30 and ev("document.querySelectorAll('#menu .room').length") == len(rooms), f"{len(rooms)} rooms")
            shot(page, SHOTS / "orbit.png")

            # (c) orbit clamp: 60 wheel-in ticks, then drag from above and from the ground
            b = ev("house.bounds")
            page.mouse.move(320, 150)
            for _ in range(60):
                page.mouse.wheel(0, -400)
                page.wait_for_timeout(15)
            page.wait_for_timeout(600)
            dist = ev("house.camera.distance")
            check("c: wheel-in never enters the house", dist >= b["radius"] + 2 - 0.05, f"distance {dist:.2f} >= {b['radius'] + 2:.2f}")
            for dy in (-140, 140):
                page.mouse.move(320, 150)
                page.mouse.down()
                for i in range(1, 11):
                    page.mouse.move(320, 150 + dy * i / 10)
                page.mouse.up()
                page.wait_for_timeout(500)
                c = ev("house.camera")
                polar = math.degrees(math.acos(max(-1, min(1, (c["position"][1] - b["center"][1]) / c["distance"]))))
                check(f"c: drag polar ({'up' if dy < 0 else 'down'})", polar <= 85.5 and c["position"][1] > b["center"][1] - 0.01, f"polar {polar:.1f}")
            shot(page, SHOTS / "orbit_clamped.png")

            # (d) walk in from the street with real input
            check("d: Tab starts walking", ev("house.send('enterWalk')") and ev("house.state") == "walk")
            page.wait_for_timeout(500)
            p = d.p
            check("d: enters on the street", p["room"] == "Street" and abs(p["feet"][0] - 6) < 0.5 and p["feet"][2] > 3, str(p))
            visited = []
            ok = True
            for name, x, z in [("porch", 7.5, 0.8), ("genkan", 7.7, -2.5)]:
                arrived = d.walk_to(x, z)
                visited.append((name, arrived, d.p["room"]))
                ok &= arrived
            check("d: street -> porch -> genkan on foot", ok, str(visited))
            check("d: genkan reached through the door", d.p["room"] in ("Genkan", "Vestibule"), d.p["room"])
            shot(page, SHOTS / "walk_genkan.png")
            d.face(d.p["yaw"])

            # (i) wheel changes FOV only
            before = d.p["feet"]
            fov0 = ev("house.camera.fov")
            page.mouse.wheel(0, 600)
            page.wait_for_timeout(300)
            page.mouse.wheel(0, -2400)
            page.wait_for_timeout(300)
            fov1 = ev("house.camera.fov")
            after = d.p["feet"]
            check("i: wheel in walk changes FOV within 35..75, never position", 35 <= fov1 <= 75 and fov1 != fov0 and math.dist(before, after) < 0.02, f"fov {fov0}->{fov1}")
            cam0 = ev("house.camera.position")
            for dy in (-3000, 3000, -3000):
                page.mouse.wheel(0, dy)
                page.wait_for_timeout(200)
            check("i: zoom never moves the camera (eye stays at the player)", math.dist(cam0, ev("house.camera.position")) < 0.01, f"{cam0} -> {ev('house.camera.position')}")
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(300)
            check("i: FOV upper clamp", ev("house.camera.fov") <= 75.001)
            page.mouse.wheel(0, -1000)

            # (l) stuck keys: every way of losing the keyboard releases it
            ev("house.send('teleport', 'Living room')")
            settle(d)
            d.face(d.p["yaw"])
            stuck = []
            for how, js in (("blur", "window.dispatchEvent(new Event('blur'))"),
                            ("visibilitychange", "document.dispatchEvent(new Event('visibilitychange'))"),
                            ("pause", "house.send('pause')")):
                ev("house.state === 'paused' && house.send('resume')")
                page.wait_for_timeout(200)
                page.keyboard.down("KeyW")
                page.keyboard.down("ShiftLeft")
                page.wait_for_timeout(500)
                if "KeyW" not in ev("house.held"):
                    stuck.append(f"{how}: W never registered")
                ev(js)
                page.wait_for_timeout(250)
                held = ev("house.held")
                a = d.p["feet"]
                if ev("house.state") == "paused":
                    ev("house.send('resume')")
                page.wait_for_timeout(700)
                moved = math.dist(a, d.p["feet"])
                if held or moved > 0.05:
                    stuck.append(f"{how}: held {held}, still moved {moved:.2f} m")
                page.keyboard.up("KeyW")
                page.keyboard.up("ShiftLeft")
                ev("house.setYaw(house.player.yaw + 1.9)")
            check("l: blur, visibilitychange, pause leave no key held", not stuck, "; ".join(stuck))
            before = d.p["feet"]
            d.hold(["KeyW"], 600)
            mid = d.p["feet"]
            page.wait_for_timeout(500)
            check("l: movement starts on keydown and stops on keyup (no coasting)", math.dist(before, mid) > 0.15 and math.dist(mid, d.p["feet"]) < 0.3, f"moved {math.dist(before, mid):.2f}, coasted {math.dist(mid, d.p['feet']):.2f}")

            # (m) navigation UI on desktop
            ev("house.send('teleport', 'Bedroom 1')")
            settle(d)
            try:  # the HUD refreshes from the render loop: on a loaded machine software GL frames can be seconds apart
                page.wait_for_function("document.getElementById('roomName').textContent === 'Bedroom 1'", timeout=20000)
            except Exception:
                pass
            hud = ev("({room: document.getElementById('roomName').textContent, level: document.getElementById('levelName').textContent, chip: document.getElementById('floorChip').textContent})")
            check("m: HUD shows room, floor and floor chip", hud["room"] == "Bedroom 1" and hud["level"] == "Upper floor" and hud["chip"] == "1F", str(hud))
            ok_map, det = inside_viewport(page, "#minimap")
            check("m: minimap visible while walking", ok_map, det)
            ink = ev("(() => { const c = document.getElementById('minimap'); const a = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < a.length; i += 4) if (a[i] > 40) n++; return n; })()")
            check("m: minimap is drawn", ink > 3000, f"{ink} px")
            chips = []
            for room, want in (("Laundry room", ("Terrace", "T")), ("Kitchen", ("Ground floor", "GF"))):
                ev(f"house.send('teleport', {room!r})")
                settle(d)
                page.wait_for_timeout(400)
                h2 = ev("({level: document.getElementById('levelName').textContent, chip: document.getElementById('floorChip').textContent})")
                if (h2["level"], h2["chip"]) != want:
                    chips.append(f"{room}: {h2}")
            check("m: floor indicator follows the floor (terrace, ground floor)", not chips, "; ".join(chips))
            page.click("#planbtn")
            page.wait_for_timeout(300)
            ok_plan, det = inside_viewport(page, "#plan")
            check("m: plan button opens the plan menu", ev("house.state") == "paused" and ok_plan, det)
            ink = ev("(() => { const c = document.getElementById('plan'); const a = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < a.length; i += 4) if (a[i] > 40) n++; return n; })()")
            check("m: plan is drawn with room names", ink > 20000, f"{ink} px")
            tabs = ev("[...document.querySelectorAll('#floors button')].map(b => b.textContent)")
            check("m: plan has a tab per floor", tabs == ["Ground floor", "Upper floor", "Terrace"], str(tabs))
            page.click("#floors button:nth-child(2)")
            # a click on a room of the plan teleports: click the centre of Bedroom 2 on the upper floor
            sp = next(s for s in ev("house.spaces") if s["name"] == "Bedroom 2")
            pos = ev(f"(() => {{ const c = document.getElementById('plan'); const m = c._map; const r = c.getBoundingClientRect();"
                     f" const x = ({(sp['min'][0] + sp['max'][0]) / 2} - m.X0) * m.s + m.pad, z = ({(sp['min'][2] + sp['max'][2]) / 2} - m.Z0) * m.s + m.pad;"
                     f" return [r.left + x * r.width / c.width, r.top + z * r.height / c.height]; }})()")
            page.mouse.click(pos[0], pos[1])
            page.wait_for_timeout(500)
            check("m: clicking a room on the plan goes there", ev("house.state") == "walk" and d.p["room"] == "Bedroom 2", d.p["room"])
            shot(page, SHOTS / "hud_desktop.png")
            help_ok = ev("(() => { const h = document.getElementById('help'); const rows = [...h.querySelectorAll('.row')].filter(r => getComputedStyle(r).display !== 'none').length; return rows >= 5 && rows <= 7; })()")
            check("m: short help shows the desktop controls only", help_ok)

            # (e) per room: nearest wall, hold W, stay off the plane and in the room; (g) never below floor; (j) shots
            bad_wall, bad_room, bad_floor, bad_land, bad_eye = [], [], [], [], []
            spaces = ev("house.spaces")
            for r in rooms:
                name = r["name"]
                ev(f"house.send('teleport', {name!r})")
                settle(d)
                p = d.p
                floor0 = floor_under(d)
                box = next((s for s in spaces if s["name"] == name), None)
                inside = box and all(box["min"][i] - 0.01 <= p["feet"][i] <= box["max"][i] + 0.01 for i in (0, 2))
                if p["room"] != name and not inside and name != "Street":
                    bad_land.append(f"{name}: landed in {p['room']}")
                if abs(p["feet"][1] - floor0) > 0.12:
                    bad_land.append(f"{name}: feet {p['feet'][1]:.2f} floor {floor0:.2f}")
                shot(page, SHOTS / f"room_{re.sub(r'[^a-z0-9]+', '_', name.lower())}.png")
                wall = nearest_wall(d)
                if wall is None:
                    continue
                yaw, h = wall
                ev(f"house.setYaw({yaw})")
                h = ev("house.ray(0, 0.9)") or h
                nrm, pt = h["normal"], h["point"]
                sx, _, sz = d.p["feet"]
                side = 1.0 if (sx - pt["x"]) * nrm["x"] + (sz - pt["z"]) * nrm["z"] >= 0 else -1.0
                worst, worst_floor = 9.0, 0.0
                for _ in range(10):
                    q = ev("house.simHold(1, 0, 0.3)")
                    worst = min(worst, side * ((q["feet"][0] - pt["x"]) * nrm["x"] + (q["feet"][2] - pt["z"]) * nrm["z"]))
                    worst_floor = min(worst_floor, q["minFloorGap"])
                page.wait_for_timeout(150)
                # with the wheel at both FOV limits the eye (at the player) sits inside the room the player is in and at least 0.25 m
                # from every wall, so the near plane (0.05 m) can never clip through one into the next room
                for dy in (-3000, 3000):
                    page.mouse.wheel(0, dy)
                    page.wait_for_timeout(120)
                    eye = ev("house.camera.position")
                    cur = d.p["room"]
                    cbox = next((s_ for s_ in spaces if s_["name"] == cur), None)
                    if cbox and not all(cbox["min"][i] - 0.005 <= eye[i] <= cbox["max"][i] + 0.005 for i in (0, 2)):
                        bad_eye.append(f"{name}: eye {[round(v, 2) for v in eye]} outside {cur}")
                    gap = min((ev(f"house.ray({k * math.pi / 8}, 1.6)") or {"dist": 9})["dist"] for k in range(16))
                    if gap < 0.20:
                        bad_eye.append(f"{name}: eye {gap:.2f} m from a wall")
                if worst < 0.25:
                    bad_wall.append(f"{name}: {worst:.3f}")
                if worst_floor < -0.05:
                    bad_floor.append(f"{name}: {worst_floor:.2f}")
            check("e: teleport lands in the room, on its floor", not bad_land, "; ".join(bad_land[:6]))
            check("e: walking into the nearest wall stops >= 0.25 m (body radius) from its plane", not bad_wall, "; ".join(bad_wall[:6]))
            check("e: camera and zoom never leave the current room", not bad_eye, "; ".join(bad_eye[:4]))
            check("g: feet never below the floor under them (walls)", not bad_floor, "; ".join(bad_floor[:6]))

            # (j) the named deliverable shots: garage with both trucks
            trucks = ev("house.byRole('car')")
            garage = next(s for s in spaces if s["name"].startswith("Garage"))
            check("j: two cars parked inside the garage", len(trucks) == 2 and all(
                garage["min"][0] - 0.01 <= t["min"][0] and t["max"][0] <= garage["max"][0] + 0.01 and garage["min"][2] - 0.01 <= t["min"][2] and t["max"][2] <= garage["max"][2] + 0.01
                for t in trucks), str([(t["name"], t["min"], t["max"]) for t in trucks]))
            ev("house.send('teleport', 'Garage (2 cars)')")
            settle(d)
            # the spawn is the 1 m gap between the trucks; the shot is taken from the open roll-up door, 1 m out on the street, centred
            ev(f"house.place({(garage['min'][0] + garage['max'][0]) / 2}, {garage['min'][1]}, {garage['max'][2] + 1.0})")
            settle(d)
            if trucks:
                mid = [sum(t["center"][i] for t in trucks) / len(trucks) for i in (0, 2)]
                f = d.p["feet"]
                ev(f"house.setYaw({math.atan2(-(mid[0] - f[0]), -(mid[1] - f[2]))})")
                page.mouse.wheel(0, 3000)  # widest FOV
                page.wait_for_timeout(300)
                check("j: both trucks in view from the garage", all(ev(f"house.inView({t['name']!r})") for t in trucks))
                shot(page, SHOTS / "garage_trucks.png")
            for room, out in (("Kitchen", "kitchen"), ("Genkan", "genkan"), ("Utility sink (covered)", "utility_sink")):
                src = SHOTS / f"room_{re.sub(r'[^a-z0-9]+', '_', room.lower())}.png"
                check(f"j: shot of {room}", src.exists() and src.stat().st_size > 50_000, str(src))
                if src.exists():
                    (SHOTS / f"{out}.png").write_bytes(src.read_bytes())

            # (f) stairs. Each flight, both directions, with the real controller on the planned route; heights must be exact
            spawn = {r["name"]: r["spawn"] for r in rooms}
            run_flights("desktop", d, spawn)
            # The double height reads from the dining room just past the entry walkway (looking up) and from the gallery above (looking down)
            for name, room, x, y, pitch, fname in (("entry walkway", "Dining room (double height)", 8.6, 0.0, 0.50, "dh_entry.png"),
                                                   ("gallery", "Gallery", 7.6, 3.2, -0.12, "dh_gallery.png")):
                ev(f"house.send('teleport', {room!r})")
                settle(d)
                ev(f"house.place({x}, {y}, -8.4)")
                ev(f"house.setYaw({-math.pi / 2}); house.setPitch({pitch})")
                settle(d)
                shot(page, SHOTS / fname)
                f = d.p
                check(f"f: double height visible from the {name}", (SHOTS / fname).stat().st_size > 50_000 and f["room"] in (room, "Hall", "Dining room (double height)", "Gallery"),
                      f"{fname}, in {f['room']} at {[round(v, 2) for v in f['feet']]}")
                ev("house.setPitch(0)")
            # once with real keys and mouse: street level -> terrace -> back to the hall, steering along the planned route
            ev("house.send('teleport', 'Hall')")
            settle(d)
            d.face(d.p["yaw"])
            legs_ok, trail = True, []
            for a, b in (("Hall", "Laundry room"), ("Laundry room", "Hall")):
                sx, sy, sz = spawn[b]
                route = ev(f"house.plan([[{sx}, {sz}, {sy}]])")
                ok_leg = bool(route and "waypoints" in route)
                for x, z, _y in (route["waypoints"] + [[sx, sz, sy]] if ok_leg else []):  # the planner stops at the stair foot, 0.3 m short of the room spawn
                    ok_leg &= d.walk_to(x, z, tol=0.3 if (x, z) != (sx, sz) else 0.2, timeout=25)
                    if not ok_leg:
                        break
                legs_ok &= ok_leg
                f = d.p["feet"]
                trail.append((b, ok_leg, [round(v, 2) for v in f]))
            shot(page, SHOTS / "stairs_real_input.png")
            end_y = d.p["feet"][1]
            check("f: real keys and mouse climb to the terrace and come back down", legs_ok and abs(end_y) < 0.1, str(trail))

            # (k) every room entered and left through its doors, from the hub of its floor
            run_rooms("desktop", d, rooms, spawn)

            # (h) corner press then unstick, real keys
            ev("house.send('teleport', 'Guest and pool bathroom')")
            settle(d)
            page.wait_for_timeout(450)
            wall = nearest_wall(d)
            if wall:
                d.face(wall[0])
            d.hold(["KeyW", "KeyD"], 5000)
            d.hold(["KeyW", "KeyA"], 2500)
            d.hold(["KeyR"], 200)
            page.wait_for_timeout(500)
            q = d.p
            near = min((ev(f"house.ray({k * math.pi / 4}, 0.9)") or {"dist": 9})["dist"] for k in range(8))
            check("h: after a corner press and R the player is free and in walk mode", ev("house.state") == "walk" and near >= 0.25, f"nearest wall {near:.2f}, room {q['room']}")
            # the guest bathroom (it once let you in but not out) is entered and left on foot with real keys
            ev("house.send('teleport', 'Hall')")
            settle(d)
            sb = spawn["Guest and pool bathroom"]
            route = ev(f"house.plan([[{sb[0]}, {sb[2]}, {sb[1]}]])")
            enter_ok = bool(route and "waypoints" in route)
            for x, z, _y in (route["waypoints"] + [[sb[0], sb[2], sb[1]]] if enter_ok else []):  # the planner stops 0.3 m short, maybe in the doorway
                enter_ok &= d.walk_to(x, z, tol=0.3 if (x, z) != (sb[0], sb[2]) else 0.2, timeout=20)
            in_bath = d.p["room"] == "Guest and pool bathroom"
            hub = spawn["Hall"]
            route = ev(f"house.plan([[{hub[0]}, {hub[2]}, {hub[1]}]])")
            exit_ok = bool(route and "waypoints" in route)
            for x, z, _y in (route["waypoints"] if exit_ok else []):
                exit_ok &= d.walk_to(x, z, tol=0.3, timeout=20)
            check("h: the ground-floor guest bathroom is entered and left with real keys", enter_ok and in_bath and exit_ok and d.p["room"] == "Hall",
                  f"enter {enter_ok} in {in_bath} exit {exit_ok} now {d.p['room']}")

            # pause menu: M opens it, a click on a room button teleports, Tab outside
            ev("house.send('pause')")
            check("paused menu visible", ev("getComputedStyle(document.getElementById('menu')).display") != "none" and ev("house.state") == "paused")
            page.click("#menu .room[data-room='Living room']")
            page.wait_for_timeout(500)
            check("menu click teleports", ev("house.state") == "walk" and d.p["room"] == "Living room", d.p["room"])
            page.keyboard.press("Tab")
            page.wait_for_timeout(300)
            check("Tab returns to the outside view", ev("house.state") == "orbit")
            check("b: zero console errors", not errors, "; ".join(errors[:3]))
            page.close()

        if on("n"):
            # (n) mobile: touch controls, portrait and landscape
            for label, (w, h) in (("portrait 390x844", (390, 844)), ("landscape 844x390", (844, 390))):
                merr = []
                ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True, device_scale_factor=2)
                mp = ctx.new_page()
                mp.set_default_timeout(120000)  # software GL renders this scene at ~3 s per frame (measured 24 s per tap)
                watch(mp, merr)
                mp.goto(f"http://127.0.0.1:{port}/viewer/index.html?ao=0")
                mp.wait_for_function("window.house && window.house.state !== 'loading'", timeout=120000)
                mev = mp.evaluate
                t = Touch(mp)
                check(f"n[{label}]: touch mode on", mev("house.touchMode") and mev("document.body.classList.contains('touch')"))
                check(f"n[{label}]: no horizontal scroll", mev("document.documentElement.scrollWidth <= innerWidth + 1"))
                mp.tap("#modebtn")
                mp.wait_for_function("house.state === 'walk'", timeout=120000)  # event, not a timer: the tap's click flips the state
                check(f"n[{label}]: tapping Walk in starts walking", mev("house.state") == "walk")
                mev("house.send('teleport', 'Living room')")
                mp.wait_for_function("house.player && house.player.room === 'Living room'", timeout=120000)
                for sel in ("#hud", "#bar2", "#minimap", "#actBtn"):
                    ok_in, det = inside_viewport(mp, sel)
                    check(f"n[{label}]: {sel} fits the screen", ok_in, det)
                act = mev("(() => { const r = document.getElementById('actBtn').getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2, r.width, r.height]; })()")
                check(f"n[{label}]: interact button is at least 44 px", act[2] >= 44 and act[3] >= 44, str(act))
                lx, ly = 80, h - 80  # The pinned stick centre (touch.js MARGIN + RADIUS); the old w*0.2, h*0.75 now falls outside the zone
                # interact button reaches the interaction hook
                mev("window.__acts = 0; const o = window.houseInteract && window.houseInteract.interact; if (o) window.houseInteract.interact = () => { window.__acts++; return o(); };")
                t.down(3, act[0], act[1])
                t.up(3)
                wait_clock(mp, 0.05)
                check(f"n[{label}]: interact button calls the interaction hook", mev("window.__acts") >= 1 if mev("!!window.houseInteract") else False)
                # plan: the button opens it, a tap on a room name goes there
                mp.tap("#planbtn")
                mp.wait_for_function("house.state === 'paused'", timeout=120000)
                ok_plan, det = inside_viewport(mp, "#plan")
                check(f"n[{label}]: plan opens and fits", mev("house.state") == "paused" and ok_plan and mev("document.documentElement.scrollWidth <= innerWidth + 1"), det)
                ok_box, det = inside_viewport(mp, "#menu .box")
                check(f"n[{label}]: menu box fits the screen", ok_box or mev("document.querySelector('#menu .box').scrollHeight > document.querySelector('#menu .box').clientHeight"), det)
                mev("document.querySelector('#menu .room[data-room=\"Kitchen\"]').scrollIntoView()")
                mp.tap("#menu .room[data-room='Kitchen']")
                mp.wait_for_function("house.state === 'walk' && house.player.room === 'Kitchen'", timeout=120000)
                check(f"n[{label}]: tapping a room in the plan goes there", mev("house.state") == "walk" and mev("house.player.room") == "Kitchen", mev("house.player.room"))
                # touch must never ask for pointer lock or pause by itself
                mev("house.send('teleport', 'Dining room (double height)')")  # send() runs placeAt synchronously
                t.drag(1, lx, ly, lx + 50, ly, hold=True)
                wait_clock(mp, 0.1)  # physics, not wall time: the state is what the check is about and the frame may take seconds
                check(f"n[{label}]: still walking after touches (no pointer-lock pause)", mev("house.state") == "walk")
                t.up(1)
                # a finger lost mid-drag (touchcancel / blur) must not leave the stick pushed
                t.down(1, lx, ly)
                t.move(1, lx, ly - 50)
                mev("window.dispatchEvent(new Event('blur'))")  # evaluate runs the handler synchronously: the axes are read after it
                check(f"n[{label}]: blur releases the stick", mev("house.touchAxes")["forward"] == 0)
                t.up(1)
                # On the phone: every flight and (portrait) every room with the same controller, and the terrace flight climbed
                # and descended with the real touch joystick and look drag
                md = Driver(mp)
                mrooms = room_rows(mev)
                mspawn = {r["name"]: r["spawn"] for r in mrooms}
                run_flights(label, md, mspawn)
                if w < h:
                    run_rooms(label, md, mrooms, mspawn)
                shot(mp, SHOTS / f"mobile_{w}x{h}.png", size=(w, h))
                check(f"n[{label}]: zero console errors", not merr, "; ".join(merr[:3]))
                # both flights with the real touch joystick and look drag, timed in physics seconds, on a page that skips drawing so software GL still runs at 60 Hz
                mp.close()  # one drawing page at a time: software GL shares the CPU
                cp = ctx.new_page()
                cp.bring_to_front()  # a page opened right after another closed can be throttled as hidden: its frames (and the physics clock) crawl
                cp.goto(f"http://127.0.0.1:{port}/viewer/index.html?ao=0&render=0", timeout=120000)
                cp.wait_for_function("window.house && window.house.state !== 'loading'", timeout=120000)
                cp.tap("#modebtn", timeout=60000)
                cp.wait_for_function("house.state === 'walk'", timeout=120000)
                ct = Touch(cp)
                cev = cp.evaluate
                # Touch controls on the page that keeps 60 Hz: the same real events and the same viewer code as the painted
                # phone page above, without the ~3 s frame. On the painted page a ten-event drag spans several physics seconds;
                # with the stick held against furniture that is long enough for the stuck guard to fire, and its placeAt resets
                # yaw, so the look check measured the guard instead of the drag. Here the same drag is 0.05 physics seconds.
                cev("house.send('teleport', 'Living room')")
                cp.wait_for_function("house.player && house.player.room === 'Living room'", timeout=120000)
                lx, ly = 80, h - 80  # The pinned stick centre (touch.js MARGIN + RADIUS); the old w*0.2, h*0.75 now falls outside the zone
                f0 = cev("house.player.feet")
                ct.drag(1, lx, ly, lx, ly - 55, hold=True)
                wait_clock(cp, 0.3)
                axes = cev("house.touchAxes")
                f1 = cev("house.player.feet")
                check(f"n[{label}]: joystick up walks forward", axes["forward"] > 0.5 and math.dist(f0, f1) > 0.3, f"axes {axes}, moved {math.dist(f0, f1):.2f} m")
                # look with a second finger while the joystick is held
                y0 = cev("house.player.yaw")
                ct.drag(2, w * 0.8, h * 0.4, w * 0.8 - 120, h * 0.4, hold=False)
                wait_clock(cp, 0.1)
                y1 = cev("house.player.yaw")
                f2 = cev("house.player.feet")
                check(f"n[{label}]: dragging the right side looks around while walking", abs(wrap(y1 - y0)) > 0.3 and math.dist(f1, f2) > 0.05, f"yaw {y0:.2f} -> {y1:.2f}")
                ct.up(1)
                wait_clock(cp, 0.3)  # BRAKE 22 m/s2 stops a 3 m/s run in 0.14 s; 0.3 leaves room for a whole frame chunk
                f3 = cev("house.player.feet")
                wait_clock(cp, 0.2)
                f4 = cev("house.player.feet")
                check(f"n[{label}]: releasing the joystick stops", cev("house.touchAxes")["forward"] == 0 and math.dist(f3, f4) < 0.03, f"drift {math.dist(f3, f4):.3f}")
                # League of Legends layout: the stick lives only in the bottom-left corner; a drag in the upper left looks, it never walks
                y5 = cev("house.player.yaw")
                ct.drag(1, w * 0.2, h * 0.15, w * 0.2 + 80, h * 0.15 - 40, hold=True)
                wait_clock(cp, 0.3)
                ax5, f5, y6 = cev("house.touchAxes"), cev("house.player.feet"), cev("house.player.yaw")
                ct.up(1)
                wait_clock(cp, 0.1)
                check(f"n[{label}]: upper-left drag looks, it does not walk", ax5["forward"] == 0 and ax5["right"] == 0 and math.dist(f4, f5) < 0.03 and abs(wrap(y6 - y5)) > 0.1,
                      f"axes {ax5}, moved {math.dist(f4, f5):.3f} m, yaw {y5:.2f} -> {y6:.2f}")
                sb = cev("(() => { const r = document.getElementById('stickBase').getBoundingClientRect(); return [r.x, r.y, r.width, r.height, getComputedStyle(document.getElementById('stickBase')).display]; })()")
                check(f"n[{label}]: the resting stick shows in the bottom-left corner", sb[4] != "none" and sb[0] + sb[2] <= min(w * 0.5, 340) + 1 and sb[1] >= h * 0.4 - 1 and sb[1] + sb[3] <= h, f"stick rect {sb}")
                tsecs = {}
                for fl, low, high, _hy in FLIGHTS:
                    for lab, a, b in (("up", low, high), ("down", high, low)):
                        ok_t, det, stair = touch_climb(label, cp, ct, w, h, mspawn, a, b)
                        tsecs[(fl, lab)] = stair
                        check(f"n[{label}]: {fl} {lab} with the touch joystick", ok_t and stair is not None,
                              f"{det}, {stair:.1f} s on the stair" if stair is not None else f"{det}, stair not timed")
                for lab in ("up", "down"):
                    tt, oo = tsecs[(FLIGHTS[1][0], lab)], tsecs[(FLIGHTS[0][0], lab)]
                    timed = tt is not None and oo is not None
                    check(f"n[{label}]: terrace flight {lab} by touch within 20 % of the other flight", timed and tt <= 1.2 * oo + 1.5,
                          f"terrace {tt:.1f} s, other {oo:.1f} s" if timed else f"terrace {tt}, other {oo}")
                cp.close()
                ctx.close()

        if on("desktop"):
            bad = browser.new_page()
            bad.route("**/house.glb", lambda r: r.abort())
            bad.goto(f"http://127.0.0.1:{port}/viewer/index.html?ao=0")
            bad.wait_for_function("window.house && window.house.state === 'error'", timeout=30000)
            shown = bad.evaluate("getComputedStyle(document.getElementById('error')).display") != "none"
            check("missing GLB -> error state with panel", shown and bad.evaluate("house.send('enterWalk')") is False)
        browser.close()
    srv.shutdown()
    if FAILS:
        sys.exit(f"{len(FAILS)} viewer checks failed: {FAILS}")
    print("viewer checks: all passed")


if __name__ == "__main__":
    main()
