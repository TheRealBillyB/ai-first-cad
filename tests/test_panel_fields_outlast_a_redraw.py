"""What is in the right panel's fields outlasts a redraw.

Every run refreshes the page, and the panel is drawn again from its
defaults. On the Bolts tab, M10 and 10.9 went back to M8 and 8.8 while the
result above still said M10. A second Check then ran an M8. Vibration,
Print, Fatigue, and Clearance did the same.

The fields are read just before each redraw, so a value set by code, such as
the hypoid role swap, comes back too, not only a value typed by hand.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"
APP = STATIC / "app.js"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const app = fs.readFileSync(process.argv[2], "utf8");
const tools = fs.readFileSync(process.argv[3], "utf8");
function cut(code, name) {
  const start = code.indexOf("function " + name + "(");
  if (start < 0) throw new Error("missing " + name);
  const ends = ["\nfunction ", "\nasync function ", "\nconst ", "\n//"].map((head) => code.indexOf(head, start + 10)).filter((at) => at > 0);
  return code.slice(start, ends.length ? Math.min(...ends) : undefined);
}
// A field as the browser draws it: value starts at the drawn default.
function input(id, value, props) {
  return Object.assign({ tagName: "INPUT", id, type: "text", value, defaultValue: value, checked: false, defaultChecked: false }, props || {});
}
function checkbox(id, checked) {
  return input(id, "on", { type: "checkbox", checked, defaultChecked: checked });
}
function select(id, values, chosen) {
  const options = values.map((value) => ({ value, defaultSelected: value === chosen }));
  return { tagName: "SELECT", id, type: "select-one", value: chosen, checked: false, options };
}
// Each tab drawn from its defaults. Part follows the selected part, the way a panel default can.
const tabs = {
  bolts: (ui) => [
    input("bolt-size", "M8"),
    select("bolt-grade", ["8.8", "10.9", "12.9"], "8.8"),
    input("bolt-part", ui.part),
    input("bolt-file", "", { type: "file" }),
  ],
  print: () => [checkbox("print-slice", false), input("print-threshold", "45")],
  library: () => [input("library-name", "")],
  gear: () => [
    select("gear-role", ["pinion", "ring"], "pinion"),
    input("gear-teeth", "13", { type: "number" }),
    input("gear-hypoid-mate", "41", { type: "number" }),
  ],
};
const context = { ui: { tab: "bolts", part: "beam" }, panel: { fields: [] } };
context.$ = (id) => (id === "panel" ? { querySelectorAll: () => context.panel.fields } : context.panel.fields.find((field) => field.id === id) || null);
context.drawPanel = () => { context.panel.fields = tabs[context.ui.tab](context.ui); };
vm.createContext(context);
vm.runInContext("var panelDrafts = {}; var panelDrawnTab = null;", context);
for (const name of ["snapshotPanelFields", "restorePanelFields", "renderPanel"]) vm.runInContext(cut(app, name), context);
vm.runInContext(cut(tools, "hypoidCountsForRole"), context);
const field = (id) => context.$(id);
const look = () => context.panel.fields.map((item) => [item.id, item.value, item.checked]);
const redraw = () => vm.runInContext("renderPanel()", context);
const out = {};

redraw();
field("bolt-size").value = "M10";
field("bolt-grade").value = "10.9";
field("bolt-file").value = "C:\\fakepath\\part.step";
redraw();
out.bolts = look();

// Code sets a field, with no input or change event.
context.ui.tab = "library";
redraw();
field("library-name").value = "bracket_copy";
redraw();
out.library = look();

// The #447 role handler: typed 9 and 41, then Ring swaps them in code.
context.ui.tab = "gear";
redraw();
field("gear-teeth").value = "9";
field("gear-hypoid-mate").value = "41";
redraw();
field("gear-role").value = "ring";
const counts = context.hypoidCountsForRole("ring", field("gear-teeth").value, field("gear-hypoid-mate").value);
field("gear-teeth").value = counts.teeth;
field("gear-hypoid-mate").value = counts.mate;
redraw();
out.gear = look();

// Back on Bolts: its values come back, and a part default nobody touched follows the new part.
context.ui.tab = "print";
redraw();
out.print = look();
field("print-slice").checked = true;
context.ui.part = "bracket";
context.ui.tab = "bolts";
redraw();
out.boltsAgain = look();
context.ui.tab = "print";
redraw();
out.printAgain = look();

// A select whose saved option is gone keeps the drawn value, and the stale draft is dropped.
context.ui.tab = "bolts";
redraw();
tabs.bolts = (ui) => [input("bolt-size", "M8"), select("bolt-grade", ["8.8"], "8.8"), input("bolt-part", ui.part)];
redraw();
out.gone = look();
redraw();
out.drafts = context.panelDrafts;
process.stdout.write(JSON.stringify(out));
"""


def _run(tmp_path: Path) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    done = subprocess.run(
        [node, str(probe), str(APP), str(STATIC / "tools.js")], capture_output=True, text=True, check=True, timeout=60
    )
    return json.loads(done.stdout)


def test_typed_values_come_back_after_the_panel_is_drawn_again(tmp_path):
    out = _run(tmp_path)
    assert out["bolts"] == [
        ["bolt-size", "M10", False],
        ["bolt-grade", "10.9", False],
        ["bolt-part", "beam", False],
        # A file field is never kept.
        ["bolt-file", "", False],
    ]


def test_a_value_set_by_code_comes_back_after_a_redraw(tmp_path):
    out = _run(tmp_path)
    assert out["library"] == [["library-name", "bracket_copy", False]]


def test_the_hypoid_role_swap_survives_the_redraw(tmp_path):
    out = _run(tmp_path)
    # A ring with 41 teeth meshing a 9-tooth pinion, not a ring with fewer teeth than its pinion.
    assert out["gear"] == [
        ["gear-role", "ring", False],
        ["gear-teeth", "41", False],
        ["gear-hypoid-mate", "9", False],
    ]


def test_each_tab_keeps_its_own_values_across_a_tab_switch(tmp_path):
    out = _run(tmp_path)
    assert out["print"] == [["print-slice", "on", False], ["print-threshold", "45", False]]
    assert out["boltsAgain"] == [
        ["bolt-size", "M10", False],
        ["bolt-grade", "10.9", False],
        # Nobody changed this field, so it follows the newly selected part.
        ["bolt-part", "bracket", False],
        ["bolt-file", "", False],
    ]
    assert out["printAgain"] == [["print-slice", "on", True], ["print-threshold", "45", False]]


def test_a_saved_option_that_is_gone_falls_back_to_the_drawn_value(tmp_path):
    out = _run(tmp_path)
    assert out["gone"] == [["bolt-size", "M10", False], ["bolt-grade", "8.8", False], ["bolt-part", "bracket", False]]
    assert out["drafts"]["bolts"] == {"bolt-size": {"value": "M10"}}


def test_the_panel_is_read_before_it_is_drawn_again():
    script = APP.read_text(encoding="utf-8")
    start = script.index("function renderPanel() {")
    body = script[start : script.index("\n}\n", start)]
    assert body.index("snapshotPanelFields(") < body.index("drawPanel();") < body.index("restorePanelFields(")
    # Drafts come from the snapshot, not from input or change events.
    assert "rememberPanelField" not in script
