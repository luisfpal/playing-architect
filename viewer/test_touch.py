"""Touch-route test, on the real GLB in headless Chromium with touch emulation.

Per phone viewport (portrait 390x844, landscape 844x390):
1. A finger held in the bottom-left joystick zone walks the player forward and leaves the view direction alone.
2. A finger that starts just outside the zone (above it, and in the middle of the screen) turns the view and never moves the player.
3. The resting stick sits fully inside the bottom-left corner.
4. Stairs feel natural: ground floor to upper floor, upper floor to terrace, both ways. Each leg arrives, stays grounded, and no
   single frame moves the eye more than 0.05 m (the eye glides over each riser at EYE_RATE; 0.05 m per frame at 60 fps is a normal walk).
5. Crossing the living room past the coffee table needs no frame jump above 0.05 m either.
Output: build/r9/touch.json. Exit 1 on any failure.
Run: uv run --no-project --python 3.12 --with playwright python viewer/test_touch.py
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_viewer import ROOT, Touch, serve, watch  # noqa: E402

OUT = ROOT / "build" / "r9" / "touch.json"
VIEWPORTS = {"portrait": (390, 844), "landscape": (844, 390)}
MAX_DY = 0.05
LEGS = [("Hall", "Rellano planta alta"), ("Rellano planta alta", "Hall"),
        ("Rellano planta alta", "Cuarto de lavado"), ("Cuarto de lavado", "Rellano planta alta")]


def open_page(browser, port, w, h, errors):
    ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True, device_scale_factor=1)
    page = ctx.new_page()
    watch(page, errors)
    page.goto(f"http://127.0.0.1:{port}/viewer/index.html?ao=0")
    page.wait_for_function("window.casa && window.casa.state !== 'loading'", timeout=240000)
    page.evaluate("casa.send('enterWalk')")
    page.wait_for_function('casa.state==="walk" && casa.player', timeout=60000)
    return page


def hold(page, touch, x0, y0, x1, y1, secs=1.2):
    """Press at (x0, y0), slide to (x1, y1), hold, release. Returns (moved metres, yaw change radians)."""
    a = page.evaluate("casa.player")
    touch.down(1, x0, y0)
    for s in range(1, 9):
        touch.move(1, x0 + (x1 - x0) * s / 8, y0 + (y1 - y0) * s / 8)
        time.sleep(0.03)
    time.sleep(secs)
    touch.up(1)
    time.sleep(0.2)
    b = page.evaluate("casa.player")
    moved = ((a["feet"][0] - b["feet"][0]) ** 2 + (a["feet"][2] - b["feet"][2]) ** 2) ** 0.5
    return moved, abs(a["yaw"] - b["yaw"])


def leg(page, spawn, a, b):
    ev = page.evaluate
    ev(f"casa.send('teleport', {a!r})")
    time.sleep(0.5)
    sx, sy, sz = spawn[b]
    route = ev(f"casa.plan([[{sx}, {sz}, {sy}]])")
    if not route or "waypoints" not in route:
        return {"leg": f"{a} -> {b}", "ok": False, "why": "no route"}
    wps = route["waypoints"]
    ev(f"casa.place({wps[0][0]}, {wps[0][2]}, {wps[0][1]})")
    w = ev(f"casa.walkRoute({wps}, {{ tol: 0.25, maxSeconds: 60 }})")
    end = ev("casa.player.feet")
    ok = bool(w["ok"] and w["grounded"] and abs(end[1] - sy) < 0.15 and w["maxDy"] <= MAX_DY)
    return {"leg": f"{a} -> {b}", "ok": ok, "seconds": round(w["seconds"], 2), "maxDy": round(w["maxDy"], 3),
            "end_y": round(end[1], 2), "want_y": round(sy, 2)}


def main():
    report, errors, bad = {"viewports": []}, [], []
    srv = serve()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        for tag, (w, h) in VIEWPORTS.items():
            page = open_page(browser, srv.server_address[1], w, h, errors)
            rooms = page.evaluate("casa.rooms")
            spawn = {r["name"]: r["spawn"] for r in rooms}
            rec = {"viewport": tag}
            touch = Touch(page)
            base = page.evaluate("(() => { const r = document.querySelector('#stickBase').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
            rec["stick_box"] = base
            rec["stick_corner_ok"] = base[0] >= 0 and base[3] <= h and base[2] <= w * 0.45 and base[1] >= h * 0.5
            cx, cy = (base[0] + base[2]) / 2, (base[1] + base[3]) / 2
            page.evaluate("casa.send('teleport', 'Sala')")
            time.sleep(0.6)
            rec["zone_walk"] = hold(page, touch, cx, cy, cx, cy - 50)
            page.evaluate("casa.send('teleport', 'Sala')")
            time.sleep(0.6)
            rec["above_zone_look"] = hold(page, touch, cx, cy - 170, cx + 120, cy - 170, secs=0.3)
            rec["middle_look"] = hold(page, touch, w / 2, h / 2, w / 2 + 120, h / 2, secs=0.3)
            rec["zone_ok"] = rec["zone_walk"][0] > 0.3 and rec["zone_walk"][1] < 0.05
            rec["look_ok"] = all(m < 0.05 and y > 0.05 for m, y in (rec["above_zone_look"], rec["middle_look"]))
            rec["legs"] = [leg(page, spawn, a, b) for a, b in LEGS]
            # the living room past the coffee table: planner route from the sofa side to the sliding door and back
            sala = spawn["Sala"]
            page.evaluate(f"casa.place({sala[0]}, {sala[1]}, {sala[2]})")
            rt = page.evaluate("(() => { const s = casa.spaces.find((x) => x.name === 'Sala'); return [s.min, s.max]; })()")
            (x0, _, z0), (x1, _, z1) = rt
            res = []
            for tx, tz in (((x0 + x1) / 2 + 0.6, z1 - 0.6), ((x0 + x1) / 2 - 0.6, z0 + 0.6)):
                route = page.evaluate(f"casa.plan([[{tx}, {tz}, {sala[1]}]])")
                if not route or "waypoints" not in route:
                    res.append({"ok": False, "maxDy": 9, "seconds": 0})
                    continue
                wps = [[w[0], w[1]] for w in route["waypoints"]]
                res.append(page.evaluate(f"casa.walkRoute({wps}, {{ tol: 0.3, maxSeconds: 20 }})"))
            rec["sala"] = {"ok": all(w["ok"] and w["maxDy"] <= MAX_DY for w in res), "maxDy": round(max(w["maxDy"] for w in res), 3),
                           "seconds": round(sum(w["seconds"] for w in res), 2)}
            for k in ("stick_corner_ok", "zone_ok", "look_ok"):
                if not rec[k]:
                    bad.append(f"{tag}: {k}")
            bad += [f"{tag}: {l}" for l in rec["legs"] if not l["ok"]]
            if not rec["sala"]["ok"]:
                bad.append(f"{tag}: sala {rec['sala']}")
            report["viewports"].append(rec)
            print(json.dumps(rec), flush=True)
            page.context.close()
        browser.close()
    report["errors"], report["fail"] = errors, bad
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1))
    for b in bad:
        print("  FAIL", b)
    for e in errors[:10]:
        print("  console:", e[:200])
    print(f"touch test: {len(bad)} failures, {len(errors)} console errors -> {OUT}")
    return 1 if bad or errors else 0


if __name__ == "__main__":
    sys.exit(main())
