"""A result from a button above the Assembly picture stays on screen.

Cables, Power, Modes, Bolt check, and Export robot painted their report but
left the report focus on the tool used before. The next refresh repainted
that older report, such as a motion study, over the new one.
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
const panels = {
  motion_study: { assembly: "arm", status: "clear", study: { name: "swing" } },
  cables: { assembly: "arm", status: "fail", cables: [{ name: "lead", status: "fail", length_mm: [80], max_strain: 0, min_bend_radius_mm: null, pinch_count: 4 }] },
  power: { assembly: "arm", actuators: [{ name: "shoulder motor", kind: "electric" }] },
  vibration: { assembly: "arm", note: "First flexible frequency 722 Hz.", modes: [], resonance: { status: "clear" } },
  bolts: { assembly: "arm", worst: "high", bolts: [{ size: "M8", grade: "8.8", status: "pass", loosening: { rating: "high" } }] },
  robot: { assembly: "arm", note: "Exported arm.", joints: [], validation: {} },
};
function paint(focus, state) {
  ui.state = state || { panels };
  ui.assembly = "arm";
  ui.reportFocus = focus;
  $("assembly-status").textContent = "";
  $("assembly-report").innerHTML = "";
  $("assembly-picture").hidden = false;
  showStoredAssemblyReports();
  return {
    status: $("assembly-status").textContent,
    heading: ($("assembly-report").innerHTML.match(/<h3>([^<]*)<\\/h3>/) || [])[1] || "",
    pictureHidden: $("assembly-picture").hidden,
  };
}
report = {
  cables: paint("cables"),
  power: paint("power"),
  vibration: paint("vibration"),
  bolts: paint("bolts"),
  robot: paint("robot"),
  study: paint("motion_study"),
  partModes: paint("vibration", { panels: { motion_study: panels.motion_study, vibration: { note: "A part.", modes: [], resonance: {} } } }),
};
done();
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(source + "\n" + driver, sandbox, { filename: "app.js" });
"""


def test_each_assembly_button_report_survives_a_refresh(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "focus-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    static = ROOT / "src" / "aicad" / "app_static"
    result = subprocess.run(
        [node, str(probe), str(static / "app.js"), str(static / "tools.js")],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    report = json.loads(result.stdout)
    assert report["cables"]["status"] == "Cables fail"
    assert report["cables"]["heading"] == "Cables"
    assert report["power"]["heading"] == "Power"
    assert report["vibration"]["heading"] == "Modes"
    assert report["bolts"]["heading"] == "Bolts"
    assert report["robot"]["heading"] == "Robot"
    for key in ("cables", "power", "bolts", "robot"):
        assert report[key]["pictureHidden"] is True, key
    assert report["study"]["status"] == "Study clear"
    # A part's modal result in the shared panel is not painted as this assembly's.
    assert report["partModes"]["heading"] != "Modes"


def test_each_assembly_button_moves_the_report_focus_to_its_own_result():
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    for button, command, focus in (
        ("assembly-cables", "check_cables", "cables"),
        ("assembly-power", "estimate_power", "power"),
        ("assembly-modes", "solve_assembly_modal", "vibration"),
        ("assembly-robot", "export_robot", "robot"),
    ):
        handler = script.split(f'$("{button}").onclick', 1)[1].split(".onclick", 1)[0]
        after = handler.split(f'command("{command}"', 1)[1]
        assert f'ui.reportFocus = "{focus}";' in after.split("await refresh()", 1)[0], button
    # Bolt check is a form in tools.js. Its runner sets the focus the same way.
    tools = (ROOT / "src" / "aicad" / "app_static" / "tools.js").read_text(encoding="utf-8")
    assert '"assembly-bolts": "bolts"' in tools
    runner = tools.split("async function runAssemblyBolts(", 1)[1].split("\nfunction ", 1)[0].split("\nasync function ", 1)[0]
    after = runner.split('command("check_bolts"', 1)[1]
    assert 'ui.reportFocus = "bolts";' in after.split("await refresh()", 1)[0]
