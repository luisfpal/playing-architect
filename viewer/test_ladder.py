"""R10-7 ladder test, on the real GLB in headless Chromium: the steel ladder on the terrace reaches the laundry roof and is climbable.

1. Physics (the same `player.step` the keys and the stick drive): facing the ladder, forward climbs from the terrace (6.40) to the
   roof deck (ladder_top, 9.02) and steps onto it; the player stands grounded on the deck and walks on it; the guard rail stops
   a walk toward the far edge; walking back over the ladder top climbs down to the terrace and walks away.
2. Halfway up, backward (S) descends to the terrace.
3. Real keys: W held facing the ladder climbs to the deck; from mid-ladder S held descends.
4. Touch joystick (phone viewport, CDP touch): stick pushed up climbs, pulled down descends.
Output: build/r10/ladder.json. Exit 1 on any failure.
Run: uv run --no-project --python 3.12 --with playwright python viewer/test_ladder.py
"""
import json
import math
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_viewer import ROOT, Touch, serve, watch  # noqa: E402

OUT = ROOT / "build" / "r10" / "ladder.json"
TERRACE = 6.40
FACE_LADDER = math.pi / 2  # yaw that looks toward -x (glTF), into the ladder on the laundry's east face


def ladder(ev):
    lad = ev("casa.byRole('escalera_gato')")
    assert len(lad) == 1, lad
    b = lad[0]
    return b, (b["min"][2] + b["max"][2]) / 2


def start(ev, b, zc, up=0.0):
    """Stand on the terrace 0.8 m in front of the ladder, facing it."""
    ev(f"casa.place({b['max'][0] + 0.8}, {TERRACE + up}, {zc})")
    ev(f"casa.setYaw({FACE_LADDER})")


def physics(ev, rec, bad):
    b, zc = ladder(ev)
    start(ev, b, zc)
    q = ev("casa.simHold(1, 0, 8)")
    p = ev("casa.player")
    deck = q["feet"][1]
    rec["climb_up"] = {"feet": [round(v, 3) for v in q["feet"]], "grounded": q["grounded"], "room": p["room"]}
    ok = abs(deck - 9.02) < 0.05 and q["grounded"] and q["feet"][0] < b["min"][0] - 0.2
    if not ok:
        bad.append(f"forward into the ladder does not reach and stand on the roof deck: {rec['climb_up']}")
    # walk on the deck toward the west party wall: stays on the deck, the guard rail stops the walk
    q = ev("casa.simHold(1, 0, 8)")
    rec["deck_walk"] = {"feet": [round(v, 3) for v in q["feet"]], "grounded": q["grounded"]}
    ok = abs(q["feet"][1] - deck) < 0.05 and q["grounded"] and q["feet"][0] > 0.2 and q["feet"][0] < 3.0
    if not ok:
        bad.append(f"walking on the roof deck: {rec['deck_walk']}")
    # the HUD names the deck (not the street) while the player stands on it
    deck_room = ev("casa.player")["room"]
    rec["deck_hud"] = deck_room
    if deck_room != "Terraza: cubierta del lavado":
        bad.append(f"HUD on the roof deck reads {deck_room!r}")
    # back to the ladder top, then over the edge: climbs down to the terrace and walks away
    ev(f"casa.place({b['min'][0] - 0.6}, {deck}, {zc})")
    ev(f"casa.setYaw({-FACE_LADDER})")
    q = ev("casa.simHold(1, 0, 10)")
    rec["climb_down_from_deck"] = {"feet": [round(v, 3) for v in q["feet"]], "grounded": q["grounded"]}
    ok = abs(q["feet"][1] - TERRACE) < 0.05 and q["grounded"] and q["feet"][0] > b["max"][0] + 0.5
    if not ok:
        bad.append(f"walking off the deck over the ladder does not climb down: {rec['climb_down_from_deck']}")
    terrace_room = ev("casa.player")["room"]
    rec["terrace_hud"] = terrace_room
    if terrace_room != "Terraza":
        bad.append(f"HUD on the terrace floor reads {terrace_room!r}, not 'Terraza'")
    # halfway up, back (S) descends
    start(ev, b, zc)
    mid = ev("casa.simHold(1, 0, 2.2)")["feet"][1]
    q = ev("casa.simHold(-1, 0, 6)")
    rec["back_descends"] = {"mid_y": round(mid, 3), "end": [round(v, 3) for v in q["feet"]], "grounded": q["grounded"]}
    ok = mid > TERRACE + 1.0 and abs(q["feet"][1] - TERRACE) < 0.05 and q["grounded"]
    if not ok:
        bad.append(f"back from mid-ladder does not descend: {rec['back_descends']}")
    # walking past the ladder sideways does not grab it
    start(ev, b, zc)
    q = ev("casa.simHold(0, 1, 1.5)")
    rec["sideways_ignored"] = {"feet": [round(v, 3) for v in q["feet"]]}
    if abs(q["feet"][1] - TERRACE) > 0.05:
        bad.append(f"walking sideways past the ladder climbs it: {rec['sideways_ignored']}")


def keys(page, rec, bad):
    ev = page.evaluate
    b, zc = ladder(ev)
    start(ev, b, zc)
    page.keyboard.down("KeyW")
    try:
        page.wait_for_function("casa.player.feet[1] > 8.0", timeout=240000)
        up = ev("casa.player.feet")[1]
    except Exception as e:  # noqa: BLE001
        up = ev("casa.player.feet")[1]
        bad.append(f"real key W did not climb: {e}")
    page.keyboard.up("KeyW")
    page.keyboard.down("KeyS")
    try:
        page.wait_for_function(f"casa.player.feet[1] < {TERRACE + 0.05}", timeout=240000)
    except Exception as e:  # noqa: BLE001
        bad.append(f"real key S did not descend: {e}")
    page.keyboard.up("KeyS")
    rec["keys"] = {"up_to": round(up, 3), "back_to": round(ev("casa.player.feet")[1], 3)}


def joystick(browser, port, rec, bad, errors):
    w, h = 390, 844
    ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True, device_scale_factor=1)
    page = ctx.new_page()
    watch(page, errors)
    page.goto(f"http://127.0.0.1:{port}/viewer/index.html?ao=0")
    page.wait_for_function("window.casa && window.casa.state !== 'loading'", timeout=240000)
    page.evaluate("casa.send('enterWalk')")
    page.wait_for_function('casa.state==="walk" && casa.player', timeout=60000)
    ev = page.evaluate
    b, zc = ladder(ev)
    start(ev, b, zc)
    touch = Touch(page)
    base = ev("(() => { const r = document.querySelector('#stickBase').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
    cx, cy = (base[0] + base[2]) / 2, (base[1] + base[3]) / 2
    res = {}
    for name, dy, cond in (("up", -50, "casa.player.feet[1] > 7.4"), ("down", 50, f"casa.player.feet[1] < {TERRACE + 0.05}")):
        touch.down(1, cx, cy)
        for s in range(1, 9):
            touch.move(1, cx, cy + dy * s / 8)
            time.sleep(0.03)
        try:
            page.wait_for_function(cond, timeout=240000)
            res[name] = round(ev("casa.player.feet")[1], 3)
        except Exception as e:  # noqa: BLE001
            res[name] = f"timeout at {ev('casa.player.feet')[1]:.2f}"
            bad.append(f"joystick {name} on the ladder: {e}")
        touch.up(1)
        time.sleep(0.3)
    rec["joystick"] = res
    ctx.close()


def main():
    rec, bad, errors = {}, [], []
    srv = serve()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = browser.new_page(viewport={"width": 640, "height": 300})
        watch(page, errors)
        page.goto(f"http://127.0.0.1:{srv.server_address[1]}/viewer/index.html?ao=0")
        page.wait_for_function("window.casa && window.casa.state !== 'loading'", timeout=240000)
        page.evaluate("casa.send('enterWalk')")
        page.wait_for_function('casa.state==="walk" && casa.player', timeout=60000)
        rec["ladders"] = page.evaluate("casa.info.ladders")
        if rec["ladders"] != 1:
            bad.append(f"the GLB has {rec['ladders']} climbable ladders, want 1")
        gates = page.evaluate("casa.byRole('puerta_seguridad').map((b) => b.name)")
        rec["gate"] = gates
        # glTF node names carry underscores for spaces (the GLB export sanitises IFC names)
        if [g.replace("_", " ") for g in gates] != ["Puerta de seguridad escalera gato"]:
            bad.append(f"the GLB has gate nodes {gates}, want the ladder-top gate")
        physics(page.evaluate, rec, bad)
        keys(page, rec, bad)
        page.close()
        joystick(browser, srv.server_address[1], rec, bad, errors)
        browser.close()
    rec["errors"], rec["fail"] = errors, bad
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rec, indent=1))
    print(json.dumps(rec, indent=1))
    print(f"ladder test: {len(bad)} failures, {len(errors)} console errors -> {OUT}")
    return 1 if bad or errors else 0


if __name__ == "__main__":
    sys.exit(main())
