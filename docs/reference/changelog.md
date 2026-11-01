# Changelog

Version 0.9.0 is a public preview ahead of 1.0. The notes below describe what this tree does. A root `CHANGELOG.md` is not on the software-docs allowlist, so this file is the record.

## 0.9.0

### Modeling and sketching

- A sketch is points, lines, circles, arcs, and splines, solved by SolveSpace. An arc is three points, a center with a start and an end, or a tangent continuation of a line. A closed spline can be a profile. Construction geometry is stored on a line, a circle, or an arc.
- Constraints are fix, coincident, parallel, perpendicular, tangent, equal, horizontal, vertical, symmetric, midpoint, distance, angle, radius, diameter, and a circular pattern. A named dimension can carry a stored tolerance. The solver reports fully constrained, under-constrained, or over-constrained.
- A sketch sits on the XY, YZ, or XZ plane, on that plane shifted or tilted, or on a planar face of a solid. Solving stays in the plane. The solid is placed on the plane when it is built.
- A solved profile can be extruded, revolved, or cut. `cut_box` and `gusset` are extra steps. Changing a named dimension rebuilds the parts made from that sketch. A conflicting change leaves the previous solid.
- A short operation list adds and cuts axis-aligned boxes and cylinders. A cylinder can name an axis. A rotated box belongs in a script.
- A fillet or a chamfer is a later step on a shape or sketch part. The radius or the distance stays editable. A size that does not fit leaves the previous solid.
- Spur, helical, internal ring, straight bevel, spiral bevel, and hypoid gears are solids. A pair check reports centre distance, backlash, contact ratio, and interference. A gear train reports speed and torque, including a planetary set and an open differential.
- The catalog has ISO fasteners, ISO 15 and ISO 355 bearings, radial shaft seals, NEMA stepper faces, and two published gearboxes. A STEP file from outside the catalog can be imported and placed. A mount can snap a catalog mate onto a face or an axis.

### Feature history and editing

- Each part keeps the steps that rebuild it: shape operations, sketch steps, a script, a catalog part, an import, a gear, and fillet or chamfer steps. A step that did not fit keeps the error from that rebuild.
- An earlier size can be changed and the solid rebuilds from that step. That includes an operation size, a sketch dimension, an extrude height, a fillet radius, a chamfer distance, a catalog length, and a top-level number in a script.
- Undo and redo restore the open project. A failed rebuild keeps the previous solid.
- An imported STEP file has no size to edit. An imported STL mesh has no feature history to edit. Both can still be moved.

### The app

- `aicad app` serves a browser workspace on this computer. The header shows the version. The page has a model view, an assembly page, and a documents page.
- The model view orbits the part, picks faces, edges, and mounts, measures a click, and switches among the standard views. The sketch toolbar draws the profile and applies constraints. The shape panel edits a size. The feature list shows the rebuild steps.
- The Plan panel holds research, assumptions, questions, and blueprint sheets. Approval is in the app. Stored build steps on that revision then run. The assistant cannot approve a plan.
- Live mode follows an assistant. Record stores design steps, and a timelapse can be written as a GIF, or as MP4 or WebM when ffmpeg is on PATH.
- The in-app chat uses an API key kept in the user config, not in the project. Notes and structured requirements live with the project. The worked motor-mount example can be opened as its own project folder beside the current one. Opening it does not run a script.

### The assistant and MCP server

- `aicad serve` speaks MCP over stdio. The tools are the same commands as the app, the command line, and the Python API.
- `aicad serve --connect` forwards those tools to a running app, so one process owns the files.
- After an edit the assistant is told to call `inspect_part` and look at the pictures.
- A design script is one Python program against the project. It stays off until the person allows it. Part scripts that only assign `part` do not need that allow step.
- A plugin is an installed entry point with a static manifest. It runs only after an explicit allow.

### Analysis and checks

- Measure reports the bounding box, volume, surface area, center of mass, and whether the solid is valid and watertight. Inspect adds a wall estimate, the feature report, and the standard views. Passing a handbook material adds mass as volume times that density.
- Wall thickness is a ray estimate and a section estimate. The report names which one set the minimum.
- Clearance is the interference volume, or the gap when the parts do not touch. Packaging checks parts against an envelope, including a posed assembly.
- A linear static check meshes with Gmsh and solves with CalculiX. Materials are PLA, PETG, ABS, nylon, aluminum 6061-T6, mild steel, 42CrMo4, and 16MnCr5. The result includes stress and displacement pictures, a safety factor, and the assumptions.
- A frequency check covers one solid or a posed assembly. Harmonic response and a Miles estimate are closed-form. Fatigue is a Goodman check of a stated stress. A bolted joint reports preload, torque, clamp, slip, stripping, and yield.
- A serial chain can be solved forward and inverse. The tip position error is the positional Jacobian times the stated joint bounds, plus link deflection when that is stated.
- Cable, hose, and tendon routes report length, bend radius, tension, and pinch. A power estimate turns gravity, mass, and payload into actuator force, hydraulic pressure and flow, and motor current. The report states what it leaves out.

### Print prep

- Six axis orientations are scored for overhang and support. A short bridge is not counted as support. Fit is checked against the Bambu Lab A1, A1 mini, P1, and X1, the Prusa MK4, and the Ender 3.
- A requirement kind `fits_printer` is that fit. PrusaSlicer or OrcaSlicer, when installed, adds time and filament grams from its default profile.
- Parts of an assembly that print as one piece can be checked for a gap, including the two solids of a joint.

### Assemblies and robot export

- Parts join with fixed, revolute, prismatic, and cylindrical joints. A gear relation, a planar linkage, and a cylinder relation drive joints in the tree. A cycle is refused.
- Named poses and motions report collisions and joint-limit stops. A motion study drives joints by an expression or by keyframes. A motion, a camera orbit, or an exploded opening can be written as a GIF, and as MP4 or WebM when ffmpeg is on PATH.
- A layout drawing is a third-angle PDF and DXF of a posed assembly. A bill of materials counts parts, catalog items, and routes. An exploded view pulls parts apart along their joints.
- `aicad robot` writes URDF, MJCF, and STL meshes. Mass and inertia use the model volume and a handbook density. When MuJoCo or PyBullet is installed, the export loads the file and steps it.

### Import and export

- Export writes STEP, STL, or 3MF. An assembly export keeps the parts as separate solids in the current pose.
- STEP import copies the file into the project and places it. STL import copies a closed mesh in as a part with no editable features. An open mesh is refused.

### Safety model

- A design script runs in a confined child process. Linux uses Landlock and seccomp. Windows uses an AppContainer and a Job Object. macOS uses a Seatbelt profile. The network stays off unless it is turned on for that process. If this computer cannot confine the script, the script does not run.
- Opening a project does not run scripts stored in it, and it does not load a plugin.
- The workspace app listens on loopback only. A request needs the session token from that launch. The person and the assistant are not the same caller. The assistant cannot approve a plan or allow a design script.
- Project files are opened from a directory handle. A link in the path is not followed.
