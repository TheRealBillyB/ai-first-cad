"""A parameter slider stays inside the range the parameter is valid over.

A thread's start count got a 0.2-step slider, so it offered 1.3 starts,
which is refused. Its clearance was a +/-40 mm offset with a 0.5 step, so
0.2 could not be set. A flank angle got 0 to 360 when only 1 to 89 is valid.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from aicad.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]


def _thread_parameters(tmp_path) -> dict:
    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "create_part_from_ops",
        {
            "name": "rod",
            "ops": [
                {"op": "add", "shape": "box", "size": [30, 30, 10], "center": [0, 0, 5]},
                {"op": "add", "shape": "thread", "diameter": 14, "pitch": 4, "starts": 2, "length": 20, "at": [0, 0, 10], "clearance_mm": 0.2},
                {"op": "cut", "shape": "countersink", "at": [10, 10, 10], "diameter": 3.4, "depth": 8, "countersink_diameter": 6.5},
            ],
        },
        source="agent",
    )
    listed = workspace.execute("list_parameters", {"name": "rod"}, source="agent")["parameters"]
    return {item["id"]: item for item in listed}


def test_the_server_says_the_valid_range(tmp_path):
    items = _thread_parameters(tmp_path)
    assert (items["op/1/starts"]["min"], items["op/1/starts"]["max"], items["op/1/starts"]["increment"]) == (1, 8, 1)
    assert (items["op/1/angle"]["min"], items["op/1/angle"]["max"]) == (1, 89)
    clearance = items["op/1/clearance_mm"]
    assert clearance["kind"] == "clearance" and clearance["min"] == 0 and clearance["increment"] == 0.05
    assert clearance["max"] < 4 / 4
    assert (items["op/2/countersink_angle"]["min"], items["op/2/countersink_angle"]["max"]) == (1, 179)
    # A box's size has no stated range, as before.
    assert "min" not in items["op/0/size.x"]


_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
function make(id) {
  return {
    id, value: "", textContent: "", hidden: false, innerHTML: "", className: "", style: {}, dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; },
    querySelectorAll() { return []; }, setAttribute() {}, getAttribute() { return ""; },
    removeAttribute() {}, appendChild() {}, focus() {}, click() {},
  };
}
const sandbox = {
  console, setTimeout, Headers,
  document: {
    getElementById(id) { return make(id); },
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
  requestAnimationFrame() {}, cancelAnimationFrame() {},
};
sandbox.window = sandbox;
vm.createContext(sandbox);
const source = process.argv.slice(2, 4).map((path) => fs.readFileSync(path, "utf8")).join("\n");
vm.runInContext(source, sandbox, { filename: "app.js" });
const items = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const out = {};
vm.runInContext("ui.part = 'rod';", sandbox);
for (const item of items) out[item.id] = sandbox.rangeFor(item);
process.stdout.write(JSON.stringify(out));
"""


def test_the_slider_uses_the_stated_range(tmp_path):
    items = _thread_parameters(tmp_path)
    wanted = ["op/1/starts", "op/1/angle", "op/1/clearance_mm", "op/2/countersink_angle", "op/0/size.x"]
    data = tmp_path / "items.json"
    data.write_text(json.dumps([items[key] for key in wanted]), encoding="utf-8")
    probe = tmp_path / "range-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    static = ROOT / "src" / "aicad" / "app_static"
    node = shutil.which("node")
    assert node
    result = subprocess.run(
        [node, str(probe), str(static / "app.js"), str(static / "tools.js"), str(data)],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    ranges = json.loads(result.stdout)
    assert ranges["op/1/starts"] == {"min": 1, "max": 8, "step": 1}
    assert ranges["op/1/angle"] == {"min": 1, "max": 89, "step": 1}
    assert ranges["op/1/clearance_mm"]["min"] == 0 and ranges["op/1/clearance_mm"]["step"] == 0.05
    assert ranges["op/2/countersink_angle"] == {"min": 1, "max": 179, "step": 1}
    # A plain size keeps the range guessed from its kind.
    assert ranges["op/0/size.x"]["min"] > 0 and ranges["op/0/size.x"]["step"] in (0.1, 0.5)
