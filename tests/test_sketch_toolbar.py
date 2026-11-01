"""The sketch toolbar exposes the solver constraints and the constraint status."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aicad.sketch import CONSTRAINT_TYPES, Sketch, constraint_list, solve_sketch
from aicad.workspace import COMMANDS

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src" / "aicad" / "app_static" / "index.html"
SCRIPT = ROOT / "src" / "aicad" / "app_static" / "app.js"

_TOOLBAR = (
    "parallel",
    "perpendicular",
    "tangent",
    "equal",
    "angle",
    "radius",
    "symmetric",
    "midpoint",
)


def test_the_toolbar_lists_the_solver_constraints():
    page = PAGE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    for kind in _TOOLBAR:
        assert f'data-constraint="{kind}"' in page
        assert kind in CONSTRAINT_TYPES
    assert 'id="construction-toggle"' in page
    assert 'id="constraint-status"' in page
    assert "function constraintFromSelection" in script
    assert "function constraintStatusLabel" in script
    assert "function applyConstructionMode" in script
    phrase = constraint_list()
    for command in ("create_sketch", "update_sketch"):
        description = COMMANDS[command].description
        parameters = COMMANDS[command].parameters["properties"]["spec"]["description"]
        for kind in _TOOLBAR:
            assert kind in description
            assert kind in parameters
        assert "construction" in description
        assert "fully_constrained" in description
        assert "under_constrained" in description
        assert "over_constrained" in description
        assert phrase.split(",")[0] in description


def test_a_parallel_construction_line_solves():
    spec = (
        Sketch()
        .point("a", 0, 0)
        .point("b", 10, 0)
        .point("c", 0, 5)
        .point("d", 10, 6)
        .line("bottom", "a", "b")
        .line("top", "c", "d", construction=True)
        .fix("a")
        .horizontal("bottom")
        .parallel("bottom", "top")
        .to_spec()
    )
    report = solve_sketch(spec, name="parallel")
    assert report.status == "under_constrained"
    assert report.geometry["lines"]["top"]["construction"] is True
    top = report.geometry["points"]
    assert top["d"]["y"] == pytest.approx(top["c"]["y"])
    assert top["b"]["y"] == pytest.approx(0)


def test_construction_circle_is_marked():
    spec = Sketch().point("o", 0, 0).fix("o").circle("c", "o", 4, construction=True).to_spec()
    assert spec["entities"][-1]["construction"] is True
    report = solve_sketch(spec, name="circle")
    assert report.geometry["circles"]["c"]["construction"] is True


_DRIVER = r"""
const fs = require("fs");
const vm = require("vm");
const elements = {};
function make(id) {
  const node = {
    id,
    textContent: "",
    className: "",
    hidden: false,
    dataset: {},
    style: {},
    classList: { toggle() {}, add() {}, remove() {} },
    setAttribute() {},
    addEventListener() {},
    querySelectorAll() { return []; },
  };
  elements[id] = node;
  return node;
}
const sandbox = {
  console,
  document: {
    getElementById(id) { return elements[id] || make(id); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
    createElement() { return make("el"); },
  },
  sessionStorage: { getItem() { return null; }, setItem() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  navigator: { userAgent: "test" },
  location: { origin: "http://127.0.0.1:9", href: "http://127.0.0.1:9/" },
  fetch() { return Promise.reject(new Error("no")); },
  structuredClone(value) { return JSON.parse(JSON.stringify(value)); },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
const driver = `
const spec = {
  entities: [
    { id: "p0", type: "point" },
    { id: "p1", type: "point" },
    { id: "bottom", type: "line" },
    { id: "side", type: "line" },
  ],
  constraints: [],
};
const parallel = constraintFromSelection("parallel", spec, ["bottom", "side"], null);
const swapped = constraintFromSelection("midpoint", spec, ["bottom", "p0"], null);
const radius = constraintFromSelection("radius", spec, ["hole"], { value: "4", name: "hole_radius" });
const missing = constraintFromSelection("tangent", spec, ["bottom"], null);
const labels = ["fully_constrained", "under_constrained", "over_constrained"].map(constraintStatusLabel);
ui.construction = true;
const drawn = { entities: [{ id: "p0", type: "point" }, { id: "p2", type: "point" }, { id: "l0", type: "line" }] };
applyConstructionMode(spec, drawn);
report = { parallel, swapped, radius, missing, labels, drawn: drawn.entities.map((entity) => [entity.id, !!entity.construction]) };
`;
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\n" + driver, sandbox, { filename: "app.js" });
process.stdout.write(JSON.stringify(sandbox.report));
"""


def test_the_toolbar_builds_constraint_records(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "constraints.js"
    probe.write_text(_DRIVER, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["parallel"] == {"type": "parallel", "a": "bottom", "b": "side"}
    assert report["swapped"] == {"type": "midpoint", "point": "p0", "line": "bottom"}
    assert report["radius"]["type"] == "radius"
    assert report["radius"]["value"] == 4
    assert report["missing"] is None
    assert report["labels"] == ["Fully constrained", "Under-constrained", "Over-constrained"]
    assert report["drawn"] == [["p0", False], ["p2", False], ["l0", True]]
