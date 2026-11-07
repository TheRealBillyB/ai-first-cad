"""The Strength Load button uses the force the person typed, not a fixed 10 N down."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aicad import fea
from aicad.document import Document

APP = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static" / "app.js"
_FACE = "plane/x+/50.000,0.000,0.000"

# The panel's own code, run under Node with a stand-in for the page and the server.
_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[2], "utf8");
function cut(name) {
  let start = code.indexOf("function " + name + "(");
  if (start < 0) throw new Error("missing " + name);
  if (code.slice(start - 6, start) === "async ") start -= 6;
  // A function ends where the next top-level line starts.
  const ends = ["function ", "async function ", "const ", "//"].map((head) => code.indexOf("\n" + head, start + 10)).filter((at) => at > 0);
  return code.slice(start, ends.length ? Math.min(...ends) : undefined);
}
const cases = JSON.parse(process.argv[3]);
const face = process.argv[4];
const out = [];
(async () => {
  for (const typed of cases) {
    const nodes = {
      "fea-material": { value: "steel" },
      "fea-mesh": { value: "3" },
      "fea-sf": { value: "2" },
      "fea-setup": { textContent: "" },
      "fea-status": { hidden: true, className: "", textContent: "" },
      "fea-run": { disabled: false },
    };
    Object.entries(typed || {}).forEach(([key, value]) => { nodes["fea-" + key] = { value }; });
    const alerts = [];
    const sent = [];
    const context = {
      $: (id) => nodes[id] || null,
      ui: { part: "beam", selectedFaces: [face] },
      feaPlan: { part: "beam", fixes: [{ name: "fix_1", select: { ref: "plane/x-/-50.000,0.000,0.000" } }], loads: [] },
      feaForm: { material: null, mesh: null, sf: null, force: null, dx: null, dy: null, dz: null },
      performance: { now: () => 0 },
      setInterval: () => 1,
      clearInterval: () => {},
      alert: (text) => alerts.push(text),
      showError: (error) => alerts.push(error.message),
      command: async (name, args) => { sent.push([name, args]); return { status: "pass" }; },
      refresh: async () => {},
    };
    vm.createContext(context);
    // A plan helper from another change is loaded when this copy of app.js has it.
    if (code.includes("function planForPart(")) vm.runInContext(cut("planForPart"), context);
    for (const name of ["feaFormValues", "feaNumber", "feaLoadVector", "feaLoadText", "feaSetupLines", "rememberLoad", "renderFeaSetup", "renderFeaStatus", "runFea"]) {
      vm.runInContext(cut(name), context);
    }
    vm.runInContext("const feaRun = { started: null, error: null, timer: null };", context);
    vm.runInContext("rememberLoad('force')", context);
    await vm.runInContext("runFea()", context);
    out.push({ alerts, setup: nodes["fea-setup"].textContent, sent: sent.map(([name, args]) => [name, args.setup.loads]) });
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


def _run(tmp_path: Path, cases: list) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    done = subprocess.run(
        [node, str(probe), str(APP), json.dumps(cases), _FACE],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(done.stdout)


def _load(vector: list[float]) -> dict:
    return {"name": "load_1", "type": "force", "vector": vector, "select": {"ref": _FACE}}


def test_a_typed_load_is_the_load_sent_to_the_solve(tmp_path):
    sideways, slanted = _run(
        tmp_path,
        [
            {"force": "25", "dx": "1", "dy": "0", "dz": "0"},
            # The direction only sets the way: 30 N along (0, 3, 4) is (0, 18, 24).
            {"force": "30", "dx": "0", "dy": "3", "dz": "4"},
        ],
    )
    assert sideways["alerts"] == []
    assert sideways["sent"] == [["solve_mechanics", [_load([25, 0, 0])]]]
    assert sideways["setup"].splitlines()[-1] == f"load_1: 25 N along 1, 0, 0 on {_FACE}"
    assert slanted["sent"] == [["solve_mechanics", [_load([0, 18, 24])]]]
    assert slanted["setup"].splitlines()[-1] == f"load_1: 30 N along 0, 0.6, 0.8 on {_FACE}"


def test_the_load_starts_at_10_newtons_down(tmp_path):
    (fresh,) = _run(tmp_path, [None])
    assert fresh["sent"] == [["solve_mechanics", [_load([0, 0, -10])]]]
    assert fresh["setup"].splitlines()[-1] == f"load_1: 10 N along 0, 0, -1 on {_FACE}"


@pytest.mark.parametrize(
    ("typed", "message"),
    [
        ({"force": "", "dx": "0", "dy": "0", "dz": "-1"}, "Enter a force above 0 N."),
        ({"force": "0", "dx": "0", "dy": "0", "dz": "-1"}, "Enter a force above 0 N."),
        ({"force": "-5", "dx": "0", "dy": "0", "dz": "-1"}, "Enter a force above 0 N."),
        ({"force": "ten", "dx": "0", "dy": "0", "dz": "-1"}, "Enter a force above 0 N."),
        ({"force": "10", "dx": "", "dy": "0", "dz": "-1"}, "Enter a number for each direction: X, Y, and Z."),
        ({"force": "10", "dx": "0", "dy": "down", "dz": "-1"}, "Enter a number for each direction: X, Y, and Z."),
        ({"force": "10", "dx": "0", "dy": "0", "dz": "0"}, "The direction can't be 0, 0, 0. Enter which way the force pushes."),
    ],
)
def test_a_bad_load_is_refused_and_nothing_is_solved(tmp_path, typed, message):
    (refused,) = _run(tmp_path, [typed])
    # The load is refused, so Solve then asks for a load instead of sending one.
    assert refused["alerts"] == [message, "Add a fixed face and a load from the selection."]
    assert refused["sent"] == []


def test_the_typed_load_reaches_the_solver_unchanged(tmp_path, monkeypatch):
    seen = {}

    def solve_part(shape, setup, **kwargs):
        seen["setup"] = setup
        return {"status": "pass"}

    monkeypatch.setattr(fea, "solve_part", solve_part)
    document = Document(tmp_path)
    document.create_part_from_ops("beam", [{"op": "add", "shape": "box", "size": [100, 10, 10], "center": [0, 0, 0]}])
    setup = {
        "material": "steel",
        "fixes": [{"name": "fix_1", "select": {"ref": "plane/x-/-50.000,0.000,0.000"}}],
        "loads": [_load([0, 18, 24])],
    }
    document.solve_mechanics("beam", json.loads(json.dumps(setup)))
    assert seen["setup"] == setup


def test_the_strength_panel_draws_the_load_fields():
    script = APP.read_text(encoding="utf-8")
    panel = script[script.index('if (ui.tab === "fea")') : script.index('if (ui.tab === "vibration")')]
    for key in ("force", "dx", "dy", "dz"):
        assert f'id="fea-{key}"' in panel
        assert f"value=\"${{escapeAttr(form.{key})}}\"" in panel
    assert "feaLoadVector(" in script[script.index("function rememberLoad(") :]
