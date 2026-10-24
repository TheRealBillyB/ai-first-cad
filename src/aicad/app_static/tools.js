/* Controls for library, gears, mesh, train, motion study, kinematics,
   packaging, drawing, bill of materials, exploded view, and animation.
   Each button calls the existing command. */

function bindWorkspaceTools() {
  $("app-notice-dismiss").onclick = () => clearNotice();
  $("open-library").onclick = () => selectModelTab("library");
  $("open-gear").onclick = () => selectModelTab("gear");
  const tools = {
    "assembly-mesh": "mesh",
    "assembly-train": "train",
    "assembly-kinematics": "kinematics",
    "assembly-package": "package",
    "assembly-drawing": "drawing",
    "assembly-bom": "bom",
    "assembly-exploded": "exploded",
    "assembly-animate": "animate",
  };
  Object.keys(tools).forEach((id) => {
    $(id).onclick = () => openAssemblyTool(tools[id]);
  });
}

function openAssemblyTool(tool) {
  if (!ui.assembly) {
    showError(new Error("Select an assembly first."));
    return;
  }
  ui.assemblyTool = tool;
  showPage("assembly");
  renderAssemblyForm();
  const form = $("assembly-form");
  form.hidden = false;
  const field = form.querySelector("input, select, button");
  if (field) field.focus();
}

function renderAssemblyForm() {
  const form = $("assembly-form");
  if (!form || !ui.assemblyTool) return;
  const current = (ui.state.assemblies || []).find((item) => item.name === ui.assembly);
  form.innerHTML = assemblyFormHtml(ui.assemblyTool, current || { instances: [], motions: [], studies: [], poses: [] });
  form.hidden = false;
  form.onsubmit = (event) => {
    event.preventDefault();
    submitAssemblyForm(ui.assemblyTool).catch(showError);
  };
  const family = form.querySelector("[data-show]");
  if (family) family.onchange = () => toggleAssemblyFields(form);
  toggleAssemblyFields(form);
}

function toggleAssemblyFields(form) {
  const kind = form.querySelector("[data-show]");
  const value = kind ? kind.value : "";
  form.querySelectorAll("[data-when]").forEach((node) => {
    node.hidden = node.dataset.when !== value;
  });
}

function assemblyFormHtml(tool, current) {
  const instances = (current.instances || []).map((item) => `<option value="${escapeAttr(item.name)}">${escapeHtml(item.name)}</option>`).join("");
  const sequences = [...(current.motions || []), ...(current.studies || [])];
  const sequenceOptions = sequences.map((name) => `<option value="${escapeAttr(name)}">${escapeHtml(name)}</option>`).join("");
  const poses = (current.poses || []).map((name) => `<option value="${escapeAttr(name)}">${escapeHtml(name)}</option>`).join("");
  if (tool === "mesh") {
    return `<h3>Gear mesh</h3>
      <p class="muted">Mesh, backlash, contact ratio, and interference. This calls check_gears.</p>
      <div class="field-grid">
        <label>Check
          <select id="mesh-scope" data-show>
            <option value="assembly">This assembly</option>
            <option value="parts">Two parts</option>
          </select>
        </label>
        <label>Steps <input id="mesh-steps" type="number" min="8" step="1" value="16" /></label>
      </div>
      <div class="field-grid" data-when="parts">
        <label>Pinion <input id="mesh-pinion" /></label>
        <label>Gear <input id="mesh-gear" /></label>
        <label>Centre distance mm <input id="mesh-centre" type="number" step="0.01" /></label>
      </div>
      <div class="field-grid">
        <label>Backlash min mm <input id="mesh-backlash-min" type="number" step="0.01" /></label>
        <label>Backlash max mm <input id="mesh-backlash-max" type="number" step="0.01" /></label>
      </div>
      <p data-tool-progress class="muted" role="status"></p>
      <p data-tool-error class="warn" hidden></p>
      <button type="submit" class="primary">Check mesh</button>`;
  }
  if (tool === "train") {
    return `<h3>Gear train</h3>
      <p class="muted">Speed and torque of every shaft. This calls solve_gear_train.</p>
      <div class="field-grid">
        <label>Input shaft <input id="train-shaft" /></label>
        <label>Speed rpm <input id="train-speed" type="number" step="1" value="100" /></label>
        <label>Torque N·m <input id="train-torque" type="number" step="0.1" value="1" /></label>
      </div>
      <div class="field-grid">
        <label>Second shaft <input id="train-speed-shaft" /></label>
        <label>Second speed rpm <input id="train-speed-rpm" type="number" step="1" /></label>
      </div>
      <p class="muted">The second speed is for a differential in a turn. Leave it blank otherwise.</p>
      <p data-tool-progress class="muted" role="status"></p>
      <p data-tool-error class="warn" hidden></p>
      <button type="submit" class="primary">Solve train</button>`;
  }
  if (tool === "kinematics") {
    return `<h3>Kinematics</h3>
      <p class="muted">Forward pose, or an inverse target. This calls solve_kinematics.</p>
      <div class="field-grid">
        <label>Tool <select id="kin-tool">${instances || `<option value="">No instances</option>`}</select></label>
        <label>Tool point X <input id="kin-at-x" type="number" step="0.1" value="0" /></label>
        <label>Y <input id="kin-at-y" type="number" step="0.1" value="0" /></label>
        <label>Z <input id="kin-at-z" type="number" step="0.1" value="0" /></label>
      </div>
      <div class="field-grid">
        <label>Target X <input id="kin-x" type="number" step="0.1" /></label>
        <label>Target Y <input id="kin-y" type="number" step="0.1" /></label>
        <label>Target Z <input id="kin-z" type="number" step="0.1" /></label>
        <label class="inline"><input id="kin-path" type="checkbox" /> Move along a line</label>
        <label>Path steps <input id="kin-steps" type="number" min="2" step="1" value="5" /></label>
      </div>
      <p class="muted">Leave the target blank to solve the pose the joints already have.</p>
      <p data-tool-progress class="muted" role="status"></p>
      <p data-tool-error class="warn" hidden></p>
      <button type="submit" class="primary">Solve</button>`;
  }
  if (tool === "package") {
    return `<h3>Packaging</h3>
      ${packageFieldsHtml({ assembly: true, poses })}
      <p data-tool-progress class="muted" role="status"></p>
      <p data-tool-error class="warn" hidden></p>
      <button type="submit" class="primary">Check fit</button>`;
  }
  if (tool === "drawing") {
    return `<h3>Drawing</h3>
      <p class="muted">A third-angle layout sheet. It is not a shop drawing and it has no GD&amp;T. This calls export_drawing.</p>
      <div class="field-grid">
        <label>Scale <input id="draw-scale" placeholder="1:2" /></label>
        <label>Exploded gap mm <input id="draw-gap" type="number" min="0" step="1" /></label>
        <label class="inline"><input id="draw-exploded" type="checkbox" /> Exploded isometric</label>
      </div>
      <p data-tool-progress class="muted" role="status"></p>
      <p data-tool-error class="warn" hidden></p>
      <button type="submit" class="primary">Write drawing</button>`;
  }
  if (tool === "bom") {
    return `<h3>Bill of materials</h3>
      <p class="muted">Counts parts, fasteners, and routes, and writes a CSV. Mass is a handbook estimate. No prices. This calls bill_of_materials.</p>
      <p data-tool-progress class="muted" role="status"></p>
      <p data-tool-error class="warn" hidden></p>
      <button type="submit" class="primary">Write bill of materials</button>`;
  }
  if (tool === "exploded") {
    return `<h3>Exploded view</h3>
      <p class="muted">Pulls parts apart along the joints. The gap is not a dimension. This calls exploded_view.</p>
      <div class="field-grid">
        <label>Gap mm <input id="explode-gap" type="number" min="0" step="1" /></label>
        <label class="inline"><input id="explode-drawing" type="checkbox" /> Also put it on the layout sheet</label>
      </div>
      <p data-tool-progress class="muted" role="status"></p>
      <p data-tool-error class="warn" hidden></p>
      <button type="submit" class="primary">Render exploded view</button>`;
  }
  return `<h3>Animation</h3>
    <p class="muted">A GIF in the project. MP4 and WebM need ffmpeg, so this writes a GIF. This calls render_animation.</p>
    <div class="field-grid">
      <label>Kind
        <select id="anim-kind" data-show>
          <option value="orbit">Orbit</option>
          <option value="motion">Motion</option>
          <option value="exploded">Exploded</option>
        </select>
      </label>
      <label>Frames <input id="anim-frames" type="number" min="2" max="300" step="1" value="8" /></label>
      <label>Frames per second <input id="anim-fps" type="number" min="1" max="60" step="1" value="8" /></label>
    </div>
    <div data-when="motion">
      <label>Motion or study
        <select id="anim-sequence">${sequenceOptions || `<option value="">None on this assembly</option>`}</select>
      </label>
    </div>
    <div data-when="exploded">
      <label>Gap mm <input id="anim-gap" type="number" min="0" step="1" /></label>
    </div>
    <p data-tool-progress class="muted" role="status"></p>
    <p data-tool-error class="warn" hidden></p>
    <button type="submit" class="primary">Render animation</button>`;
}

function packageFieldsHtml(options) {
  const poses = options.poses
    ? `<label>Pose <select data-pack-pose><option value="">Stored pose</option>${options.poses}</select></label>`
    : `<label>Pose <input data-pack-pose /></label>`;
  return `<p class="muted">Wall clearance, protrusion, and overlap. This calls check_packaging.</p>
    <div class="field-grid">
      <label>Envelope part <input data-pack-envelope /></label>
      <label>Or box mm <input data-pack-box placeholder="80, 60, 40" /></label>
      <label>Parts <input data-pack-parts placeholder="base, cover" /></label>
      <label>Min clearance mm <input data-pack-gap type="number" min="0" step="0.1" value="0" /></label>
      ${options.assembly ? `<label class="inline"><input data-pack-use-assembly type="checkbox" checked /> Use this assembly</label>${poses}` : ""}
    </div>`;
}

function renderLibraryPanel() {
  const root = $("panel");
  const report = ui.reports.library;
  const pick = ui.reports.libraryPick;
  const rows = report && report.matches ? report.matches : [];
  const list = rows.length
    ? `<div class="result-list">${rows.map((item, index) => {
      const title = item.size ? `${item.kind} ${item.size}` : item.kind;
      const on = pick && pick.kind === item.kind && pick.size === item.size ? " on" : "";
      return `<button type="button" data-match="${index}" class="${on.trim()}">${escapeHtml(title)}</button>`;
    }).join("")}</div>`
    : `<p class="muted">${report ? "Nothing in the catalog matches that." : "Search a designation such as 608, M5, or NEMA17."}</p>`;
  const note = report && report.truncated ? `<p class="warn">Showing the first matches. Narrow the search.</p>` : "";
  const chosen = pick && pick.size
    ? `<p>${escapeHtml(pick.description || "")}</p>`
    : "";
  const length = pick && (pick.kind === "socket_head_cap_screw" || (pick.parameters && pick.parameters.length))
    ? `<label>Length under the head mm <input id="library-length" type="number" min="0" step="1" value="12" /></label>`
    : "";
  root.innerHTML = `<h3>Parts library</h3>
    <form id="library-form" class="tool-form">
      <label>Search <input id="library-query" value="${escapeAttr((report && report.query) || "")}" /></label>
      <button type="submit" class="primary">Search</button>
      ${note}
      ${list}
      ${chosen}
      <label>Part name <input id="library-name" value="${escapeAttr(ui.reports.libraryName || "")}" /></label>
      ${length}
      <p data-tool-progress class="muted" role="status"></p>
      <p data-tool-error class="warn" hidden></p>
      <button type="button" id="library-insert" class="primary">Insert</button>
      <p class="muted">Insert uses insert_standard_part. The solid shows in the model view.</p>
    </form>`;
  $("library-form").onsubmit = (event) => {
    event.preventDefault();
    runLibrarySearch().catch(showError);
  };
  $("library-insert").onclick = () => runLibraryInsert().catch(showError);
  root.querySelectorAll("[data-match]").forEach((button) => {
    button.onclick = () => {
      const match = rows[Number(button.dataset.match)];
      ui.reports.libraryPick = match;
      if (match && match.size && !$("library-name").value.trim()) {
        ui.reports.libraryName = String(match.size).replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "part";
      }
      renderLibraryPanel();
    };
  });
}

function renderGearPanel() {
  const root = $("panel");
  const draft = ui.gearDraft || {
    name: "pinion",
    family: "spur",
    module: "2",
    teeth: "18",
    pressure: "20",
    face: "6",
    helix: "15",
    mate: "36",
    shaft: "90",
    spiral: "35",
    role: "pinion",
    hypoidMate: "16",
    offset: "12",
    ring: "80",
    hypoidSpiral: "35",
    hypoidShaft: "90",
  };
  const report = ui.reports.gear;
  root.innerHTML = `<h3>Gear</h3>
    <form id="gear-form" class="tool-form">
      <p class="muted">Spur, helical, bevel, or hypoid. This calls create_gear. The solid shows in the model view.</p>
      <div class="field-grid">
        <label>Name <input id="gear-name" value="${escapeAttr(draft.name)}" /></label>
        <label>Family
          <select id="gear-family" data-show>
            ${gearOption("spur", "Spur", draft.family)}
            ${gearOption("helical", "Helical", draft.family)}
            ${gearOption("straight_bevel", "Straight bevel", draft.family)}
            ${gearOption("spiral_bevel", "Spiral bevel", draft.family)}
            ${gearOption("hypoid", "Hypoid", draft.family)}
          </select>
        </label>
        <label>Teeth <input id="gear-teeth" type="number" min="4" step="1" value="${escapeAttr(draft.teeth)}" /></label>
        <label>Face width mm <input id="gear-face" type="number" min="0" step="0.1" value="${escapeAttr(draft.face)}" /></label>
        <label>Pressure angle ° <input id="gear-pressure" type="number" step="0.1" value="${escapeAttr(draft.pressure)}" /></label>
      </div>
      <div class="field-grid" data-when="module">
        <label>Module mm <input id="gear-module" type="number" min="0" step="0.1" value="${escapeAttr(draft.module)}" /></label>
      </div>
      <div class="field-grid" data-when="helical">
        <label>Helix angle ° <input id="gear-helix" type="number" step="0.1" value="${escapeAttr(draft.helix)}" /></label>
      </div>
      <div class="field-grid" data-when="bevel">
        <label>Mate teeth <input id="gear-mate" type="number" min="4" step="1" value="${escapeAttr(draft.mate)}" /></label>
        <label>Shaft angle ° <input id="gear-shaft-angle" type="number" step="0.1" value="${escapeAttr(draft.shaft)}" /></label>
      </div>
      <div class="field-grid" data-when="spiral_bevel">
        <label>Spiral angle ° <input id="gear-spiral" type="number" step="0.1" value="${escapeAttr(draft.spiral)}" /></label>
      </div>
      <div class="field-grid" data-when="hypoid">
        <label>Role
          <select id="gear-role">
            <option value="pinion" ${draft.role === "pinion" ? "selected" : ""}>Pinion</option>
            <option value="ring" ${draft.role === "ring" ? "selected" : ""}>Ring</option>
          </select>
        </label>
        <label>Mate teeth <input id="gear-hypoid-mate" type="number" min="6" step="1" value="${escapeAttr(draft.hypoidMate)}" /></label>
        <label>Offset mm <input id="gear-offset" type="number" step="0.1" value="${escapeAttr(draft.offset)}" /></label>
        <label>Ring diameter mm <input id="gear-ring" type="number" min="0" step="0.1" value="${escapeAttr(draft.ring)}" /></label>
        <label>Spiral angle ° <input id="gear-hypoid-spiral" type="number" step="0.1" value="${escapeAttr(draft.hypoidSpiral)}" /></label>
        <label>Shaft angle ° <input id="gear-hypoid-shaft" type="number" step="0.1" value="${escapeAttr(draft.hypoidShaft)}" /></label>
      </div>
      <p class="muted" data-when="hypoid">A hypoid is one member of an offset pair. Build the pinion and the ring, then check the mesh.</p>
      <p data-tool-progress class="muted" role="status"></p>
      <p data-tool-error class="warn" hidden></p>
      <button type="submit" id="gear-create" class="primary">Create gear</button>
    </form>
    <div id="gear-result"></div>`;
  showGearSummary(report);
  const form = $("gear-form");
  $("gear-family").onchange = () => {
    rememberGearDraft();
    toggleGearFields();
  };
  form.onsubmit = (event) => {
    event.preventDefault();
    runCreateGear().catch(showError);
  };
  toggleGearFields();
}

function gearOption(value, label, selected) {
  return `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`;
}

function toggleGearFields() {
  const family = $("gear-family") ? $("gear-family").value : "spur";
  const show = {
    module: family !== "hypoid",
    helical: family === "helical",
    bevel: family === "straight_bevel" || family === "spiral_bevel",
    spiral_bevel: family === "spiral_bevel",
    hypoid: family === "hypoid",
  };
  document.querySelectorAll("#gear-form [data-when]").forEach((node) => {
    node.hidden = !show[node.dataset.when];
  });
}

function rememberGearDraft() {
  if (!$("gear-family")) return;
  ui.gearDraft = {
    name: $("gear-name").value,
    family: $("gear-family").value,
    module: $("gear-module").value,
    teeth: $("gear-teeth").value,
    pressure: $("gear-pressure").value,
    face: $("gear-face").value,
    helix: $("gear-helix").value,
    mate: $("gear-mate").value,
    shaft: $("gear-shaft-angle").value,
    spiral: $("gear-spiral").value,
    role: $("gear-role").value,
    hypoidMate: $("gear-hypoid-mate").value,
    offset: $("gear-offset").value,
    ring: $("gear-ring").value,
    hypoidSpiral: $("gear-hypoid-spiral").value,
    hypoidShaft: $("gear-hypoid-shaft").value,
  };
}

function gearSpecFromForm() {
  rememberGearDraft();
  const family = $("gear-family").value;
  const spec = {
    type: family,
    teeth: requiredNumber($("gear-teeth").value, "Enter the tooth count."),
    face_width_mm: requiredNumber($("gear-face").value, "Enter the face width in millimeters."),
  };
  const pressure = optionalNumber($("gear-pressure").value, "Pressure angle must be a number.");
  if (pressure != null) spec.pressure_angle_deg = pressure;
  if (family !== "hypoid") {
    spec.module_mm = requiredNumber($("gear-module").value, "Enter the module in millimeters.");
  }
  if (family === "helical") {
    spec.helix_angle_deg = requiredNumber($("gear-helix").value, "Enter the helix angle in degrees.");
  }
  if (family === "straight_bevel" || family === "spiral_bevel") {
    spec.mate_teeth = requiredNumber($("gear-mate").value, "Enter the mating tooth count.");
    const shaft = optionalNumber($("gear-shaft-angle").value, "Shaft angle must be a number.");
    if (shaft != null) spec.shaft_angle_deg = shaft;
  }
  if (family === "spiral_bevel") {
    spec.spiral_angle_deg = requiredNumber($("gear-spiral").value, "Enter the spiral angle in degrees.");
  }
  if (family === "hypoid") {
    spec.role = $("gear-role").value;
    spec.mate_teeth = requiredNumber($("gear-hypoid-mate").value, "Enter the mating tooth count.");
    spec.offset_mm = requiredNumber($("gear-offset").value, "Enter the pinion offset in millimeters.");
    spec.ring_diameter_mm = requiredNumber($("gear-ring").value, "Enter the ring diameter in millimeters.");
    spec.spiral_angle_deg = requiredNumber($("gear-hypoid-spiral").value, "Enter the ring spiral angle in degrees.");
    const shaft = optionalNumber($("gear-hypoid-shaft").value, "Shaft angle must be a number.");
    if (shaft != null) spec.shaft_angle_deg = shaft;
  }
  return spec;
}

function renderPackagePanel() {
  const root = $("panel");
  const draft = ui.packageDraft || { envelope: "", box: "", parts: "", gap: "0", pose: "" };
  const report = (ui.state && ui.state.panels && ui.state.panels.packaging) || ui.reports.packaging;
  root.innerHTML = `<h3>Packaging</h3>
    <form id="package-form" class="tool-form">
      ${packageFieldsHtml({ assembly: false })}
      <p data-tool-progress class="muted" role="status"></p>
      <p data-tool-error class="warn" hidden></p>
      <button type="submit" class="primary">Check fit</button>
    </form>
    <div id="package-result"></div>`;
  const fields = $("package-form");
  fields.querySelector("[data-pack-envelope]").value = draft.envelope;
  fields.querySelector("[data-pack-box]").value = draft.box;
  fields.querySelector("[data-pack-parts]").value = draft.parts;
  fields.querySelector("[data-pack-gap]").value = draft.gap;
  fields.onsubmit = (event) => {
    event.preventDefault();
    runPackaging(fields.querySelector("button")).catch(showError);
  };
  if (report) $("package-result").innerHTML = packagingHtml(report);
}

async function runLibrarySearch() {
  const button = $("library-form").querySelector("button[type=submit]");
  const query = $("library-query").value.trim();
  const report = await runTool(button, "Searching…", () => command("search_parts", { query }));
  if (!report) return;
  ui.reports.library = report;
  ui.reports.libraryPick = null;
  renderLibraryPanel();
}

async function runLibraryInsert() {
  const pick = ui.reports.libraryPick;
  if (!pick || !pick.size) {
    showError(new Error("Search a designation such as 608 or M5, then choose that size."));
    return;
  }
  const name = $("library-name").value.trim();
  if (!name) {
    showError(new Error("Name the part."));
    return;
  }
  ui.reports.libraryName = name;
  const args = { name, kind: pick.kind, size: pick.size };
  const lengthField = $("library-length");
  if (lengthField) args.length = requiredNumber(lengthField.value, "Enter the length under the head, in millimeters.");
  const report = await runTool($("library-insert"), "Inserting…", () => command("insert_standard_part", args));
  if (!report) return;
  clearNotice();
  ui.part = name;
  await refresh({ fit: "part", focus: name, solid: true });
}

async function runCreateGear() {
  const name = $("gear-name").value.trim();
  if (!name) {
    showError(new Error("Name the gear."));
    return;
  }
  const spec = gearSpecFromForm();
  const report = await runTool($("gear-create"), "Building the gear…", () => command("create_gear", { name, spec }));
  if (!report) return;
  clearNotice();
  ui.reports.gear = report;
  ui.part = name;
  await refresh({ fit: "part", focus: name, solid: true });
  showGearSummary(report);
}

function showGearSummary(report) {
  const slot = $("gear-result");
  if (!slot) return;
  if (!report) {
    slot.innerHTML = "";
    return;
  }
  const gear = report.gear || {};
  const pitch = gear.pitch_diameter_mm != null ? gear.pitch_diameter_mm : gear.ring_diameter_mm;
  slot.innerHTML = `${claimBlock(report)}<div class="kv">
      <span>Type</span><b>${escapeHtml(gear.type || "")}</b>
      <span>Teeth</span><b>${escapeHtml(gear.teeth != null ? gear.teeth : "")}</b>
      <span>Pitch</span><b>${pitch != null ? `${pitch} mm` : "n/a"}</b>
    </div>
    <p class="muted">The gear is in the model view.</p>`;
}

async function runPackaging(button) {
  const root = button.closest("form");
  rememberPackageDraft(root);
  const args = packagingArguments(root);
  const report = await runTool(button, "Checking fit…", () => command("check_packaging", args));
  if (!report) return;
  clearNotice();
  ui.reports.packaging = report;
  ui.reportFocus = "packaging";
  await refresh();
  if (ui.page === "assembly") showPackagingReport(report);
  else if ($("package-result")) $("package-result").innerHTML = packagingHtml(report);
}

function rememberPackageDraft(root) {
  const envelope = root.querySelector("[data-pack-envelope]");
  if (!envelope) return;
  ui.packageDraft = {
    envelope: envelope.value,
    box: root.querySelector("[data-pack-box]").value,
    parts: root.querySelector("[data-pack-parts]").value,
    gap: root.querySelector("[data-pack-gap]").value,
    pose: root.querySelector("[data-pack-pose]") ? root.querySelector("[data-pack-pose]").value : "",
  };
}

function packagingArguments(root) {
  const envelopeName = root.querySelector("[data-pack-envelope]").value.trim();
  const box = root.querySelector("[data-pack-box]").value.trim();
  let envelope = null;
  if (envelopeName) envelope = envelopeName;
  else if (box) {
    const size = box.split(",").map((piece) => Number(piece.trim()));
    if (size.length !== 3 || size.some((value) => !Number.isFinite(value))) {
      throw new Error("A box is three millimeters, for example 80, 60, 40.");
    }
    envelope = { shape: "box", size };
  } else {
    throw new Error("Name an envelope part, or type a box size.");
  }
  const gap = optionalNumber(root.querySelector("[data-pack-gap]").value, "Clearance must be a number.");
  const args = { envelope, min_clearance_mm: gap == null ? 0 : gap };
  const parts = root.querySelector("[data-pack-parts]").value.split(",").map((piece) => piece.trim()).filter(Boolean);
  const useAssembly = root.querySelector("[data-pack-use-assembly]");
  if (useAssembly && useAssembly.checked) {
    if (!ui.assembly) throw new Error("Select an assembly first.");
    args.assembly = ui.assembly;
    const pose = root.querySelector("[data-pack-pose]").value.trim();
    if (pose) args.pose = pose;
  } else if (parts.length) {
    args.parts = parts;
  } else {
    throw new Error("Name the parts to pack, or pass an assembly.");
  }
  return args;
}

function packagingHtml(report) {
  const rows = (report.parts || []).map((part) =>
    `<li><strong>${escapeHtml(part.name)}</strong> ${part.passed ? "pass" : "fail"} · clearance ${part.min_clearance_mm} mm${part.protrudes ? ` · sticks out ${part.max_protrusion_mm} mm` : ""}</li>`
  ).join("");
  const overlaps = (report.overlaps || []).map((hit) =>
    `<li>${escapeHtml(hit.part_a)} × ${escapeHtml(hit.part_b)} · ${hit.volume_mm3} mm³</li>`
  ).join("");
  return `${claimBlock(report)}<div class="kv">
      <span>Status</span><b>${report.passed ? "pass" : "fail"}</b>
      <span>Closest</span><b>${escapeHtml(report.min_clearance_part || "")} ${report.min_clearance_mm} mm</b>
    </div>
    <ul>${rows}</ul>
    ${overlaps ? `<h3>Overlap</h3><ul>${overlaps}</ul>` : ""}
    <p class="muted">${escapeHtml(report.note || "")}</p>`;
}

function submitAssemblyForm(tool) {
  const button = $("assembly-form").querySelector("button[type=submit]");
  const runners = {
    mesh: runGearMesh,
    train: runGearTrain,
    kinematics: runKinematics,
    package: () => runPackaging(button),
    drawing: runDrawing,
    bom: runBom,
    exploded: runExploded,
    animate: runAnimation,
  };
  return Promise.resolve(runners[tool](button));
}

async function runGearMesh(button) {
  const scope = $("mesh-scope").value;
  const steps = requiredNumber($("mesh-steps").value, "Enter how many steps to sample. At least 8.");
  const args = { steps };
  const low = optionalNumber($("mesh-backlash-min").value, "Backlash minimum must be a number.");
  const high = optionalNumber($("mesh-backlash-max").value, "Backlash maximum must be a number.");
  if (low != null || high != null) {
    if (low == null || high == null) throw new Error("Enter both backlash limits, or leave both blank.");
    args.backlash_mm = { min: low, max: high };
  }
  if (scope === "assembly") {
    if (!ui.assembly) throw new Error("Select an assembly first.");
    args.assembly = ui.assembly;
  } else {
    const pinion = $("mesh-pinion").value.trim();
    const gear = $("mesh-gear").value.trim();
    if (!pinion || !gear) throw new Error("Name the pinion and the gear.");
    args.pinion = pinion;
    args.gear = gear;
    const centre = optionalNumber($("mesh-centre").value, "Centre distance must be a number.");
    if (centre != null) args.centre_distance_mm = centre;
  }
  const report = await runTool(button, "Checking mesh…", () => command("check_gears", args));
  if (!report) return;
  clearNotice();
  ui.reportFocus = "gears";
  await refresh();
  showGearMeshReport(report);
}

async function runGearTrain(button) {
  if (!ui.assembly) throw new Error("Select an assembly first.");
  const drive = {
    inputs: [{
      shaft: $("train-shaft").value.trim(),
      speed_rpm: requiredNumber($("train-speed").value, "Enter the input speed in rpm."),
      torque_nm: requiredNumber($("train-torque").value, "Enter the input torque in newton-metres."),
    }],
  };
  if (!drive.inputs[0].shaft) throw new Error("Name the input shaft.");
  const extra = $("train-speed-shaft").value.trim();
  const extraSpeed = optionalNumber($("train-speed-rpm").value, "The second speed must be a number.");
  if (extra || extraSpeed != null) {
    if (!extra || extraSpeed == null) throw new Error("Name the second shaft and its speed, or leave both blank.");
    drive.speeds = [{ shaft: extra, speed_rpm: extraSpeed }];
  }
  const report = await runTool(button, "Solving the train…", () => command("solve_gear_train", { name: ui.assembly, drive }));
  if (!report) return;
  clearNotice();
  ui.reportFocus = "gear_train";
  await refresh();
  showGearTrainReport(report);
}

async function runKinematics(button) {
  if (!ui.assembly) throw new Error("Select an assembly first.");
  const tool = $("kin-tool").value;
  if (!tool) throw new Error("Choose the tool instance.");
  const args = {
    name: ui.assembly,
    tool,
    at: [
      requiredNumber($("kin-at-x").value, "Tool point X must be a number."),
      requiredNumber($("kin-at-y").value, "Tool point Y must be a number."),
      requiredNumber($("kin-at-z").value, "Tool point Z must be a number."),
    ],
  };
  const target = [$("kin-x").value, $("kin-y").value, $("kin-z").value];
  const anyTarget = target.some((value) => String(value).trim() !== "");
  if (anyTarget) {
    const position = [
      requiredNumber(target[0], "Enter the target X, Y, and Z."),
      requiredNumber(target[1], "Enter the target X, Y, and Z."),
      requiredNumber(target[2], "Enter the target X, Y, and Z."),
    ];
    if ($("kin-path").checked) {
      args.path = {
        kind: "line",
        steps: requiredNumber($("kin-steps").value, "Enter the path steps."),
        target: { position },
      };
    } else {
      args.target = { position };
    }
  }
  const report = await runTool(button, "Solving kinematics…", () => command("solve_kinematics", args));
  if (!report) return;
  clearNotice();
  ui.reportFocus = "kinematics";
  await refresh();
  showKinematicsReport(report);
}

async function runDrawing(button) {
  if (!ui.assembly) throw new Error("Select an assembly first.");
  const args = { name: ui.assembly, exploded: $("draw-exploded").checked };
  const scale = $("draw-scale").value.trim();
  if (scale) args.scale = scale;
  const gap = optionalNumber($("draw-gap").value, "Gap must be a number.");
  if (gap != null) args.gap_mm = gap;
  const report = await runTool(button, "Writing the drawing…", () => command("export_drawing", args));
  if (!report) return;
  clearNotice();
  ui.reportFocus = "drawing";
  await refresh();
  showDrawingReport(report);
}

async function runBom(button) {
  if (!ui.assembly) throw new Error("Select an assembly first.");
  const report = await runTool(button, "Writing the bill of materials…", () => command("bill_of_materials", { name: ui.assembly }));
  if (!report) return;
  clearNotice();
  ui.reportFocus = "bom";
  await refresh();
  showBomReport(report);
}

async function runExploded(button) {
  if (!ui.assembly) throw new Error("Select an assembly first.");
  const args = { name: ui.assembly, on_drawing: $("explode-drawing").checked };
  const gap = optionalNumber($("explode-gap").value, "Gap must be a number.");
  if (gap != null) args.gap_mm = gap;
  const report = await runTool(button, "Rendering the exploded view…", () => command("exploded_view", args));
  if (!report) return;
  clearNotice();
  ui.reportFocus = "exploded";
  await refresh();
  showExplodedReport(report);
}

async function runAnimation(button) {
  if (!ui.assembly) throw new Error("Select an assembly first.");
  const kind = $("anim-kind").value;
  const args = {
    name: ui.assembly,
    kind,
    frames: requiredNumber($("anim-frames").value, "Enter the frame count."),
    fps: requiredNumber($("anim-fps").value, "Enter the frames per second."),
    video: "gif",
  };
  if (kind === "motion") {
    const sequence = $("anim-sequence").value;
    if (!sequence) throw new Error("Pick a motion or a study to play.");
    args.sequence = sequence;
  }
  if (kind === "exploded") {
    const gap = optionalNumber($("anim-gap").value, "Gap must be a number.");
    if (gap != null) args.gap_mm = gap;
  }
  const report = await runTool(button, "Rendering the animation…", () => command("render_animation", args));
  if (!report) return;
  clearNotice();
  ui.reportFocus = "animation";
  await refresh();
  showAnimationReport(report);
}

async function runMotionStudy(sequence, button) {
  if (!ui.assembly) {
    showError(new Error("Select an assembly first."));
    return;
  }
  const args = { name: ui.assembly, sequence };
  const steps = $("study-steps") ? optionalNumber($("study-steps").value, "Steps must be a number.") : null;
  const clearance = $("study-clearance") ? optionalNumber($("study-clearance").value, "Clearance must be a number.") : null;
  if (steps != null) args.steps = steps;
  if (clearance != null) args.clearance_mm = clearance;
  const report = await runTool(button, "Running the study…", () => command("run_motion_study", args));
  if (!report) return;
  clearNotice();
  ui.assemblySignature = "";
  ui.reportFocus = "motion_study";
  await refresh();
  showStudyReport(report);
}

async function runTool(button, label, work) {
  const previous = button ? button.textContent : "";
  const root = button && button.closest("form") ? button.closest("form") : document.body;
  const progress = root.querySelector("[data-tool-progress]");
  const local = root.querySelector("[data-tool-error]");
  if (local) {
    local.hidden = true;
    local.textContent = "";
  }
  if (progress) progress.textContent = label;
  if (ui.page === "assembly" && $("assembly-status")) $("assembly-status").textContent = label;
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  }
  try {
    return await work();
  } catch (error) {
    const message = error && error.message ? error.message : String(error || "The command failed.");
    showError(error instanceof Error ? error : new Error(message));
    if (ui.page === "assembly" && $("assembly-status")) $("assembly-status").textContent = message;
    if (local) {
      local.hidden = false;
      local.textContent = message;
    }
    if (progress) progress.textContent = "";
    return null;
  } finally {
    if (button && button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.textContent = previous;
    }
    if (progress && progress.isConnected && progress.textContent === label) progress.textContent = "";
  }
}

function optionalNumber(text, message) {
  const trimmed = String(text == null ? "" : text).trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value)) throw new Error(message);
  return value;
}

function showPicture(path, alt) {
  const picture = $("assembly-picture");
  if (!picture) return;
  if (!path) {
    picture.hidden = true;
    picture.removeAttribute("src");
    return;
  }
  picture.hidden = false;
  picture.alt = alt || "Result";
  picture.src = fileUrl(path);
}

function showFocusedReport() {
  const focus = ui.reportFocus;
  if (!focus || !ui.state || !ui.state.panels) return false;
  const report = ui.state.panels[focus];
  if (!report) return false;
  if (report.assembly && ui.assembly && report.assembly !== ui.assembly && focus !== "gears") return false;
  return paintAssemblyReport(focus, report);
}

function showKnownAssemblyReport() {
  const panels = ui.state.panels || {};
  const order = ["motion_study", "gears", "gear_train", "kinematics", "packaging", "drawing", "bom", "exploded", "animation"];
  for (let index = 0; index < order.length; index += 1) {
    const key = order[index];
    const report = panels[key];
    if (!report) continue;
    if (report.assembly && report.assembly !== ui.assembly) continue;
    return paintAssemblyReport(key, report);
  }
  return false;
}

function paintAssemblyReport(key, report) {
  const painters = {
    motion_study: showStudyReport,
    gears: showGearMeshReport,
    gear_train: showGearTrainReport,
    kinematics: showKinematicsReport,
    packaging: showPackagingReport,
    drawing: showDrawingReport,
    bom: showBomReport,
    exploded: showExplodedReport,
    animation: showAnimationReport,
  };
  if (!painters[key]) return false;
  painters[key](report);
  return true;
}

function showStudyReport(report) {
  $("assembly-status").textContent = `Study ${report.status || ""}`;
  showPicture(null);
  const hits = (report.collisions || []).slice(0, 8).map((hit) =>
    `<li>${escapeHtml(hit.a || "")} × ${escapeHtml(hit.b || "")}</li>`
  ).join("");
  const limits = (report.limit_hits || []).slice(0, 8).map((hit) =>
    `<li>${escapeHtml(hit.joint || hit.name || "joint")}</li>`
  ).join("");
  $("assembly-report").innerHTML = `${claimBlock(report)}<h3>${escapeHtml(report.study && report.study.name ? report.study.name : "Motion study")}</h3>
    <div class="kv">
      <span>Status</span><b>${escapeHtml(report.status || "")}</b>
      <span>Collisions</span><b>${report.collision_count != null ? report.collision_count : 0}</b>
      <span>Limit hits</span><b>${report.limit_hit_count != null ? report.limit_hit_count : 0}</b>
    </div>
    ${hits ? `<ul>${hits}</ul>` : ""}
    ${limits ? `<h3>Limits</h3><ul>${limits}</ul>` : ""}
    <p class="muted">${escapeHtml(report.note || "")}</p>`;
}

function showGearMeshReport(report) {
  const pairs = report.pairs || [report];
  const failed = pairs.filter((pair) => pair.status && pair.status !== "pass").length;
  $("assembly-status").textContent = failed ? `Mesh ${failed} failed` : `Mesh ${report.status || "pass"}`;
  showPicture(null);
  const rows = pairs.map((pair) => {
    const backlash = pair.backlash || {};
    const names = (pair.instances || []).join(" × ") || "Pair";
    return `<li><strong>${escapeHtml(names)}</strong> ${escapeHtml(pair.status || "")}
      · backlash ${backlash.circumferential_mm != null ? backlash.circumferential_mm : "n/a"} mm
      · contact ${pair.total_contact_ratio != null ? pair.total_contact_ratio : "n/a"}
      · interference ${pair.interference ? "yes" : "no"}</li>`;
  }).join("");
  $("assembly-report").innerHTML = `${claimBlock(report)}<h3>Gear mesh</h3><ul>${rows}</ul>`;
}

function showGearTrainReport(report) {
  $("assembly-status").textContent = `Train ${report.status || ""}`;
  showPicture(null);
  const rows = (report.shafts || []).map((shaft) =>
    `<li><strong>${escapeHtml(shaft.name)}</strong> ${escapeHtml(shaft.role || "")}
      · ${shaft.speed_rpm != null ? shaft.speed_rpm : "n/a"} rpm
      · ${shaft.torque_nm != null ? shaft.torque_nm : "n/a"} N·m</li>`
  ).join("");
  $("assembly-report").innerHTML = `${claimBlock(report)}<h3>Gear train</h3><ul>${rows || "<li>No shafts</li>"}</ul>
    <p class="muted">${escapeHtml(report.note || "")}</p>`;
}

function showKinematicsReport(report) {
  const forward = report.forward || {};
  const inverse = report.inverse || {};
  const position = (forward.position_mm || []).map((value) => Number(value).toFixed(2)).join(", ");
  const solutions = (inverse.solutions || []).map((item) =>
    `<li>${escapeHtml(item.name || item.status || "solution")} ${escapeHtml(JSON.stringify(item.joints || {}))}</li>`
  ).join("");
  $("assembly-status").textContent = `Kinematics ${report.task || ""} ${inverse.status || ""}`.trim();
  showPicture(null);
  $("assembly-report").innerHTML = `${claimBlock(report)}<h3>Kinematics</h3>
    <div class="kv">
      <span>Task</span><b>${escapeHtml(report.task || "")}</b>
      <span>Tool</span><b>${position || "n/a"}</b>
      <span>Inverse</span><b>${escapeHtml(inverse.status || "")}</b>
    </div>
    ${solutions ? `<ul>${solutions}</ul>` : ""}
    <p class="muted">${escapeHtml(report.note || "")}</p>`;
}

function showPackagingReport(report) {
  $("assembly-status").textContent = report.passed ? "Packaging pass" : "Packaging fail";
  showPicture(null);
  $("assembly-report").innerHTML = `<h3>Packaging</h3>${packagingHtml(report)}`;
}

function showDrawingReport(report) {
  const view = (report.views || [])[0];
  $("assembly-status").textContent = `Drawing ${report.scale || ""}`.trim();
  showPicture(view && view.path, "Layout drawing");
  const files = report.files || {};
  $("assembly-report").innerHTML = `${claimBlock(report)}<h3>Drawing</h3>
    <p>Scale ${escapeHtml(report.scale || "")}. PDF ${escapeHtml(baseName(files.pdf))}. DXF ${escapeHtml(baseName(files.dxf))}.</p>
    <p class="muted">${escapeHtml(report.note || "")}</p>`;
}

function showBomReport(report) {
  const view = (report.views || [])[0];
  $("assembly-status").textContent = "Bill of materials";
  showPicture(view && (view.path || view), "Bill of materials");
  const rows = (report.rows || []).map((row) =>
    `<li><strong>${escapeHtml(row.part || row.name || row.designation || "")}</strong>
      × ${row.quantity != null ? row.quantity : ""}
      ${escapeHtml(row.category || "")}</li>`
  ).join("");
  $("assembly-report").innerHTML = `${claimBlock(report)}<h3>Bill of materials</h3>
    <p>${escapeHtml(baseName(report.csv || ""))}</p>
    <ul>${rows || "<li>No rows</li>"}</ul>`;
}

function showExplodedReport(report) {
  const view = (report.views || []).find((item) => item.name === "exploded") || (report.views || [])[0];
  $("assembly-status").textContent = "Exploded view";
  showPicture(view && view.path, "Exploded view");
  $("assembly-report").innerHTML = `${claimBlock(report)}<h3>Exploded view</h3>
    <p>Gap ${report.gap_mm != null ? report.gap_mm : ""} mm.</p>
    <p class="muted">${escapeHtml(report.note || "")}</p>`;
}

function showAnimationReport(report) {
  $("assembly-status").textContent = `Animation ${report.kind || ""}`.trim();
  showPicture(report.gif || (report.views && report.views[0] && report.views[0].path), "Animation");
  $("assembly-report").innerHTML = `${claimBlock(report)}<h3>Animation</h3>
    <p>${report.frames || ""} frames · ${escapeHtml(baseName(report.gif || ""))}</p>
    <p class="muted">${escapeHtml(report.video_message || report.note || "")}</p>`;
}

function baseName(path) {
  const text = String(path || "");
  const parts = text.split(/[\\/]/);
  return parts[parts.length - 1] || text;
}

boot().catch(showError);
