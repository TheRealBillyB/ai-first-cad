"""The gear mesh report on the Assembly page says why a pair failed.

The page showed each pair's status, backlash, contact ratio, and
interference, but not its findings. A pair the check refused, such as one
asked for too many steps, read "fail · backlash n/a · interference no" with no
reason, and "no" stood for an interference that was never computed.
"""

import json
import shutil
import subprocess
from pathlib import Path

from aicad.gears import mesh_placement
from aicad.workspace import Workspace

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
sandbox.reports = JSON.parse(fs.readFileSync(process.argv[5], "utf8"));
vm.createContext(sandbox);
const source = [process.argv[2], process.argv[3], process.argv[4]].map((path) => fs.readFileSync(path, "utf8")).join("\n");
const driver = `
report = {};
for (const key of Object.keys(reports)) {
  showGearMeshReport(reports[key]);
  report[key] = { status: $("assembly-status").textContent, html: $("assembly-report").innerHTML };
}
done();
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(source + "\n" + driver, sandbox, { filename: "app.js" });
"""


def _spur(teeth: int) -> dict:
    return {"type": "spur", "module_mm": 2, "teeth": teeth, "pressure_angle_deg": 20, "face_width_mm": 6}


def _paint(tmp_path, reports: dict) -> dict:
    node = shutil.which("node")
    assert node
    probe = tmp_path / "mesh-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    data = tmp_path / "reports.json"
    data.write_text(json.dumps(reports), encoding="utf-8")
    static = ROOT / "src" / "aicad" / "app_static"
    result = subprocess.run(
        [node, str(probe), str(static / "project_path.js"), str(static / "app.js"), str(static / "tools.js"), str(data)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_failed_gear_pair_shows_its_findings(tmp_path):
    workspace = Workspace(tmp_path / "drive")
    placement = mesh_placement(_spur(20), _spur(40))
    workspace.execute("create_gear", {"name": "pinion", "spec": _spur(20)})
    workspace.execute(
        "create_gear",
        {
            "name": "wheel",
            "spec": _spur(40),
            "position": placement["gear"]["position_mm"],
            "rotation_deg": placement["gear"]["rotation_deg"],
        },
    )
    workspace.execute(
        "create_part_from_ops",
        {"name": "frame", "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]},
    )
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
                    {
                        "name": "wheel_spin",
                        "type": "revolute",
                        "parent": "frame",
                        "child": "wheel",
                        "parent_anchor": placement["gear"]["position_mm"],
                        "axis": [0, 0, 1],
                    },
                ],
                "relations": [
                    {
                        "name": "mesh",
                        "type": "gear",
                        "driver": "pinion_spin",
                        "driven": "wheel_spin",
                        "ratio": -0.5,
                        "offset": placement["gear"]["rotation_deg"][2],
                    }
                ],
            }
        },
    )
    refused = workspace.execute("check_gears", {"assembly": "drive", "steps": 10**9})
    assert refused["pairs"][0]["interference"] is None
    tight = workspace.execute(
        "check_gears",
        {"assembly": "drive", "steps": 16, "backlash_mm": {"min": 0.1, "max": 0.3}},
    )
    good = workspace.execute("check_gears", {"assembly": "drive", "steps": 16})
    painted = _paint(tmp_path, {"refused": refused, "tight": tight, "good": good})

    assert painted["refused"]["status"] == "Mesh 1 failed"
    assert "steps must be an integer from 8 to 1440" in painted["refused"]["html"]
    assert "interference n/a" in painted["refused"]["html"]
    assert "Circumferential backlash 0.0000 mm is outside 0.1000 to 0.3000 mm." in painted["tight"]["html"]
    assert "interference no" in painted["tight"]["html"]
    assert painted["good"]["status"] == "Mesh pass"
    # A passing pair has no findings, so its row has no nested list.
    assert painted["good"]["html"].split("<h3>Gear mesh</h3>", 1)[1].count("<ul>") == 1
