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

The order below is a part you can trust, then a place where a person can design, then machines that move, with hoses, cables, and a power estimate. The rules for that order are in [Principles](PRINCIPLES.md).

## Done

### A shared way to build a part and check it

A Python library, a command-line tool (`aicad`), and an MCP server (`aicad serve`). The same operations are available on all three.

- **Views.** Front, back, left, right, top, bottom, and isometric, plus one contact sheet. A headless rasterizer draws them with no screen and no graphics card.
- **Measurements.** Bounding box, volume, surface area, center of mass, and whether the solid is valid and watertight (a closed surface, one real inside and outside).
- **Thinnest wall.** A sampled estimate. The report names the method that produced the number. A passing check is evidence to read alongside the picture.
- **Clearance.** Overlap between two parts, or the gap when they do not touch.
- **Packaging.** Whether a set of parts fits inside an envelope. The envelope is a solid, a primitive, or a flat outline extruded along an axis. The check uses the part geometry, including a posed assembly, and reports clearance, protrusion, and part-to-part overlap.
- **Files.** Export STEP (the format other CAD tools and machine shops expect) and STL (the triangle file a 3D printer slices).
- **An example.** A motor-mount bracket, and a shaft meant to pass through its bore. Units are millimeters.

### Seeing inside the part, and a small catalog of real parts

- **Section views.** Cut the part on a plane and get a picture of the profile, the cut area, and the thinnest width in that plane.
- **Feature contribution.** Each added or subtracted shape reports how much material was new, already buried, or actually removed. An added shape is flagged when at least half of its volume was already inside the part. A cut is flagged when it removes nothing. This came from the example bracket: the first ribs spanned a band that sat almost entirely inside the boss, so only the tips stuck out. The old ribs are kept as an example that should fail.
- **A parts library.** ISO 4762 socket-head screws, ISO 4032 nuts, ISO 7089 washers, ISO 15 deep-groove bearings, ISO 355 tapered roller bearings, DIN 3760 / ISO 6194 radial shaft seals, NEMA ICS 16 stepper faces, and a small set of published gearboxes. Common sizes are rows in the catalog tables. Fastener sizes outside those tables still come from [bd_warehouse](https://github.com/gumyr/bd_warehouse). A STEP file from somewhere else (a McMaster-Carr download, a plate) can be imported and placed. Clearance works against any of these.
- **The bracket, redrawn.** The current example uses three gussets with a blunt toe. The feature report does not flag them. The buried-rib version remains so the check still has a failure to show.

### Sketches driven by constraints

Two-dimensional sketches whose lines and arcs are held by constraints (a length, a right angle, two lines that must stay parallel), solved by SolveSpace.

The sketch says when it is under-constrained (it can still wiggle; a dimension is missing) and when it is over-constrained (the dimensions disagree). Changing a dimension rebuilds the solid. This is also the groundwork for tolerances: how far a size is allowed to drift and still count as correct.

### A first strength check

Linear static FEA: a numerical estimate of stress under a steady load. Gmsh meshes the solid and CalculiX solves it.

A run has a material, loads, and a safety factor (how much stronger the part needs to be than the load). The result includes a stress map and the assumptions, written out: what was held still, what was pushed, and what this kind of analysis leaves out. A design review reads that result and says what passed, what failed, and why, so the next edit is aimed at the failure.

## Next

These tracks follow what is already in the tree. They are separate enough to be built in parallel once the command layer they share is settled.

### The Workspace app

A real editing application. A browser interface talks to a local Python backend. Wrapping that as a desktop app can come after the interface is solid.

A person designs the part here: sketch, extrude, select a face, edit a dimension. Those are the same commands the assistant uses. The screen has:

- a live 3D view
- a chat panel where the user connects their own AI
- a Documents page where requirements and design decisions are written and kept

Requirements are checked against every design. The first kinds of goal: it holds this load, it fits the printer bed, it stays under this mass.

### Gears, mechanisms, and checks

**Planned.**

- **Gear generator.** Done. Spur and helical gears from ISO 21771 and the ISO 53 basic rack, and straight and spiral bevel gears from ISO 23509 pitch cones with the AGMA 2005 virtual cylindrical gear. Module, tooth count, pressure angle, face width, helix or spiral angle, and profile shift. Bores, hubs, DIN 6885-1 keyways, and ISO 14 or DIN 5480 splines are optional. Hypoid gears remain a separate item.
- **Hypoid gears.** Generates a true hypoid gear from offset, spiral angle, and ratio, with tooth geometry produced by simulating the generating and cutting process rather than an approximation.
- **Gear mesh and backlash check.** Done. A spur, helical, or bevel pair, or every gear pair in an assembly, reports centre distance against the zero-backlash centre, circumferential and normal backlash against a range, contact ratio, undercut, and interference through one full turn of the smaller gear. A hypoid pair is the same report once its checker is registered.
- **Gear-train solver.** Done. Speed and torque for a simple or compound train, an idler, a planetary set, and an open differential. The report says when the train is under-constrained or over-constrained.
- **Motion study.** **Done.** Drives assembly joints through angles or paths over time, and reports collisions and joint-limit violations.
- **Kinematics.** Solves forward and inverse kinematics for a serial chain of joints, including the joint angles that reach a target pose.
- **Positional error budget.** Estimates the end-point position error of a jointed mechanism from link deflection (using the strength check), gear backlash, joint stiffness, and encoder resolution, and reports pass or fail against a tolerance.
- **Cable and pulley routing.** Routes cables over pulleys and guides through joints, and checks how cable length and tension change across a joint's range of motion.
- **Standard parts library.** **Done.** Inserts bearings, seals, fasteners, small motors, and gearboxes by catalog designation.
- **Animated renders.** Renders a motion study to a GIF or a video for documentation.

### Assemblies and motion

Parts joined by real joints. A revolute joint is a hinge. A prismatic joint is a slider. Gears, and other joint types, belong here too.

The motion can be swept through its range while the program looks for collisions. Scripted tests cover what a machine actually does: walking, lifting, standing.

### Cabling and hoses

Route electrical cables, hydraulic lines, and the brackets and clips that hold them. Checks cover bend radius (the tightest curve the line can take), how the length changes through the motion, the strain that causes, pinching, and collisions with the mechanism.

This is the start of flexible-body behavior. The lines have to bend, change length, and move the way hoses and cables do.

### Power and fluid estimates

Start from the loads and the motions, and estimate what it takes to drive them:

- force and torque at each actuator
- hydraulic pressure, flow, fluid volume, and pump power
- motor current, electrical power, and battery size

The numbers are estimates with their assumptions attached, in the same spirit as the strength check.

## Planned

### Print and manufacturing prep

For printing: overhangs, supports, and a slicer, so print time and filament cost are numbers. For other processes: checks aimed at CNC machining and sheet metal.

### Robot export

Write the files a physics simulator already understands, such as URDF and MJCF, so a robot design can be dropped in and tried at walking, lifting, and drilling.

## Later

- **Drawings.** A layout sheet and a parts count are in. Still later: tolerances, GD&T (the standard language for how far a size and a form may vary), and prices on the bill of materials.
- **History you can see.** Visual version history, and diffs an assistant can compare, so "what changed, and did it get better?" has a picture as well as a sentence.
- **Electronics.** Board layout through an existing tool, with KiCad the likely one, under the same "reuse the engine" rule.

## Related

- [Principles](PRINCIPLES.md)
- [Decisions](DECISIONS.md)
