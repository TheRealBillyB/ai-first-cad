# Roadmap

Where the software work stands. Statuses describe this repository. They are not a release checklist.

| Marker | Meaning |
| --- | --- |
| **Done** | Present in this repository |
| **In progress** | Started, and not finished |
| **Queued** | Agreed, and not in the tree yet |
| **Next** | The tracks that follow what is already in the tree |
| **Planned** | The direction after those tracks |
| **Later** | Far enough out that the shape can still change |

What is already in the tree is listed under Done. The next release does not add a feature track. The tracks after that release are the ones that are not in the tree yet.

## Done

### A shared way to build a part and check it

A Python library, a command-line tool (`aicad`), and an MCP server (`aicad serve`). The same operations are available on all three. The workspace app is a fourth door onto that same command layer.

- **Views.** Front, back, left, right, top, bottom, and isometric, plus one contact sheet. A headless rasterizer draws them with no screen and no graphics card.
- **Measurements.** Bounding box, volume, surface area, center of mass, and whether the solid is valid and watertight (a closed surface, one real inside and outside).
- **Thinnest wall.** A sampled estimate. The report names the method that produced the number. A passing check is evidence to read alongside the picture.
- **Clearance.** Overlap between two parts, or the gap when they do not touch.
- **Packaging.** Whether a set of parts fits inside an envelope. The envelope is a solid, a primitive, or a flat outline extruded along an axis. The check uses the part geometry, including a posed assembly, and reports clearance, protrusion, and part-to-part overlap.
- **Files.** Export STEP (the format other CAD tools and machine shops expect) and STL (the triangle file a 3D printer slices).
- **An example.** A motor-mount bracket, and a shaft meant to pass through its bore. Units are millimeters.
- **Design scripts.** A script runs in a confined child process on Linux, Windows, and macOS. The network stays off unless it is turned on for that run. If this computer cannot confine the script, the script does not run.
- **Plugins.** An analysis, a part library, or an exporter can be added as an entry point with a static manifest. It runs only after an explicit allow.

### Seeing inside the part, and a small catalog of real parts

- **Section views.** Cut the part on a plane and get a picture of the profile, the cut area, and the thinnest width in that plane.
- **Feature contribution.** Each added or subtracted shape reports how much material was new, already buried, or actually removed. An added shape is flagged when at least half of its volume was already inside the part. A cut is flagged when it removes nothing. This came from the example bracket: the first ribs spanned a band that sat almost entirely inside the boss, so only the tips stuck out. The old ribs are kept as an example that should fail.
- **A parts library.** ISO 4762 socket-head screws, ISO 4032 nuts, ISO 7089 washers, ISO 15 deep-groove bearings, ISO 355 tapered roller bearings, DIN 3760 / ISO 6194 radial shaft seals, NEMA ICS 16 stepper faces, and a small set of published gearboxes. Common sizes are rows in the catalog tables. Fastener sizes outside those tables still come from [bd_warehouse](https://github.com/gumyr/bd_warehouse). A STEP file from somewhere else (a McMaster-Carr download, a plate) can be imported and placed. Clearance works against any of these.
- **The bracket, redrawn.** The current example uses three gussets with a blunt toe. The feature report does not flag them. The buried-rib version remains so the check still has a failure to show.

### Sketches driven by constraints

Two-dimensional sketches whose lines and arcs are held by constraints (a length, a right angle, two lines that must stay parallel), solved by SolveSpace.

The sketch says when it is under-constrained (it can still wiggle; a dimension is missing) and when it is over-constrained (the dimensions disagree). Changing a dimension rebuilds the solid. A stored tolerance says how far a size is allowed to drift. It does not move the geometry.

### A first strength check

Linear static FEA: a numerical estimate of stress under a steady load. Gmsh meshes the solid and CalculiX solves it.

A run has a material, loads, and a safety factor (how much stronger the part needs to be than the load). The result includes a stress map and the assumptions, written out: what was held still, what was pushed, and what this kind of analysis leaves out. A design review reads that result and says what passed, what failed, and why, so the next edit is aimed at the failure. A coarse mesh cannot support a pass.

### The Workspace app

A browser interface talks to a local Python backend (`aicad app`). Sketch, extrude, face selection, and dimension edits are the same commands the assistant uses. The screen has a live 3D view, a chat panel, and a documents page where requirements and design notes are kept. Requirements are checked against the current design, including load, mass, and printer fit. Undo restores the open project. A desktop shell around the server is not packaged.

- **Design session record.** While a session runs from the app, the command line, or MCP, an opt-in record stores each design step. Replay reads that record and does not run the stored commands. Export writes a timelapse GIF, and MP4 or WebM when ffmpeg is on PATH.

### Plans

A plan can hold research, assumptions, open questions, and rough blueprint sheets before a solid exists. Modeling or simulation started from that plan waits until the plan is approved in the app. The assistant cannot approve it.

### Gears and mechanisms

- **Gear generator.** Spur and helical gears from ISO 21771 and the ISO 53 basic rack, and straight and spiral bevel gears from ISO 23509 pitch cones with the AGMA 2005 virtual cylindrical gear. Module, tooth count, pressure angle, face width, helix or spiral angle, and profile shift. A bore, a hub, a DIN 6885-1 keyway, or an ISO 14 or DIN 5480 spline is optional.
- **Hypoid gears.** A ring and pinion from ratio, pinion offset, shaft angle, ring diameter, face width, and spiral angle. The ring tooth is a face-mill blade cone. The pinion is the blank left after that tooth is rolled about the offset shafts at the tooth ratio. Face-hobbing is not generated, and the edge radius is recorded rather than cut.
- **Gear mesh and backlash.** A spur, helical, or bevel pair, or every such pair in an assembly, reports centre distance against the zero-backlash centre, circumferential and normal backlash, contact ratio, undercut, and interference through one full turn of the smaller gear. A hypoid pair reports the axis offset, the tooth ratio, backlash along the tooth, and interference of the face-mill solids through one pinion tooth at the mounted placement. That sweep is one pinion tooth, not a full turn, and it is not a spiral-bevel sweep.
- **Gear-train solver.** Speed and torque for a simple or compound train, an idler, a planetary set, and an open differential. The report says when the train is under-constrained or over-constrained.

### Assemblies and motion

Parts join with fixed, revolute, prismatic, and cylindrical joints. A gear relation is a linear map between joint values. A linkage is a planar four-bar and does not close a spatial loop. A cylinder relation writes the barrel angle and the rod extension so the rod eye stays on the moving clevis. The joint tree stays a tree.

Named poses and motions sweep that range and report collisions and joint-limit stops. A motion study drives joints by an expression or by keyframes over time. The examples include a lift and a short walk cycle that collides when the foot is planted. There is no standing controller.

- **Kinematics.** Forward and inverse kinematics for a serial chain, including the joint values that reach a target pose, and a tool path stepped through the motion study.
- **Positional error budget.** The tip position error of a serial chain is the positional Jacobian times each joint bound (an encoder step, a gear-mesh or stated backlash, and a stated load over joint stiffness), plus a small screw for link deflection from the strength check or a stated deflection. Each contribution is listed. The worst case tries every sign. A root-sum-square is reported beside it and does not grant a pass. The check names the pose where the worst case is largest. It passes when that worst case is within the tolerance and every contribution was computed. An offset gear pair's backlash along the tooth is not turned into an angle. Thermal growth, bearing runout, and an assembly stack-up are omitted unless they are stated as one of those contributions.
- **Animated renders.** A motion study, a camera orbit, or an exploded opening, written as a GIF, and as MP4 or WebM when ffmpeg is on PATH.

### Cables and hoses

Cable routes and hose routes are both in the tree. A route is a polyline through waypoints fixed on parts, and each waypoint can be a clip. The check reports length change, strain, tension, bend radius at each corner, and pinch against the moving solids, including across a motion. A hose uses that same check. Its default strain limit is tighter than a cable's. Samples next to a waypoint are the clip seat and are not counted as a pinch of that part.

A tendon is the same family of check on a different path: anchors, pulleys, guides, and wraps. Length is the straight spans plus the arc. Tension follows the capstan equation from a stated actuator force. Bend radius and pinch are checked, including along the pulley arc.

The line does not sag, and clips do not slip. A flexible-body rod is not in the tree. The length, bend, and gap numbers above are the limits that model would still have to pass.

### Power and fluid estimates

Quasi-static loads from gravity, part mass, and payload, over the stored pose or a named motion:

- force and torque at each actuator, with a gear, linkage, or cylinder reflecting the driven load onto the driver when the driven joint has no actuator of its own
- hydraulic pressure, flow, displaced volume, and pump power
- motor current, electrical power, and battery energy and charge

The report states the assumptions. Inertia, friction, seal drag, line pressure drop, and pump dynamics are omitted. A hydraulic moment arm is a number on the actuator, not a cylinder solid in the assembly. Electric braking energy is counted as spent.

### Vibration, fatigue, and bolted joints

Natural frequencies come from a CalculiX frequency solve, for one solid or for a posed assembly tied at its joints. A running speed (motor, drill, or pump, including harmonics) is compared with those frequencies. Harmonic response and a Miles random-vibration estimate are closed-form add-ons. Fatigue is a Goodman check of a stated stress amplitude, not a second finite-element solve. A bolted joint reports preload, tightening torque, clamp, slip, thread stripping, and yield. Slip below 1 is the loosening case, and it can occur while the bolt is still below yield.

### Print preparation

**In progress.** The printing checks are in. The other manufacturing checks are not.

- **Printing.** An overhang map for the six axis orientations, the support area after the bed face is excluded, and fit against the build volumes in the printer table (Bambu Lab A1, A1 mini, P1, and X1, Prusa MK4, and Ender 3). A requirement kind `fits_printer` is that fit. When PrusaSlicer or OrcaSlicer is on PATH, the report also reads time and filament grams from the slicer comments. A missing slicer is reported, and the overhang and fit checks still stand. A diagonal orientation is not searched, and the slicer runs its default profile.
- **CNC machining and sheet metal.** Not in the tree. They are the first feature track after v1.0.

### Robot export

An assembly can be written as URDF (`robot.urdf`), MJCF (`robot.xml`), and STL meshes. Meshes stay in millimeters and the robot file scales them to meters. MJCF body euler is degrees, the same unit as that file's compiler. URDF joint origins stay in radians. Mass and inertia use the model volume and a handbook density, plus a payload point mass when one is set. Joint limits are written. A cylindrical joint becomes a revolute plus a prismatic. Gear, linkage, and cylinder relations are comments, so a simulator does not enforce them. Effort and velocity in the file are placeholders, not the power estimate. When MuJoCo or PyBullet is installed, the export loads the file and steps it. A missing install still writes the files and says they were not stepped.

### Layout drawings, parts lists, and exploded views

A third-angle sheet of a posed assembly, as PDF and DXF, with the overall box, a title block, and a scale. It is a layout drawing. A bill of materials is a count of parts, catalog items, and routes, with a handbook mass where the solid has a volume. An exploded view pulls parts apart along their joints. The gap is not a dimension. These three are commands in the workspace, the command line, and MCP.

## Next release

**v1.0** is a stability and safety release for the feature set already in this repository. No new feature track starts before it.

## After v1.0

The tracks that follow v1.0, in order, are power and fluid estimates, hose routing with bend-radius and pinch checks, robot export (URDF and MJCF), and CNC machining and sheet-metal checks.

The power estimate, the hose checks, and the robot export are already in the tree and are described under Done. The first of these tracks that is not in the tree is:

### CNC machining and sheet-metal checks

**Next.** Checks aimed at CNC machining and at sheet metal. Print overhangs, support area, printer fit, and an optional slicer estimate stay the print half of print preparation. They do not cover a toolpath, a bend allowance, or a flat pattern.

## Later

- **Drawings.** The layout sheet, the parts count, and the exploded view are in. Still later: tolerances, GD&T (the standard language for how far a size and a form may vary), and prices on the bill of materials.
- **History you can see.** Visual version history, and diffs an assistant can compare, so "what changed, and did it get better?" has a picture as well as a sentence. The design session record is a timelapse of steps. It is not this history.
- **Electronics.** Board layout through an existing tool, with KiCad the likely one, under the same "reuse the engine" rule.

## Related

- [Principles](PRINCIPLES.md)
- [Decisions](DECISIONS.md)
