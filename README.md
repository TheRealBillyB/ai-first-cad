# aicad

A CAD tool built so an assistant can use it as well as a person can.

Today an assistant writing CAD code (OpenSCAD, CadQuery, build123d) works blind. It emits a script and hopes the solid is the one you described. aicad is the missing perception loop: after each modeling step the assistant can look at the part and check it. The focus is hard-body mechanical parts, the kind you print or bolt onto a robot.

This is a small first version. It does not invent a geometry kernel. [build123d](https://github.com/gumyr/build123d) (OpenCascade) sits behind one module, `aicad.kernel`, so it can be swapped later. Everything an assistant calls is plain Python, a CLI, or an MCP server.

![Motor mount bracket from the standard views](examples/renders/motor_mount_contact.png)

## What you can do

- Create or replace a part by running a build123d script, or with a short list of `add` / `cut` boxes and cylinders.
- Keep a document of named parts on disk. The script (or the operation list, or a copied STEP file) is the source. Opening the workspace rebuilds the solids.
- Measure a part: bounding box, volume, surface area, center of mass, face and edge counts, and whether the solid is valid and watertight.
- Render it headless, with no GPU and no display: front, back, left, right, top, bottom, isometric, and one contact sheet. The MCP tools return those pictures so the assistant can see them.
- Export STEP and STL.
- Estimate minimum wall thickness and compare it with a threshold (a useful 3D-print check).
- Check interference or clearance between two parts.

Units are millimeters.

## Install

You need Python 3.11 or newer. The OpenCascade wheel that build123d depends on is large.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Try the motor mount

The worked example is a 72 × 52 × 4 mm plate with a boss, a 16 mm bore, four motor screws, four frame holes, a cable slot on the +Y edge, and two ribs. `examples/shaft.py` is a 15.6 mm shaft meant to pass through that bore.

```bash
python examples/motor_mount.py
```

That writes `examples/renders/` and `examples/motor_mount_report.json`. The same checks from the CLI:

```bash
aicad run examples/motor_mount.py --name motor_mount --workspace ./aicad_workspace
aicad run examples/shaft.py --name shaft --workspace ./aicad_workspace
aicad inspect motor_mount --min-wall 1.2 --workspace ./aicad_workspace
aicad check-clearance motor_mount shaft --min 0.1 --workspace ./aicad_workspace
aicad export motor_mount --format step --out motor_mount.step --workspace ./aicad_workspace
aicad export motor_mount --format stl --out motor_mount.stl --workspace ./aicad_workspace
```

On the build that produced the pictures in this repo, inspect reported:

| Check | Result |
| --- | --- |
| Bounding box | 72 × 52 × 12 mm |
| Volume | 20617 mm³ |
| Valid and watertight | yes, one solid |
| Estimated minimum wall | 2.4 mm, at the web between the bore and a motor screw |
| Against a 1.2 mm print rule | pass |
| Gap to the 15.6 mm shaft | 0.2 mm, no interference |

The wall number is a sample, not a proof. It happened to land on the thin web. A thinner spot between samples would not show up. See [Limitations](#limitations).

## Connect an assistant

`aicad serve` speaks [MCP](https://modelcontextprotocol.io) over stdio. The tools are the same operations as the CLI.

Cursor (`~/.cursor/mcp.json`, or the project's `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "aicad": {
      "command": "aicad",
      "args": ["serve", "--workspace", "/absolute/path/to/aicad_workspace"]
    }
  }
}
```

Claude Desktop:

```json
{
  "mcpServers": {
    "aicad": {
      "command": "/absolute/path/to/.venv/bin/aicad",
      "args": ["serve", "--workspace", "/absolute/path/to/aicad_workspace"]
    }
  }
}
```

Use the virtualenv's `aicad` if it is not already on the assistant's `PATH`. The workspace directory is the session. It holds `session.json`, rendered PNGs, exports, and any STEP files you import.

A resource at `aicad://guide` repeats the script rules below. The server instructions tell the assistant to call `inspect_part` after every edit and to look at the pictures.

### Tools

| Tool | What it does |
| --- | --- |
| `create_part_from_script` | Run a build123d script. The solid must be assigned to `part`. |
| `create_part_from_ops` | Build from a list of add/cut boxes and cylinders. |
| `update_part_script` / `update_part_ops` | Replace a part. A failed rebuild keeps the previous solid. |
| `import_step` | Copy a STEP file in as a named part (a motor, a bearing, a plate). |
| `inspect_part` | Measure, estimate wall thickness, and return the pictures. Call this after every edit. |
| `measure_part` | Bounding box, volume, area, center of mass, counts, valid, watertight. |
| `render_part` | The standard views and the contact sheet, as images. |
| `check_wall_thickness` | Sampled minimum thickness against a threshold in mm. |
| `check_clearance` | Overlap volume, or the gap between two parts. |
| `export_part` | Write STEP or STL. |
| `list_parts` / `get_part_source` / `delete_part` | Document housekeeping. |

`render_part` and `inspect_part` return PNG image blocks plus a JSON report. The assistant should look at the contact sheet, not only the numbers.

## Writing a part

A script is ordinary Python. `build123d` and `math` are already imported. End by assigning the solid to `part`.

```python
base = Pos(0, 0, 2) * Box(60, 40, 4)
boss = Pos(0, 0, 6) * Cylinder(12, 8)
part = base + boss
part -= Pos(0, 0, 6) * Cylinder(4, 20)  # shaft bore
```

`part = a + b` fuses. `part -= tool` cuts. A sketch or a curve is rejected: a part has to have volume.

The same plate as operations, which is enough for a stack of boxes and holes:

```json
[
  {"op": "add", "shape": "box", "size": [60, 40, 4], "center": [0, 0, 2]},
  {"op": "cut", "shape": "cylinder", "radius": 4, "height": 10, "center": [0, 0, 2]}
]
```

Boxes are axis-aligned. A cylinder takes `radius`, `height`, an optional `center`, and an optional `axis` (default `[0, 0, 1]`). The first operation has to be `add`.

Views use Z up. The label on each picture says which way the camera is looking (`front` looks toward +Y). A small X/Y/Z triad is drawn on the view, and the footer carries the bounding box and a scale bar.

## How the checks work

**Watertight.** A part is watertight when build123d reports it valid and manifold: every edge belongs to exactly two faces, the solid has volume, and the OpenCascade shape check passes. That is the right meaning for a single closed body. `solid_count` tells you when the result is more than one body.

**Wall thickness.** A grid of points is taken on each face. From each point a ray is shot into the material, and the distance to the next roughly parallel face is kept. The minimum is the estimate. `thinnest_point_mm` is where that sample sat, which is often enough to see which feature is thin. Samples can miss a slot, a knife edge, or a web that falls between the grid.

**Clearance.** The interference volume is the boolean intersection of the two solids. If they do not overlap, `min_distance_mm` is the gap between the surfaces, and the closest points are reported. The gap is not a penetration depth: overlapping parts report distance 0 and a volume.

## Development

```bash
pytest
python examples/motor_mount.py   # refresh examples/renders and the report
```

`src/aicad/kernel.py` is the only module that imports build123d or OpenCascade.

## Limitations

- Scripts are trusted Python, not a sandbox. The server runs code the assistant sends. Point it at a workspace you are willing to let it write.
- There is no feature tree. Editing means replacing the script or the operation list. A failed replacement does not destroy the previous solid.
- Structured operations are only axis-aligned boxes and cylinders. Fillets, chamfers, and rotated boxes belong in a script.
- Wall thickness is a sampled estimate. A passing check is evidence, not a guarantee, and anything thinner than about 0.15 mm is below the ray step.
- Clearance does not report how deep an interference goes, only its volume.
- Renders are shaded orthographic pictures from a software rasterizer, not a dimensioned drawing. There is no section view and no click-to-measure.
- The session file is for one writer. Two servers on the same workspace will overwrite each other.
- No parts library yet. `import_step` is the way to bring in a screw, a motor, or a bearing.

## Next steps

- A real feature history, so an assistant can change one dimension without rewriting the whole script.
- A parts catalog (screws, bearings, NEMA motors), likely on top of STEP rather than a new kernel.
- Section views and a few driving dimensions on the pictures.
- A tighter wall-thickness check, for example an offset of the solid, used beside the ray samples.
- Assemblies and joints, so a bracket and a motor can be placed, not only checked for a gap.
- A small viewer a person can orbit while the assistant works.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
