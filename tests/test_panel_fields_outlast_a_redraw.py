"""What the person typed in the right panel outlasts a redraw.

Every run refreshes the page, and the panel is drawn again from its
defaults. On the Bolts tab, M10 and 10.9 went back to M8 and 8.8 while the
result above still said M10. A second Check then ran an M8. Vibration,
Print, Fatigue, and Clearance did the same.
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
function field(tagName, id, props) {
  return Object.assign({ tagName, id, type: tagName === "SELECT" ? "select-one" : "text", value: "", checked: false }, props || {});
}
function select(id, values, value) {
  return field("SELECT", id, { options: values.map((item) => ({ value: item })), value });
}
// The panel as drawn from the defaults.
function draw() {
  return [
    field("INPUT", "bolt-size", { value: "M8" }),
    select("bolt-grade", ["8.8", "10.9", "12.9"], "8.8"),
    field("INPUT", "print-slice", { type: "checkbox", checked: false }),
    select("clearance-a", ["beam", "blockA"], "beam"),
    field("INPUT", "bolt-file", { type: "file" }),
  ];
}
const context = { ui: { tab: "bolts" }, panelDrafts: {} };
vm.createContext(context);
vm.runInContext(cut("rememberPanelField") + "\n" + cut("restorePanelFields"), context);
const typed = draw();
typed[0].value = "M10";
typed[1].value = "10.9";
typed[2].checked = true;
typed[3].value = "blockA";
typed[4].value = "C:\\fakepath\\part.step";
typed.forEach((target) => vm.runInContext("rememberPanelField(event)", Object.assign(context, { event: { target } })));
// A select whose typed option is gone keeps the drawn value.
context.panelDrafts.bolts["bolt-grade-gone"] = { value: "4.6" };
const drawn = draw();
drawn.push(select("bolt-grade-gone", ["8.8"], "8.8"));
const root = { querySelectorAll: () => drawn };
context.root = root;
vm.runInContext("restorePanelFields(root, panelDrafts[ui.tab])", context);
const other = draw();
context.other = { querySelectorAll: () => other };
vm.runInContext("restorePanelFields(other, panelDrafts.print)", context);
process.stdout.write(JSON.stringify({
  drafts: context.panelDrafts,
  bolts: drawn.map((item) => [item.id, item.value, item.checked]),
  other: other.map((item) => [item.id, item.value, item.checked]),
}));
"""


def _run(tmp_path: Path) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    done = subprocess.run([node, str(probe), str(APP)], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(done.stdout)


def test_typed_values_come_back_after_the_panel_is_drawn_again(tmp_path):
    out = _run(tmp_path)
    assert out["bolts"] == [
        ["bolt-size", "M10", False],
        ["bolt-grade", "10.9", False],
        ["print-slice", "", True],
        ["clearance-a", "blockA", False],
        ["bolt-file", "", False],
        ["bolt-grade-gone", "8.8", False],
    ]
    # A file field is never remembered.
    assert "bolt-file" not in out["drafts"]["bolts"]


def test_one_tab_does_not_fill_another(tmp_path):
    out = _run(tmp_path)
    assert out["other"] == [
        ["bolt-size", "M8", False],
        ["bolt-grade", "8.8", False],
        ["print-slice", "", False],
        ["clearance-a", "beam", False],
        ["bolt-file", "", False],
    ]


def test_the_panel_redraw_puts_the_typed_values_back():
    script = APP.read_text(encoding="utf-8")
    start = script.index("function renderPanel() {")
    body = script[start : script.index("\n}\n", start)]
    assert "drawPanel();" in body
    assert 'restorePanelFields($("panel"), panelDrafts[ui.tab]);' in body
    assert '$("panel").addEventListener("input", rememberPanelField);' in script
    assert '$("panel").addEventListener("change", rememberPanelField);' in script
