"""Proof that the web copy of the scene looks the same as the full one (rule: do not nerf the model).

  uv run --no-project --python 3.12 --with playwright --with pillow --with numpy python tools/web/compare_shots.py \
      [A.glb B.glb OUT_DIR]          (defaults: build/house.glb build/web/house.glb build/r11/compare)

Loads the real viewer (headless Chromium, swiftshader) once per GLB with ?glb=, takes the same walking views (car
close-ups: wheels, lamps, cabins; rooms), and writes per view A | B | 8x difference, plus a
table: mean absolute difference (0-255) and the share of pixels that differ by more than 24/255. Exit 1 when a view
differs by more than MAX_MEAN or MAX_SHARE (rendering noise between two runs of the same file is the floor; see --self)."""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "viewer"))
from test_viewer import serve  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
A, B, OUT = (args + ["build/house.glb", "build/web/house.glb", "build/r11/compare"][len(args):])[:3]
OUT = ROOT / OUT
MAX_MEAN, MAX_SHARE = 1.5, 0.01
ONLY = next((a.split("=", 1)[1].split(",") for a in sys.argv[1:] if a.startswith("--only=")), None)  # e.g. --only=room_kitchen
# feet (glTF x, y, z), yaw, pitch. Cars: west x 1.05-2.97, east x 3.91-5.94, tails near z -0.3, noses near z -5.1/-5.75;
# yaw +pi/2 looks toward -x (the west car from the gap), -pi/2 toward +x (the east car).
VIEWS = {
    "east_front_wheel": (3.35, 0.0, -4.65, -1.5708, -0.75),
    "east_rear_wheel": (3.35, 0.0, -1.55, -1.5708, -0.75),
    "west_front_wheel": (3.45, 0.0, -4.55, 1.5708, -0.75),
    "west_rear_wheel": (3.45, 0.0, -1.45, 1.5708, -0.75),
    "east_cabin": (3.40, 0.0, -3.55, -1.5708, -0.35),
    "west_cabin": (3.40, 0.0, -3.55, 1.5708, -0.35),
    "east_tail": (4.85, 0.0, 0.9, 0.0, -0.45),
    "west_tail": (1.98, 0.0, 0.9, 0.0, -0.45),
    "east_nose_q": (3.45, 0.0, -5.6, -2.55, -0.20),
    "west_nose_q": (3.45, 0.0, -5.6, 2.55, -0.20),
    "street": (3.40, 0.0, 3.5, 0.0, -0.10),
}
ROOMS = ["Living room", "Kitchen", "Dining room (double height)", "Main bedroom", "Terrace"]


def shoot(glb, tag, port, pw):
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(f"http://127.0.0.1:{port}/viewer/index.html?glb=../{glb}")
    page.wait_for_function("window.house && window.house.state !== 'loading'", timeout=600000)
    info = page.evaluate("house.info")
    page.evaluate("house.send('teleport', 'Garage (2 cars)')")
    page.wait_for_timeout(3000)
    shots = {}
    for name, (x, y, z, yaw, pitch) in VIEWS.items():
        if ONLY and name not in ONLY:
            continue
        # stand on the ground there (placing in the air lets the fall time differ between runs: sub-pixel jitter)
        put = f"house.place({x}, house.groundAt({x}, {z}, 1.5)?.y ?? {y}, {z}); house.setYaw({yaw}); house.setPitch({pitch})"
        page.evaluate(put)
        page.wait_for_timeout(2500)
        page.evaluate(put)
        page.wait_for_timeout(1500)
        p = OUT / f"{name}_{tag}.png"
        page.screenshot(path=str(p))
        shots[name] = p
    rooms = [r["name"] for r in page.evaluate("house.rooms")]
    for want in ROOMS:
        if ONLY and "room_" + want.replace(" ", "_") not in ONLY:
            continue
        r = next((n for n in rooms if n.startswith(want)), None)
        if not r:
            continue
        page.evaluate(f"house.send('teleport', {json.dumps(r)})")
        page.wait_for_timeout(4000)
        p = OUT / f"room_{want.replace(' ', '_')}_{tag}.png"
        page.screenshot(path=str(p))
        shots["room_" + want] = p
    b.close()
    return shots, info, errors


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    srv = serve()
    port = srv.server_address[1]
    with sync_playwright() as pw:
        sa, ia, ea = shoot(A, "a", port, pw)
        sb, ib, eb = shoot(B, "b", port, pw)
    srv.shutdown()
    print(f"A {A}: {ia}\nB {B}: {ib}")
    print(f"console errors A {len(ea)} B {len(eb)}", (ea + eb)[:5])
    bad = []
    rows = []
    for k in sa:
        a = np.asarray(Image.open(sa[k]).convert("RGB")).astype(int)
        b = np.asarray(Image.open(sb[k]).convert("RGB")).astype(int)
        d = np.abs(a - b)
        mean, share = d.mean(), (d.max(axis=2) > 24).mean()
        rows.append((k, mean, share))
        diff = np.clip(d * 8, 0, 255).astype(np.uint8)
        w = a.shape[1]
        side = Image.new("RGB", (w * 3, a.shape[0]))
        side.paste(Image.open(sa[k]).convert("RGB"), (0, 0))
        side.paste(Image.open(sb[k]).convert("RGB"), (w, 0))
        side.paste(Image.fromarray(diff), (2 * w, 0))
        side.save(OUT / f"cmp_{k.replace(' ', '_')}.png")
        if mean > MAX_MEAN or share > MAX_SHARE:
            bad.append(k)
    for k, m, s in rows:
        print(f"{'FAIL' if k in bad else 'ok  '} {k:28s} mean |diff| {m:5.2f}  pixels > 24/255: {100 * s:5.2f} %")
    if ia.get("triangles") != ib.get("triangles"):
        bad.append("triangles")
        print("FAIL triangle count", ia.get("triangles"), ib.get("triangles"))
    if eb:
        bad.append("console errors")
    print("compare_shots:", "FAIL " + ", ".join(bad) if bad else "PASS")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
