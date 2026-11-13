"""Headless proof of the openable parts (viewer/interact.js, contract docs/glb_contract.md "Openable parts").

Real keyboard and mouse input in Chromium against build/casa.glb: the player stands in front of a part, aims with the mouse,
presses E (or taps on a phone viewport) and the test measures how far the part really moved.
 a  room door: E closes it (yaw turns by close_deg), a closed door blocks the player, E opens it, the player walks through
 b  drawers: a sample of drawers from different furniture slide by `slide`, E again closes them, the tray is modelled
 c  wardrobe doors swing by `swing` and the cabinet is a real cavity
 d  refrigerator door swings and shows a visible interior
 e  phone viewport 390x844: a tap on a drawer opens it
 f  every openable in the GLB has the extras the contract demands
Run: uv run --no-project --python 3.12 --with playwright python viewer/test_interact.py
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

ROOT = Path(__file__).resolve().parents[1]
LOOK = 0.0022  # radians per mouse pixel, same constant as main.js
WIRE = "import('./interact.js').then((m) => m.initInteract(scene, camera, renderer, { world: world.phys.world }));"
FAILS = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name} {detail}", flush=True)
    if not ok:
        FAILS.append(name)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def translate_path(self, path):
        # CASA_GLB=build/web/casa.glb runs the same tests on the web copy the site serves (R11)
        if os.environ.get("CASA_GLB") and path.split("?")[0] == "/build/casa.glb":
            return str(ROOT / os.environ["CASA_GLB"])
        return super().translate_path(path)


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def new_page(browser, port, errors, **kw):
    page = browser.new_page(**kw)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "ointer" not in m.text else None)

    def patch(route):  # until main.js calls initInteract itself, wire it here (the line C1 adds to main.js)
        body = route.fetch().text()
        if "initInteract" not in body:
            body = body.replace("buildMenu();", "buildMenu();\n  " + WIRE, 1)
        route.fulfill(body=body, content_type="text/javascript")

    page.route(re.compile(r".*/viewer/main\.js"), patch)
    # 49 MB GLB: while another page still renders under swiftshader, the load event can exceed the 30 s default.
    # The real gate is the app state below, which waits for the GLB and the physics world.
    page.goto(f"http://127.0.0.1:{port}/viewer/index.html?ao=0", wait_until="commit", timeout=120000)
    page.wait_for_function("window.casa && window.casa.state !== 'loading' && window.casaInteract", timeout=180000)
    return page


class Player:
    def __init__(self, page):
        self.page = page
        w, h = page.viewport_size["width"], page.viewport_size["height"]
        self.w, self.h = w, h
        self.mx, self.my = w // 2, h // 2

    def ev(self, js):
        return self.page.evaluate(js)

    @property
    def p(self):
        return self.ev("casa.player")

    def _mouse(self, dx_px=0, dy_px=0):
        """Move the mouse by whole pixels in steps the viewer accepts; at an edge jump (over 250 px is ignored as a spike)."""
        for axis, px, lo, hi in ((0, dx_px, 40, self.w - 40), (1, dy_px, 40, self.h - 40)):
            while abs(px) > 1:
                step = max(-100, min(100, px))
                cur = self.mx if axis == 0 else self.my
                if not lo <= cur + step <= hi:
                    jump = (lo + 10) if step > 0 else (hi - 10)
                    if abs(jump - cur) < 260:  # window too small to jump: park in the middle instead
                        jump = (lo + hi) // 2
                    self._set(axis, jump)
                    cur = jump
                self._set(axis, cur + step)
                px -= step

    def _set(self, axis, v):
        if axis == 0:
            self.mx = int(v)
        else:
            self.my = int(v)
        self.page.mouse.move(self.mx, self.my)

    def aim(self, yaw, pitch):
        for _ in range(8):
            p = self.p
            ey, ep = wrap(yaw - p["yaw"]), pitch - p["pitch"]
            if abs(ey) < 0.01 and abs(ep) < 0.01:
                return
            self._mouse(-ey / LOOK, -ep / LOOK)
            self.page.wait_for_timeout(60)

    def settle(self, secs=20):
        for _ in range(int(secs * 5)):
            if self.ev("casaInteract.settled"):
                return True
            self.page.wait_for_timeout(200)
        return False

    def walk(self, key, ms):
        """Hold key until the player stops (blocked) or has covered 3.5 m; ms is the longest wait. Software GL is slow, so
        wall-clock time alone says nothing about how far a free player would have gone."""
        self.page.keyboard.down(key)
        start = last = self.p["feet"]
        frames0 = self.ev("casa.frames")
        t = 0
        while t < max(ms, 30000):
            self.page.wait_for_timeout(100)
            t += 100
            f = self.p["feet"]
            frames = self.ev("casa.frames")
            if math.dist(f, last) >= 0.01:
                last, frames0 = f, frames
            elif frames - frames0 >= 8 and t >= 600:  # 8 rendered frames without moving: blocked (before the viewer's 2 s unstick fires)
                break
            if math.dist(f, start) > 3.5:
                break
        self.page.keyboard.up(key)


def stand_before(pl, name, dists=(1.0, 0.8, 1.2, 0.6, 1.4)):
    """Place the player so the hover ray lands on `name`; returns True when casaInteract.target == name."""
    fr = pl.ev(f"casaInteract.frame({name!r})")
    c = [(a + b) / 2 for a, b in zip(fr["min"], fr["max"])]
    # Aim points: the middle of the part's box, then the middle of its front face. A desk drawer's box middle sits
    # under the desk top, so from standing height the ray to it meets the top first (Escritorio_estudio__drawer_0);
    # a person aims at the face. The pass condition is unchanged: the hover ray must name this part.
    half = sum(abs(f) * (b - a) / 2 for f, a, b in zip(fr["front"], fr["min"], fr["max"]))
    face = [m + f * half for m, f in zip(c, fr["front"])]
    for dist in dists:
        for k in range(24):
            a = k * math.pi / 12
            x, z = c[0] + dist * math.cos(a), c[2] + dist * math.sin(a)
            fy = pl.ev(f"casa.groundAt({x}, {z}, {fr['min'][1] + 1.0})")
            if not fy or abs(fy["y"] - fr["min"][1]) > 0.7 or pl.ev(f"casa.clearAt({x}, {z}, {fr['min'][1] + 1.0}, 0.0)") is None:
                continue
            pl.ev(f"casa.place({x}, {fy['y'] + 0.02}, {z})")
            for p in (c, face):
                yaw = math.atan2(-(p[0] - x), -(p[2] - z))
                pl.ev(f"casa.setYaw({yaw})")
                pitch = math.atan2(p[1] - (fy["y"] + 1.6), math.hypot(p[0] - x, p[2] - z))
                pl.aim(yaw, max(-1.4, min(1.4, pitch)))
                pl.page.wait_for_timeout(350)
                if pl.ev("casaInteract.target") == name:
                    return True
    return False


def stand_before_window(pl, name, level, dists=(1.0, 0.8, 1.2, 1.4)):
    """A window hangs above the floor, so stand_before's floor test cannot be used: the floor is 3.2 m per level."""
    fr = pl.ev(f"casaInteract.frame({name!r})")
    return stand_at(pl, name, fr, 3.2 * level, dists)


def stand_at(pl, name, fr, floor, dists=(1.0, 0.8, 1.2, 1.4)):
    c = [(a + b) / 2 for a, b in zip(fr["min"], fr["max"])]
    for dist in dists:
        for k in range(24):
            a = k * math.pi / 12
            x, z = c[0] + dist * math.cos(a), c[2] + dist * math.sin(a)
            fy = pl.ev(f"casa.groundAt({x}, {z}, {floor + 2.0})")
            if not fy or abs(fy["y"] - floor) > 0.35 or pl.ev(f"casa.clearAt({x}, {z}, {floor + 1.0}, 0.0)") is None:
                continue
            pl.ev(f"casa.place({x}, {fy['y'] + 0.02}, {z})")
            yaw = math.atan2(-(c[0] - x), -(c[2] - z))
            pl.ev(f"casa.setYaw({yaw})")
            pitch = math.atan2(c[1] - (fy["y"] + 1.6), dist)
            pl.aim(yaw, max(-1.4, min(1.4, pitch)))
            pl.page.wait_for_timeout(350)
            if pl.ev("casaInteract.target") == name:
                return True
    return False


def aimed_at(pl, name, ms=1500):
    """Wait for the hint to name `name`: on touch the hint is the screen-centre ray, and the camera follows the aim on the
    next rendered frame."""
    try:
        pl.page.wait_for_function(f"casaInteract.target === {name!r}", timeout=ms)
        return True
    except Exception:
        return False


def press_e_and_measure(pl, name):
    before = pl.ev(f"casaInteract.pose({name!r})")
    pl.page.keyboard.press("KeyE")
    done = pl.settle()
    after = pl.ev(f"casaInteract.pose({name!r})")
    return before, after, done


def dist3(a, b):
    return math.dist(a, b)


FIXTURE = """<!DOCTYPE html><html><head><script type="importmap">{"imports":{"three":"/viewer/vendor/three/build/three.module.js",
"@dimforge/rapier3d-compat":"/viewer/vendor/rapier/rapier.mjs"}}</script></head><body><script type="module">
import * as THREE from 'three';
import { initInteract } from '/viewer/interact.js';
const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(60, 1, 0.05, 50);
const renderer = new THREE.WebGLRenderer(); renderer.setSize(innerWidth, innerHeight); camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
document.body.style.margin = '0'; document.body.appendChild(renderer.domElement);
const mat = new THREE.MeshBasicMaterial();
const box = (w, h, d, x, y, z) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, z); return m; };
// a 0.6 m wide cabinet facing +Z (glTF), turned 90 degrees about Y: one drawer, a door hinged left, a door hinged right
const cab = new THREE.Group(); cab.name = 'Fix'; cab.rotation.y = Math.PI / 2; cab.position.set(2, 0, 0); scene.add(cab);
cab.add(box(0.6, 0.8, 0.5, 0, 0.4, 0));
const mk = (name, x, extras) => { const n = new THREE.Group(); n.name = name; n.position.set(x, 0.4, 0.25); n.userData = extras; cab.add(n); return n; };
const dr = mk('Fix__drawer_0', 0, { slide: 0.35 });
dr.add(box(0.5, 0.15, 0.02, 0, 0, 0.01), box(0.46, 0.02, 0.35, 0, -0.06, -0.17)); // front + tray bottom
const dl = mk('Fix__door_0', -0.3, { hinge: 'L', swing: 100 }); dl.add(box(0.28, 0.5, 0.02, 0.14, 0.1, 0));
const dR = mk('Fix__door_1', 0.3, { hinge: 'R', swing: 100 }); dR.add(box(0.28, 0.5, 0.02, -0.14, 0.1, 0));
camera.position.set(3.0, 1.0, 0); camera.lookAt(2, 0.5, 0);
window.casa = { state: 'walk', player: { feet: [9, 0, 9] } };
initInteract(scene, camera, renderer);
window.fx = { scene, THREE, camera };
window.ready = true;
</script></body></html>"""


def synthetic(browser, port):
    """g: the contract math on a cabinet turned 90 degrees: the drawer slides along its front normal, L and R doors swing outward."""
    (ROOT / "build").mkdir(exist_ok=True)
    (ROOT / "build" / "interact_fixture.html").write_text(FIXTURE)
    page = browser.new_page(viewport={"width": 400, "height": 300})
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(f"http://127.0.0.1:{port}/build/interact_fixture.html", wait_until="commit")
    page.wait_for_function("window.ready", polling=200, timeout=60000)
    pl = Player(page)
    for n, kind in (("Fix__drawer_0", "drawer"), ("Fix__door_0", "L"), ("Fix__door_1", "R")):
        c = pl.ev(f"casaInteract.pose({n!r})")["centre"]
        pl.ev(f"fx.camera.position.set({c[0] + 0.9}, {c[1]}, {c[2]}); fx.camera.lookAt({c[0]}, {c[1]}, {c[2]})")
        page.wait_for_timeout(350)
        check(f"g: the centre ray hovers {n}", pl.ev("casaInteract.target") == n, str(pl.ev("casaInteract.target")))
        b, a, done = press_e_and_measure(pl, n)
        if kind == "drawer":
            dx = a["centre"][0] - b["centre"][0]
            check("g: drawer slides 0.35 m along the cabinet front", done and abs(dx - 0.35) < 0.01 and abs(a["centre"][2] - b["centre"][2]) < 0.01, f"dx {dx:.3f}")
            o = [a["centre"][0], 2.0, a["centre"][2]]
            hit = pl.ev(f"casaInteract.shoot({o}, [0, -1, 0], 4)")
            check("g: the pulled-out drawer shows its tray", bool(hit) and hit["part"] == n, str(hit))
        else:
            turn = math.degrees(wrap(a["yaw"] - b["yaw"]))
            want = -100 if kind == "L" else 100
            check(f"g: {kind} door turns {want} degrees", done and abs(turn - want) < 1.0, f"turned {turn:.1f}")
            check(f"g: {kind} door opens toward the front", a["centre"][0] > b["centre"][0] + 0.05, f"{b['centre'][0]:.2f} -> {a['centre'][0]:.2f}")
    check("g: no page errors", not errs, str(errs))
    page.close()
    # a tap (touch pointer events) on the drawer opens it; a drag does not
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    mp = ctx.new_page()
    mp.goto(f"http://127.0.0.1:{port}/build/interact_fixture.html", wait_until="commit")
    mp.wait_for_function("window.ready", polling=200, timeout=60000)
    c = mp.evaluate("casaInteract.pose('Fix__drawer_0')")["centre"]
    mp.evaluate(f"fx.camera.position.set({c[0] + 0.9}, {c[1]}, {c[2]}); fx.camera.lookAt({c[0]}, {c[1]}, {c[2]})")
    mpl = Player(mp)
    x, y = mp.evaluate("casaInteract.screenOf('Fix__drawer_0')")
    mp.touchscreen.tap(x, y)
    mpl.settle()
    after = mp.evaluate("casaInteract.pose('Fix__drawer_0')")["centre"]
    check("g: a tap on a phone opens the drawer", abs(after[0] - c[0] - 0.35) < 0.01, f"dx {after[0] - c[0]:.3f}")
    ctx.close()


def main():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    args = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=args)
        page = new_page(browser, port, errors, viewport={"width": 1280, "height": 720})
        synthetic(browser, port)
        pl = Player(page)
        items = pl.ev("casaInteract.list()")
        by_kind = {}
        for it in items:
            by_kind.setdefault(it["kind"], []).append(it)
        print({k: len(v) for k, v in by_kind.items()}, flush=True)
        pl.ev("casa.send('enterWalk')")
        page.wait_for_timeout(500)

        # f: contract extras
        bad = [i["name"] for i in items if (i["kind"] == "drawer" and not i["slide"]) or
               (i["kind"] == "window" and not (i["slide"] or i["openDeg"] is not None)) or
               (i["kind"] not in ("drawer", "window") and i["closeDeg"] is None and not (i["swing"] and i["hinge"] in ("L", "R")))]
        check("f: every openable carries slide / hinge+swing / close_deg / window motion", not bad and len(items) > 0, f"{len(items)} parts, bad {bad[:5]}")

        # a: a room door
        doors = [i for i in by_kind.get("door", []) if i["closeDeg"] is not None]
        check("a: the GLB has animated room doors", len(doors) >= 5, f"{len(doors)}")
        door = next((d for d in doors if "baño social" in d["name"].replace("_", " ")), doors[0])
        name = door["name"]
        if stand_before(pl, name):
            b, a, done = press_e_and_measure(pl, name)
            turned = math.degrees(abs(wrap(a["yaw"] - b["yaw"])))
            check("a: E closes the room door by close_deg", done and abs(turned - abs(door["closeDeg"])) < 2.5, f"{name} turned {turned:.1f} of {door['closeDeg']}")
            # the closed leaf fills the opening: stand 1 m in front of its centre on the thin axis, face it, and walk
            fr = pl.ev(f"casaInteract.frame({name!r})")
            c = [(x + y) / 2 for x, y in zip(fr["min"], fr["max"])]
            thin = 0 if (fr["max"][0] - fr["min"][0]) < (fr["max"][2] - fr["min"][2]) else 2
            spot = None
            for off in (1.0, 0.8, 0.65):
                for side in (1, -1):
                    x, z = (c[0] + side * off, c[2]) if thin == 0 else (c[0], c[2] + side * off)
                    g = pl.ev(f"casa.groundAt({x}, {z}, {fr['min'][1] + 1.0})")
                    if g and abs(g["y"] - fr["min"][1]) < 0.3 and pl.ev(f"casa.clearAt({x}, {z}, {fr['min'][1] + 1.0}, 0.0)") is not None:
                        spot = (x, g["y"], z)
                        break
                if spot:
                    break
            check("a: a clear spot in front of the closed door", spot is not None, f"centre {c}, thin {thin}, min y {fr['min'][1]:.2f}")
            if spot:
                def face_door():
                    pl.ev(f"casa.place({spot[0]}, {spot[1] + 0.02}, {spot[2]})")
                    yaw = math.atan2(-(c[0] - spot[0]), -(c[2] - spot[2]))
                    pl.ev(f"casa.setYaw({yaw})")
                    pl.aim(yaw, 0.0)
                    pl.page.wait_for_timeout(350)

                def crossed_after(ms):
                    face_door()
                    pl.walk("KeyW", ms)
                    f = pl.p["feet"]
                    return (f[thin] - c[thin]) * (spot[thin] - c[thin]) < 0, math.dist(f, spot)

                crossed, moved = crossed_after(1800)
                check("a: a closed door blocks the player", not crossed and moved < 1.0, f"moved {moved:.2f} m")
                face_door()
                check("a: the hover finds the closed door", pl.ev("casaInteract.target") == name, str(pl.ev("casaInteract.target")))
                b2, a2, done2 = press_e_and_measure(pl, name)
                back = math.degrees(abs(wrap(a2["yaw"] - b2["yaw"])))
                check("a: E opens it again", done2 and abs(back - abs(door["closeDeg"])) < 2.5, f"turned back {back:.1f}")
                crossed, moved = crossed_after(2600)
                check("a: the open doorway lets the player through", crossed, f"moved {moved:.2f} m")
        else:
            check("a: found a standing spot facing the door", False, name)

        # b: drawers
        drawers = by_kind.get("drawer", [])
        check("b: the GLB has drawers", len(drawers) >= 10, f"{len(drawers)}")
        groups = {}
        for d in drawers:
            groups.setdefault(d["group"], []).append(d)
        sample = [g[0] for g in list(groups.values())[:: max(1, len(groups) // 4)][:4]]
        for d in sample:
            n = d["name"]
            if not stand_before(pl, n):
                check(f"b: aim at drawer {n}", False)
                continue
            b, a, done = press_e_and_measure(pl, n)
            moved = dist3(b["centre"], a["centre"])
            check(f"b: {n} slides by slide", done and abs(moved - d["slide"]) < 0.03, f"moved {moved:.3f} of {d['slide']}")
            # tray modelled: a ray dropped into the pulled-out part lands on a mesh of the drawer, below its top edge
            fo = pl.ev(f"casaInteract.frame({n!r})")
            depth = min(d["slide"] * 0.5, 0.15)
            o = [fo["origin"][0] - fo["front"][0] * depth, fo["max"][1] + 0.3, fo["origin"][2] - fo["front"][2] * depth]
            hit = pl.ev(f"casaInteract.shoot({o}, [0, -1, 0], 2)")
            drop = (fo["max"][1] + 0.3) - hit["dist"] if hit else None
            check(f"b: {n} has a tray", bool(hit) and hit["part"] == n and fo["max"][1] - drop >= 0.04, f"hit {hit}")
            b2, a2, done2 = press_e_and_measure(pl, n)
            check(f"b: {n} closes again", done2 and dist3(a2["centre"], b["centre"]) < 0.01)
        pl.ev("casaInteract.interact()")

        # c: wardrobe doors
        wardrobe = [i for i in by_kind.get("door", []) if i["closeDeg"] is None and re.search(r"closet|armario|ropero|vestidor|wardrobe", i["group"], re.I)]
        check("c: the GLB has wardrobe doors", len(wardrobe) >= 2, f"{len(wardrobe)}")
        for dr in wardrobe[:2]:
            n = dr["name"]
            if not stand_before(pl, n):
                check(f"c: aim at {n}", False)
                continue
            b, a, done = press_e_and_measure(pl, n)
            turned = math.degrees(abs(wrap(a["yaw"] - b["yaw"])))
            check(f"c: {n} swings by swing", done and abs(turned - dr["swing"]) < 2.5, f"turned {turned:.1f} of {dr['swing']}")
        # d: the refrigerator
        fridge = [i for i in by_kind.get("fridge_door", [])]
        check("d: the GLB has a refrigerator door", len(fridge) >= 1, f"{len(fridge)}")
        if fridge:
            f0 = fridge[0]
            n = f0["name"]
            if stand_before(pl, n):
                fr0 = pl.ev(f"casaInteract.frame({n!r})")
                mid = [(fr0["parentMin"][0] + fr0["parentMax"][0]) / 2, 1.0, (fr0["parentMin"][2] + fr0["parentMax"][2]) / 2]
                front = fr0["front"]
                o = [mid[0] + front[0] * 1.2, 1.0, mid[2] + front[2] * 1.2]
                closed_hit = pl.ev(f"casaInteract.shoot({o}, {[-front[0], 0, -front[2]]}, 3)")
                b, a, done = press_e_and_measure(pl, n)
                turned = math.degrees(abs(wrap(a["yaw"] - b["yaw"])))
                check("d: the fridge door swings by swing", done and abs(turned - f0["swing"]) < 2.5, f"turned {turned:.1f} of {f0['swing']}")
                open_hit = pl.ev(f"casaInteract.shoot({o}, {[-front[0], 0, -front[2]]}, 3)")
                check("d: the open fridge shows a cavity", bool(closed_hit and open_hit) and open_hit["dist"] - closed_hit["dist"] >= 0.30 and open_hit["part"] != n,
                      f"closed {closed_hit}, open {open_hit}")
            else:
                check("d: aim at the fridge door", False, n)

        # h: every floor has an operable window; E opens and closes it and the hint speaks Spanish
        windows = by_kind.get("window", [])
        levels = sorted({w["level"] for w in windows if w["level"] is not None})
        check("h: the GLB has openable window sashes on every floor", len(windows) >= 3 and levels == [0, 1, 2], f"{len(windows)} sashes, levels {levels}")
        for level in levels:
            ws = [w for w in windows if w["level"] == level]
            w = next((x for x in ws if x["apertura"] == "corrediza"), ws[0])
            n = w["name"]
            if not stand_before_window(pl, n, level):
                check(f"h: aim at window {n} (level {level})", False)
                continue
            hint = pl.ev("document.getElementById('interactHint')?.textContent ?? ''")  # target verified: this is the hint
            check("h: the window hint is Spanish and says ventana", "ventana" in hint.lower(), hint)
            b, a, done = press_e_and_measure(pl, n)
            moved = dist3(b["centre"], a["centre"])
            check(f"h: E opens window {n} (level {level})", done and moved > 0.05, f"moved {moved:.2f} m")
            fr = pl.ev(f"casaInteract.frame({n!r})")  # current (open) pose, not the stale closed box
            closed = stand_at(pl, n, fr, 3.2 * level)
            if closed:
                b2, a2, done2 = press_e_and_measure(pl, n)
                closed = done2 and dist3(a2["centre"], b["centre"]) < 0.03
            check(f"h: E closes window {n} again", closed)
        # e: phone, tap on a drawer, then on a window. Touch has no pointer, so the hint is the screen-centre ray: aim at
        # the middle of the drawer front face (a drawer node origin is the front-face centre at floor level), wait until
        # the hint names it, then tap the centre. Tapping the projected bounds centre can land on the drawer above,
        # because that centre lies inside the cabinet.
        mob = new_page(browser, port, errors, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
        mp = Player(mob)
        mp.ev("casa.send('enterWalk')")
        mob.wait_for_timeout(500)
        tapped = None
        for d in sample:
            n = d["name"]
            fr = mp.ev(f"casaInteract.frame({n!r})")
            pt = [fr["origin"][0], (fr["min"][1] + fr["max"][1]) / 2, fr["origin"][2]]
            for dist in (1.0, 0.8, 1.2):
                x, z = pt[0] + fr["front"][0] * dist, pt[2] + fr["front"][2] * dist
                g = mp.ev(f"casa.groundAt({x}, {z}, {fr['min'][1] + 1.0})")
                if not g or abs(g["y"] - fr["min"][1]) > 0.35 or mp.ev(f"casa.clearAt({x}, {z}, {fr['min'][1] + 1.0}, 0.0)") is None:
                    continue
                mp.ev(f"casa.place({x}, {g['y'] + 0.02}, {z})")
                yaw = math.atan2(-(pt[0] - x), -(pt[2] - z))
                mp.ev(f"casa.setYaw({yaw})")
                mp.ev(f"casa.setPitch({max(-1.4, min(1.4, math.atan2(pt[1] - (g['y'] + 1.6), dist)))})")
                if aimed_at(mp, n):
                    tapped = d
                    break
            if tapped:
                break
        if tapped:
            n = tapped["name"]
            b = mp.ev(f"casaInteract.pose({n!r})")
            mob.touchscreen.tap(mp.w // 2, mp.h // 2)
            done = mp.settle()
            a = mp.ev(f"casaInteract.pose({n!r})")
            moved = dist3(b["centre"], a["centre"])
            check("e: a tap on a phone opens the drawer", done and abs(moved - tapped["slide"]) < 0.03, f"{n} moved {moved:.3f} of {tapped['slide']}")
        else:
            check("e: aimed at a drawer on a phone", False, [d["name"] for d in sample])

        mwin = next((x for x in mp.ev("casaInteract.list()") if x["kind"] == "window" and x["level"] == 0), None)
        if mwin:
            n = mwin["name"]
            if stand_before_window(mp, n, 0):
                xy = mp.ev(f"casaInteract.screenOf({n!r})")
                if xy and 0 < xy[0] < 390 and 0 < xy[1] < 844:
                    b = mp.ev(f"casaInteract.pose({n!r})")
                    mob.touchscreen.tap(xy[0], xy[1])
                    done = mp.settle()
                    a = mp.ev(f"casaInteract.pose({n!r})")
                    check("e: a tap on a phone opens a window", done and dist3(b["centre"], a["centre"]) > 0.05,
                          f"moved {dist3(b['centre'], a['centre']):.3f} m")
                else:
                    check("e: the window is on screen on a phone", False, n)
            else:
                check("e: aim at a window on a phone", False, n)
        else:
            check("e: a window exists for the phone test", False)

        check("no console errors", not errors, str(errors[:3]))
        browser.close()
    print("FAILED:" if FAILS else "all interaction checks passed", FAILS or "")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
