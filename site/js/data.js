window.ROADMAP_ITEMS = [
 {
  "id": "command-layer",
  "status": "done",
  "title": "One command layer, four ways in",
  "category": "Core",
  "desc": "The workspace app, the command line (aicad), the Python library and the MCP server all call the same operations on the same document. A person and an AI assistant use the same tools.",
  "n": 1
 },
 {
  "id": "views-measure",
  "status": "done",
  "title": "Views and measurements",
  "category": "Core",
  "desc": "Standard views, an isometric view and a contact sheet, rendered headless with no screen or graphics card. Bounding box, volume, surface area, center of mass, and whether the solid is valid and watertight.",
  "n": 2
 },
 {
  "id": "checks",
  "status": "done",
  "title": "Wall, clearance and packaging checks",
  "category": "Core",
  "desc": "A sampled estimate of the thinnest wall that names its method, overlap or gap between two parts, and whether a set of parts fits inside an envelope.",
  "n": 3
 },
 {
  "id": "sandbox",
  "status": "done",
  "title": "Confined design scripts",
  "category": "Core",
  "desc": "A design script runs in a confined child process on Linux, Windows and macOS. The network stays off unless it is turned on for that run, and if the computer cannot confine the script, it does not run.",
  "n": 4
 },
 {
  "id": "plugins",
  "status": "done",
  "title": "Plugins",
  "category": "Core",
  "desc": "An analysis, a part library or an exporter can be added as a plugin with a static manifest. It runs only after an explicit allow.",
  "n": 5
 },
 {
  "id": "files",
  "status": "done",
  "title": "STEP, STL and 3MF",
  "category": "Core",
  "desc": "Export STEP for other CAD tools and machine shops, and STL or 3MF for printing. Import a STEP file, or a closed STL mesh, and place it.",
  "n": 6
 },
 {
  "id": "section",
  "status": "done",
  "title": "Section views and feature report",
  "category": "Inspect",
  "desc": "Cut a part on a plane to see the profile, the cut area and the thinnest width. Each added or subtracted shape reports how much material was actually new or removed, so a buried rib or a cut that does nothing gets flagged.",
  "n": 7
 },
 {
  "id": "parts-library",
  "status": "done",
  "title": "Standard parts library",
  "category": "Parts",
  "desc": "ISO screws, nuts, washers, ball and tapered roller bearings, shaft seals, NEMA stepper faces and a small set of gearboxes. A STEP file from somewhere else can be imported and placed too.",
  "n": 8
 },
 {
  "id": "sketches",
  "status": "done",
  "title": "Constraint-driven sketches",
  "category": "Modeling",
  "desc": "Lines, arcs, circles and splines held by constraints and solved by SolveSpace, on standard planes, datums or a face. The sketch says when it is under- or over-constrained, and changing a dimension rebuilds the solid.",
  "n": 9
 },
 {
  "id": "fillet-history",
  "status": "done",
  "title": "Fillets, chamfers and feature history",
  "category": "Modeling",
  "desc": "Fillets and chamfers with editable sizes. An earlier step can be edited and the solid rebuilds from there.",
  "n": 10
 },
 {
  "id": "fea",
  "status": "done",
  "title": "Strength check (FEA)",
  "category": "Analysis",
  "desc": "Linear static analysis meshed with Gmsh and solved with CalculiX. The result includes a stress map, a safety factor and the assumptions written out, and a coarse mesh cannot support a pass.",
  "n": 11
 },
 {
  "id": "workspace-app",
  "status": "done",
  "title": "The workspace app",
  "category": "App",
  "desc": "A local browser interface with a live 3D view, a chat panel and a documents page for requirements and design notes. Sketching, extruding, face picking and dimension edits are the same commands the assistant uses.",
  "n": 12
 },
 {
  "id": "session-record",
  "status": "done",
  "title": "Design session record",
  "category": "App",
  "desc": "An opt-in record of each design step from the app, the command line or MCP. Replay it, or export a timelapse as a GIF, MP4 or WebM.",
  "n": 13
 },
 {
  "id": "plans",
  "status": "done",
  "title": "Plans before parts",
  "category": "App",
  "desc": "A plan holds research, assumptions, open questions and rough blueprint sheets before a solid exists. Modeling waits until a person approves the plan in the app, and the assistant cannot approve it.",
  "n": 14
 },
 {
  "id": "gears",
  "status": "done",
  "title": "Gear generator",
  "category": "Mechanisms",
  "desc": "Spur, helical, bevel, hypoid and internal ring gears from published standards, with optional bores, hubs, keyways and splines.",
  "n": 15
 },
 {
  "id": "gear-trains",
  "status": "done",
  "title": "Gear mesh and gear trains",
  "category": "Mechanisms",
  "desc": "Backlash, contact ratio, undercut and interference for a gear pair. Speed and torque for simple, compound and planetary trains and an open differential.",
  "n": 16
 },
 {
  "id": "assemblies-motion",
  "status": "done",
  "title": "Assemblies and motion",
  "category": "Mechanisms",
  "desc": "Fixed, revolute, prismatic and cylindrical joints, plus gear and linkage relations. Named poses and motion studies sweep a range and report collisions and joint-limit stops.",
  "n": 17
 },
 {
  "id": "kinematics",
  "status": "done",
  "title": "Kinematics and error budget",
  "category": "Mechanisms",
  "desc": "Forward and inverse kinematics for a serial chain. A positional error budget lists each contribution to tip error and names the pose where the worst case is largest.",
  "n": 18
 },
 {
  "id": "routing",
  "status": "done",
  "title": "Cable, hose and tendon routing",
  "category": "Mechanisms",
  "desc": "Routes through clips, pulleys and guides, checked for length change, strain, tension, bend radius and pinching, including across a motion.",
  "n": 19
 },
 {
  "id": "power-fluid",
  "status": "done",
  "title": "Power and fluid estimates",
  "category": "Analysis",
  "desc": "Quasi-static force and torque at each actuator, hydraulic pressure, flow and pump power, and motor current, power and battery energy. The report states what it leaves out.",
  "n": 20
 },
 {
  "id": "vibration-bolts",
  "status": "done",
  "title": "Vibration, fatigue and bolted joints",
  "category": "Analysis",
  "desc": "Natural frequencies from CalculiX compared with a running speed, a Goodman fatigue check of a stated stress, and bolted-joint preload, torque, slip and yield.",
  "n": 21
 },
 {
  "id": "print-prep",
  "status": "done",
  "title": "3D print preparation",
  "category": "Make",
  "desc": "Overhang maps, support area and fit against common printer build volumes. With PrusaSlicer or OrcaSlicer installed, the report adds print time and filament use.",
  "n": 22
 },
 {
  "id": "robot-export",
  "status": "done",
  "title": "Robot export (URDF and MJCF)",
  "category": "Make",
  "desc": "An assembly exports as URDF and MJCF with meshes, joint limits, masses and inertias. When MuJoCo or PyBullet is installed, the export is loaded and stepped.",
  "n": 23
 },
 {
  "id": "drawings",
  "status": "done",
  "title": "Layout drawings, parts lists, exploded views",
  "category": "Make",
  "desc": "A layout sheet of a posed assembly as PDF and DXF, a parts list with handbook masses, and an exploded view along the joints.",
  "n": 24
 },
 {
  "id": "joint-reducers",
  "status": "wip",
  "title": "Joint-sized reducers",
  "category": "Finishing for 1.0",
  "desc": "Listed in the roadmap as left for 1.0: a reducer sized from the joint it drives.",
  "n": 25
 },
 {
  "id": "mount-orientation",
  "status": "wip",
  "title": "Catalog mounting orientation",
  "category": "Finishing for 1.0",
  "desc": "Listed in the roadmap as left for 1.0: a mount can snap a catalog part onto a face or an axis and turn in steps of 15 degrees.",
  "n": 26
 },
 {
  "id": "cnc-checks",
  "status": "next",
  "title": "CNC checks",
  "category": "CAM",
  "desc": "Checks aimed at CNC machining, such as tool reach, internal corner radii and features a cutter cannot get to.",
  "n": 27
 },
 {
  "id": "sheet-metal",
  "status": "next",
  "title": "Sheet-metal checks",
  "category": "CAM",
  "desc": "Bend radius, bend allowance and a flat pattern for sheet-metal parts.",
  "n": 28
 },
 {
  "id": "toolpaths",
  "status": "next",
  "title": "Toolpaths",
  "category": "CAM",
  "desc": "Milling toolpaths, and lathe toolpaths where the part is turned, generated through an existing open-source engine. Candidates are under review, and no engine is chosen yet.",
  "n": 29
 },
 {
  "id": "toolpath-sim",
  "status": "next",
  "title": "Toolpath simulation",
  "category": "CAM",
  "desc": "The toolpath runs against the stock before anything is written, so a gouge, a collision or uncut material shows up first.",
  "n": 30
 },
 {
  "id": "gcode",
  "status": "next",
  "title": "G-code output",
  "category": "CAM",
  "desc": "A post-processor writes G-code for common machine controllers. 3D printing stays with PrusaSlicer and OrcaSlicer.",
  "n": 31
 },
 {
  "id": "pcb",
  "status": "future",
  "title": "Custom circuit boards",
  "category": "Electronics",
  "desc": "Board layout through an existing tool, most likely KiCad, so a board and the parts around it live in one project.",
  "n": 32
 },
 {
  "id": "circuits",
  "status": "future",
  "title": "Circuit design",
  "category": "Electronics",
  "desc": "Schematics that an assistant can read and edit, connected to the board layout and the mechanical design.",
  "n": 33
 },
 {
  "id": "board-enclosure",
  "status": "future",
  "title": "Electronics co-design",
  "category": "Electronics",
  "desc": "The assistant designs the circuit board in the same session as the box it lives in, and the two stay in step. Checks include fit, connector cutouts, mounting bosses, clearances and heat. When the enclosure changes, the board updates, and the other way around.",
  "n": 34
 },
 {
  "id": "chips",
  "status": "future",
  "title": "Chip and package design",
  "category": "Electronics",
  "desc": "Concept-level chip packages: die size, pinout and package outline, placed on a board like any other part.",
  "n": 35
 },
 {
  "id": "emi-shielding",
  "status": "future",
  "title": "EMI, EMP and solar-flare shielding",
  "category": "Electronics",
  "desc": "Rough guidance only, not a certified electromagnetic test: whether a box keeps a pulse or a strong field out, from the enclosure material, thickness and the holes that break the shield. A later pass could use an open-source full-wave solver when the handbook rules are too coarse.",
  "n": 36
 },
 {
  "id": "pressure",
  "status": "future",
  "title": "Pressure tests",
  "category": "Analysis",
  "desc": "Internal pressure for tanks, and external pressure for hulls, vacuum chambers or parts in space. External pressure also needs a buckling check, because a shell can cave in while the stress still looks fine.",
  "n": 37
 },
 {
  "id": "wind-aero",
  "status": "future",
  "title": "Wind and air loads",
  "category": "Analysis",
  "desc": "A load from wind or moving air on a vehicle, aircraft or building. The first version is a handbook drag and side-load estimate. A later version would run an airflow solve and put those pressures back on the structure.",
  "n": 38
 },
 {
  "id": "fatigue-wear",
  "status": "future",
  "title": "Fatigue and wear",
  "category": "Analysis",
  "desc": "How many cycles until a part is likely to crack, and which part would crack first, from the stress the solver already computes and an S-N curve. Wear is a separate, rough guess for faces that slide. Today's check of a typed-in stress stays as the smaller version.",
  "n": 39
 },
 {
  "id": "thermal",
  "status": "future",
  "title": "Thermal analysis",
  "category": "Analysis",
  "desc": "Heat from motors, electronics and the sun: where a part gets hot, and whether that heat moves into the next part.",
  "n": 40
 },
 {
  "id": "radiation",
  "status": "future",
  "title": "Radiation shielding and exposure",
  "category": "Analysis",
  "desc": "Rough guidance, not a certified analysis. Estimate dose through walls and shielding for space, surface habitats and nuclear facilities, mark the weak spots, and flag materials that age under radiation. A detailed pass could hand the same geometry to an open-source transport code.",
  "n": 41
 },
 {
  "id": "crash-impact",
  "status": "future",
  "title": "Impact and crash",
  "category": "Analysis",
  "desc": "What happens in a short violent event such as a vehicle crash, a ship collision or a drop test, using a different solver from the static strength check.",
  "n": 42
 },
 {
  "id": "materials-testing",
  "status": "future",
  "title": "Materials library and virtual testing",
  "category": "Analysis",
  "desc": "A richer material database, with real test data behind the numbers, plus virtual tensile, bend and impact tests on a coupon or a part.",
  "n": 43
 },
 {
  "id": "corrosion-uv",
  "status": "future",
  "title": "Environmental degradation",
  "category": "Analysis",
  "desc": "How a material holds up in seawater, salt spray or humid air, and how long a plastic or a coating lasts in the sun. Dissimilar metals that touch can corrode as a pair. This is rough guidance from material rules and data, not a chemistry simulation.",
  "n": 44
 },
 {
  "id": "load-distribution",
  "status": "future",
  "title": "Weight and load distribution",
  "category": "Analysis",
  "desc": "The mass of the whole machine, where the center of gravity sits, and how much load lands on each support: a wheel, a leg, an outrigger or a mount. Then a tipping margin for any machine that can fall over.",
  "n": 45
 },
 {
  "id": "structures",
  "status": "future",
  "title": "Architecture and construction",
  "category": "Construction",
  "desc": "Small structures first: a shed, a frame, a tower or a dome, under ordinary loads such as self-weight, wind and snow. Full building-information modeling, and a check against a building code, is a separate and larger effort for later.",
  "n": 46
 },
 {
  "id": "building-loads",
  "status": "future",
  "title": "Building environmental loads",
  "category": "Construction",
  "desc": "Wind uplift on roofs, flood loads that push and soak, and snow. Mostly handbook formulas applied to small structures, not a stamped code check.",
  "n": 47
 },
 {
  "id": "earthquakes",
  "status": "future",
  "title": "Earthquake response",
  "category": "Construction",
  "desc": "Shake a structure with a response spectrum built on the modal analysis the tool already runs. A full time-history, the ground motion played second by second, would come later.",
  "n": 48
 },
 {
  "id": "marine-hydrostatics",
  "status": "future",
  "title": "Marine hydrostatics",
  "category": "Marine",
  "desc": "How a hull floats: buoyancy, displacement, draft, trim, center of buoyancy, metacentric height and the righting-arm curve for intact stability. It uses the hull geometry and the mass properties from the weight check.",
  "n": 49
 },
 {
  "id": "damaged-stability",
  "status": "future",
  "title": "Damaged stability and flooding",
  "category": "Marine",
  "desc": "Mark a compartment as breached, then compute the new draft, the heel, the stability that remains and whether the ship is still afloat. This is for planning a casualty, not for certifying a ship.",
  "n": 50
 },
 {
  "id": "seakeeping",
  "status": "future",
  "title": "Seakeeping and waves",
  "category": "Marine",
  "desc": "How a hull moves in waves. An open-source boundary-element code is the practical step. A full wave tank, with water on deck and slamming, would be a heavier later solve.",
  "n": 51
 },
 {
  "id": "factory-planning",
  "status": "future",
  "title": "Robotic manufacturing planning",
  "category": "Robotics",
  "desc": "A much later step: given a product, plan the robotic cell or the factory that builds it, then simulate the robots doing the assembly. It waits on an assembly-sequence planner and on the robot export the tool already writes.",
  "n": 52
 },
 {
  "id": "assembly-manuals",
  "status": "future",
  "title": "Step-by-step assembly manuals",
  "category": "App",
  "desc": "A step-by-step book. Each page is one move: the parts and fasteners for that step, a picture, and the torque on each bolt. The order has to leave a path for the next part and room for a tool on the fastener.",
  "n": 53
 },
 {
  "id": "bom-cost",
  "status": "future",
  "title": "Bill of materials and a cost estimate",
  "category": "App",
  "desc": "A list of every part, fastener and hose, with a quantity, a rough price and a supplier to try. Prices would come from a table the user can edit, plus a public catalog when a supplier publishes one, marked rough and dated.",
  "n": 54
 },
 {
  "id": "version-history",
  "status": "future",
  "title": "Design history and a visual diff",
  "category": "App",
  "desc": "Every version of a design, a picture of what changed, and the reason for the change. Named versions, a short written reason, and a view that shows material added and removed.",
  "n": 55
 },
 {
  "id": "domain-packs",
  "status": "future",
  "title": "Domain packs",
  "category": "App",
  "desc": "A way for other people to add a domain pack, such as electronics, architecture or marine design, without editing the core. The plugin door already exists; the packs themselves are not built.",
  "n": 56
 }
];
window.ROADMAP_SECTIONS = {"done": ["1.0", "What's in 1.0"], "wip": ["1.0", "Finishing for 1.0"], "next": ["Next", "CAM: from model to machine"], "future": ["Future", "Ideas board"]};
