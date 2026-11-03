"""The Strength form shows the last solve, not the defaults, after a redraw."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static" / "app.js"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[2], "utf8");
function cut(name) {
  const start = code.indexOf("function " + name + "(");
  if (start < 0) throw new Error("missing " + name);
  const end = code.indexOf("\nfunction ", start + 1);
  return code.slice(start, end < 0 ? undefined : end);
}
const context = {};
vm.createContext(context);
vm.runInContext(cut("feaFormValues") + "\n" + cut("feaSetupLines"), context);
const cases = JSON.parse(process.argv[3]);
const out = cases.map((item) =>
  item.kind === "form"
    ? context.feaFormValues(item.typed, item.report, item.ids)
    : context.feaSetupLines(item.plan, item.report)
);
process.stdout.write(JSON.stringify(out));
"""

_IDS = ["pla", "petg", "steel", "aluminum"]
_REPORT = {
    "material": {"id": "steel"},
    "mesh": {"size_mm": 3.0, "requested_size_mm": 2.5},
    "review": {"target_safety_factor": 3},
    "fixes": [{"name": "hub", "face_count": 4}],
    "loads": [{"name": "pads", "face_count": 1}],
}
_EMPTY_TYPED = {"material": None, "mesh": None, "sf": None}
_EMPTY_PLAN = {"fixes": [], "loads": []}


def _run(tmp_path: Path, cases: list[dict]) -> list:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    done = subprocess.run(
        [node, str(probe), str(APP), json.dumps(cases)], capture_output=True, text=True, check=True, timeout=60
    )
    return json.loads(done.stdout)


def test_the_form_starts_from_the_last_solve(tmp_path):
    (form,) = _run(tmp_path, [{"kind": "form", "typed": _EMPTY_TYPED, "report": _REPORT, "ids": _IDS}])
    assert form == {"material": "steel", "mesh": "2.5", "sf": "3"}


def test_typed_values_outlast_a_redraw(tmp_path):
    typed = {"material": "aluminum", "mesh": "4", "sf": None}
    (form,) = _run(tmp_path, [{"kind": "form", "typed": typed, "report": _REPORT, "ids": _IDS}])
    assert form == {"material": "aluminum", "mesh": "4", "sf": "3"}


def test_no_solve_keeps_the_defaults_and_an_unknown_material_falls_back(tmp_path):
    fresh, unknown = _run(
        tmp_path,
        [
            {"kind": "form", "typed": _EMPTY_TYPED, "report": None, "ids": _IDS},
            {"kind": "form", "typed": _EMPTY_TYPED, "report": {"material": {"id": "unobtainium"}}, "ids": _IDS},
        ],
    )
    assert fresh == {"material": "pla", "mesh": "1.5", "sf": "2"}
    assert unknown["material"] == "pla"


def test_the_setup_names_the_last_solve_until_faces_are_picked(tmp_path):
    plan = {"fixes": [{"select": {"ref": "plane/z-/0,0,0"}}], "loads": []}
    last, picked, nothing = _run(
        tmp_path,
        [
            {"kind": "setup", "plan": _EMPTY_PLAN, "report": _REPORT},
            {"kind": "setup", "plan": plan, "report": _REPORT},
            {"kind": "setup", "plan": _EMPTY_PLAN, "report": None},
        ],
    )
    assert last[0] == "Last solve: fixed hub (4 faces); loaded pads (1 face)."
    assert picked == ["fix plane/z-/0,0,0"]
    assert nothing == []


def test_the_strength_panel_uses_the_helpers():
    script = APP.read_text(encoding="utf-8")
    assert 'value="1.5"' not in script
    assert "feaFormValues(feaForm, report" in script
    assert "feaSetupLines(feaPlan, report)" in script
    assert "No supports or loads yet." in script
