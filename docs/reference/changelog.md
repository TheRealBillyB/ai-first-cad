# Changelog

Version 0.9.0 is a public preview for feedback before 1.0. These notes describe the current tree, including its stability and safety fixes. A root `CHANGELOG.md` is not on the software-docs allowlist, so this file is the record.

## 0.9.0 — public preview

Model and Assembly now share one workspace and one 3D view, but the assembly flow is new and still rough. Version 1.0 will add editing a part in place inside an assembly and polish that flow, shaped by [preview feedback](https://github.com/TheRealBillyB/ai-first-cad/issues/new?template=feedback.yml). Joint-sized reducers and a stored catalog mounting orientation remain planned for after 1.0.

### Windows sandbox

- The installer offers a one-time administrator setup, `aicad sandbox-setup`, for the script network block. A run installs nothing. A script allowed to use the network gets its own container.
- `aicad sandbox-setup --status` confirms the firewall rules from a standard account, without administrator rights.
- Scripts and solves run in a normal AppContainer. The stricter one (LPAC) is no longer tried, so there is no fallback message.
- A container child that cannot start exits with a named reason instead of showing a Windows error dialog. A probe that does not finish is stopped.
- The script container can make private folders with a current Python (3.11.10 and later), where it once spun until its CPU limit. It can also pass through the links on its Python's paths, such as uv's version folder.
- A refused firewall rule says why.

### The app

- The app's geometry runs in a second process, so the page keeps answering during a long rebuild or solve. Gmsh loads in that process on Windows, so the Strength solve no longer hangs.
- The page answers at once and says the project is opening while it loads.
- Meshing for the 3D view reads OpenCascade's triangles straight into arrays, with the same result. On a 38-part finger rig, meshing every part went from 9.1 s to 5.0 s, and the assembly pieces from 4.3 s to 1.8 s.
- One dark theme, with a grid and origin axes in the model view.
- Model and Assembly are one workspace with one 3D view. Assembly shows the placed, colored instances to turn and click, moves with its joint sliders, and opens an instance's part with Edit part. A click there no longer reports a face reference error, and driven and fixed joints are marked.
- A joint change in Assembly sends only where each instance sits, and the page moves the parts it already has. A refresh where nothing changed sends no mesh. Moving a part meshes only that part again. On a 38-part finger rig, a slider release went from 4.2 s and 45 MB to 0.13 s and 21 KB, and a move from 10.8 s to 2.2 s.
- Turning or panning the 3D view no longer rebuilds and resends the model's arrays each frame. They are made again only when the mesh, the selection, the part, live mode or a drag changes them. On the 38-part finger rig (257 thousand triangles), every frame took a median of 24 ms. Now an orbit frame takes 0.1 ms, a frame with a new face pick 1.6 ms, and a frame of a Move drag 7 ms.
- A running Strength solve shows its time, and a failed one says why. Every failed page action shows in the notice bar. Each app command and its result is logged to the console.
- Clicking a report picture opens it at full size over the app, with zoom, drag, and close.
- Report pictures named with a full path load. The page buttons stay on screen when the Plan page opens.
- The app warns when another app has the same project open. A page that drops its live connection no longer fills the console.

### Install

- Double-click install and launch scripts for Windows, macOS, and Linux.
- `install.py --with-calculix`, and checks for libGLU, a C++ compiler, and Python headers. A failed apt-get step is reported instead of crashing.

### Checks and modeling

- The static solve takes a torque load. A CalculiX timeout reports "not solved". A folded quadratic mesh is solved straight-sided before falling back to linear. Each Gmsh mesh runs on one long-lived thread, which fixed a macOS crash.
- New materials: grey cast iron (ASTM A48 Class 30) and TPU 95A. New catalog rows: ISO 15 miniature and flanged miniature bearings, and M1.6, M2, and M2.5 cap screws, nuts, and washers.
- A fixed joint can turn its child. Tendon wraps stay continuous through a study, and a tendon can be tied off on its own drum and state its line modulus. Cable collision checks skip parts out of reach.
- A part an assembly uses cannot be deleted. A solid with a sphere pole or a cone apex counts as watertight.
- Clearer messages for an impossible sketch dimension, a refused export path, a hidden project folder, a bolt check without `grip_mm`, and whether this computer can run a design script.

### Known limits

- Parts cannot yet be edited in place inside an assembly; Edit part opens the part in Model. The assembly flow is being polished for 1.0.
- On Windows, design scripts and solves need a signed-in desktop. Task Scheduler, a Windows service, and an SSH session are not supported. See the README's Limitations.

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
- A linear static check meshes with Gmsh and solves with CalculiX. Materials are PLA, PETG, ABS, nylon, aluminum 6061-T6, mild steel, 42CrMo4, and 16MnCr5. Loads are a force, a pressure, a bolt load, or a torque. The result includes stress and displacement pictures, a safety factor, and the assumptions.
- A frequency check covers one solid or a posed assembly. Harmonic response and a Miles estimate are closed-form. Fatigue is a Goodman check of a stated stress. A bolted joint reports preload, torque, clamp, slip, stripping, and yield.
- A serial chain can be solved forward and inverse. The tip position error is the positional Jacobian times the stated joint bounds, plus link deflection when that is stated.
- When CalculiX finds folded quadratic tetrahedra, a static or frequency solve first puts each mid-edge node back on its straight edge and solves the quadratic mesh again. Linear tetrahedra are the last resort, and only that result is marked degraded.
- Cable, hose, and tendon routes report length, bend radius, tension, and pinch. A power estimate turns gravity, mass, and payload into actuator force, hydraulic pressure and flow, and motor current. The report states what it leaves out.
- A static or frequency solve that runs past the CalculiX time limit is reported as not solved, with a hint to use a coarser mesh. It is not raised as an error.

### Print prep

- Six axis orientations are scored for overhang and support. A short bridge is not counted as support. Fit is checked against the Bambu Lab A1, A1 mini, P1, and X1, the Prusa MK4, and the Ender 3.
- A requirement kind `fits_printer` is that fit. PrusaSlicer or OrcaSlicer, when installed, adds time and filament grams from its default profile.
- Parts of an assembly that print as one piece can be checked for a gap, including the two solids of a joint.

### Assemblies and robot export

- Parts join with fixed, revolute, prismatic, and cylindrical joints. A gear relation, a planar linkage, and a cylinder relation drive joints in the tree. A cycle is refused.
- Named poses and motions report collisions and joint-limit stops. A motion study drives joints by an expression or by keyframes. A pair or a joint seat whose relative pose repeats an earlier step reuses that result, so parts on one rigid body are intersected once per study. A motion, a camera orbit, or an exploded opening can be written as a GIF, and as MP4 or WebM when ffmpeg is on PATH.
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
