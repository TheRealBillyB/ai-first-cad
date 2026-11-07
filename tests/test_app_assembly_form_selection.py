"""An open Assembly tool form follows the assembly that is selected.

The form lists the instances, joints, poses, and motions of one assembly.
After another assembly was picked in the list, the form kept the old lists,
so Solve sent an instance the new assembly does not have.
"""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const elements = {};
function make(id, value) {
  const node = {
    id,
    value: value || "",
    textContent: "",
    hidden: false,
    innerHTML: "",
    className: "",
    style: {},
    dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    setAttribute() {},
    getAttribute() { return ""; },
    removeAttribute() {},
    appendChild() {},
    focus() {},
    click() {},
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
  fetch: () => Promise.reject(new Error("no")),
  WebSocket: function () {},
  requestAnimationFrame() {},
  cancelAnimationFrame() {},
};
sandbox.window = sandbox;
sandbox.elements = elements;
vm.createContext(sandbox);
const source = process.argv.slice(2).map((path) => fs.readFileSync(path, "utf8")).join("\n");
const driver = `
const arm = {
  name: "arm",
  instances: [{ name: "base" }, { name: "link1" }, { name: "link2" }],
  joints: [{ name: "shoulder", type: "revolute", value: 0 }, { name: "elbow", type: "revolute", value: 0 }],
  poses: ["rest"],
  motions: ["nod"],
  studies: ["swing"],
};
const drive = {
  name: "drive",
  instances: [{ name: "frame" }, { name: "pinion" }, { name: "wheel" }],
  joints: [{ name: "pinion_spin", type: "revolute", value: 0 }],
  poses: [],
  motions: [],
  studies: [],
};
ui.state = { assemblies: [arm, drive], panels: {} };
ui.page = "assembly";
const shown = {};
for (const tool of ["kinematics", "animate", "error"]) {
  ui.assembly = "arm";
  ui.assemblyTool = tool;
  renderAssemblyForm();
  const before = $("assembly-form").innerHTML;
  ui.assembly = "drive";
  renderAssemblies();
  shown[tool] = { before, after: $("assembly-form").innerHTML };
}
$("assembly-form").hidden = true;
ui.assembly = "arm";
renderAssemblies();
shown.closed = $("assembly-form").innerHTML;
report = shown;
done();
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(source + "\n" + driver, sandbox, { filename: "app.js" });
"""


def test_the_open_tool_form_is_drawn_again_for_the_selected_assembly(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "form-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    static = ROOT / "src" / "aicad" / "app_static"
    result = subprocess.run(
        [node, str(probe), str(static / "project_path.js"), str(static / "app.js"), str(static / "tools.js")],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    kinematics = report["kinematics"]
    assert 'value="link2"' in kinematics["before"]
    assert 'value="link2"' not in kinematics["after"]
    assert 'value="pinion"' in kinematics["after"]
    animate = report["animate"]
    assert 'value="nod"' in animate["before"]
    assert 'value="nod"' not in animate["after"]
    assert "None on this assembly" in animate["after"]
    error = report["error"]
    assert 'data-error-joint="shoulder"' in error["before"]
    assert 'data-error-joint="shoulder"' not in error["after"]
    assert 'data-error-joint="pinion_spin"' in error["after"]
    # A closed form is left alone.
    assert report["closed"] == error["after"]
