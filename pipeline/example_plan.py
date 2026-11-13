"""Step 1 of the pipeline, on a toy example: a floor plan written as code becomes an IFC file.

  uv run --no-project --python 3.12 --with ifcopenshell python pipeline/example_plan.py build/example.ifc

The real house used the same idea at a larger scale: rooms, walls, openings and furniture are data in Python, so an
agent can change the plan, rebuild, and re-measure it (checks read the IFC back) instead of editing a model by hand."""
import sys

import ifcopenshell
import ifcopenshell.api as api

OUT = sys.argv[1] if len(sys.argv) > 1 else "build/example.ifc"
H, T = 2.7, 0.15  # storey height and wall thickness, metres

# A 9 x 6 m single-storey plan: living room on the left, bedroom on the right, one door between them, one entry door.
ROOMS = {"Living room": (0, 0, 5, 6), "Bedroom 1": (5, 0, 9, 6)}
WALLS = [((0, 0), (9, 0)), ((9, 0), (9, 6)), ((9, 6), (0, 6)), ((0, 6), (0, 0)), ((5, 0), (5, 6))]
DOORS = [("Front door", 0, 2.0, 0.9), ("Bedroom 1 door", 4, 3.0, 0.8)]  # (name, wall index, offset, width)

f = ifcopenshell.file(schema="IFC4")
proj = api.run("root.create_entity", f, ifc_class="IfcProject", name="example")
api.run("unit.assign_unit", f)
ctx = api.run("context.add_context", f, context_type="Model")
body = api.run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
site = api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
bldg = api.run("root.create_entity", f, ifc_class="IfcBuilding", name="House")
storey = api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="Ground floor")
api.run("aggregate.assign_object", f, relating_object=proj, products=[site])
api.run("aggregate.assign_object", f, relating_object=site, products=[bldg])
api.run("aggregate.assign_object", f, relating_object=bldg, products=[storey])


def place(el, x, y, angle=0.0):
    import numpy as np
    c, s = np.cos(angle), np.sin(angle)
    m = np.array([[c, -s, 0, x], [s, c, 0, y], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=float)
    api.run("geometry.edit_object_placement", f, product=el, matrix=m)


walls = []
for i, ((x0, y0), (x1, y1)) in enumerate(WALLS):
    import math
    L, a = math.hypot(x1 - x0, y1 - y0), math.atan2(y1 - y0, x1 - x0)
    w = api.run("root.create_entity", f, ifc_class="IfcWall", name=f"Wall {i + 1}")
    rep = api.run("geometry.add_wall_representation", f, context=body, length=L, height=H, thickness=T)
    api.run("geometry.assign_representation", f, product=w, representation=rep)
    place(w, x0, y0, a)
    api.run("spatial.assign_container", f, relating_structure=storey, products=[w])
    walls.append((w, x0, y0, a))

for name, wi, off, width in DOORS:
    import math
    w, x0, y0, a = walls[wi]
    op = api.run("root.create_entity", f, ifc_class="IfcOpeningElement", name=f"Opening {name}")
    rep = api.run("geometry.add_wall_representation", f, context=body, length=width, height=2.1, thickness=T * 3)
    api.run("geometry.assign_representation", f, product=op, representation=rep)
    place(op, x0 + math.cos(a) * off - math.sin(a) * -T, y0 + math.sin(a) * off + math.cos(a) * -T, a)
    api.run("feature.add_feature", f, feature=op, element=w)

for name, (x0, y0, x1, y1) in ROOMS.items():
    sp = api.run("root.create_entity", f, ifc_class="IfcSpace", name=name)
    rep = api.run("geometry.add_slab_representation", f, context=body, depth=0.1,
                  polyline=[(0, 0), (x1 - x0, 0), (x1 - x0, y1 - y0), (0, y1 - y0)])
    api.run("geometry.assign_representation", f, product=sp, representation=rep)
    place(sp, x0, y0)
    api.run("aggregate.assign_object", f, relating_object=storey, products=[sp])

f.write(OUT)
print(f"wrote {OUT}: {len(f.by_type('IfcWall'))} walls, {len(f.by_type('IfcOpeningElement'))} openings, {len(f.by_type('IfcSpace'))} spaces")
