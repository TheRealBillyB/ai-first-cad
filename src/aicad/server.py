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

## What to check

- `valid` and `watertight` should both be true for a printable solid.
- `volume_mm3` and `bounding_box` catch a part that exploded or collapsed.
- `features` on `inspect_part` flags an add that was mostly already inside the part.
- `check_wall_thickness` reports a ray estimate and a section estimate, and which one won.
- `section_part` shows internal geometry that the outside views hide.
- `check_clearance` on two parts reports interference, or the gap if they are apart.
- `inspect_part` renders front, back, left, right, top, bottom, and isometric views.
""".strip()


def serve(workspace: str | Path) -> None:
    server = build_server(workspace)
    server.run(transport="stdio")


def build_server(workspace: str | Path) -> MCPServer:
    document = Document(workspace)
    mcp = MCPServer(
        "aicad",
        instructions=_INSTRUCTIONS,
        version="0.2.0",
    )

    def guard(fn):
        try:
            return fn()
        except AICADError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def list_parts() -> dict:
        """List the parts in the current document, with volume and bounding box."""
        return guard(lambda: {"units": "mm", "parts": [part.to_dict() for part in document.list_parts()]})

    @mcp.tool()
    def create_part_from_script(name: str, script: str) -> dict:
        """Create a part by running a build123d script. Assign the solid to `part`.

        Units are millimeters. build123d and math are already imported. Example:
        `plate = Box(40, 20, 3)` then `part = plate`. After this returns, call
        inspect_part and look at the pictures before editing again.
        """
        return guard(
            lambda: {
                "part": document.create_part_from_script(name, script).to_dict(),
                "next": "Call inspect_part and look at the contact sheet before the next edit.",
            }
        )

    @mcp.tool()
    def create_part_from_ops(name: str, ops: list[dict[str, Any]]) -> dict:
        """Create a part from structured add/cut operations.

        Each operation is `{"op": "add"|"cut", "shape": "box"|"cylinder", ...}`.
        A box needs `size` [x, y, z]. A cylinder needs `radius` and `height`.
        Optional `center` [x, y, z] and, for cylinders, `axis` [x, y, z].
        Then call inspect_part.
        """
        return guard(
            lambda: {
                "part": document.create_part_from_ops(name, ops).to_dict(),
                "next": "Call inspect_part and look at the contact sheet before the next edit.",
            }
        )

    @mcp.tool()
    def update_part_script(name: str, script: str) -> dict:
        """Replace a part by running a new script. A failed script keeps the old solid."""
        return guard(
            lambda: {
                "part": document.update_part_script(name, script).to_dict(),
                "next": "Call inspect_part and look at the contact sheet before the next edit.",
            }
        )

    @mcp.tool()
    def update_part_ops(name: str, ops: list[dict[str, Any]]) -> dict:
        """Replace a part with a new operation list. A failed rebuild keeps the old solid."""
        return guard(
            lambda: {
                "part": document.update_part_ops(name, ops).to_dict(),
                "next": "Call inspect_part and look at the contact sheet before the next edit.",
            }
        )

    @mcp.tool()
    def delete_part(name: str) -> dict:
        """Delete a part and its rendered views from the document."""
        def _delete() -> dict:
            document.delete_part(name)
            return {"deleted": name}

        return guard(_delete)

    @mcp.tool()
    def get_part_source(name: str) -> dict:
        """Return the script, operations, or STEP note that rebuilds this part."""
        return guard(lambda: document.get_source(name))

    @mcp.tool()
    def measure_part(name: str) -> dict:
        """Bounding box, volume, area, center of mass, counts, valid, and watertight."""
        return guard(lambda: document.measure(name).to_dict())

    @mcp.tool()
    def check_wall_thickness(name: str, min_wall_mm: float, samples: int = 4) -> dict:
        """Estimate minimum wall thickness and compare it to min_wall_mm.

        The estimate samples each face. It can miss a thin spot between samples.
        A typical FDM rule of thumb is 1.2 mm. `samples` is 1 to 8 per face edge.
        """
        return guard(lambda: document.check_wall_thickness(name, min_wall_mm, samples).to_dict())

    @mcp.tool()
    def check_clearance(part_a: str, part_b: str, min_clearance_mm: float = 0.0) -> dict:
        """Check interference or gap between two parts.

        If they overlap, `intersecting` is true and `interference_volume_mm3` is
        the overlap. Otherwise `min_distance_mm` is the gap between surfaces.
        `min_clearance_mm` of 0 requires that the parts do not touch or overlap.
        """
        return guard(
            lambda: document.check_clearance(part_a, part_b, min_clearance_mm).to_dict()
        )

    @mcp.tool()
    def render_part(name: str) -> list[str | Image]:
        """Render front, back, left, right, top, bottom, isometric, and a contact sheet.

        The images are what the part looks like. The text lists their file paths.
        """
        def _render() -> list[str | Image]:
            report_views = document.render(name)
            return _with_images({"views": [view.to_dict() for view in report_views]}, report_views)

        return guard(_render)

    @mcp.tool()
    def inspect_part(
        name: str, min_wall_mm: float | None = None, samples: int = 4
    ) -> list[str | Image]:
        """Measure, estimate wall thickness, and render a part. Call this after every edit.

        Pass min_wall_mm (for example 1.2) to score the estimate against a print rule.
        Look at the contact sheet, then the individual views. Do not treat a script
        that ran cleanly as a part that is correct.
        """
        def _inspect() -> list[str | Image]:
            report: InspectReport = document.inspect(name, min_wall_mm, samples)
            return _with_images(report.to_dict(), report.views)

        return guard(_inspect)

    @mcp.tool()
    def export_part(name: str, format: str, path: str | None = None) -> dict:
        """Export a part to STEP or STL. `format` is `step` or `stl`.

        `path` is optional. When omitted, the file is written under the workspace
        exports directory. STEP is the solid; STL is a triangle mesh for printing.
        """
        def _export() -> dict:
            destination = path
            if destination is None:
                destination = str(document.workspace / "exports" / f"{name}.{format}")
            written = document.export(name, destination, format)
            return {"format": format, "path": str(written), "bytes": written.stat().st_size}

        return guard(_export)

    @mcp.tool()
    def feature_report(name: str) -> dict:
        """How much of each boolean step became new material, or was already inside the part.

        An added feature is flagged when at least half of its volume was buried.
        A cut is flagged when it removes nothing. Parts built without feature.add
        or feature.cut return tracked=false. Operation lists are always tracked.
        """
        return guard(lambda: document.feature_report(name).to_dict())

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
        def _section() -> list[str | Image]:
            if origin is not None or normal is not None:
                if origin is None or normal is None:
                    raise ToolError("An arbitrary section needs both origin and normal.")
                plane = {"origin": origin, "normal": normal}
            elif axis is not None:
                plane = {"axis": axis, "offset": offset}
            else:
                raise ToolError("Pass axis and offset, or origin and normal.")
            report = document.section(name, plane)
            view = report.view
            payload = report.to_dict()
            if view is None:
                return [json.dumps(payload, indent=2)]
            return [json.dumps(payload, indent=2), Image(path=view.path)]

        return guard(_section)

    @mcp.tool()
    def search_parts(query: str = "") -> dict:
        """Search standard screws, nuts, washers, and ball bearings.

        An empty query lists the kinds. Try 'M3', 'screw', 'nut', 'washer',
        'bearing', or '608'. Screws need a length when inserted. A screw's shank
        points down, with the underside of the head on local z=0.
        """
        return guard(lambda: document.search_parts(query))

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
            lambda: {
                "part": document.insert_standard_part(
                    name, kind, size, length, position, rotation_deg, fastener_type
                ).to_dict(),
                "next": "Call check_clearance against the mating part, and inspect_part to see it.",
            }
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
        return guard(
            lambda: {
                "part": document.place_part(name, position, rotation_deg).to_dict(),
                "next": "Call check_clearance if this part should fit another part.",
            }
        )

    @mcp.tool()
    def import_step(name: str, path: str) -> dict:
        """Copy a STEP file into the document as a named part.

        Use this to bring in a motor, a bearing, or another solid to check
        clearance against. The file is copied into the workspace so the part
        can be rebuilt later.
        """
        return guard(
            lambda: {
                "part": document.import_step(name, path).to_dict(),
                "next": "Call inspect_part if you need to see the imported solid.",
            }
        )

    @mcp.resource("aicad://guide", mime_type="text/markdown")
    def guide() -> str:
        """How to write a script or an operation list, and what to check afterwards."""
        return _GUIDE

    return mcp


def _with_images(payload: dict, views) -> list[str | Image]:
    ordered = sorted(views, key=lambda view: _VIEW_RANK.get(view.name, 50))
    blocks: list[str | Image] = [json.dumps(payload, indent=2)]
    for view in ordered:
        blocks.append(Image(path=view.path))
    return blocks


_VIEW_RANK = {
    "contact": 0,
    "section": 1,
    "iso": 2,
    "front": 3,
    "right": 4,
    "top": 5,
    "back": 6,
    "left": 7,
    "bottom": 8,
}
