"""MCP server: the same document, as tools an assistant can call.

Run it on stdio with ``aicad serve``. After every modeling change the assistant
should call ``inspect_part`` and look at the pictures, not only the numbers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aicad.document import Document
from aicad.errors import AICADError
from aicad.models import InspectReport

try:
    from mcp.server.mcpserver import Image, MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer
    from mcp.server.fastmcp import Image
    from mcp.server.fastmcp.exceptions import ToolError

_INSTRUCTIONS = """
aicad is a CAD session for hard-body mechanical parts. Units are millimeters.
The script or the operation list is the part: edit by replacing it, then look.

After every create or update, call inspect_part. Read the measurements and look
at the contact sheet before changing the part again. A script that returns
without an error can still be the wrong shape.

Scripts are build123d Python. The finished solid must be assigned to `part`.
build123d is already imported. `feature.add(name, solid)` and
`feature.cut(name, tool)` are optional: they record how much of each step
actually added or removed material. Plain scripts that ignore `feature` still work.

Structured operations are a list of add/cut boxes and cylinders when a script
is more than the part needs. Each operation can include a `name`.

Wall thickness is the smaller of a ray estimate and a section estimate, and
the result says which one was used. `section_part` shows a cross-section.
`search_parts` and `insert_standard_part` bring in real fasteners and bearings.
Clearance between two parts reports interference volume or the gap.

For a shape that should stay consistent when a size changes, use a sketch:
entities plus geometric and dimensional constraints. `create_sketch` solves it
and says whether it is fully constrained, still free, or conflicting.
`set_dimension` changes a named dimension and rebuilds parts made from that
sketch. Read the sketch image; the dimensions are labeled on it.

To see whether a part survives a load, call `describe_faces`, pick a material
from `list_materials`, and call `solve_mechanics`. Read the safety factor, the
warnings, and the stress picture before you add or remove material. The report
lists its assumptions. A 3D-printed material is weaker across layers than the
in-plane strength used here.

An assembly places several parts with joints (fixed, revolute, prismatic,
cylindrical), gear or linkage relations, named poses, and motions.
`run_motion` sweeps those joints and reports collisions, with a picture per
frame. `check_cables` measures length, strain, bend radius, and pinching.
`estimate_power` turns gravity and payloads into joint torque and then into
hydraulic pressure and flow, or motor current and a rough battery figure.
Read the assumptions on those reports. Cables are kinematic polylines.
""".strip()

_GUIDE = """
# Writing an aicad part

Units are millimeters. The document stores each part by name.

## Script

A script is Python. `build123d` and `math` are already imported. Assign the
finished solid to `part`.

```python
base = Pos(0, 0, 2) * Box(60, 40, 4)
boss = Pos(0, 0, 6) * Cylinder(12, 8)
part = base + boss
part -= Pos(0, 0, 6) * Cylinder(4, 20)
```

`part = a + b` fuses solids. `part -= tool` cuts. A sketch or a curve is not a
part. If an edit fails, the previous solid is kept.

## Feature steps

`feature` is already defined. Use it when you want a contribution report.
An added feature is flagged when at least half of it was already inside the part.

```python
bracket = feature.add("base", Pos(0, 0, 2) * Box(60, 40, 4))
bracket = feature.add("boss", Pos(0, 0, 6) * Cylinder(12, 8))
bracket = feature.cut("bore", Pos(0, 0, 6) * Cylinder(4, 20))
part = bracket
```

`part = feature.result` is the same solid. A script that never calls `feature`
is not tracked, and that is fine.

## Sections

`section_part` cuts on an axis at an offset, or on an arbitrary plane. The
image is the cut, and `area_mm2` is the material in that plane. Use it to see
a rib that is buried inside a boss.

## Standard parts

`search_parts` finds socket-head screws, hex nuts, plain washers, and
deep-groove ball bearings (including the 608 bearing as size `M8-22-7`).
`insert_standard_part` places one. A screw's shank points down: the underside
of the head is on local z=0. Nuts, washers, and bearings sit on z=0.
`import_step` plus `place_part` brings in an arbitrary STEP file, such as a
McMaster-Carr download. Then `check_clearance` against your part.

## Sketches

A sketch is points, lines, arcs, and circles, plus constraints. Initial `at`
coordinates are guesses. `fix` locks a point at its guess. The solver is
SolveSpace. The report's `status` is `fully_constrained`, `under_constrained`
(with `free` naming the entities that can still move), or `over_constrained`
(with `conflicts` naming the constraints that disagree).

A point-to-line distance is signed. Positive is to the right as you walk from
the line's start to its end. On a counterclockwise rectangle the interior is
the negative side, so an inset of 6 mm is distance −6.

```python
sketch = {
  "plane": "xy",
  "entities": [
    {"id": "origin", "type": "point", "at": [0, 0]},
    {"id": "b0", "type": "point", "at": [8, 8]},
    {"id": "b1", "type": "point", "at": [-8, 8]},
    {"id": "bore", "type": "circle", "center": "origin", "radius": 8}
  ],
  "constraints": [
    {"type": "fix", "entity": "origin"},
    {"type": "diameter", "entity": "bore", "value": 16, "name": "bore_diameter",
     "tolerance": 0.05},
    {"type": "pattern_on_circle", "center": "origin", "points": ["b0", "b1"],
     "diameter": 24, "start_angle": 45, "name": "bolt_circle"}
  ]
}
```

`pattern_on_circle` needs at least two points. `create_part_from_sketch` turns
the solved geometry into a solid with extrude, cut, revolve, cut_box, and
gusset steps. An over-constrained sketch is not built. An under-constrained
sketch is built from the current solution, and the report says what is still
free. `set_dimension` on `bore_diameter` or `bolt_circle` rebuilds the solid.

A tolerance such as `0.05` or `{"plus": 0.05, "minus": 0.02}` is stored on the
dimension and shown in the report. It does not change the solved geometry.

## Operations

```json
[
  {"op": "add", "shape": "box", "size": [60, 40, 4], "center": [0, 0, 2]},
  {"op": "add", "shape": "cylinder", "radius": 12, "height": 8, "center": [0, 0, 6]},
  {"op": "cut", "shape": "cylinder", "radius": 4, "height": 20, "center": [0, 0, 6]}
]
```

Boxes are axis-aligned. Cylinders take an optional `axis` (default `[0, 0, 1]`).
`center` defaults to the origin. The first operation must be `add`. Optional
`name` is used in the feature report. Operations are always tracked.

## Mechanics

`list_materials` is a small handbook library: PLA, PETG, ABS, nylon, aluminum
6061, and mild steel. The numbers are typical, not a certified allowable.

`describe_faces` lists every face. A boolean does not keep the name you used
in the script, so you select a face by geometry:

- `{"kind": "plane", "normal": [0, 0, -1]}` is every downward face. Add
  `"at": [0, 0, 0]` for the one closest to that point.
- `{"kind": "cylinder", "radius": 1.7, "axis": [0, 0, 1]}` is every vertical
  hole of that radius. The default tolerance is 0.05 mm, so 1.7 does not also
  match 1.6. Omit `at` to take all of them (the four frame holes).
- `{"kind": "nearest", "at": [0, 0, 12]}` is the face whose center is closest.
- `{"kind": "index", "index": 3}` is one face from the list.

`solve_mechanics` runs a linear static solve. Gmsh builds the tetrahedra and
CalculiX solves them. A setup looks like this:

```json
{
  "material": "pla",
  "mesh_size_mm": 1.5,
  "target_safety_factor": 2,
  "fixes": [
    {"name": "frame_bolts", "select": {"kind": "cylinder", "radius": 1.7, "axis": [0, 0, 1]}}
  ],
  "loads": [
    {"name": "shaft_side", "type": "force", "vector": [100, 0, 0],
     "select": {"kind": "cylinder", "radius": 8, "axis": [0, 0, 1]}},
    {"name": "motor_weight", "type": "force", "vector": [0, 0, -30],
     "select": {"kind": "plane", "normal": [0, 0, 1], "at": [0, 0, 12]}}
  ]
}
```

`type` is `force` (a total force in newtons, shared by the nodes), `pressure`
(MPa, positive pushes into the face), or `bolt` (`axial` newtons along the
hole, positive in the axis direction). `each: true` applies that force or bolt
load once per selected face.

The result has the peak von Mises stress, the peak displacement, the minimum
safety factor and where it occurs, and colored pictures from the front, the
right, the top, and isometric. Each picture is scaled to its own maximum; the
title carries that maximum. `review.status` is `pass`, `fail`, or
`inconclusive`. Overstressed regions are candidates for added material.
Regions carrying almost none of the peak are candidates for removed material.

Read `assumptions` and `warnings` every time. The solve is linear, isotropic,
and small-displacement. Printed materials are weaker across layers. A part
with nothing fixed, a load that hits no face, or a very coarse mesh is reported
and, when the setup cannot be solved, `status` is `not_solved`.

## What to check

- `valid` and `watertight` should both be true for a printable solid.
- `volume_mm3` and `bounding_box` catch a part that exploded or collapsed.
- `features` on `inspect_part` flags an add that was mostly already inside the part.
- `check_wall_thickness` reports a ray estimate and a section estimate, and which one won.
- `section_part` shows internal geometry that the outside views hide.
- `check_clearance` on two parts reports interference, or the gap if they are apart.
- `inspect_part` renders front, back, left, right, top, bottom, and isometric views.
- `solve_mechanics` is a linear static check. Read the safety factor, the review, and the stress picture. A pass on a coarse mesh is `inconclusive`.
- `create_assembly` places parts that are already in the document. A joint is a tree edge: fixed, revolute (degrees), prismatic (millimeters), or cylindrical (degrees plus slide). A gear is `driven = ratio * driver + offset`. A linkage is a planar four-bar, not a spatial loop.
- `run_motion` takes a named sequence or an inline sweep `{sweep: {joint, start, end, frames}}` or keyframes. It reports where solids that are not joined to each other intersect, and returns an isometric frame per sample.
- `check_cables` routes polylines through waypoints fixed on parts. It reports length change, strain, bend-radius violations, and pinching. It does not simulate a flexible body.
- `estimate_power` is quasi-static. Gravity is along −Z. Hydraulic actuators need bore_mm and, on a revolute joint, moment_arm_mm. Electric actuators need a torque constant. The report states what it leaves out.
""".strip()


def serve(workspace: str | Path, connect: str | None = None) -> None:
    server = build_server(workspace, connect=connect)
    server.run(transport="stdio")


def build_server(workspace: str | Path, connect: str | None = None) -> MCPServer:
    if connect:
        from aicad.app import HttpCommandClient

        client = HttpCommandClient(connect)
    else:
        from aicad.workspace import Workspace

        client = Workspace(workspace)
    mcp = MCPServer(
        "aicad",
        instructions=_INSTRUCTIONS,
        version="0.6.0",
    )

    def run(command: str, **arguments):
        payload = {key: value for key, value in arguments.items() if value is not None}
        return client.execute(command, payload, source="mcp")

    def guard(fn):
        try:
            return fn()
        except AICADError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def list_parts() -> dict:
        """List the parts in the current document, with volume and bounding box."""
        return guard(lambda: run("list_parts"))

    @mcp.tool()
    def create_part_from_script(name: str, script: str) -> dict:
        """Create a part by running a build123d script. Assign the solid to `part`.

        Units are millimeters. build123d and math are already imported. Example:
        `plate = Box(40, 20, 3)` then `part = plate`. After this returns, call
        inspect_part and look at the pictures before editing again.
        """
        return guard(lambda: run("create_part_from_script", name=name, script=script))

    @mcp.tool()
    def create_part_from_ops(name: str, ops: list[dict[str, Any]]) -> dict:
        """Create a part from structured add/cut operations.

        Each operation is `{"op": "add"|"cut", "shape": "box"|"cylinder", ...}`.
        A box needs `size` [x, y, z]. A cylinder needs `radius` and `height`.
        Optional `center` [x, y, z] and, for cylinders, `axis` [x, y, z].
        Then call inspect_part.
        """
        return guard(lambda: run("create_part_from_ops", name=name, ops=ops))

    @mcp.tool()
    def update_part_script(name: str, script: str) -> dict:
        """Replace a part by running a new script. A failed script keeps the old solid."""
        return guard(lambda: run("update_part_script", name=name, script=script))

    @mcp.tool()
    def update_part_ops(name: str, ops: list[dict[str, Any]]) -> dict:
        """Replace a part with a new operation list. A failed rebuild keeps the old solid."""
        return guard(lambda: run("update_part_ops", name=name, ops=ops))

    @mcp.tool()
    def delete_part(name: str) -> dict:
        """Delete a part and its rendered views from the document."""
        return guard(lambda: run("delete_part", name=name))

    @mcp.tool()
    def get_part_source(name: str) -> dict:
        """Return the script, operations, or STEP note that rebuilds this part."""
        return guard(lambda: run("get_part_source", name=name))

    @mcp.tool()
    def measure_part(name: str) -> dict:
        """Bounding box, volume, area, center of mass, counts, valid, and watertight."""
        return guard(lambda: run("measure_part", name=name))

    @mcp.tool()
    def check_wall_thickness(name: str, min_wall_mm: float, samples: int = 4) -> dict:
        """Estimate minimum wall thickness and compare it to min_wall_mm.

        The estimate samples each face. It can miss a thin spot between samples.
        A typical FDM rule of thumb is 1.2 mm. `samples` is 1 to 8 per face edge.
        """
        return guard(lambda: run("check_wall_thickness", name=name, min_wall_mm=min_wall_mm, samples=samples))

    @mcp.tool()
    def check_clearance(part_a: str, part_b: str, min_clearance_mm: float = 0.0) -> dict:
        """Check interference or gap between two parts.

        If they overlap, `intersecting` is true and `interference_volume_mm3` is
        the overlap. Otherwise `min_distance_mm` is the gap between surfaces.
        `min_clearance_mm` of 0 requires that the parts do not touch or overlap.
        """
        return guard(
            lambda: run("check_clearance", part_a=part_a, part_b=part_b, min_clearance_mm=min_clearance_mm)
        )

    @mcp.tool()
    def render_part(name: str) -> list[str | Image]:
        """Render front, back, left, right, top, bottom, isometric, and a contact sheet.

        The images are what the part looks like. The text lists their file paths.
        """
        return guard(lambda: _images(run("render_part", name=name)))

    @mcp.tool()
    def inspect_part(
        name: str, min_wall_mm: float | None = None, samples: int = 4
    ) -> list[str | Image]:
        """Measure, estimate wall thickness, and render a part. Call this after every edit.

        Pass min_wall_mm (for example 1.2) to score the estimate against a print rule.
        Look at the contact sheet, then the individual views. Do not treat a script
        that ran cleanly as a part that is correct.
        """
        return guard(lambda: _images(run("inspect_part", name=name, min_wall_mm=min_wall_mm, samples=samples)))

    @mcp.tool()
    def export_part(name: str, format: str, path: str | None = None) -> dict:
        """Export a part to STEP or STL. `format` is `step` or `stl`.

        `path` is optional. When omitted, the file is written under the workspace
        exports directory. STEP is the solid; STL is a triangle mesh for printing.
        """
        return guard(lambda: run("export_part", name=name, format=format, path=path))

    @mcp.tool()
    def feature_report(name: str) -> dict:
        """How much of each boolean step became new material, or was already inside the part.

        An added feature is flagged when at least half of its volume was buried.
        A cut is flagged when it removes nothing. Parts built without feature.add
        or feature.cut return tracked=false. Operation lists are always tracked.
        """
        return guard(lambda: run("feature_report", name=name))

    @mcp.tool()
    def section_part(
        name: str,
        axis: str | None = None,
        offset: float = 0.0,
        origin: list[float] | None = None,
        normal: list[float] | None = None,
    ) -> list[str | Image]:
        """Render a planar cross-section and return its area and outline.

        Pass axis ('x', 'y', or 'z') and offset in millimeters, or origin and
        normal as [x, y, z]. The image is the cut. area_mm2 is the material in
        that plane. Use this to see whether a rib is buried inside a boss.
        """
        return guard(lambda: _images(run("section_part", name=name, axis=axis, offset=offset, origin=origin, normal=normal)))

    @mcp.tool()
    def create_sketch(name: str, spec: dict[str, Any]) -> list[str | Image]:
        """Solve and store a 2D sketch of entities and constraints.

        spec has `entities` (point, line, circle, arc) and `constraints`
        (fix, coincident, parallel, perpendicular, tangent, equal, horizontal,
        vertical, symmetric, midpoint, distance, angle, radius, diameter,
        pattern_on_circle). Initial `at` coordinates are guesses. `fix` locks
        a point. Name a distance, diameter, radius, angle, or pattern so
        set_dimension can change it later. A tolerance is a number or
        {plus, minus}; it is stored and does not move the geometry.

        The result says fully_constrained, under_constrained (which entities
        are still free), or over_constrained (which constraints conflict).
        The image shows the solved sketch with dimensions labeled.
        """
        return guard(lambda: _images(run("create_sketch", name=name, spec=spec)))

    @mcp.tool()
    def get_sketch(name: str) -> list[str | Image]:
        """Solve a stored sketch again and return the report plus a labeled image.

        Read status, dof, free, and conflicts before building a solid from it.
        """
        return guard(lambda: _images(run("get_sketch", name=name)))

    @mcp.tool()
    def set_dimension(sketch: str, dimension: str, value: float) -> list[str | Image]:
        """Change a named sketch dimension, re-solve, and rebuild parts made from it.

        value is millimeters, or degrees for an angle. A pattern_on_circle
        dimension is the circle's diameter. A conflicting value is rejected
        and the previous solid stays. rebuilt_parts lists the solids that changed.
        """
        return guard(lambda: _images(run("set_dimension", sketch=sketch, dimension=dimension, value=value)))

    @mcp.tool()
    def create_part_from_sketch(name: str, sketch: str, steps: list[dict[str, Any]]) -> list[str | Image]:
        """Extrude, revolve, or cut a solved sketch into a solid.

        steps is a list. An extrude or cut uses `points` (ids, in order) or
        `circle` (a circle id), plus `height` and optional `z` (the bottom).
        revolve uses `points`, `axis` (`x` or `y`), and optional `angle`.
        cut_box uses `center` and `size`. gusset uses `angle`, `outer`,
        `plate_thickness`, and `boss_height`. Each step can have a `name`.

        Over-constrained and non-converged sketches are refused. An
        under-constrained sketch builds the current solution and the report
        names what is still free. Then call inspect_part.
        """
        return guard(lambda: _images(run("create_part_from_sketch", name=name, sketch=sketch, steps=steps)))

    @mcp.tool()
    def search_parts(query: str = "") -> dict:
        """Search standard screws, nuts, washers, and ball bearings.

        An empty query lists the kinds. Try 'M3', 'screw', 'nut', 'washer',
        'bearing', or '608'. Screws need a length when inserted. A screw's shank
        points down, with the underside of the head on local z=0.
        """
        return guard(lambda: run("search_parts", query=query))

    @mcp.tool()
    def insert_standard_part(
        name: str,
        kind: str,
        size: str,
        length: float | None = None,
        position: list[float] | None = None,
        rotation_deg: list[float] | None = None,
        fastener_type: str | None = None,
    ) -> dict:
        """Insert a catalog part and place it.

        kind is socket_head_cap_screw, hex_nut, plain_washer, or ball_bearing.
        size is a designation such as M3-0.5, M3, or M8-22-7 (608 is accepted).
        length is required for screws and is the shank length under the head, in mm.
        position and rotation_deg are [x, y, z]. Rotation is degrees about X, Y, Z
        and is applied before the translation. Then check_clearance against your part.
        """
        return guard(
            lambda: run(
                "insert_standard_part",
                name=name,
                kind=kind,
                size=size,
                length=length,
                position=position,
                rotation_deg=rotation_deg,
                fastener_type=fastener_type,
            )
        )

    @mcp.tool()
    def place_part(
        name: str,
        position: list[float],
        rotation_deg: list[float] | None = None,
    ) -> dict:
        """Move a part, including an imported STEP solid. Replaces any previous placement.

        position is [x, y, z] in millimeters. rotation_deg is degrees about X, Y, and Z,
        applied before the translation.
        """
        return guard(lambda: run("place_part", name=name, position=position, rotation_deg=rotation_deg))

    @mcp.tool()
    def list_materials() -> dict:
        """List the small material library used by solve_mechanics.

        PLA, PETG, ABS, nylon, aluminum 6061, and mild steel, with modulus,
        Poisson's ratio, and a strength. Plastics use a tensile strength.
        Metals use a yield strength. These are handbook ballparks, not a
        certified allowable. Printed materials are weaker across layers than
        the in-plane number used here.
        """
        return guard(lambda: run("list_materials"))

    @mcp.tool()
    def describe_faces(name: str) -> dict:
        """List a part's faces so a support or a load can name them.

        Each face has a kind (plane, cylinder, or other), a center, an area,
        and, when it applies, an outward normal or a radius and axis. Feature
        names do not survive a boolean, so select by geometry in solve_mechanics:
        plane plus normal, cylinder plus radius, nearest plus at, or an index.
        """
        return guard(lambda: run("describe_faces", name=name))

    @mcp.tool()
    def solve_mechanics(name: str, setup: dict[str, Any]) -> list[str | Image]:
        """Run a linear static solve and return stress and displacement pictures.

        setup needs material (a name from list_materials), fixes, and loads.
        Optional mesh_size_mm and target_safety_factor (default 2). A fix is
        {"name", "select"}. A load adds "type" of force, pressure, or bolt:
        force has vector [x, y, z] in newtons, pressure has magnitude in MPa
        (positive pushes into the face), bolt has axial newtons along the hole.
        select is the same geometry selector describe_faces is for.

        Read status, min_safety_factor, review, assumptions, and warnings.
        The stress contact sheet is the first image. Each picture is scaled to
        its own maximum, and the title carries that maximum. review.status is
        pass, fail, or inconclusive. Overstressed regions are candidates for
        added material; nearly unloaded regions are candidates for removed material.
        """
        return guard(lambda: _images(run("solve_mechanics", name=name, setup=setup)))

    @mcp.tool()
    def import_step(name: str, path: str) -> dict:
        """Copy a STEP file into the document as a named part.

        Use this to bring in a motor, a bearing, or another solid to check
        clearance against. The file is copied into the workspace so the part
        can be rebuilt later.
        """
        return guard(lambda: run("import_step", name=name, path=path))

    @mcp.tool()
    def describe_edges(name: str) -> dict:
        """List edges with stable ref strings, the same strings a click in the workspace uses."""
        return guard(lambda: run("describe_edges", name=name))

    @mcp.tool()
    def update_sketch(name: str, spec: dict[str, Any]) -> list[str | Image]:
        """Replace a sketch and rebuild parts made from it. The report says if it is fully constrained."""
        return guard(lambda: _images(run("update_sketch", name=name, spec=spec)))

    @mcp.tool()
    def update_part_from_sketch(name: str, sketch: str, steps: list[dict[str, Any]]) -> list[str | Image]:
        """Replace a part with new extrude, revolve, or cut steps from a sketch."""
        return guard(lambda: _images(run("update_part_from_sketch", name=name, sketch=sketch, steps=steps)))

    @mcp.tool()
    def set_selection(part: str | None = None, faces: list[str] | None = None, edges: list[str] | None = None) -> dict:
        """Select faces and edges by ref. The workspace viewport highlights the same selection."""
        return guard(lambda: run("set_selection", part=part, faces=faces, edges=edges))

    @mcp.tool()
    def get_requirements() -> dict:
        """Structured requirements and the latest pass, fail, or unchecked result for each."""
        return guard(lambda: run("get_requirements"))

    @mcp.tool()
    def set_requirements(requirements: list[dict[str, Any]]) -> dict:
        """Replace structured requirements. Kinds: max_mass, fits_build_volume, min_wall, min_safety_factor."""
        return guard(lambda: run("set_requirements", requirements=requirements))

    @mcp.tool()
    def check_requirements(run_checks: bool = True) -> dict:
        """Score requirements against the current design. Safety-factor checks solve when run_checks is true."""
        return guard(lambda: run("check_requirements", run=run_checks))

    @mcp.tool()
    def list_documents() -> dict:
        """List the project's markdown notes."""
        return guard(lambda: run("list_documents"))

    @mcp.tool()
    def read_document(name: str) -> dict:
        """Read one markdown note, such as decisions.md."""
        return guard(lambda: run("read_document", name=name))

    @mcp.tool()
    def write_document(name: str, markdown: str) -> dict:
        """Write a markdown note in the project documents folder."""
        return guard(lambda: run("write_document", name=name, markdown=markdown))

    @mcp.tool()
    def create_assembly(spec: dict[str, Any]) -> dict:
        """Place parts that are already in the document.

        spec needs name and instances [{name, part, material, payload_kg}].
        joints are {name, type, parent, child, parent_anchor, child_anchor, axis, value, min, max}.
        type is fixed, revolute (degrees), prismatic (millimeters), or cylindrical
        (degrees in value, millimeters in slide). relations are gear {driver, driven, ratio, offset}
        or linkage {driver, driven, ground_mm, crank_mm, coupler_mm, rocker_mm, branch}.
        A linkage is a planar four-bar map, not a closed spatial loop.
        poses, motions, cables, and actuators live on the same spec.
        """
        return guard(lambda: run("create_assembly", spec=spec))

    @mcp.tool()
    def update_assembly(spec: dict[str, Any]) -> dict:
        """Replace an assembly spec."""
        return guard(lambda: run("update_assembly", spec=spec))

    @mcp.tool()
    def get_assembly(name: str) -> dict:
        """Solve an assembly and return joint values, limits, and instance origins."""
        return guard(lambda: run("get_assembly", name=name))

    @mcp.tool()
    def list_assemblies() -> dict:
        """List assemblies, joint values, poses, and motion names."""
        return guard(lambda: run("list_assemblies"))

    @mcp.tool()
    def set_joint(name: str, joint: str, value: float, slide: float | None = None) -> dict:
        """Set a joint. Revolute values are degrees. Prismatic values are millimeters."""
        return guard(lambda: run("set_joint", name=name, joint=joint, value=value, slide=slide))

    @mcp.tool()
    def set_pose(name: str, pose: str) -> dict:
        """Apply a named pose. A driven joint follows its gear or linkage."""
        return guard(lambda: run("set_pose", name=name, pose=pose))

    @mcp.tool()
    def run_motion(name: str, sequence: str | None = None, motion: dict[str, Any] | None = None) -> list[str | Image]:
        """Sweep a joint or play keyframes. Reports collisions and returns a frame per sample.

        sequence names a motion stored on the assembly. motion is an inline
        {sweep: {joint, start, end, frames}} or {keyframes, samples, duration_s}.
        A collision record has the frame, the two instances, and a point.
        Parent and child of a joint are allowed to touch.
        """
        return guard(lambda: _images(run("run_motion", name=name, sequence=sequence, motion=motion)))

    @mcp.tool()
    def check_cables(name: str, sequence: str | None = None, motion: dict[str, Any] | None = None) -> dict:
        """Check cables and hoses through the current pose or a motion.

        Each route has waypoints {instance, at, clip}, diameter_mm, min_bend_radius_mm,
        and stiffness (low, medium, high, or newtons per unit strain). The report
        has length change, strain, bend-radius violations, and pinches. The route
        is a polyline. It does not sag.
        """
        return guard(lambda: run("check_cables", name=name, sequence=sequence, motion=motion))

    @mcp.tool()
    def estimate_power(
        name: str,
        sequence: str | None = None,
        motion: dict[str, Any] | None = None,
        actuators: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Estimate joint torque and actuator power from gravity and payloads.

        Quasi-static: no inertia. A hydraulic_cylinder needs bore_mm, rod_mm, and
        moment_arm_mm on a revolute joint. An electric actuator needs
        torque_constant_nm_per_a or force_constant_n_per_a, plus voltage.
        Read assumptions. Pressure is MPa, flow is mm³/s, power is watts.
        """
        return guard(
            lambda: run(
                "estimate_power",
                name=name,
                sequence=sequence,
                motion=motion,
                actuators=actuators,
            )
        )

    @mcp.tool()
    def render_assembly(name: str) -> list[str | Image]:
        """Render the assembly in its current pose."""
        return guard(lambda: _images(run("render_assembly", name=name)))

    @mcp.tool()
    def get_state() -> dict:
        """Parts, assemblies, sketches, the current selection, requirement checks, and the latest panels."""
        return guard(lambda: run("get_state"))

    @mcp.resource("aicad://guide", mime_type="text/markdown")
    def guide() -> str:
        """How to write a script or an operation list, and what to check afterwards."""
        return _GUIDE

    return mcp


def _images(payload: dict) -> list[str | Image]:
    views = []
    for view in payload.get("views") or []:
        if isinstance(view, dict) and view.get("path"):
            views.append(_View(view.get("name") or "view", view["path"]))
    single = payload.get("view")
    if isinstance(single, dict) and single.get("path"):
        views.append(_View(single.get("name") or "view", single["path"]))
    if not views:
        return [json.dumps(payload, indent=2)]
    return _with_images(payload, views)


class _View:
    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path


def _with_images(payload: dict, views) -> list[str | Image]:
    ordered = sorted(views, key=lambda view: _VIEW_RANK.get(view.name, 50))
    blocks: list[str | Image] = [json.dumps(payload, indent=2)]
    for view in ordered:
        blocks.append(Image(path=view.path))
    return blocks


_VIEW_RANK = {
    "contact": 0,
    "stress_contact": 0,
    "displacement_contact": 1,
    "stress_iso": 2,
    "displacement_iso": 3,
    "sketch": 1,
    "section": 1,
    "iso": 2,
    "stress_front": 4,
    "stress_right": 5,
    "stress_top": 6,
    "displacement_front": 7,
    "displacement_right": 8,
    "displacement_top": 9,
    "front": 3,
    "right": 4,
    "top": 5,
    "back": 6,
    "left": 7,
    "bottom": 8,
}
