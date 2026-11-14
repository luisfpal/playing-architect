"""Sink visibility in the real viewer (headless Chromium, real keys): the kitchen arrival faces the sink wall, the walker can
stand within 0.7 m of the counter front facing the sink, and the screenshots (desktop 1280x720 and phone 390x844) are written to the
folder given as argv[1] (default build/r8_sink_shots). uv run --no-project --python 3.12 --with playwright python viewer/test_sink.py [out]"""
import json
import math
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_viewer as tv  # noqa: E402
from test_viewer import Driver, serve  # noqa: E402

COUNTER_FRONT_Y = 16.20  # IFC y of the sink-run counter front
SINK = (8.35, 16.506)  # IFC x, y of the sink centre


def shoot(page, out, name, d, results):
    page.wait_for_timeout(1500)
    p = d.p
    page.screenshot(path=str(out / f"{name}.png"))
    results[name] = {"feet_ifc": [round(p["feet"][0], 3), round(-p["feet"][2], 3), round(p["feet"][1], 3)],
                     "yaw_deg": round(math.degrees(p["yaw"]), 1), "pitch_deg": round(math.degrees(p["pitch"]), 1), "room": p["room"]}


def run(out):
    out.mkdir(parents=True, exist_ok=True)
    srv = serve()
    res = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        for tag, vp in (("desktop", {"width": 1280, "height": 720}), ("phone", {"width": 390, "height": 844})):
            page = browser.new_page(viewport=vp)
            page.on("pageerror", lambda e: print("pageerror:", e))
            page.goto(f"http://127.0.0.1:{srv.server_address[1]}/viewer/index.html")
            page.wait_for_function("window.house && window.house.state !== 'loading'", timeout=120000)
            page.wait_for_timeout(2000)
            d = Driver(page)
            page.evaluate("house.send('teleport', 'Kitchen')")
            page.wait_for_timeout(2500)
            shoot(page, out, f"kitchen_spawn_{tag}", d, res)
            # a real-key walk to 1 m in front of the counter (counter front y 16.20, so y 15.20), then keep W down until the counter stops us
            fx, _, fz = d.p["feet"]
            ok = d.walk_to(SINK[0], -(COUNTER_FRONT_Y - 1.0), tol=0.12)
            d.face(math.atan2(-(SINK[0] - d.p["feet"][0]), -(-SINK[1] - d.p["feet"][2])))
            res[f"walk_1m_{tag}_arrived"] = ok
            shoot(page, out, f"sink_1m_{tag}", d, res)
            page.keyboard.down("KeyW")
            page.wait_for_timeout(4000)
            page.keyboard.up("KeyW")
            q = d.p
            res[f"closest_{tag}"] = {"feet_ifc_y": round(-q["feet"][2], 3), "gap_to_counter_front_m": round(COUNTER_FRONT_Y - (-q["feet"][2]), 3)}
            shoot(page, out, f"sink_close_{tag}", d, res)
            page.close()
        browser.close()
    (out / "viewer_results.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
    bad = [k for k in ("desktop", "phone") if not res[f"walk_1m_{k}_arrived"] or res[f"closest_{k}"]["gap_to_counter_front_m"] > 0.7]
    if bad:
        print("FAIL: cannot stand within 0.7 m of the counter facing the sink:", bad)
        sys.exit(1)
    print("PASS: the walker stands within 0.7 m of the counter front facing the sink (desktop and phone)")


if __name__ == "__main__":
    run(Path(sys.argv[1]) if len(sys.argv) > 1 else tv.ROOT / "build" / "r8_sink_shots")
