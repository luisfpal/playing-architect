"""Step 2 of the pipeline, on a toy example: IFC geometry becomes a GLB whose nodes carry the viewer contract.

  uv run --no-project --python 3.12 --with ifcopenshell --with trimesh python pipeline/ifc_to_glb.py build/example.ifc build/example.glb

Every wall becomes a mesh node with extras {ifc, name, collider: "mesh"}; every IfcSpace becomes an invisible marker
plus a Spawn_<room> node, which is what viewer/main.js reads for the room menu and teleports (docs/glb_contract.md).
In the real house a Blender step sat between these two: assets, materials, lights, the two trucks and procedural
furniture were added in Blender (bpy, run headless) and exported with the same extras, then tools/web/optimize.mjs
made the web copy. The checks rendered the scene from fixed cameras and looked at the images after every change."""
import json
import sys

import ifcopenshell
import ifcopenshell.geom
import numpy as np
import trimesh

src, out = (sys.argv[1:] + ["build/example.ifc", "build/example.glb"])[:2]
f = ifcopenshell.open(src)
settings = ifcopenshell.geom.settings()
settings.set("use-world-coords", True)
scene = trimesh.Scene()
# IFC is Z-up, glTF is Y-up: (x, y, z) -> (x, z, -y)
ZUP = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=float)
for el in f.by_type("IfcWall") + f.by_type("IfcSpace"):
    shape = ifcopenshell.geom.create_shape(settings, el)
    v = np.array(shape.geometry.verts).reshape(-1, 3)
    tri = np.array(shape.geometry.faces).reshape(-1, 3)
    mesh = trimesh.Trimesh(v, tri, process=False)
    mesh.apply_transform(ZUP)
    space = el.is_a("IfcSpace")
    extras = {"ifc": el.is_a(), "name": el.Name, "collider": "none" if space else "mesh"}
    if space:
        extras["type"] = "IfcSpace"
    scene.add_geometry(mesh, node_name=(f"IfcSpace - {el.Name}" if space else el.Name), geom_name=el.Name, metadata=extras)
    if space:
        c = mesh.bounds.mean(axis=0)
        scene.add_geometry(trimesh.points.PointCloud([[0, 0, 0]]), node_name=f"Spawn_{el.Name}",
                           transform=trimesh.transformations.translation_matrix([c[0], 0, c[2]]),
                           metadata={"spawn": True, "room": el.Name, "yaw": 0, "level": 0})
scene.export(out)
print(f"wrote {out}: {len(scene.geometry)} geometries, {sum(len(g.faces) for g in scene.geometry.values() if hasattr(g, 'faces'))} triangles")
