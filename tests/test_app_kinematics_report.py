"""The kinematics report on the Assembly page shows what the solver found.

With Move along a line, the page showed only the task and the start pose. The
path's status, its message, and its poses never appeared. For a single
target, the solver's message, such as how far out of reach the target is,
was dropped too.
"""

import json
import shutil
import subprocess
from pathlib import Path

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
  showKinematicsReport(reports[key]);
  report[key] = { status: $("assembly-status").textContent, html: $("assembly-report").innerHTML };
}
done();
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(source + "\n" + driver, sandbox, { filename: "app.js" });
"""


def _paint(tmp_path, reports: dict) -> dict:
    node = shutil.which("node")
    assert node
    probe = tmp_path / "kinematics-probe.js"
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


def _box(workspace, name, size, center):
    workspace.execute(
        "create_part_from_ops",
        {"name": name, "ops": [{"op": "add", "shape": "box", "size": size, "center": center}]},
    )


def test_the_kinematics_report_shows_the_path_and_the_solver_message(tmp_path):
    workspace = Workspace(tmp_path / "arm")
    _box(workspace, "base", [30, 30, 12], [0, 0, 6])
    _box(workspace, "link1", [60, 12, 12], [30, 0, 0])
    _box(workspace, "link2", [40, 10, 10], [20, 0, 0])
    workspace.execute(
        "create_assembly",
        {
            "spec": {
                "name": "arm",
                "instances": [
                    {"name": "base", "part": "base"},
                    {"name": "link1", "part": "link1"},
                    {"name": "link2", "part": "link2"},
                ],
                "joints": [
                    {"name": "shoulder", "type": "revolute", "parent": "base", "child": "link1",
                     "parent_anchor": [0, 0, 20], "axis": [0, 1, 0], "min": -45, "max": 90, "value": 60},
                    {"name": "elbow", "type": "revolute", "parent": "link1", "child": "link2",
                     "parent_anchor": [60, 0, 0], "axis": [0, 1, 0], "min": -90, "max": 90, "value": -30},
                ],
            }
        },
    )
    base = {"name": "arm", "tool": "link2", "at": [40, 0, 0]}
    reachable = workspace.execute("solve_kinematics", {**base, "target": {"position": [80, 0, 20]}})
    unreachable = workspace.execute("solve_kinematics", {**base, "target": {"position": [500, 0, 0]}})
    line = workspace.execute(
        "solve_kinematics",
        {**base, "path": {"kind": "line", "steps": 5, "target": {"position": [80, 0, 20]}}},
    )
    assert line["path"]["status"] == "solved"
    painted = _paint(tmp_path, {"reachable": reachable, "unreachable": unreachable, "line": line})

    assert reachable["inverse"]["message"] in painted["reachable"]["html"]
    assert "elbow_down" in painted["reachable"]["html"]
    assert painted["unreachable"]["status"] == "Kinematics position unreachable"
    assert "400.3998 mm outside the reach of 100.0000 mm" in painted["unreachable"]["html"]
    assert painted["line"]["status"] == "Kinematics path solved"
    html = painted["line"]["html"]
    assert "<span>Path</span><b>solved</b>" in html
    assert line["path"]["message"] in html
    assert html.count("<li>") >= 5
    assert "80.00, 0.00, 20.00" in html
