const $ = (id) => document.getElementById(id);

const ui = {
  page: "model",
  part: null,
  sketchName: null,
  sketch: null,
  tool: "select",
  pick: "face",
  stage: "solid",
  tab: "inspect",
  state: null,
  mesh: null,
  selectedFaces: [],
  selectedEdges: [],
  sketchSelection: [],
  lineStart: null,
  rectStart: null,
  circleCenter: null,
  doc: "requirements.md",
  assembly: null,
  viewAssembly: null,
  assemblySignature: "",
};

async function command(name, args = {}) {
  const response = await fetch("/api/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, arguments: args, source: "ui" }),
  });
  const body = await response.json();
  if (!body.ok) throw new Error(body.error || "Command failed");
  return body.result;
}

function fileUrl(path) {
  if (!path) return "";
  return "/api/file?path=" + encodeURIComponent(path);
}

async function refresh() {
  const response = await fetch("/api/state");
  const body = await response.json();
  if (!body.ok) throw new Error(body.error);
  ui.state = body.result;
  if (!ui.part && ui.state.parts.length) ui.part = ui.state.parts[0].name;
  if (ui.sketchName) {
    const found = (ui.state.sketches || []).find((item) => item.name === ui.sketchName);
    if (found) ui.sketch = found;
  }
  renderTree();
  renderPanel();
  renderDocs();
  renderAssemblies();
  renderDimensions();
  $("undo").disabled = !ui.state.can_undo;
  $("redo").disabled = !ui.state.can_redo;
  if (ui.page === "model" && ui.stage === "solid" && (ui.viewAssembly || ui.part)) await loadMesh();
  else if (ui.page === "model" && ui.stage === "sketch") drawSketch();
}

async function loadMesh() {
  if (!ui.viewAssembly && !ui.part) {
    ui.mesh = null;
    drawSolid();
    return;
  }
  const query = ui.viewAssembly
    ? "assembly=" + encodeURIComponent(ui.viewAssembly)
    : "part=" + encodeURIComponent(ui.part);
  const response = await fetch("/api/mesh?" + query);
  const body = await response.json();
  if (!body.ok) {
    ui.mesh = null;
    $("empty").hidden = false;
    $("empty").textContent = body.error;
    return;
  }
  const firstLook = !ui.mesh || ui.mesh.part !== body.result.part;
  ui.mesh = body.result;
  $("empty").hidden = true;
  if (firstLook || ui.viewAssembly) view.fit();
  drawSolid();
}

function renderTree() {
  const state = ui.state;
  $("part-list").innerHTML = state.parts
    .map((part) => {
      const size = part.bounding_box.size;
      return `<li data-part="${part.name}" class="${part.name === ui.part ? "on" : ""}">
        <span>${part.name}</span><span class="meta">${size.x.toFixed(0)}×${size.y.toFixed(0)}×${size.z.toFixed(0)}</span></li>`;
    })
    .join("") || `<li class="meta">No parts yet</li>`;
  $("sketch-list").innerHTML = state.sketches
    .map((sketch) => `<li data-sketch="${sketch.name}" class="${sketch.name === ui.sketchName ? "on" : ""}">
      <span>${sketch.name}</span><span class="meta">${sketch.status.replaceAll("_", " ")}</span></li>`)
    .join("") || `<li class="meta">No sketches yet</li>`;
  const features = state.panels.features;
  const stale = state.panel_revision.features !== state.revision;
  if (!features || features.part && false) return;
  const steps = features && features.steps ? features.steps : [];
  $("feature-list").innerHTML = steps.length
    ? steps.map((step) => `<li><span>${step.name}</span>${step.redundant ? `<span class="flag">buried</span>` : `<span class="meta">${step.op}</span>`}</li>`).join("")
        + (stale ? `<li class="meta">Feature list is from an older revision.</li>` : "")
    : `<li class="meta">${features && features.tracked === false ? "Not tracked" : "Run Inspect or Features"}</li>`;
  $("part-list").querySelectorAll("[data-part]").forEach((node) => {
    node.onclick = async () => {
      ui.viewAssembly = null;
      ui.part = node.dataset.part;
      ui.stage = "solid";
      setStage();
      await refresh();
    };
  });
  $("sketch-list").querySelectorAll("[data-sketch]").forEach((node) => {
    node.onclick = () => openSketch(node.dataset.sketch);
  });
}

function renderPanel() {
  const root = $("panel");
  const state = ui.state;
  const part = ui.part;
  if (ui.tab === "inspect") {
    const report = state.panels.inspect;
    const stale = state.panel_revision.inspect !== state.revision;
    root.innerHTML = `
      <h3>Inspect ${part || ""}</h3>
      <div class="row"><label>Min wall <input id="min-wall" type="number" value="1.2" step="0.1" /></label>
      <button type="button" id="run-inspect" class="primary">Inspect</button></div>
      ${report ? inspectHtml(report, stale) : `<p class="muted">Measure the part, estimate wall thickness, and show the pictures.</p>`}`;
    $("run-inspect").onclick = () => runInspect();
    return;
  }
  if (ui.tab === "section") {
    const report = state.panels.section;
    root.innerHTML = `
      <h3>Section</h3>
      <div class="row">
        <select id="sec-axis"><option>x</option><option selected>y</option><option>z</option></select>
        <input id="sec-offset" type="number" value="0" step="1" />
        <button type="button" id="run-section" class="primary">Cut</button>
      </div>
      ${report ? `<p>Area ${report.area_mm2} mm². Thickness ${report.min_thickness_mm ?? "n/a"} mm.</p>
        ${report.view ? `<img alt="section" src="${fileUrl(report.view.path)}" />` : ""}
        <p class="muted">${report.note || ""}</p>` : `<p class="muted">A cut through the solid. Use it to see a rib inside a boss.</p>`}`;
    $("run-section").onclick = () => runSection();
    return;
  }
  if (ui.tab === "features") {
    const report = state.panels.features;
    root.innerHTML = `
      <h3>Feature contribution</h3>
      <div class="row"><button type="button" id="run-features" class="primary">Refresh</button></div>
      ${report ? featureHtml(report) : `<p class="muted">Flags an add that was already mostly inside the part.</p>`}`;
    $("run-features").onclick = async () => {
      if (!ui.part) return;
      await command("feature_report", { name: ui.part });
      await refresh();
    };
    return;
  }
  if (ui.tab === "fea") {
    const report = state.panels.fea;
    const materials = (state.materials.materials || []).map((item) => `<option value="${item.id}">${item.name}</option>`).join("");
    root.innerHTML = `
      <h3>Linear static</h3>
      <div class="row"><select id="fea-material">${materials}</select>
        <label>mesh <input id="fea-mesh" type="number" value="1.5" step="0.1" style="width:64px" /></label>
        <label>SF <input id="fea-sf" type="number" value="2" step="0.5" style="width:56px" /></label></div>
      <div class="row">
        <button type="button" id="fea-fix">Fix selection</button>
        <button type="button" id="fea-load">Load selection</button>
        <button type="button" id="fea-run" class="primary">Solve</button>
      </div>
      <div id="fea-setup" class="muted"></div>
      ${report ? feaHtml(report, state.panel_revision.fea !== state.revision) : `<p class="muted">Hold faces, apply a force, and read the safety factor. A selected face is a ref the solver accepts.</p>`}`;
    $("fea-fix").onclick = () => rememberLoad("fix");
    $("fea-load").onclick = () => rememberLoad("force");
    $("fea-run").onclick = () => runFea();
    renderFeaSetup();
    return;
  }
  if (ui.tab === "vibration") {
    const report = state.panels.vibration;
    const materials = (state.materials.materials || []).map((item) => `<option value="${item.id}" ${item.id === "steel" ? "selected" : ""}>${item.name}</option>`).join("");
    root.innerHTML = `
      <h3>Modes</h3>
      <div class="row"><select id="modal-material">${materials}</select>
        <label>mesh <input id="modal-mesh" type="number" value="4" step="0.5" style="width:64px" /></label>
        <label>modes <input id="modal-count" type="number" value="4" step="1" style="width:48px" /></label></div>
      <div class="row">
        <select id="modal-kind"><option value="drill">drill</option><option value="motor">motor</option><option value="spindle">spindle</option><option value="engine">engine</option><option value="pump">pump</option></select>
        <label>rpm <input id="modal-rpm" type="number" value="120" step="10" style="width:72px" /></label>
      </div>
      <div class="row">
        <button type="button" id="modal-fix">Fix selection</button>
        <button type="button" id="modal-run" class="primary">Solve</button>
      </div>
      <div id="modal-setup" class="muted"></div>
      ${report && report.part ? modalHtml(report) : `<p class="muted">Hold a face, name the running speed, and read which modes sit near that excitation. The picture is a magnified shape.</p>`}`;
    $("modal-fix").onclick = () => rememberModalFix();
    $("modal-run").onclick = () => runModal().catch(showError);
    renderModalSetup();
    return;
  }
  if (ui.tab === "bolts") {
    const report = state.panels.bolts;
    root.innerHTML = `
      <h3>Bolted joint</h3>
      <div class="row">
        <label>size <input id="bolt-size" value="M8" style="width:72px" /></label>
        <select id="bolt-grade"><option>8.8</option><option>10.9</option><option>12.9</option><option>a2-70</option></select>
        <select id="bolt-condition"><option value="dry">dry</option><option value="zinc">zinc</option><option value="lubricated">lubricated</option><option value="threadlocker">threadlocker</option></select>
      </div>
      <div class="row">
        <label>axial N <input id="bolt-axial" type="number" value="1000" style="width:72px" /></label>
        <label>shear N <input id="bolt-shear" type="number" value="2500" style="width:72px" /></label>
        <label>grip <input id="bolt-grip" type="number" value="20" style="width:56px" /></label>
      </div>
      <div class="row">
        <label>μ <input id="bolt-friction" type="number" value="0.14" step="0.01" style="width:64px" /></label>
        <select id="bolt-vibration"><option>mild</option><option>moderate</option><option selected>intense</option></select>
        <button type="button" id="bolt-run" class="primary">Check</button>
      </div>
      ${report ? boltHtml(report) : `<p class="muted">Slip under a sideways load is what lets a nut walk. The rating says so before the bolt yields.</p>`}`;
    $("bolt-run").onclick = () => runBolts().catch(showError);
    return;
  }
  if (ui.tab === "print") {
    const report = state.panels.print;
    root.innerHTML = `
      <h3>Print</h3>
      <div class="row">
        <select id="print-orientation">
          <option value="best">best</option>
          <option value="z_up">z up</option>
          <option value="z_down">z down</option>
          <option value="x_up">x up</option>
          <option value="x_down">x down</option>
          <option value="y_up">y up</option>
          <option value="y_down">y down</option>
        </select>
        <label>overhang ° <input id="print-threshold" type="number" value="45" style="width:56px" /></label>
      </div>
      <div class="row">
        <select id="print-printer">
          <option value="">any bed</option>
          <option value="bambu_a1">Bambu A1</option>
          <option value="bambu_a1_mini">A1 mini</option>
          <option value="bambu_p1">Bambu P1</option>
          <option value="bambu_x1">Bambu X1</option>
          <option value="prusa_mk4">Prusa MK4</option>
          <option value="ender_3">Ender 3</option>
        </select>
        <label><input id="print-slice" type="checkbox" /> slice</label>
        <button type="button" id="print-run" class="primary">Analyze</button>
      </div>
      ${report && report.part === ui.part ? printHtml(report) : `<p class="muted">Colors show how far each face leans from vertical. A face past the overhang angle needs support, unless it is the face on the bed.</p>`}`;
    $("print-run").onclick = () => runPrint().catch(showError);
    return;
  }
  const checks = state.requirements || [];
  root.innerHTML = `
    <h3>Requirements</h3>
    <div class="row"><button type="button" id="checks-run" class="primary">Check now</button></div>
    ${checks.map(checkHtml).join("") || `<p class="muted">Add a max mass, a build volume, or a safety factor on the Documents page.</p>`}`;
  $("checks-run").onclick = () => runChecks();
}

function inspectHtml(report, stale) {
  const m = report.measurement || {};
  const wall = report.wall || {};
  const box = m.bounding_box && m.bounding_box.size;
  const contact = (report.views || []).find((view) => view.name === "contact");
  return `<div class="kv">
      <span>Volume</span><b>${m.volume_mm3} mm³</b>
      <span>Size</span><b>${box ? `${box.x} × ${box.y} × ${box.z}` : ""}</b>
      <span>Watertight</span><b>${m.watertight ? "yes" : "no"}</b>
      <span>Wall</span><b>${wall.estimated_min_mm ?? "n/a"} mm ${wall.passed === false ? "(under the rule)" : ""}</b>
    </div>
    ${stale ? `<p class="warn">This inspection is from an older revision.</p>` : ""}
    <p class="muted">${wall.note || ""}</p>
    ${contact ? `<img alt="contact sheet" src="${fileUrl(contact.path)}" />` : ""}`;
}

function featureHtml(report) {
  if (!report.tracked) return `<p>${report.note}</p>`;
  const rows = (report.steps || []).map((step) => `<li><span>${step.name} · ${step.op}</span>${step.redundant ? `<span class="flag">flagged</span>` : ""}</li>`).join("");
  return `<p>${report.note}</p><ul>${rows}</ul>${report.warning ? `<p class="warn">${report.warning}</p>` : ""}`;
}

function feaHtml(report, stale) {
  const contact = (report.views || []).find((view) => view.name === "stress_contact");
  const review = report.review || {};
  return `<div class="kv">
      <span>Status</span><b>${report.status}</b>
      <span>von Mises</span><b>${report.max_von_mises_mpa ?? "n/a"} MPa</b>
      <span>Safety factor</span><b>${report.min_safety_factor ?? "n/a"}</b>
      <span>Displacement</span><b>${report.max_displacement_mm ?? "n/a"} mm</b>
      <span>Review</span><b><span class="chip ${review.status || "unchecked"}">${review.status || "n/a"}</span></b>
    </div>
    ${stale ? `<p class="warn">This solve is from an older revision.</p>` : ""}
    ${(report.warnings || []).map((line) => `<p class="warn">${line}</p>`).join("")}
    <p>${report.note || ""}</p>
    ${(report.assumptions || []).map((line) => `<p class="muted">${line}</p>`).join("")}
    ${contact ? `<img alt="von Mises" src="${fileUrl(contact.path)}" />` : ""}`;
}

function checkHtml(item) {
  return `<div class="row" style="align-items:flex-start"><span class="chip ${item.status}">${item.status}</span>
    <div><strong>${item.title}</strong><div class="muted">${item.detail || ""}</div></div></div>`;
}

const feaPlan = { fixes: [], loads: [] };
const modalPlan = { fixes: [] };

function rememberModalFix() {
  const ref = ui.selectedFaces[0];
  if (!ref) return alert("Select a face in the viewport first.");
  modalPlan.fixes.push({ name: "fix_" + (modalPlan.fixes.length + 1), select: { ref } });
  renderModalSetup();
}

function renderModalSetup() {
  const node = $("modal-setup");
  if (!node) return;
  node.textContent = modalPlan.fixes.map((item) => `fix ${item.select.ref}`).join("\n") || "No support yet.";
}

async function runModal() {
  if (!ui.part) return;
  if (!modalPlan.fixes.length) return alert("Fix a face from the selection.");
  const kind = $("modal-kind").value;
  await command("solve_modal", {
    name: ui.part,
    setup: {
      material: $("modal-material").value,
      mesh_size_mm: Number($("modal-mesh").value),
      modes: Number($("modal-count").value),
      render_modes: 1,
      fixes: modalPlan.fixes,
      excitations: [{ name: kind, kind, rpm: Number($("modal-rpm").value) }],
    },
  });
  await refresh();
}

async function runPrint() {
  if (!ui.part) return;
  const printer = $("print-printer").value;
  await command("check_print", {
    name: ui.part,
    setup: {
      orientation: $("print-orientation").value,
      threshold_deg: Number($("print-threshold").value),
      printer: printer || undefined,
      slice: $("print-slice").checked,
    },
  });
  await refresh();
}

function printHtml(report) {
  const contact = (report.views || []).find((view) => String(view.name).endsWith("_contact")) || (report.views || [])[0];
  const printers = (report.printers || []).map((row) =>
    `<li>${escapeHtml(row.name)}: ${row.fits ? "fits" : "does not fit"}</li>`
  ).join("");
  const slicer = report.slicer || {};
  const sliceLine = slicer.status === "sliced"
    ? `${escapeHtml(slicer.time || "")}, ${slicer.grams} g${slicer.cost != null ? `, cost ${slicer.cost}` : ""}`
    : escapeHtml(slicer.note || slicer.status || "");
  return `<div class="kv">
      <span>Status</span><b>${report.status}</b>
      <span>Orientation</span><b>${escapeHtml(report.orientation || "")}</b>
      <span>Support</span><b>${report.support_area_mm2} mm²</b>
      <span>Height</span><b>${report.height_mm} mm</b>
    </div>
    <p>${escapeHtml(report.note || "")}</p>
    <h3>Printers</h3><ul>${printers}</ul>
    <h3>Slicer</h3><p>${sliceLine}</p>
    ${contact ? `<img alt="overhang map" src="${fileUrl(contact.path)}" />` : ""}`;
}

async function runBolts() {
  await command("check_bolts", {
    spec: {
      size: $("bolt-size").value,
      grade: $("bolt-grade").value,
      condition: $("bolt-condition").value,
      axial_n: Number($("bolt-axial").value),
      shear_n: Number($("bolt-shear").value),
      grip_mm: Number($("bolt-grip").value),
      friction: Number($("bolt-friction").value),
      vibration: $("bolt-vibration").value,
      part: ui.part || undefined,
    },
  });
  await refresh();
}

function modalHtml(report) {
  const contact = (report.views || []).find((view) => String(view.name).endsWith("_contact")) || (report.views || [])[0];
  const rows = (report.modes || []).map((mode) => `<li>mode ${mode.index}: ${mode.frequency_hz} Hz${mode.rigid_body ? " (rigid body)" : ""}</li>`).join("");
  const flags = ((report.resonance && report.resonance.flags) || []).map((flag) =>
    `<li>mode ${flag.mode} near ${escapeHtml(flag.source)} harmonic ${flag.harmonic} (${flag.excitation_hz} Hz)</li>`
  ).join("");
  const suggestions = ((report.resonance && report.resonance.suggestions) || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  const assumptions = (report.assumptions || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  return `<div class="kv">
      <span>Status</span><b>${report.resonance ? report.resonance.status : ""}</b>
      <span>First flexible</span><b>${(report.modes || []).filter((mode) => !mode.rigid_body).map((mode) => mode.frequency_hz)[0] ?? "n/a"} Hz</b>
    </div>
    <p>${escapeHtml(report.note || "")}</p>
    <ul>${rows}</ul>
    ${flags ? `<h3>Resonance</h3><ul>${flags}</ul>` : ""}
    ${suggestions ? `<h3>Suggestions</h3><ul>${suggestions}</ul>` : ""}
    <h3>Assumptions</h3><ul>${assumptions}</ul>
    ${contact ? `<img alt="mode shape" src="${fileUrl(contact.path)}" />` : ""}`;
}

function boltHtml(report) {
  const row = report.bolts ? report.bolts[0] : report;
  const loosening = row.loosening || {};
  const counters = (loosening.countermeasures || []).filter((item) => item.recommended).map((item) =>
    `<li><strong>${escapeHtml(item.name)}</strong> — ${escapeHtml(item.effect)}</li>`
  ).join("");
  const assumptions = (report.assumptions || row.assumptions || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  return `<div class="kv">
      <span>Status</span><b>${row.status || report.worst || ""}</b>
      <span>Loosening</span><b>${loosening.rating || report.worst || ""}</b>
      <span>Preload</span><b>${row.preload_n} N</b>
      <span>Torque</span><b>${row.tightening_torque_nm} N·m</b>
      <span>Slip margin</span><b>${row.slip_margin ?? "n/a"} (${row.slip_status || ""})</b>
      <span>Yield margin</span><b>${row.yield_margin ?? "n/a"}</b>
    </div>
    <p>${escapeHtml(loosening.summary || report.note || "")}</p>
    ${(loosening.reasons || []).map((line) => `<p class="warn">${escapeHtml(line)}</p>`).join("")}
    ${counters ? `<h3>Countermeasures</h3><ul>${counters}</ul>` : ""}
    <h3>Assumptions</h3><ul>${assumptions}</ul>`;
}

function rememberLoad(kind) {
  const ref = ui.selectedFaces[0];
  if (!ref) return alert("Select a face in the viewport first.");
  if (kind === "fix") feaPlan.fixes.push({ name: "fix_" + (feaPlan.fixes.length + 1), select: { ref } });
  else feaPlan.loads.push({ name: "load_" + (feaPlan.loads.length + 1), type: "force", vector: [0, 0, -10], select: { ref } });
  renderFeaSetup();
}

function renderFeaSetup() {
  const node = $("fea-setup");
  if (!node) return;
  const lines = [
    ...feaPlan.fixes.map((item) => `fix ${item.select.ref}`),
    ...feaPlan.loads.map((item) => `force ${item.vector.join(",")} on ${item.select.ref}`),
  ];
  node.textContent = lines.join("\n") || "No supports or loads yet.";
}

async function runInspect() {
  if (!ui.part) return;
  await command("inspect_part", { name: ui.part, min_wall_mm: Number($("min-wall").value) });
  await command("feature_report", { name: ui.part });
  await refresh();
}

async function runSection() {
  if (!ui.part) return;
  await command("section_part", { name: ui.part, axis: $("sec-axis").value, offset: Number($("sec-offset").value) });
  await refresh();
}

async function runFea() {
  if (!ui.part) return;
  if (!feaPlan.fixes.length || !feaPlan.loads.length) return alert("Add a fixed face and a load from the selection.");
  await command("solve_mechanics", {
    name: ui.part,
    setup: {
      material: $("fea-material").value,
      mesh_size_mm: Number($("fea-mesh").value),
      target_safety_factor: Number($("fea-sf").value),
      fixes: feaPlan.fixes,
      loads: feaPlan.loads,
    },
  });
  await refresh();
}

async function runChecks() {
  await command("check_requirements", { run: true });
  await refresh();
}

function setStage() {
  const sketching = ui.stage === "sketch";
  $("view").hidden = sketching;
  $("sketch-canvas").hidden = !sketching;
  $("sketch-tools").hidden = !sketching;
  $("sketch-status").hidden = !sketching;
  $("empty").hidden = sketching || !!ui.mesh;
  document.querySelectorAll("#stage-mode button").forEach((button) => {
    button.classList.toggle("on", button.dataset.stage === ui.stage);
  });
}

async function openSketch(name) {
  ui.sketchName = name;
  ui.stage = "sketch";
  const report = await command("get_sketch", { name });
  ui.sketch = report;
  setStage();
  drawSketch();
  renderTree();
}

async function commitSketch(spec) {
  const report = await command("update_sketch", { name: ui.sketchName, spec });
  ui.sketch = report;
  ui.sketch.spec = spec;
  await refresh();
  drawSketch();
}

function drawSketch() {
  const canvas = $("sketch-canvas");
  if (canvas.hidden || !ui.sketch) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  const geometry = ui.sketch.geometry || { points: {}, lines: {}, circles: {}, arcs: {} };
  const points = Object.values(geometry.points || {});
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const visit = (x, y) => {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  };
  points.forEach((point) => visit(point.x, point.y));
  Object.values(geometry.circles || {}).forEach((circle) => {
    visit(circle.center.x - circle.radius, circle.center.y - circle.radius);
    visit(circle.center.x + circle.radius, circle.center.y + circle.radius);
  });
  if (!Number.isFinite(minX)) { minX = 0; maxX = 0; minY = 0; maxY = 0; }
  let span = Math.max(maxX - minX, maxY - minY);
  if (span < 40) {
    const midX = (minX + maxX) / 2;
    const midY = (minY + maxY) / 2;
    minX = midX - 50;
    maxX = midX + 50;
    minY = midY - 50;
    maxY = midY + 50;
    span = 100;
  }
  const pad = 24;
  const scale = Math.min((rect.width - pad * 2) / span, (rect.height - pad * 2) / span);
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  canvas._map = { scale, cx, cy, width: rect.width, height: rect.height };
  const toScreen = (x, y) => [rect.width / 2 + (x - cx) * scale, rect.height / 2 - (y - cy) * scale];
  canvas._toScreen = toScreen;
  ctx.strokeStyle = "#e4ddd0";
  ctx.lineWidth = 1;
  for (let x = Math.floor(minX / 10) * 10; x <= maxX; x += 10) {
    const [sx] = toScreen(x, 0);
    ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, rect.height); ctx.stroke();
  }
  const free = new Set((ui.sketch.free || []).map((item) => item.entity));
  const selected = new Set(ui.sketchSelection);
  ctx.lineWidth = 1.6;
  Object.entries(geometry.lines || {}).forEach(([id, line]) => {
    const [x0, y0] = toScreen(line.start.x, line.start.y);
    const [x1, y1] = toScreen(line.end.x, line.end.y);
    ctx.strokeStyle = selected.has(id) ? "#8d4e22" : "#1d1a16";
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
  });
  Object.entries(geometry.circles || {}).forEach(([id, circle]) => {
    const [sx, sy] = toScreen(circle.center.x, circle.center.y);
    ctx.strokeStyle = selected.has(id) ? "#8d4e22" : "#1d1a16";
    ctx.beginPath(); ctx.arc(sx, sy, circle.radius * scale, 0, Math.PI * 2); ctx.stroke();
  });
  Object.entries(geometry.points || {}).forEach(([id, point]) => {
    const [sx, sy] = toScreen(point.x, point.y);
    ctx.fillStyle = selected.has(id) ? "#8d4e22" : free.has(id) ? "#2a5f8a" : "#1d1a16";
    ctx.beginPath(); ctx.arc(sx, sy, 3.5, 0, Math.PI * 2); ctx.fill();
  });
  ctx.fillStyle = "#6d655b";
  ctx.font = "12px ui-sans-serif, sans-serif";
  (ui.sketch.dimensions || []).forEach((dimension, index) => {
    ctx.fillText(`${dimension.name || dimension.kind} ${dimension.text || dimension.value}`, 12, 28 + index * 16);
  });
  const status = $("sketch-status");
  status.hidden = false;
  status.className = "sketch-status " + (ui.sketch.status || "");
  status.textContent = `${ui.sketch.status || "unsolved"} · dof ${ui.sketch.dof ?? "?"} · ${(ui.sketch.conflicts || []).join(", ") || ui.sketch.note || ""}`;
  renderDimensions();
}

function renderDimensions() {
  const root = $("dimension-list");
  if (!root) return;
  const dimensions = (ui.sketch && ui.sketch.dimensions) || [];
  root.innerHTML = dimensions
    .filter((dimension) => dimension.name)
    .map((dimension) => `<button type="button" data-dimension="${dimension.name}" data-value="${dimension.value}">${dimension.name} ${dimension.value}</button>`)
    .join("");
  root.querySelectorAll("button").forEach((button) => {
    button.onclick = () => editDimension(button.dataset.dimension, button.dataset.value).catch(showError);
  });
}

async function editDimension(name, value) {
  if (!ui.sketchName) return;
  const answer = await ask(`Edit ${name}`, [{ name: "value", label: "New value", value: String(value) }]);
  if (!answer) return;
  await command("set_dimension", { sketch: ui.sketchName, dimension: name, value: Number(answer.value) });
  await refresh();
  if (ui.stage === "sketch") drawSketch();
}

function screenToSketch(event) {
  const canvas = $("sketch-canvas");
  const rect = canvas.getBoundingClientRect();
  const map = canvas._map;
  if (!map) return [0, 0];
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  return [map.cx + (x - map.width / 2) / map.scale, map.cy - (y - map.height / 2) / map.scale];
}

function nearestSketchEntity(x, y) {
  const geometry = ui.sketch.geometry || {};
  let best = null;
  let bestD = 8;
  Object.entries(geometry.points || {}).forEach(([id, point]) => {
    const d = Math.hypot(point.x - x, point.y - y);
    if (d < bestD) { best = id; bestD = d; }
  });
  return best;
}

function freshId(prefix, spec) {
  const used = new Set(spec.entities.map((entity) => entity.id));
  let index = 0;
  while (used.has(prefix + index)) index += 1;
  return prefix + index;
}

async function onSketchClick(event) {
  if (!ui.sketch) return;
  const [x, y] = screenToSketch(event);
  const spec = structuredClone(ui.sketch.spec || { plane: "xy", entities: [], constraints: [] });
  if (ui.tool === "select") {
    const hit = nearestSketchEntity(x, y);
    ui.sketchSelection = hit ? [hit] : [];
    drawSketch();
    return;
  }
  if (ui.tool === "point") {
    const id = freshId("p", spec);
    spec.entities.push({ id, type: "point", at: [round(x), round(y)] });
    await commitSketch(spec);
    return;
  }
  if (ui.tool === "line") {
    const hit = nearestSketchEntity(x, y);
    const id = hit || freshId("p", spec);
    if (!hit) spec.entities.push({ id, type: "point", at: [round(x), round(y)] });
    if (!ui.lineStart) {
      ui.lineStart = id;
      ui.sketch.spec = spec;
      return;
    }
    const line = freshId("l", spec);
    spec.entities.push({ id: line, type: "line", start: ui.lineStart, end: id });
    ui.lineStart = null;
    await commitSketch(spec);
    return;
  }
  if (ui.tool === "circle") {
    if (!ui.circleCenter) {
      const id = nearestSketchEntity(x, y) || freshId("p", spec);
      if (!spec.entities.some((entity) => entity.id === id)) spec.entities.push({ id, type: "point", at: [round(x), round(y)] });
      ui.circleCenter = id;
      ui.sketch.spec = spec;
      return;
    }
    const center = spec.entities.find((entity) => entity.id === ui.circleCenter);
    const at = center.at || [0, 0];
    const radius = Math.max(0.5, Math.hypot(x - at[0], y - at[1]));
    const id = freshId("c", spec);
    spec.entities.push({ id, type: "circle", center: ui.circleCenter, radius });
    spec.constraints.push({ type: "diameter", entity: id, value: round(radius * 2), name: id + "_diameter" });
    ui.circleCenter = null;
    await commitSketch(spec);
    return;
  }
  if (ui.tool === "rectangle") {
    if (!ui.rectStart) { ui.rectStart = [x, y]; return; }
    const [x0, y0] = ui.rectStart;
    ui.rectStart = null;
    await commitSketch(rectangleSpec(x0, y0, x, y));
  }
}

function rectangleSpec(x0, y0, x1, y1) {
  const width = Math.max(0.5, Math.abs(x1 - x0));
  const height = Math.max(0.5, Math.abs(y1 - y0));
  return {
    plane: "xy",
    entities: [
      { id: "p0", type: "point", at: [round(x0), round(y0)] },
      { id: "p1", type: "point", at: [round(x1), round(y0)] },
      { id: "p2", type: "point", at: [round(x1), round(y1)] },
      { id: "p3", type: "point", at: [round(x0), round(y1)] },
      { id: "bottom", type: "line", start: "p0", end: "p1" },
      { id: "right", type: "line", start: "p1", end: "p2" },
      { id: "top", type: "line", start: "p2", end: "p3" },
      { id: "left", type: "line", start: "p3", end: "p0" },
    ],
    constraints: [
      { type: "fix", entity: "p0" },
      { type: "horizontal", entity: "bottom" },
      { type: "horizontal", entity: "top" },
      { type: "vertical", entity: "right" },
      { type: "vertical", entity: "left" },
      { type: "distance", a: "p0", b: "p1", value: round(width), name: "width" },
      { type: "distance", a: "p0", b: "p3", value: round(height), name: "height" },
    ],
  };
}

function round(value) { return Math.round(value * 1000) / 1000; }

function requiredNumber(text, message) {
  const value = Number(text);
  if (!text || !Number.isFinite(value)) throw new Error(message);
  return value;
}

async function applyConstraint(kind) {
  if (!ui.sketch) return;
  const spec = structuredClone(ui.sketch.spec);
  const selected = ui.sketchSelection;
  if (kind === "distance") {
    const value = await ask("Distance", [{ name: "value", label: "Millimeters", value: "10" }, { name: "name", label: "Name", value: "distance" }]);
    if (!value || selected.length < 2) return;
    spec.constraints.push({ type: "distance", a: selected[0], b: selected[1], value: Number(value.value), name: value.name });
  } else if (kind === "diameter") {
    const value = await ask("Diameter", [{ name: "value", label: "Millimeters", value: "10" }, { name: "name", label: "Name", value: "diameter" }]);
    if (!value || !selected.length) return;
    spec.constraints.push({ type: "diameter", entity: selected[0], value: Number(value.value), name: value.name });
  } else if (kind === "coincident") {
    if (selected.length < 2) return;
    spec.constraints.push({ type: "coincident", a: selected[0], b: selected[1] });
  } else if (kind === "fix" || kind === "horizontal" || kind === "vertical") {
    if (!selected.length) return;
    spec.constraints.push({ type: kind, entity: selected[0] });
  }
  await commitSketch(spec);
}

async function buildSolid() {
  if (!ui.sketchName || !ui.sketch) return;
  const spec = ui.sketch.spec;
  const height = Number($("extrude-height").value);
  const op = $("extrude-op").value;
  const name = $("extrude-name").value.trim() || "part";
  const circles = (spec.entities || []).filter((entity) => entity.type === "circle").map((entity) => entity.id);
  let step;
  if (circles.length && op !== "revolve") {
    step = { op: op === "cut" ? "cut" : "extrude", circle: circles[0], height, name: op };
  } else {
    const points = ["p0", "p1", "p2", "p3"].filter((id) => (spec.entities || []).some((entity) => entity.id === id));
    const loop = points.length >= 3 ? points : loopPoints(spec);
    if (op === "revolve") step = { op: "revolve", points: loop, axis: "y", name: "revolve" };
    else step = { op: op === "cut" ? "cut" : "extrude", points: loop, height, name: op };
  }
  const exists = (ui.state.parts || []).some((part) => part.name === name);
  if (exists) await command("update_part_from_sketch", { name, sketch: ui.sketchName, steps: [step] });
  else await command("create_part_from_sketch", { name, sketch: ui.sketchName, steps: [step] });
  ui.viewAssembly = null;
  ui.part = name;
  ui.stage = "solid";
  setStage();
  await refresh();
}

function loopPoints(spec) {
  const lines = (spec.entities || []).filter((entity) => entity.type === "line");
  if (!lines.length) return [];
  const chain = [lines[0].start, lines[0].end];
  const used = new Set([lines[0].id]);
  let cursor = lines[0].end;
  while (used.size < lines.length) {
    const next = lines.find((line) => !used.has(line.id) && (line.start === cursor || line.end === cursor));
    if (!next) break;
    used.add(next.id);
    cursor = next.start === cursor ? next.end : next.start;
    if (cursor !== chain[0]) chain.push(cursor);
  }
  return chain;
}

const view = {
  yaw: 0.7,
  pitch: 0.55,
  distance: 80,
  target: [0, 0, 0],
  dragging: null,
  gl: null,
  program: null,
  edgeProgram: null,
};

function initView() {
  const canvas = $("view");
  const gl = canvas.getContext("webgl", { antialias: true });
  if (!gl) {
    $("empty").hidden = false;
    $("empty").textContent = "This browser has no WebGL, so the solid cannot be drawn.";
    return;
  }
  view.gl = gl;
  view.program = program(gl, SOLID_VS, SOLID_FS);
  view.edgeProgram = program(gl, EDGE_VS, EDGE_FS);
  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  window.addEventListener("resize", () => { if (ui.stage === "solid") drawSolid(); else drawSketch(); });
}

function program(gl, vs, fs) {
  const compiled = gl.createProgram();
  const vert = shader(gl, gl.VERTEX_SHADER, vs);
  const frag = shader(gl, gl.FRAGMENT_SHADER, fs);
  gl.attachShader(compiled, vert);
  gl.attachShader(compiled, frag);
  gl.linkProgram(compiled);
  return compiled;
}

function shader(gl, type, source) {
  const item = gl.createShader(type);
  gl.shaderSource(item, source);
  gl.compileShader(item);
  if (!gl.getShaderParameter(item, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(item) || "The viewport shader did not compile.");
  }
  return item;
}

function drawSolid() {
  const canvas = $("view");
  const gl = view.gl;
  if (!gl) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return;
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0.08, 0.09, 0.11, 1);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.enable(gl.DEPTH_TEST);
  if (!ui.mesh || !ui.mesh.positions.length) return;
  const mvp = cameraMatrix(canvas.width / canvas.height);
  gl.useProgram(view.program);
  bindAttr(gl, view.program, "aPos", new Float32Array(ui.mesh.positions), 3);
  bindAttr(gl, view.program, "aNormal", new Float32Array(ui.mesh.normals), 3);
  const vertexCount = ui.mesh.positions.length / 3;
  const colors = new Float32Array(vertexCount * 3);
  const source = ui.mesh.colors;
  if (source && source.length === ui.mesh.positions.length) {
    for (let i = 0; i < source.length; i += 1) colors[i] = source[i] / 255;
  } else {
    for (let i = 0; i < vertexCount; i += 1) {
      colors[i * 3] = 0.72;
      colors[i * 3 + 1] = 0.66;
      colors[i * 3 + 2] = 0.56;
    }
  }
  bindAttr(gl, view.program, "aColor", colors, 3);
  const selected = new Float32Array(ui.mesh.triangle_face.length * 3);
  ui.mesh.triangle_face.forEach((face, index) => {
    const on = ui.selectedFaces.includes(ui.mesh.faces[face].ref) ? 1 : 0;
    selected[index * 3] = on; selected[index * 3 + 1] = on; selected[index * 3 + 2] = on;
  });
  bindAttr(gl, view.program, "aSelect", selected, 1);
  gl.uniformMatrix4fv(gl.getUniformLocation(view.program, "uMvp"), false, mvp);
  gl.drawArrays(gl.TRIANGLES, 0, ui.mesh.positions.length / 3);
  gl.useProgram(view.edgeProgram);
  const edgePositions = [];
  const edgeSelect = [];
  ui.mesh.edges.forEach((edge) => {
    const on = ui.selectedEdges.includes(edge.ref) ? 1 : 0;
    for (let i = 1; i < edge.points.length; i += 1) {
      edgePositions.push(...edge.points[i - 1], ...edge.points[i]);
      edgeSelect.push(on, on);
    }
  });
  if (edgePositions.length) {
    bindAttr(gl, view.edgeProgram, "aPos", new Float32Array(edgePositions), 3);
    bindAttr(gl, view.edgeProgram, "aSelect", new Float32Array(edgeSelect), 1);
    gl.uniformMatrix4fv(gl.getUniformLocation(view.edgeProgram, "uMvp"), false, mvp);
    gl.drawArrays(gl.LINES, 0, edgePositions.length / 3);
  }
}

function bindAttr(gl, program, name, data, size) {
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
  const loc = gl.getAttribLocation(program, name);
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
}

function cameraMatrix(aspect) {
  const eye = eyePosition();
  const viewM = lookAt(eye, view.target, [0, 0, 1]);
  const proj = perspective(0.7, aspect, 0.1, view.distance * 20);
  return multiply(proj, viewM);
}

function eyePosition() {
  const cp = Math.cos(view.pitch);
  return [
    view.target[0] + view.distance * cp * Math.sin(view.yaw),
    view.target[1] - view.distance * cp * Math.cos(view.yaw),
    view.target[2] + view.distance * Math.sin(view.pitch),
  ];
}

view.fit = function fit() {
  if (!ui.mesh) return;
  const box = ui.mesh.bounding_box;
  view.target = [(box.min[0] + box.max[0]) / 2, (box.min[1] + box.max[1]) / 2, (box.min[2] + box.max[2]) / 2];
  const span = Math.hypot(box.max[0] - box.min[0], box.max[1] - box.min[1], box.max[2] - box.min[2]);
  view.distance = Math.max(span * 1.4, 10);
};

function onPointerDown(event) {
  view.dragging = { x: event.clientX, y: event.clientY, yaw: view.yaw, pitch: view.pitch, target: view.target.slice(), moved: false, button: event.button };
  event.target.setPointerCapture(event.pointerId);
}

function onPointerMove(event) {
  if (!view.dragging) return;
  const dx = event.clientX - view.dragging.x;
  const dy = event.clientY - view.dragging.y;
  if (Math.hypot(dx, dy) > 3) view.dragging.moved = true;
  if (view.dragging.button === 2 || event.shiftKey) {
    const eye = eyePosition();
    const forward = normalize(sub(view.target, eye));
    const right = normalize(cross(forward, [0, 0, 1]));
    const up = cross(right, forward);
    const scale = view.distance * 0.002;
    view.target = add(view.dragging.target, add(scaleV(right, -dx * scale), scaleV(up, dy * scale)));
  } else {
    view.yaw = view.dragging.yaw + dx * 0.008;
    view.pitch = Math.max(-1.2, Math.min(1.2, view.dragging.pitch + dy * 0.008));
  }
  drawSolid();
}

async function onPointerUp(event) {
  const drag = view.dragging;
  view.dragging = null;
  if (!drag || drag.moved || !ui.mesh) return;
  const rect = $("view").getBoundingClientRect();
  const hit = ui.pick === "face" ? pickFace(event.clientX - rect.left, event.clientY - rect.top, rect) : pickEdge(event.clientX - rect.left, event.clientY - rect.top, rect);
  if (!hit) {
    ui.selectedFaces = [];
    ui.selectedEdges = [];
  } else if (ui.pick === "face") {
    ui.selectedFaces = [hit];
    ui.selectedEdges = [];
  } else {
    ui.selectedEdges = [hit];
    ui.selectedFaces = [];
  }
  $("selection-readout").textContent = hit || "Nothing selected";
  drawSolid();
  if (ui.part) await command("set_selection", { part: ui.part, faces: ui.selectedFaces, edges: ui.selectedEdges });
}

function onWheel(event) {
  event.preventDefault();
  view.distance = Math.max(2, view.distance * (event.deltaY > 0 ? 1.08 : 0.92));
  drawSolid();
}

function pickFace(x, y, rect) {
  const origin = eyePosition();
  const dir = rayDirection(x, y, rect);
  let best = null;
  let bestT = Infinity;
  const pos = ui.mesh.positions;
  ui.mesh.triangle_face.forEach((face, index) => {
    const a = pos.slice(index * 9, index * 9 + 3);
    const b = pos.slice(index * 9 + 3, index * 9 + 6);
    const c = pos.slice(index * 9 + 6, index * 9 + 9);
    const t = intersect(origin, dir, a, b, c);
    if (t !== null && t < bestT) { bestT = t; best = ui.mesh.faces[face].ref; }
  });
  return best;
}

function pickEdge(x, y, rect) {
  let best = null;
  let bestD = 18;
  ui.mesh.edges.forEach((edge) => {
    edge.points.forEach((point) => {
      const screen = project(point, rect);
      if (!screen) return;
      const d = Math.hypot(screen[0] - x, screen[1] - y);
      if (d < bestD) { bestD = d; best = edge.ref; }
    });
  });
  return best;
}

function rayDirection(x, y, rect) {
  const ndcX = (x / rect.width) * 2 - 1;
  const ndcY = 1 - (y / rect.height) * 2;
  const inv = invert(cameraMatrix(rect.width / rect.height));
  const near = transformPoint(inv, [ndcX, ndcY, -1]);
  const far = transformPoint(inv, [ndcX, ndcY, 1]);
  return normalize(sub(far, near));
}

function project(point, rect) {
  const clip = transformPoint(cameraMatrix(rect.width / rect.height), point);
  if (clip[2] < -1 || clip[2] > 1) return null;
  return [(clip[0] * 0.5 + 0.5) * rect.width, (1 - (clip[1] * 0.5 + 0.5)) * rect.height];
}

function intersect(origin, dir, a, b, c) {
  const eps = 1e-8;
  const ab = sub(b, a);
  const ac = sub(c, a);
  const p = cross(dir, ac);
  const det = dot(ab, p);
  if (Math.abs(det) < eps) return null;
  const inv = 1 / det;
  const tvec = sub(origin, a);
  const u = dot(tvec, p) * inv;
  if (u < 0 || u > 1) return null;
  const q = cross(tvec, ab);
  const v = dot(dir, q) * inv;
  if (v < 0 || u + v > 1) return null;
  const t = dot(ac, q) * inv;
  return t > eps ? t : null;
}

function renderDocs() {
  if (!$("page-docs") || $("page-docs").hidden) return;
  const docs = ui.state.documents || [];
  $("doc-list").innerHTML = docs.map((name) => `<li data-doc="${name}" class="${name === ui.doc ? "on" : ""}">${name}</li>`).join("");
  $("doc-list").querySelectorAll("[data-doc]").forEach((node) => { node.onclick = () => loadDoc(node.dataset.doc); });
  const checks = ui.state.requirements || [];
  $("req-list").innerHTML = checks.length
    ? checks.map((item) => `<li><div><strong>${item.title}</strong><div class="muted">${item.detail || ""}</div></div><span class="chip ${item.status}">${item.status}</span></li>`).join("")
    : `<li class="meta">No requirements yet</li>`;
  if (ui.part) $("req-part").value = $("req-part").value || ui.part;
}

async function loadDoc(name) {
  ui.doc = name;
  const result = await command("read_document", { name });
  $("doc-name").textContent = name;
  $("doc-editor").value = result.markdown;
  $("doc-preview").innerHTML = markdown(result.markdown);
  renderDocs();
}

function markdown(text) {
  return text.split(/\n\n+/).map((block) => {
    if (block.startsWith("# ")) return `<h1>${escapeHtml(block.slice(2))}</h1>`;
    if (block.startsWith("## ")) return `<h2>${escapeHtml(block.slice(3))}</h2>`;
    if (block.startsWith("- ")) return `<ul>${block.split("\n").map((line) => `<li>${escapeHtml(line.replace(/^- /, ""))}</li>`).join("")}</ul>`;
    return `<p>${escapeHtml(block)}</p>`;
  }).join("");
}

function escapeHtml(text) {
  return text.replace(/[&<>]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[char]));
}

function ask(title, fields) {
  const dialog = $("modal");
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = `<div class="stack">${fields.map((field) => `<label>${field.label}<input name="${field.name}" value="${field.value || ""}" /></label>`).join("")}</div>`;
  dialog.showModal();
  return new Promise((resolve) => {
    const form = $("modal-form");
    const cancel = () => { dialog.close(); resolve(null); };
    $("modal-cancel").onclick = cancel;
    form.onsubmit = (event) => {
      event.preventDefault();
      const data = {};
      fields.forEach((field) => { data[field.name] = form.querySelector(`[name="${field.name}"]`).value; });
      dialog.close();
      resolve(data);
    };
  });
}

function renderAssemblies() {
  if (!$("assembly-list") || !ui.state) return;
  const assemblies = ui.state.assemblies || [];
  if (!ui.assembly && assemblies.length) ui.assembly = assemblies[0].name;
  if (ui.assembly && !assemblies.some((item) => item.name === ui.assembly)) ui.assembly = assemblies[0] ? assemblies[0].name : null;
  $("assembly-list").innerHTML = assemblies.map((item) =>
    `<li data-assembly="${item.name}" class="${item.name === ui.assembly ? "on" : ""}"><span>${item.name}</span><span class="meta">${item.joints.length} joints</span></li>`
  ).join("") || `<li class="meta">No assemblies yet</li>`;
  $("assembly-list").querySelectorAll("[data-assembly]").forEach((node) => {
    node.onclick = () => {
      ui.assembly = node.dataset.assembly;
      ui.assemblySignature = "";
      renderAssemblies();
    };
  });
  const current = assemblies.find((item) => item.name === ui.assembly);
  if (!current) {
    $("assembly-controls").innerHTML = `<p class="muted">Create an assembly with create_assembly, or run <code>python examples/rod_arm.py</code> and open that workspace.</p>`;
    return;
  }
  const signature = JSON.stringify(current);
  if (ui.assemblySignature !== signature) {
    ui.assemblySignature = signature;
    const joints = current.joints.map((joint) => `
      <div class="joint-row">
        <span style="width:92px">${joint.name}</span>
        <span class="meta" style="width:72px">${joint.type}</span>
        <input data-joint="${joint.name}" type="number" step="1" value="${joint.value}" ${joint.min != null ? `min="${joint.min}"` : ""} ${joint.max != null ? `max="${joint.max}"` : ""} />
        <button type="button" data-apply="${joint.name}">Apply</button>
      </div>`).join("");
    const poses = current.poses.map((pose) => `<button type="button" data-pose="${pose}">${pose}</button>`).join(" ");
    const motions = current.motions.map((motion) => `<button type="button" data-motion="${motion}">${motion}</button>`).join(" ");
    $("assembly-controls").innerHTML = `
      <h3>Joints</h3>
      ${joints || `<p class="muted">Grounded only.</p>`}
      <h3>Poses</h3>
      <div class="row">${poses || `<span class="muted">None</span>`}</div>
      <h3>Motion</h3>
      <div class="row">${motions || `<span class="muted">None</span>`}</div>
      <p class="muted">${current.instances.map((item) => item.name).join(", ")}</p>`;
    $("assembly-controls").querySelectorAll("[data-apply]").forEach((button) => {
      button.onclick = async () => {
        const input = $("assembly-controls").querySelector(`[data-joint="${button.dataset.apply}"]`);
        await command("set_joint", { name: ui.assembly, joint: button.dataset.apply, value: Number(input.value) });
        ui.assemblySignature = "";
        await refresh();
      };
    });
    $("assembly-controls").querySelectorAll("[data-pose]").forEach((button) => {
      button.onclick = async () => {
        await command("set_pose", { name: ui.assembly, pose: button.dataset.pose });
        ui.assemblySignature = "";
        await refresh();
        await showAssemblyPicture();
      };
    });
    $("assembly-controls").querySelectorAll("[data-motion]").forEach((button) => {
      button.onclick = async () => {
        $("assembly-status").textContent = "Running " + button.dataset.motion + "…";
        await command("run_motion", { name: ui.assembly, sequence: button.dataset.motion });
        ui.assemblySignature = "";
        await refresh();
        showMotionReport();
      };
    });
  }
  showStoredAssemblyReports();
}

function showStoredAssemblyReports() {
  const motion = ui.state.panels.motion;
  const cables = ui.state.panels.cables;
  const power = ui.state.panels.power;
  const vibration = ui.state.panels.vibration;
  const bolts = ui.state.panels.bolts;
  if (motion && motion.assembly === ui.assembly && motion.views && motion.views.length) showMotionReport();
  else if (cables && cables.assembly === ui.assembly) showCableReport(cables);
  else if (power && power.assembly === ui.assembly) showPowerReport(power);
  else if (vibration && vibration.assembly === ui.assembly) showModalReport(vibration);
  else if (bolts && bolts.assembly === ui.assembly) showBoltReport(bolts);
}

function showMotionReport() {
  const motion = ui.state.panels.motion;
  if (!motion || motion.assembly !== ui.assembly) return;
  const strip = (motion.views || []).find((view) => view.name === "strip") || (motion.views || [])[0];
  const picture = $("assembly-picture");
  if (strip) {
    picture.hidden = false;
    picture.src = fileUrl(strip.path);
  }
  const hits = motion.collision_count != null ? `${motion.status || ""} · ${motion.collision_count} collisions` : "preview";
  $("assembly-status").textContent = hits;
  const frames = (motion.frames || []).map((frame) => {
    const pairs = (frame.collisions || []).map((hit) => `${hit.a} × ${hit.b}`).join(", ");
    return `<li>t=${Number(frame.time_s).toFixed(2)}s ${pairs || "clear"}</li>`;
  }).join("");
  $("assembly-report").innerHTML = `<h3>${escapeHtml(motion.motion || motion.name || "Assembly")}</h3><ul>${frames}</ul>`;
}

function showCableReport(report) {
  $("assembly-status").textContent = "Cables " + report.status;
  const rows = (report.cables || []).map((cable) =>
    `<li><strong>${escapeHtml(cable.name)}</strong> ${cable.status} · length ${cable.length_mm.map((value) => Number(value).toFixed(1)).join(" → ")} mm · strain ${cable.max_strain} · bend ${cable.min_bend_radius_mm == null ? "straight" : cable.min_bend_radius_mm + " mm"} · pinches ${cable.pinch_count}</li>`
  ).join("");
  const assumptions = (report.assumptions || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  $("assembly-report").innerHTML = `<h3>Cables</h3><ul>${rows}</ul><h3>Assumptions</h3><ul>${assumptions}</ul>`;
}

function showPowerReport(report) {
  const rows = (report.actuators || []).map((item) =>
    `<li><strong>${escapeHtml(item.name)}</strong> ${item.kind} · torque ${item.peak_torque_nm} N·m · force ${item.peak_force_n} N · pressure ${item.peak_pressure_mpa} MPa · flow peak in the samples · power ${item.peak_power_w} W · displaced ${item.displaced_volume_mm3} mm³</li>`
  ).join("");
  const pump = report.pump ? `<p>Pump mean ${report.pump.mean_power_w} W, displaced ${report.pump.displaced_volume_mm3} mm³ over ${report.duration_s} s.</p>` : "";
  const battery = report.battery ? `<p>Battery ${report.battery.energy_wh} Wh at ${report.battery.voltage} V (${report.battery.charge_ah} Ah).</p>` : "";
  const assumptions = (report.assumptions || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  $("assembly-status").textContent = "Power";
  $("assembly-report").innerHTML = `<h3>Power</h3><ul>${rows}</ul>${pump}${battery}<h3>Assumptions</h3><ul>${assumptions}</ul>`;
}

function showModalReport(report) {
  const contact = (report.views || []).find((view) => String(view.name).endsWith("_contact")) || (report.views || [])[0];
  if (contact) {
    $("assembly-picture").hidden = false;
    $("assembly-picture").src = fileUrl(contact.path);
  }
  const resonance = report.resonance || {};
  $("assembly-status").textContent = `Modes ${resonance.status || ""}`;
  const rows = (report.modes || []).map((mode) => `<li>mode ${mode.index}: ${mode.frequency_hz} Hz${mode.rigid_body ? " rigid" : ""}</li>`).join("");
  const flags = (resonance.flags || []).map((flag) => `<li>mode ${flag.mode} near ${escapeHtml(flag.source)} harmonic ${flag.harmonic} at ${flag.excitation_hz} Hz</li>`).join("");
  const suggestions = (resonance.suggestions || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  const assumptions = (report.assumptions || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  $("assembly-report").innerHTML = `<h3>Modes</h3><p>${escapeHtml(report.note || "")}</p><ul>${rows}</ul>${flags ? `<h3>Resonance</h3><ul>${flags}</ul>` : ""}<h3>Suggestions</h3><ul>${suggestions}</ul><p class="muted">${escapeHtml((report.support && report.support.description) || "")}</p><h3>Assumptions</h3><ul>${assumptions}</ul>`;
}

function showRobotReport(report) {
  const validation = report.validation || {};
  const mujoco = validation.mujoco || {};
  const bullet = validation.pybullet || {};
  const rows = (report.joints || []).map((joint) =>
    `<li>${escapeHtml(joint.name)} ${escapeHtml(joint.type)} · ${escapeHtml(joint.parent)} → ${escapeHtml(joint.child)}</li>`
  ).join("");
  const assumptions = (report.assumptions || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  $("assembly-status").textContent = "Robot export";
  $("assembly-report").innerHTML = `<h3>Robot</h3><p>${escapeHtml(report.note || "")}</p>
    <p>MuJoCo ${escapeHtml(mujoco.status || "")}. PyBullet ${escapeHtml(bullet.status || "")}.</p>
    <p class="muted">${escapeHtml(report.urdf || "")}</p>
    <ul>${rows}</ul><h3>Assumptions</h3><ul>${assumptions}</ul>`;
}

function showBoltReport(report) {
  const row = report.bolts ? null : report;
  const rows = (report.bolts || [report]).map((item) => {
    const loosening = item.loosening || {};
    return `<li><strong>${escapeHtml(item.size || "")} ${escapeHtml(String(item.grade || ""))}</strong> ${item.status} · loosening ${loosening.rating} · preload ${item.preload_n} N · torque ${item.tightening_torque_nm} N·m · slip ${item.slip_margin} · yield ${item.yield_margin}</li>`;
  }).join("");
  const lead = (report.bolts && report.bolts[0]) || report;
  const counters = (((lead.loosening || {}).countermeasures) || []).filter((item) => item.recommended).map((item) =>
    `<li><strong>${escapeHtml(item.name)}</strong> — ${escapeHtml(item.effect)}</li>`
  ).join("");
  const assumptions = (report.assumptions || (lead.assumptions || [])).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  $("assembly-status").textContent = "Bolts " + (report.worst || (row && row.loosening && row.loosening.rating) || "");
  $("assembly-report").innerHTML = `<h3>Bolts</h3><ul>${rows}</ul><h3>Countermeasures</h3><ul>${counters}</ul><h3>Assumptions</h3><ul>${assumptions}</ul>`;
}

async function showAssemblyPicture() {
  const report = await command("render_assembly", { name: ui.assembly });
  await refresh();
  if (report.views && report.views[0]) {
    $("assembly-picture").hidden = false;
    $("assembly-picture").src = fileUrl(report.views[0].path);
  }
}

function showPage(page) {
  ui.page = page;
  $("page-model").hidden = page !== "model";
  $("page-docs").hidden = page !== "documents";
  $("page-assembly").hidden = page !== "assembly";
  $("nav-model").classList.toggle("on", page === "model");
  $("nav-docs").classList.toggle("on", page === "documents");
  $("nav-assembly").classList.toggle("on", page === "assembly");
  if (page === "documents" && ui.state) {
    renderDocs();
    if (!$("doc-editor").value) loadDoc(ui.doc);
  } else if (page === "assembly") {
    renderAssemblies();
  } else if (ui.stage === "solid") requestAnimationFrame(() => drawSolid());
  else requestAnimationFrame(() => drawSketch());
}

async function boot() {
  initView();
  $("nav-model").onclick = () => showPage("model");
  $("nav-assembly").onclick = () => showPage("assembly");
  $("nav-docs").onclick = () => showPage("documents");
  $("assembly-preview").onclick = () => showAssemblyPicture().catch(showError);
  $("assembly-orbit").onclick = async () => {
    if (!ui.assembly) return;
    ui.viewAssembly = ui.assembly;
    ui.stage = "solid";
    showPage("model");
    await loadMesh();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    view.fit();
    drawSolid();
  };
  $("assembly-cables").onclick = async () => {
    if (!ui.assembly) return;
    const current = (ui.state.assemblies || []).find((item) => item.name === ui.assembly);
    const sequence = current && current.motions[0];
    const report = await command("check_cables", sequence ? { name: ui.assembly, sequence } : { name: ui.assembly });
    await refresh();
    showCableReport(report);
  };
  $("assembly-power").onclick = async () => {
    if (!ui.assembly) return;
    const current = (ui.state.assemblies || []).find((item) => item.name === ui.assembly);
    const sequence = current && current.motions[0];
    const report = await command("estimate_power", sequence ? { name: ui.assembly, sequence } : { name: ui.assembly });
    await refresh();
    showPowerReport(report);
  };
  $("assembly-modes").onclick = async () => {
    try {
      if (!ui.assembly) return;
      const rpm = Number($("assembly-rpm").value) || 120;
      $("assembly-status").textContent = "Solving modes…";
      const report = await command("solve_assembly_modal", {
        name: ui.assembly,
        setup: {
          mesh_size_mm: 12,
          modes: 4,
          render_modes: 1,
          excitations: [
            { name: "drill rotary", kind: "drill", rpm },
            { name: "hydraulic pump", kind: "pump", hz: Math.max(rpm / 60, 1) * 2 },
          ],
        },
      });
      await refresh();
      showModalReport(report);
    } catch (error) {
      showError(error);
    }
  };
  $("assembly-robot").onclick = async () => {
    try {
      if (!ui.assembly) return;
      $("assembly-status").textContent = "Exporting robot…";
      const report = await command("export_robot", { name: ui.assembly });
      await refresh();
      showRobotReport(report);
    } catch (error) {
      showError(error);
    }
  };
  $("assembly-bolts").onclick = async () => {
    try {
      if (!ui.assembly) return;
      $("assembly-status").textContent = "Checking bolts…";
      const report = await command("check_bolts", {
        spec: {
          assembly: ui.assembly,
          vibration: "intense",
          friction: 0.14,
          grip_mm: 20,
          condition: "dry",
          bolts: [
            { name: "base M8", size: "M8", grade: "8.8", axial_n: 1000, shear_n: 2500 },
            { name: "contrast M12", size: "M12", grade: "10.9", axial_n: 1000, shear_n: 800 },
          ],
        },
      });
      await refresh();
      showBoltReport(report);
    } catch (error) {
      showError(error);
    }
  };
  $("undo").onclick = async () => { await command("undo"); await refresh(); };
  $("redo").onclick = async () => { await command("redo"); await refresh(); };
  $("fit").onclick = () => { view.fit(); drawSolid(); };
  $("new-sketch").onclick = async () => {
    const answer = await ask("New sketch", [{ name: "name", label: "Name", value: "sketch" }]);
    if (!answer) return;
    await command("create_sketch", {
      name: answer.name,
      spec: { plane: "xy", entities: [{ id: "origin", type: "point", at: [0, 0] }], constraints: [{ type: "fix", entity: "origin" }] },
    });
    await refresh();
    await openSketch(answer.name);
  };
  $("pick-mode").onclick = (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    ui.pick = button.dataset.mode;
    $("pick-mode").querySelectorAll("button").forEach((item) => item.classList.toggle("on", item === button));
  };
  $("stage-mode").onclick = async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    ui.stage = button.dataset.stage;
    setStage();
    if (ui.stage === "sketch" && ui.sketchName) await openSketch(ui.sketchName);
    else { await refresh(); }
  };
  $("draw-tools").onclick = (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    ui.tool = button.dataset.tool;
    ui.lineStart = null; ui.rectStart = null; ui.circleCenter = null;
    $("draw-tools").querySelectorAll("button").forEach((item) => item.classList.toggle("on", item === button));
  };
  document.querySelectorAll("[data-constraint]").forEach((button) => {
    button.onclick = () => applyConstraint(button.dataset.constraint).catch(showError);
  });
  $("extrude").onclick = () => buildSolid().catch(showError);
  $("sketch-canvas").addEventListener("pointerdown", (event) => onSketchClick(event).catch(showError));
  $("view").addEventListener("contextmenu", (event) => event.preventDefault());
  $("tabs").onclick = (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    ui.tab = button.dataset.tab;
    $("tabs").querySelectorAll("button").forEach((item) => item.classList.toggle("on", item === button));
    renderPanel();
  };
  $("doc-save").onclick = async () => {
    await command("write_document", { name: ui.doc, markdown: $("doc-editor").value });
    $("doc-preview").innerHTML = markdown($("doc-editor").value);
    await refresh();
  };
  $("doc-editor").addEventListener("input", () => { $("doc-preview").innerHTML = markdown($("doc-editor").value); });
  $("req-form").onsubmit = async (event) => {
    event.preventDefault();
    try {
    const current = await command("get_requirements");
    const kind = $("req-kind").value;
    const item = { id: $("req-id").value, title: $("req-title").value, kind, part: $("req-part").value || ui.part };
    const limitText = $("req-limit").value.trim();
    if (kind === "max_mass") {
      item.max_grams = requiredNumber(limitText, "Enter the mass limit in grams.");
      item.material = $("req-material").value;
    }
    if (kind === "fits_build_volume") {
      const size = limitText.split(",").map((piece) => Number(piece.trim()));
      if (size.length !== 3 || size.some((value) => !Number.isFinite(value))) {
        throw new Error("A build volume is three millimeters, for example 200,200,200.");
      }
      item.size_mm = size;
    }
    if (kind === "min_wall") item.min_mm = requiredNumber(limitText, "Enter the minimum wall in millimeters.");
    if (kind === "fits_printer") {
      item.printer = limitText || "bambu_a1";
      item.orientation = $("req-material").value || "best";
    }
    if (kind === "min_safety_factor") {
      item.minimum = requiredNumber(limitText, "Enter the minimum safety factor.");
      if (feaPlan.fixes.length && feaPlan.loads.length) {
        item.setup = {
          material: $("req-material").value || "pla",
          mesh_size_mm: 2,
          fixes: feaPlan.fixes,
          loads: feaPlan.loads,
        };
      }
    }
    const requirements = [...(current.requirements || []).filter((row) => row.id !== item.id), item];
    await command("set_requirements", { requirements });
    await refresh();
    } catch (error) {
      showError(error);
    }
  };
  $("req-run").onclick = () => runChecks().catch(showError);
  $("chat-settings").onclick = () => editSettings().catch(showError);
  $("chat-form").onsubmit = (event) => { event.preventDefault(); sendChat().catch(showError); };
  window.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
      event.preventDefault();
      command(event.shiftKey ? "redo" : "undo").then(refresh).catch(showError);
    }
  });
  const events = new EventSource("/api/events");
  events.onmessage = (message) => {
    const event = JSON.parse(message.data);
    $("who").textContent = event.source === "ui" ? "" : `Updated by ${event.source}`;
    if (event.source !== "ui") refresh().catch(showError);
  };
  const settings = await (await fetch("/api/settings")).json();
  if (settings.ok) $("chat-key").textContent = settings.result.has_key ? `${settings.result.provider} ${settings.result.key_hint}` : "No API key";
  await refresh();
  setStage();
}

const chatMessages = [];

async function sendChat() {
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  chatMessages.push({ role: "user", content: text });
  appendBubble("user", text);
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages: chatMessages }),
  });
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let assistant = "";
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop();
    parts.forEach((part) => {
      const line = part.replace(/^data: /, "");
      if (!line || line.startsWith(":")) return;
      const event = JSON.parse(line);
      if (event.kind === "text") {
        assistant += event.text;
        appendBubble("assistant", event.text);
      } else if (event.kind === "tool") {
        appendBubble("tool", `${event.ok ? "ran" : "failed"} ${event.name}${event.error ? ": " + event.error : ""}`);
      } else if (event.kind === "error") {
        appendBubble("tool", event.error);
      }
    });
  }
  if (assistant) chatMessages.push({ role: "assistant", content: assistant });
  await refresh();
}

function appendBubble(role, text) {
  const node = document.createElement("div");
  node.className = "bubble " + role;
  node.textContent = text;
  $("chat-log").appendChild(node);
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
}

async function editSettings() {
  const current = (await (await fetch("/api/settings")).json()).result;
  const answer = await ask("Assistant API", [
    { name: "provider", label: "openai, anthropic, or compatible", value: current.provider },
    { name: "base_url", label: "Base URL", value: current.base_url },
    { name: "model", label: "Model", value: current.model },
    { name: "api_key", label: "API key", value: "" },
  ]);
  if (!answer) return;
  if (!answer.api_key) delete answer.api_key;
  const saved = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(answer) });
  const body = await saved.json();
  if (!body.ok) throw new Error(body.error);
  $("chat-key").textContent = body.result.has_key ? `${body.result.provider} ${body.result.key_hint}` : "No API key";
}

function showError(error) {
  appendBubble("tool", error.message || String(error));
}

function add(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
function scaleV(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }
function normalize(a) { const n = Math.hypot(...a) || 1; return [a[0] / n, a[1] / n, a[2] / n]; }

function lookAt(eye, target, up) {
  const z = normalize(sub(eye, target));
  const x = normalize(cross(up, z));
  const y = cross(z, x);
  return new Float32Array([
    x[0], y[0], z[0], 0,
    x[1], y[1], z[1], 0,
    x[2], y[2], z[2], 0,
    -dot(x, eye), -dot(y, eye), -dot(z, eye), 1,
  ]);
}

function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  const nf = 1 / (near - far);
  return new Float32Array([
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (far + near) * nf, -1,
    0, 0, 2 * far * near * nf, 0,
  ]);
}

function multiply(a, b) {
  const out = new Float32Array(16);
  for (let col = 0; col < 4; col += 1) {
    for (let row = 0; row < 4; row += 1) {
      out[col * 4 + row] = a[row] * b[col * 4] + a[4 + row] * b[col * 4 + 1] + a[8 + row] * b[col * 4 + 2] + a[12 + row] * b[col * 4 + 3];
    }
  }
  return out;
}

function invert(m) {
  const inv = new Float32Array(16);
  inv[0] = m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15] + m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10];
  inv[4] = -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15] - m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10];
  inv[8] = m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15] + m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9];
  inv[12] = -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14] - m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9];
  inv[1] = -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15] - m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10];
  inv[5] = m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15] + m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10];
  inv[9] = -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15] - m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9];
  inv[13] = m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14] + m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9];
  inv[2] = m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15] + m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6];
  inv[6] = -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15] - m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6];
  inv[10] = m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15] + m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5];
  inv[14] = -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14] - m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5];
  inv[3] = -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11] - m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6];
  inv[7] = m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11] + m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6];
  inv[11] = -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11] - m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5];
  inv[15] = m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10] + m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5];
  let det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];
  if (!det) return new Float32Array(m);
  det = 1 / det;
  for (let i = 0; i < 16; i += 1) inv[i] *= det;
  return inv;
}

function transformPoint(matrix, point) {
  const x = point[0], y = point[1], z = point[2];
  const w = matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15] || 1;
  return [
    (matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12]) / w,
    (matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13]) / w,
    (matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]) / w,
  ];
}

const SOLID_VS = `
attribute vec3 aPos;
attribute vec3 aNormal;
attribute vec3 aColor;
attribute float aSelect;
uniform mat4 uMvp;
varying vec3 vNormal;
varying vec3 vColor;
varying float vSelect;
void main() {
  vNormal = aNormal;
  vColor = aColor;
  vSelect = aSelect;
  gl_Position = uMvp * vec4(aPos, 1.0);
}`;
const SOLID_FS = `
precision mediump float;
varying vec3 vNormal;
varying vec3 vColor;
varying float vSelect;
void main() {
  vec3 n = normalize(vNormal);
  vec3 key = normalize(vec3(0.35, 0.45, 0.82));
  vec3 fill = normalize(vec3(-0.6, -0.2, 0.4));
  float light = 0.22 + 0.62 * abs(dot(n, key)) + 0.16 * abs(dot(n, fill));
  vec3 base = vColor * light;
  vec3 selected = vec3(0.86, 0.55, 0.22);
  gl_FragColor = vec4(mix(base, selected, clamp(vSelect, 0.0, 1.0)), 1.0);
}`;
const EDGE_VS = `
attribute vec3 aPos;
attribute float aSelect;
uniform mat4 uMvp;
varying float vSelect;
void main() {
  vSelect = aSelect;
  gl_Position = uMvp * vec4(aPos, 1.0);
}`;
const EDGE_FS = `
precision mediump float;
varying float vSelect;
void main() {
  gl_FragColor = mix(vec4(0.05, 0.05, 0.06, 1.0), vec4(0.91, 0.62, 0.24, 1.0), clamp(vSelect, 0.0, 1.0));
}`;

boot().catch(showError);
