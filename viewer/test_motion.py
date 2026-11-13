"""Movement feels the same at any frame rate: walking speed, mouse turn rate and per-frame camera motion at 30, 60 and
120 frames per second. The page runs on a fake animation clock (requestAnimationFrame and performance.now are replaced),
so the frame rate is exact and the wall clock does not matter. ?render=0: physics and input only.

Fails when the speed or the turn rate differs by more than 5% between frame rates, or when the camera stands still on
some frames while W is held (fixed-step physics drawn without interpolation does that on a 120 Hz screen)."""
import sys, os, json, math, threading, http.server, socketserver, statistics
from functools import partial
from playwright.sync_api import sync_playwright
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLB = os.environ.get('HOUSE_GLB_URL', '../build/house.glb')
H = partial(http.server.SimpleHTTPRequestHandler, directory=ROOT)
http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
srv = socketserver.ThreadingTCPServer(('127.0.0.1', 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
INIT = """(() => { const FPS = %d; let t = 0; performance.now = () => t;
 window.__fakeStep = (n) => new Promise((res) => { let k = 0; const go = () => { if (k++ >= n) return res(); t += 1000 / FPS; const q = window.__raf; window.__raf = []; q.forEach((cb) => cb(t)); setTimeout(go, 0); }; go(); });
 window.__raf = []; window.requestAnimationFrame = (cb) => { window.__raf.push(cb); return 1; };
 window.__auto = true; (function tick(){ if (window.__auto) { t += 1000 / FPS; const q = window.__raf; window.__raf = []; q.forEach((cb) => cb(t)); setTimeout(tick, 0); } })(); })();"""
out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch(args=['--use-gl=swiftshader', '--enable-unsafe-swiftshader'])
    for fps in (30, 60, 120):
        pg = b.new_page(viewport={'width': 960, 'height': 540})
        pg.add_init_script(INIT % fps)
        pg.goto(f'http://127.0.0.1:{port}/viewer/?render=0&ao=0&glb={GLB}')
        pg.wait_for_function("window.house && window.house.state === 'orbit'", timeout=600000)
        pg.evaluate("window.__auto = false")
        pg.evaluate("house.send('enterWalk')"); pg.evaluate("house.send('teleport', 'Street')")
        pg.evaluate("window.__fakeStep(30)")
        p0 = pg.evaluate("house.player"); pg.evaluate(f"house.setYaw({p0['yaw'] + math.pi / 2})")  # along the street: open ground
        pg.keyboard.down('KeyW')
        pg.evaluate("window.__fakeStep(%d)" % (fps // 2))  # accelerate
        cams = []
        for _ in range(fps):  # one simulated second
            pg.evaluate("window.__fakeStep(1)")
            cams.append(pg.evaluate("house.camera.position"))
        pg.keyboard.up('KeyW')
        d = [math.dist(cams[i][::2], cams[i + 1][::2]) for i in range(len(cams) - 1)]
        speed = sum(d) / ((len(cams) - 1) / fps)
        # turn: synthetic mouse movement events of 10 px each, 60 per simulated second regardless of fps
        y0 = pg.evaluate("house.player.yaw")
        pg.evaluate("for (let i = 0; i < 60; i++) document.dispatchEvent(new MouseEvent('mousemove', { movementX: 10 }))")
        turn = abs(pg.evaluate("house.player.yaw") - y0)
        out[fps] = {'speed_mps': round(speed, 3), 'frame_step_mean_cm': round(100 * statistics.mean(d), 2), 'frame_step_cv': round(statistics.pstdev(d) / max(1e-9, statistics.mean(d)), 3), 'zero_move_frames': sum(1 for x in d if x < 1e-4), 'turn_rad_600px': round(turn, 4), 'frame_cm_min_max': [round(100 * min(d), 2), round(100 * max(d), 2)], 'blocked': pg.evaluate('house.blocked().slice(0, 3)')}
        # real cost of one physics step (wall clock)
        out[fps]['step_ms'] = pg.evaluate("(() => { const t0 = Date.now(); house.simHold(1, 0, 2); return (Date.now() - t0) / 120; })()")
        print(fps, out[fps], flush=True)
        pg.close()
    b.close()
print(json.dumps(out))
bad = []
def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name} {detail}")
    if not ok: bad.append(name)
sp = [v['speed_mps'] for v in out.values()]; tr = [v['turn_rad_600px'] for v in out.values()]
check("walking speed is the same at 30/60/120 fps (within 5%)", max(sp) <= 1.05 * min(sp), str(sp))
check("walking speed is the walk speed (2.0 m/s within 5%)", all(abs(s - 2.0) <= 0.1 for s in sp), str(sp))
check("mouse turn rate is the same at 30/60/120 fps (within 5%)", max(tr) <= 1.05 * min(tr), str(tr))
check("the camera moves on every frame while W is held", all(v['zero_move_frames'] == 0 for v in out.values()), str({k: v['zero_move_frames'] for k, v in out.items()}))
check("per-frame camera motion is even (coefficient of variation < 0.1)", all(v['frame_step_cv'] < 0.1 for v in out.values()), str({k: v['frame_step_cv'] for k, v in out.items()}))
print("ALL OK" if not bad else f"{len(bad)} FAILED")
sys.exit(1 if bad else 0)
