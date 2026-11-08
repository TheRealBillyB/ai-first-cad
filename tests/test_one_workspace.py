"""Model and Assembly are one workspace with one 3D view.

Assembly used to be a page of buttons and pictures. Its only live 3D view was
Orbit, which quietly swapped the Model view to the assembly. A click there then
sent the picked instance as a face of the selected part, and the server
answered "Face reference 'instance/proximal' is not on part 'palm'" on every
click.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "aicad" / "app_static"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const elements = {};
function make(id, value) {
  const classes = new Set();
  const node = {
    id,
    value: value || "",
    textContent: "",
    hidden: false,
    innerHTML: "",
    className: "",
    style: {},
    dataset: {},
    classes,
    classList: {
      toggle(name, on) { if (on === undefined ? !classes.has(name) : on) classes.add(name); else classes.delete(name); },
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    setAttribute() {},
    removeAttribute() {},
    getAttribute() { return ""; },
    appendChild() {},
    focus() {},
    click() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 300 }; },
  };
  elements[id] = node;
  return node;
}
const sandbox = {
  console,
  setTimeout,
  document: {
    getElementById(id) { return elements[id] || make(id); },
    querySelector() { return { getAttribute() { return ""; } }; },
    querySelectorAll() { return []; },
    addEventListener() {},
    createElement() { return make("el"); },
  },
  sessionStorage: { getItem() { return null; }, setItem() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  navigator: { userAgent: "test" },
  location: { origin: "http://127.0.0.1:8765", href: "http://127.0.0.1:8765/" },
  fetch: () => Promise.reject(new Error("no fetch in this probe")),
  WebSocket: function () {},
  requestAnimationFrame() {},
  cancelAnimationFrame() {},
};
sandbox.window = sandbox;
sandbox.elements = elements;
vm.createContext(sandbox);
const driver = `
const sent = [];
const meshes = [];
command = async (name, args) => { sent.push([name, args]); return {}; };
loadMesh = async () => { meshes.push(ui.page === "assembly" ? "assembly:" + ui.viewAssembly : "parts"); };
ui.state = {
  parts: [{ name: "palm" }, { name: "proximal" }],
  sketches: [],
  panels: {},
  assemblies: [{
    name: "finger_rig",
    instances: [{ name: "palm", part: "palm" }, { name: "proximal", part: "proximal" }],
    joints: [
      { name: "fix_bench_plate_to_the_palm", type: "fixed", parent: "palm", child: "plate", value: 0, slide: 0, min: null, max: null },
      { name: "mcp", type: "revolute", parent: "palm", child: "proximal", value: 10, slide: 0, min: 0, max: 90 },
      { name: "spin", type: "revolute", parent: "palm", child: "proximal", value: 0, slide: 0, min: null, max: null },
      { name: "pip", type: "revolute", parent: "palm", child: "proximal", value: 40.38, slide: 0, min: 0, max: 95, driven_by: "servo_p" },
    ],
    poses: [],
    motions: [],
    studies: [],
  }],
};
ui.part = "palm";
async function probe() {
  const out = {};
  showPage("assembly");
  out.entered = {
    modelHidden: $("page-model").hidden,
    assemblyMode: $("page-model").classes.has("assembly-mode"),
    viewAssembly: ui.viewAssembly,
    meshes: meshes.slice(),
  };
  const controls = $("assembly-controls").innerHTML;
  out.joints = {
    slider: controls.includes('data-slide="mcp"'),
    noSliderWithoutLimits: !controls.includes('data-slide="spin"'),
    fixedFolded: controls.includes("Fixed joints (1)"),
    fixedHasNoInput: !controls.includes('data-joint="fix_bench_plate_to_the_palm"'),
    inlineWidths: /style="width/.test(controls),
    drivenNamed: controls.includes('data-driven="pip">driven by servo_p'),
    drivenHasNoSlider: !controls.includes('data-slide="pip"') && !controls.includes('data-apply="pip"'),
  };
  // A click on an instance in the 3D view.
  ui.mesh = { positions: [], triangle_face: [], faces: [], edges: [] };
  pickHit = () => ({ ref: "instance/proximal", part: "palm" });
  view.dragging = { x: 0, y: 0, moved: false, button: 0, mode: "orbit" };
  await onPointerUp({ clientX: 10, clientY: 10 });
  out.picked = {
    sent: sent.slice(),
    instance: ui.selectedInstance,
    faces: ui.selectedFaces.slice(),
    readout: $("selection-readout").textContent,
    card: $("instance-card").innerHTML,
    cardHidden: $("instance-card").hidden,
  };
  // A leftover Move mode does not drag a part out of the assembly view.
  ui.pick = "move";
  onPointerDown({ clientX: 10, clientY: 10, button: 0, shiftKey: false, target: { setPointerCapture() {} }, pointerId: 1 });
  out.moveInAssembly = view.dragging && view.dragging.mode;
  view.dragging = null;
  ui.pick = "face";
  showPage("model");
  out.left = {
    assemblyMode: $("page-model").classes.has("assembly-mode"),
    viewAssembly: ui.viewAssembly,
    instance: ui.selectedInstance,
    faces: ui.selectedFaces.slice(),
    readout: $("selection-readout").textContent,
  };
  // An Assembly view with no assembly says so instead of showing the parts.
  ui.state.assemblies = [];
  ui.assembly = null;
  loadMesh = originalLoadMesh;
  showPage("assembly");
  await originalLoadMesh();
  out.empty = { mesh: ui.mesh, message: $("empty-message").textContent, shown: !$("empty").hidden };
  return out;
}
probe().then((value) => { report = value; done(); }, (error) => { report = { error: String(error && error.stack || error) }; done(); });
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\nconst originalLoadMesh = loadMesh;\n" + driver, sandbox, { filename: "app.js" });
"""


def _report(tmp_path) -> dict:
    node = shutil.which("node")
    assert node
    probe = tmp_path / "one-workspace-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(STATIC / "app.js")], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    report = json.loads(result.stdout)
    assert "error" not in report, report
    return report


def test_assembly_is_the_model_workspace_showing_the_assembled_instances(tmp_path):
    entered = _report(tmp_path)["entered"]
    assert entered["modelHidden"] is False
    assert entered["assemblyMode"] is True
    assert entered["viewAssembly"] == "finger_rig"
    assert entered["meshes"] == ["assembly:finger_rig"]


def test_a_click_on_an_instance_selects_it_and_sends_nothing(tmp_path):
    picked = _report(tmp_path)["picked"]
    assert picked["sent"] == []
    assert picked["instance"] == "proximal"
    assert picked["faces"] == ["instance/proximal"]
    assert picked["readout"] == "proximal (part proximal)"
    assert picked["cardHidden"] is False
    assert "Edit part" in picked["card"]


def test_a_leftover_move_mode_only_orbits_in_the_assembly(tmp_path):
    assert _report(tmp_path)["moveInAssembly"] == "orbit"


def test_back_on_model_the_instance_selection_is_gone(tmp_path):
    left = _report(tmp_path)["left"]
    assert left["assemblyMode"] is False
    assert left["viewAssembly"] is None
    assert left["instance"] is None
    assert left["faces"] == []
    assert left["readout"] == "Nothing selected"


def test_joints_with_limits_get_a_slider_and_fixed_and_driven_joints_do_not(tmp_path):
    """A joint a relation drives follows its driver, so a slider on it would do nothing."""
    joints = _report(tmp_path)["joints"]
    assert joints == {
        "slider": True,
        "noSliderWithoutLimits": True,
        "fixedFolded": True,
        "fixedHasNoInput": True,
        "inlineWidths": False,
        "drivenNamed": True,
        "drivenHasNoSlider": True,
    }


def test_an_assembly_view_without_an_assembly_says_so(tmp_path):
    empty = _report(tmp_path)["empty"]
    assert empty["mesh"] is None
    assert empty["shown"] is True
    assert empty["message"].startswith("No assembly yet.")


def test_the_page_has_one_workspace_with_both_halves():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    model = page[page.index('<div id="page-model"') : page.index('<div id="page-docs"')]
    for marker in ('id="assembly-list"', 'id="assembly-controls"', 'id="assembly-side"', 'id="assembly-report"', 'id="instance-card"'):
        assert marker in model, marker
    assert 'id="assembly-orbit"' not in page
    assert re.search(r'class="[^"]*\bparts-only\b[^"]*" id="tabs"|id="tabs"[^>]*parts-only', page)
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".shell.assembly-mode .parts-only, .shell:not(.assembly-mode) .assembly-only { display: none !important; }" in css


def test_the_assembly_summary_names_the_driver_of_a_driven_joint(tmp_path):
    from aicad.workspace import Workspace

    workspace = Workspace(tmp_path / "drive")
    for name in ("frame", "pinion", "wheel"):
        workspace.execute("create_part_from_ops", {"name": name, "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]})
    workspace.execute(
        "create_assembly",
        {
            "spec": {
                "name": "drive",
                "instances": [
                    {"name": "frame", "part": "frame"},
                    {"name": "pinion", "part": "pinion"},
                    {"name": "wheel", "part": "wheel"},
                ],
                "joints": [
                    {"name": "pinion_spin", "type": "revolute", "parent": "frame", "child": "pinion", "axis": [0, 0, 1]},
                    {"name": "wheel_spin", "type": "revolute", "parent": "frame", "child": "wheel", "axis": [0, 0, 1]},
                ],
                "relations": [{"name": "mesh", "type": "gear", "driver": "pinion_spin", "driven": "wheel_spin", "ratio": -0.5}],
            }
        },
    )
    listed = {joint["name"]: joint for joint in workspace.document.list_assemblies()[0]["joints"]}
    assert listed["wheel_spin"]["driven_by"] == "pinion_spin"
    assert listed["pinion_spin"]["driven_by"] is None
