# playing-architect

![Walking through the house](docs/demo.gif)

**[Walk the house in your browser](https://luisfpal.github.io/playing-architect/)** (desktop or phone)

A two-storey house, designed and modelled almost entirely by coding agents, that you can walk through in the
browser: real collisions, stairs, a climbable ladder, doors, drawers, windows and a fridge that open, a room menu and
a minimap, and touch controls on a phone. It started half as play ("what if I let agents be the architect?") and
turned into a real design that was handed to a real architect.

## How it was built

The house is code. A Python program describes the plan (rooms, walls, openings, stairs, furniture) and writes an IFC
file, the open BIM format. Checks read the IFC back and measure it: room sizes, door clearances, stair rise and run,
every room reachable. Blender, run headless, turns the IFC into a scene: materials, lights, appliances, procedural
furniture and two cars in the garage, exported as a glTF binary (GLB) whose nodes carry a small contract
(`docs/glb_contract.md`): what collides, what opens and about which hinge, where each room's spawn point is.

The agents (an orchestrating assistant, Claude Code sessions for the heavy edits, and a cheap DeepSeek agent for drafts,
bulk downloads and mechanical edits) did the work in review rounds, about a dozen of them. Every round ended
with the same loop:

1. **Change the code**, rebuild the IFC and the GLB.
2. **Render and look.** Fixed cameras in every room and close-ups of the hard parts (wheels, badges, lamps, stairs)
   were rendered headless, and an agent opened each picture and wrote down what it saw. Many bugs were invisible to
   the numeric checks: vehicles that looked crashed, one vehicle wearing the other's wheels.
3. **Walk it for real.** Headless Chromium drives the actual viewer with real key, mouse and touch events: walk in from
   the street, take the stairs, enter and leave every room, press into corners, climb the ladder, open every door.
4. **Prove the checks can fail.** Deliberately broken models (a wall across a doorway, a missing step) must turn the
   matching check red.

`pipeline/` shows the first two steps on a tiny neutral floor plan (IFC to GLB with the same node contract), and
`skills/` has the instructions the agents followed, rewritten in generic form.

## The viewer

Plain ES modules, no build step: [three.js](https://threejs.org) for rendering, [Rapier](https://rapier.rs) for the
character controller and colliders (one trimesh or box collider per GLB node, chosen by the node's `collider` extra).
The viewer's modes (loading, orbit, walk, paused, error) are a statechart in `docs/statecharts.md`;
`viewer/machine.js` holds the same chart as a table, `send()` is the only way to change mode, and
`viewer/test_viewer.py` fails if the diagram and the table ever differ.

| File | What it does |
|---|---|
| `viewer/main.js` | scene, lighting, loading, orbit and walk cameras, HUD, room menu |
| `viewer/machine.js` | the mode statechart as a table |
| `viewer/physics.js` | Rapier world, capsule controller, autostep, unstick, ladder climbing |
| `viewer/interact.js` | doors, drawers, sliding windows, aim and open with E or a tap |
| `viewer/nav.js` | grid navigation used to place spawns and by the tests |
| `viewer/minimap.js` | floor plan minimap, click a room to go there |
| `viewer/touch.js` | phone controls: stick on the left, look on the right, a hand button |

## Making a 189 MB scene load on a phone, without nerfing it

The raw export was 189 MB and took minutes to load. The rule was that nothing visible may change, so
`tools/web/optimize.mjs` only keeps steps that are lossless or provably invisible:

- prune vertex attributes no material reads (meshes carried up to 8 unused UV sets), dedup, weld identical vertices;
- PNG textures to **lossless** WebP, with an assertion that every pixel is identical; JPEGs untouched;
- meshopt compression with 16-bit positions (0.08 mm on a 5.5 m car) and 12-bit normals.

Lossy WebP (q90 and q95) and 8-bit normals were tried and dropped: `tools/web/compare_shots.py` renders the same
views from both files in the real viewer and writes A | B | 8x-difference images, and both showed up (banding on the
vehicle paint). The script asserts that the triangle count (about 2.03 million) and every named node are unchanged.
Result: **46 MB**, 39 MB over the wire with gzip, for the original scene. This public copy, with two different cars,
is 101 MB raw and 38 MB optimised (about 1.02 million triangles), by the same steps. It is served by `deploy/serve.py`
with ETag revalidation, so a repeat visit costs one `304`.

## What broke, and how it was proven fixed

- **Trapped in a bathroom.** In an early round the player could walk into the ground-floor bathroom and not get out.
  Fixes: an unstick rule (input held for 2 s with under 1 cm of movement returns you to the last spot with 0.35 m of
  free radius, also on the R key) and a test that walks in and out with real keys and presses into a corner.
- **Ground under a slab.** A downward ray could pass through a thin floor shell and report the ground below it.
  Ground now counts as standable only with 0.5 m of headroom above it; the tests walk every flight up and down.
- **Vehicles that looked crashed.** Wheels floated or sank and paint showed inside the tyres. A Blender check now
  requires four wheels on the ground, no paint inside the tyres and the steering wheel on the left, and close-up
  renders are looked at after every vehicle change.
- **A drawer test that failed although the drawer worked.** The test aimed at the middle of the drawer's box, which
  sits under the desk top. The test was wrong, not the viewer; it now aims at the drawer's front face.
- **The wrong wheels.** One vehicle wore the other one's rims. Found by looking at a close-up render, not by any number.
- **A washer that opened like a box.** Its porthole door swung open onto a flat panel, because the drum sat inside a solid
  cabinet. The cabinet now has a real loading opening with the seal and the drum behind it, and the machines stand
  0.45 m off the wall so the water valves, the dryer duct and the gas valve behind them can be seen.

## Run it

```sh
make glb          # download the scene (release asset, 38 MB) to build/casa.glb
make serve        # http://127.0.0.1:8080/ with gzip + ETag, same server as production
make test         # headless Chromium: statechart, walking, stairs, rooms, touch, ladder, doors and drawers
```

Needs Python 3, [uv](https://docs.astral.sh/uv/) (fetches Playwright on the fly) and, for `make optimize`, Node 20+.
The tests run under software rendering, so the full suite takes a while.

Deploy: `make deploy HOST=user@server` copies the site and installs a systemd user unit
(`deploy/playing-architect.service.sample`); put any TLS reverse proxy in front.

## Honest limits

- The viewer UI and the room names are in Spanish.
- It is a design model, not construction documents: structure, plumbing and electrical were sketched for the
  architect, not engineered, and they are not part of this repository.
- Software rendering in CI is slow, and the tests are end-to-end, so `make test` is minutes, not seconds.
- Personal details were removed from this public copy, and the garage holds two generic cars (a Model Y and a
  Cybertruck, both CC BY models) instead of the original vehicles, without licence plates.
- Third-party models, textures and the sky keep their own licences (CC BY 4.0 and CC0); see [CREDITS.md](CREDITS.md).
  The code is MIT ([LICENSE](LICENSE)).
