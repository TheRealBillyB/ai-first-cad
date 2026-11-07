"""Strength and Vibration faces and results belong to the part they came from.

Faces picked on one part used to stay in the setup after another part was
selected. The next solve then held and loaded the nearest faces of the new
part, which nobody picked. The panels also showed the other part's result.
"""

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
const alerts = [];
const context = {
  ui: { part: null, selectedFaces: [] },
  alert: (text) => alerts.push(text),
  renderFeaSetup: () => {},
  renderModalSetup: () => {},
  feaPlan: { part: null, fixes: [], loads: [] },
  modalPlan: { part: null, fixes: [] },
  // No form on screen: the Load button takes the form's defaults.
  $: () => null,
  feaForm: {},
};
vm.createContext(context);
// The load form's helpers, when this copy of app.js has them.
const helpers = ["feaFormValues", "feaNumber", "feaLoadVector"].filter((name) => code.includes("function " + name + "("));
vm.runInContext([...helpers, "planForPart", "rememberLoad", "rememberModalFix"].map(cut).join("\n"), context);
const pick = (part, ref) => { context.ui.part = part; context.ui.selectedFaces = [ref]; };
const refs = (plan) => ({ part: plan.part, fixes: plan.fixes.map((item) => item.select.ref), loads: (plan.loads || []).map((item) => item.select.ref) });
const steps = [];
pick("beam", "plane/x+/50,0,0"); vm.runInContext('rememberLoad("fix")', context);
pick("beam", "plane/z+/0,0,5"); vm.runInContext('rememberLoad("force")', context);
pick("beam", "plane/x+/50,0,0"); vm.runInContext("rememberModalFix()", context);
steps.push({ fea: refs(context.feaPlan), modal: refs(context.modalPlan) });
pick("bracket", "plane/z-/0,-80,-3"); vm.runInContext('rememberLoad("fix")', context);
vm.runInContext("rememberModalFix()", context);
steps.push({ fea: refs(context.feaPlan), modal: refs(context.modalPlan) });
context.ui.part = "beam";
vm.runInContext("planForPart(feaPlan, ui.part)", context);
steps.push({ fea: refs(context.feaPlan) });
process.stdout.write(JSON.stringify(steps));
"""


def _run(tmp_path: Path) -> list:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    done = subprocess.run([node, str(probe), str(APP)], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(done.stdout)


def test_faces_from_one_part_are_not_carried_to_another(tmp_path):
    same, other, back = _run(tmp_path)
    assert same["fea"] == {"part": "beam", "fixes": ["plane/x+/50,0,0"], "loads": ["plane/z+/0,0,5"]}
    assert same["modal"] == {"part": "beam", "fixes": ["plane/x+/50,0,0"], "loads": []}
    # Picking on the bracket starts a new setup. The beam's faces are gone.
    assert other["fea"] == {"part": "bracket", "fixes": ["plane/z-/0,-80,-3"], "loads": []}
    assert other["modal"] == {"part": "bracket", "fixes": ["plane/z-/0,-80,-3"], "loads": []}
    # Selecting the beam again does not bring the bracket's faces with it.
    assert back["fea"] == {"part": "beam", "fixes": [], "loads": []}


def test_the_panels_check_the_part_before_using_a_plan_or_a_result():
    script = APP.read_text(encoding="utf-8")
    assert "${report && report.part === part ? feaHtml(" in script
    assert "${report && report.part === part ? modalHtml(" in script
    for name in ("runFea", "runModal", "renderFeaSetup", "renderModalSetup"):
        start = script.index(f"function {name}(")
        body = script[start : script.index("\n}\n", start)]
        assert "planForPart(" in body, name
    # A safety-factor requirement only takes the Strength faces picked on its own part.
    assert "feaPlan.part === item.part && feaPlan.fixes.length" in script
