# aicad

A CAD tool built so an assistant can use it as well as a person can.

Today an assistant writing CAD code (OpenSCAD, CadQuery, build123d) works blind. It emits a script and hopes the solid is the one you described. aicad is the missing perception loop: after each modeling step the assistant can look at the part and check it. The focus is hard-body mechanical parts, the kind you print or bolt onto a robot.

This is a small first version. It does not invent a geometry kernel. [build123d](https://github.com/gumyr/build123d) (OpenCascade) sits behind one module, `aicad.kernel`, so it can be swapped later. Everything an assistant calls is plain Python, a CLI, or an MCP server.

![Motor mount bracket from the standard views](examples/renders/motor_mount_contact.png)

![Section on y = 0, through the bore and the gussets](examples/renders/motor_mount_section_y0.png)

## What you can do

- Create or replace a part by running a build123d script, or with a short list of `add` / `cut` boxes and cylinders.
- Optionally record each boolean step with `feature.add` / `feature.cut`, and see how much of an added solid was already buried inside the part.
- Keep a document of named parts on disk. The script (or the operation list, a catalog part, or a copied STEP file) is the source. Opening the workspace rebuilds the solids.
- Measure a part: bounding box, volume, surface area, center of mass, face and edge counts, and whether the solid is valid and watertight.
- Render it headless, with no GPU and no display: front, back, left, right, top, bottom, isometric, one contact sheet, and a planar section. The MCP tools return those pictures so the assistant can see them.
- Export STEP and STL. Import a STEP file and place it.
- Search a small catalog of real screws, nuts, washers, and ball bearings, insert one, and check clearance against your part.
- Estimate minimum wall thickness from rays and from cross-sections, and compare it with a threshold. The report says which method produced the number.
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

The worked example is a 72 × 52 × 4 mm plate with a boss, a 16 mm bore, four motor screws, four frame holes, a cable slot on the +Y edge, and three gussets that brace the boss to the plate. `examples/shaft.py` is a 15.6 mm shaft meant to pass through that bore.

An earlier version used two ribs centered at x = ±16 mm. Those ribs span x = 14..18 mm while the boss radius is 18 mm, so most of each rib was already inside the boss. Nothing in a bounding box would say that. `examples/motor_mount_buried_ribs.py` is that design, kept so the feature report can flag it. `examples/feature_report.json` is the before/after: the ribs are redundant, the gussets are not. A cut through the old boss at y = 0 does not show a rib. The cut at y = 13 shows the little end that sticks out.

![Old ribs, section through the boss at y = 0. The ribs are not in this cut.](examples/renders/buried_ribs_section_y0.png)

![Old ribs, section at y = 13, where the ends leave the boss](examples/renders/buried_ribs_section_y13.png)

```bash
python examples/motor_mount.py
```

That writes `examples/renders/` and `examples/motor_mount_report.json`. The same checks from the CLI:

```bash
aicad run examples/motor_mount.py --name motor_mount --workspace ./aicad_workspace
aicad run examples/shaft.py --name shaft --workspace ./aicad_workspace
aicad inspect motor_mount --min-wall 1.2 --workspace ./aicad_workspace
aicad section motor_mount --axis y --offset 0 --workspace ./aicad_workspace
aicad features motor_mount --workspace ./aicad_workspace
aicad check-clearance motor_mount shaft --min 0.1 --workspace ./aicad_workspace
aicad catalog "M3 screw" --workspace ./aicad_workspace
aicad insert m3 --kind socket_head_cap_screw --size M3-0.5 --length 12 --at 0,0,12 --workspace ./aicad_workspace
aicad export motor_mount --format step --out motor_mount.step --workspace ./aicad_workspace
aicad export motor_mount --format stl --out motor_mount.stl --workspace ./aicad_workspace
```

On the build that produced the pictures in this repo, inspect reported:

| Check | Result |
| --- | --- |
| Bounding box | 72 × 52 × 12 mm |
| Volume | 20426 mm³ |
| Valid and watertight | yes, one solid |
| Estimated minimum wall | 2.4 mm, from ray samples (sections estimated 2.6 mm) |
| Against a 1.2 mm print rule | pass |
| Gap to the 15.6 mm shaft | 0.2 mm, no interference |
| Gussets | not flagged; 24% to 35% of each gusset was already inside the boss |
| Old ribs | flagged; 55% of each rib was already inside the boss |

The wall number is a sample, not a proof. See [Limitations](#limitations).

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
| `import_step` | Copy a STEP file in as a named part (a McMaster download, a motor, a plate). |
| `place_part` | Move a part. Rotation is degrees about X, Y, Z, then the translation. It replaces the previous placement. |
| `search_parts` | Find a socket-head screw, hex nut, plain washer, or deep-groove ball bearing. `608` is the 8×22×7 bearing. |
| `insert_standard_part` | Insert one catalog part and place it. Screws need `length` (shank under the head, mm). |
| `section_part` | A planar cross-section: an image, the cut area, and the outline. Axis plus offset, or an origin and a normal. |
| `feature_report` | For each boolean step, how much material was new, buried, or actually removed. |
| `inspect_part` | Measure, estimate wall thickness, report feature contribution, and return the pictures. Call this after every edit. |
| `measure_part` | Bounding box, volume, area, center of mass, counts, valid, watertight. |
| `render_part` | The standard views and the contact sheet, as images. |
| `check_wall_thickness` | Ray estimate and section estimate. The minimum is scored against a threshold in mm, and `method` says which one won. |
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

`part = a + b` fuses. `part -= tool` cuts. A sketch or a curve is rejected: a part has to have volume. A script that never mentions `feature` is unchanged.

To record what each step actually did, use the `feature` object that the runner defines:

```python
bracket = feature.add("base", Pos(0, 0, 2) * Box(60, 40, 4))
bracket = feature.add("boss", Pos(0, 0, 6) * Cylinder(12, 8))
bracket = feature.cut("bore", Pos(0, 0, 6) * Cylinder(4, 20))
part = bracket
```

An added feature is flagged when at least half of its volume was already inside the part. A cut is flagged when it removes nothing. A through hole whose cutter sticks out of the part is not flagged; `removed_fraction` still says how much of the cutter was inside. `part = feature.result` is the same solid. `inspect_part` includes this report.

The same plate as operations, which is enough for a stack of boxes and holes. Operations are always tracked. An optional `name` shows up in the report:

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

**Wall thickness.** Two estimates are computed. Rays start on a grid on each face and measure the distance to the next roughly parallel face. Sections slice the part at 25%, 50%, and 75% of its extent along X, Y, and Z and measure the width between roughly parallel edges in that plane. The smaller number is the estimate. `method` is `ray`, `section`, or `ray+section` when they agree within 0.05 mm. Both numbers are in the report. A requested `section_part` also reports the minimum width in that one plane, which is how you look inside a boss.

**Feature contribution.** For an add, new material is the volume increase and buried material is the rest of the tool. For a cut, the report is the volume that disappeared. This is why the buried ribs are flagged and the gussets are not.

**Clearance.** The interference volume is the boolean intersection of the two solids. If they do not overlap, `min_distance_mm` is the gap between the surfaces, and the closest points are reported. The gap is not a penetration depth: overlapping parts report distance 0 and a volume.

**Catalog parts.** Screws, nuts, washers, and bearings come from [bd_warehouse](https://github.com/gumyr/bd_warehouse), which builds them with build123d from published dimensions. A screw's shank points down (−Z) and the underside of the head sits on local z = 0. Nuts, washers, and bearings sit on z = 0 and grow toward +Z. Screws and nuts are built with `simple=True`, so the thread is not cut; a screw's modeled shank is near the minor diameter, and a clearance check against a hole uses that solid. `import_step` plus `place_part` is the path for a STEP file from McMaster-Carr or anywhere else.

## Development

```bash
pytest
python examples/motor_mount.py   # refresh examples/renders and the report
```

`src/aicad/kernel.py` and `src/aicad/library.py` are the kernel boundary. They are the modules that import build123d, OpenCascade, or bd_warehouse.

## Limitations

- Scripts are trusted Python, not a sandbox. The server runs code the assistant sends. Point it at a workspace you are willing to let it write.
- There is no parametric feature tree. `feature.add` records contribution, not a dimension you can edit later. Changing a part still means replacing the script or the operation list. A failed replacement does not destroy the previous solid.
- Structured operations are only axis-aligned boxes and cylinders. Fillets, chamfers, and rotated boxes belong in a script.
- Wall thickness can still miss a thin spot that no ray and no slice crosses. A passing check is evidence, not a guarantee. Anything thinner than about 0.15 mm is below the ray step. A knife-edge gusset reads as a very thin wall, which is why the example gussets end in a 2.4 mm toe.
- A section only shows the plane you asked for. Buried geometry between planes is still invisible until you cut there.
- Clearance does not report how deep an interference goes, only its volume. Catalog screws omit threads, so a fit check is against the simple shank, not the major diameter.
- The catalog is screws, nuts, washers, and one family of ball bearings. It is not a McMaster mirror. An arbitrary bought part is a STEP import.
- Renders are shaded orthographic pictures from a software rasterizer, not a dimensioned drawing. Sections are filled profiles with a scale bar, not a hatched drawing. There is no click-to-measure.
- The session file is for one writer. Two servers on the same workspace will overwrite each other.

## Next steps

- A real feature history, so an assistant can change one dimension without rewriting the whole script.
- More catalog parts (NEMA motors, extrusions) and a way to drop a screw into a hole by naming the hole.
- Driving dimensions on the pictures.
- A tighter wall-thickness check, for example an offset of the solid, used beside the rays and the sections.
- Assemblies and joints, so a bracket and a motor can be constrained, not only placed and checked for a gap.
- A small viewer a person can orbit while the assistant works.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
