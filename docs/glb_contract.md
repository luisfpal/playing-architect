# GLB contract (scene pipeline to viewer)

Written by the scene exporter (Blender, headless) and enforced by checks that read the GLB back; those scripts stay in the
original project. `pipeline/ifc_to_glb.py` writes the minimal subset (walls, spaces, spawns) for the neutral example. Frame: glTF Y up, metres; IFC (x, y, z) becomes glTF (x, z, -y).

| Node | Extras | Meaning |
|---|---|---|
| `Level_0`, `Level_1`, `Level_2` | `label` | Storeys (Planta baja, Planta alta, Terraza). Every building node is a child of one |
| any mesh node | `collider`: `mesh`, `box` or `none` | `mesh`: Rapier trimesh (walls, slabs, stairs, railings, windows, pool shell, terrain). `box`: cuboid of the node's bounding box (furniture, vehicles, door leaves, `Limite_*`). `none`: walk through (spaces, drains, water, window frames) |
| furniture node | `role`, `fitted`: `procedural`, `asset` or `box` | `box` is allowed only for the roles in `BOX_ROLES` (structure and water), plus `mampara` (fixed shower glass) and `canal_pluvial` (condensate channel) |
| `IfcSpace - <name>` | `type`, `name`, `area_m2`, `dims`, `collider` = none | Room volumes: the viewer reads the room from them, never draws them |
| `Spawn_<room>` (empty) | `spawn`, `room`, `level`, `yaw` | Standing point 0.35 m clear of walls and furniture, 0.05 m above the floor; `yaw` in degrees, counter-clockwise from IFC +x, facing the farthest corner |
| `Spawn_Calle` | same, `room` = `Calle` | On the street facing the gate (+y) |
| `Limite_1` to `Limite_4` | `collider` = box | Invisible walls that close the walkable arena; the viewer must hide them |
| door leaves | `collider` = box | Exported open (swung 90 degrees about the hinge jamb), the doorway stays clear |

Rooms without a spawn: stair, voids, pool, service strips. They are reached from the rooms next to them.

## Openable parts (runtime: `viewer/interact.js`, proof: `viewer/test_interact.py`)

Every drawer slides, every cabinet, wardrobe and fridge door swings, every room door turns. The viewer finds a part by the
node name `<parent>__<kind>_<i>`, or by `openable` in the extras (room doors). Names pass through three.js, which turns spaces
into `_`. Frames below are the Blender frame before export (front = local -Y); in glTF the front becomes +Z.

| Node | Extras | Meaning |
|---|---|---|
| `<parent>__drawer_<i>` | `slide` (metres) | Child of the furniture node. Origin = centre of the closed front face at floor level. Slides `slide` metres along the front normal. Must contain the tray (front, two sides, back, bottom), not a lone front plate; depth = cabinet depth - 0.05 m. `collider` = none |
| `<parent>__door_<i>` | `hinge` (`L` or `R` seen from the front), `swing` (degrees) | Cabinet or wardrobe door, modelled closed. Origin = the hinge axis (vertical). Turns about it by `swing`, outward. `collider` = none |
| `<parent>__fridge_door_<i>` | same as `__door_<i>` | Refrigerator or freezer door. The body must be a hollow cavity with shelves so the interior is visible when the door is open |
| `<parent>__window_<i>` | `apertura` = `corrediza` with `slide` (metres), `slide_x`, `slide_y` (IFC unit direction of the slide); or `apertura` = `abatible` with `pivot_x`, `pivot_y` (IFC metres, hinge axis), `open_deg` (degrees counter-clockwise about +z, closed to open) | Operable window sash, exported CLOSED with world-space vertices and node origin at 0 (like a room door). Sliding sashes slide `slide` metres along the wall run; hinged sashes turn about the jamb. `collider` = box, which `interact.js` replaces with one that follows the sash |
| `<parent>` (window, no `__window_` child) | `apertura` = `fija` | A fixed window or pane (fixed sashes, louvre `Rejilla`, fixed lantern glass): it never moves and carries no openable child. A whole fixed window has `collider` = mesh. The fixed half of a moving window (`<parent> fijo`) has `collider` = none: it is glass in front of the sash and must not block the aim ray; the wall under its sill blocks the player |
| room door leaf (`ifc` = IfcDoor, swing doors only) | `openable` = `door`, `pivot_x`, `pivot_y` (IFC metres, hinge axis), `close_deg` (counter-clockwise about +z) | Exported OPEN (vertices in world space, node origin at 0). The viewer closes it by turning `close_deg` about the pivot and starts open so every doorway is clear. Its `collider` = box follows the leaf: closed it blocks the opening, open it hugs the wall. Rolling and sliding doors are not openable |

Rules the runtime applies: a part toggles by E, a click (locked pointer: the centre ray; otherwise the pointer), a tap, or
`casaInteract.interact()` (touch button). Reach 2.4 m, occluded by walls through the Rapier ray, with a 25 degree aim assist
inside 1.8 m. A door refuses to move into the player (`Estás en el paso`) and a door whose move ends on the player reverses.
A window never refuses: its sash swings above the sill and the person opening it stands in front of the sweep.
Hover hint `[E] Abrir cajón` (phones: `Abrir cajón`). Windows use the same triggers and the noun `ventana` (`[E] Abrir
ventana`); `apertura` in the sink: `corrediza` (sliding sash), `abatible` (hinged sash), `fija` (never moves).
`window.casaInteract` is the hook for tests and the touch button.
