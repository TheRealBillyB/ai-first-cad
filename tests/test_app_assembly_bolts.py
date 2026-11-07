"""Bolt check on the Assembly page checks the bolt that was typed.

The button used to check two bolts written into the page, an M8 and an M12
with fixed loads, and showed the result as if it were this assembly's. It
now opens a form like the other assembly tools.
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
vm.createContext(sandbox);
const source = process.argv.slice(2).map((path) => fs.readFileSync(path, "utf8")).join("\n");
const driver = `
const values = {
  "asm-bolt-size": "M10",
  "asm-bolt-grade": "10.9",
  "asm-bolt-condition": "lubricated",
  "asm-bolt-axial": "1500",
  "asm-bolt-shear": "400",
  "asm-bolt-grip": "25",
  "asm-bolt-friction": "0.2",
  "asm-bolt-vibration": "moderate",
};
function fill(overrides) {
  for (const [id, value] of Object.entries({ ...values, ...overrides })) $(id).value = value;
}
function attempt(overrides) {
  fill(overrides);
  try { return assemblyBoltSpec(); } catch (error) { return error.message; }
}
ui.assembly = "arm";
const html = assemblyFormHtml("bolts", { instances: [], motions: [], studies: [], poses: [] });
report = {
  html,
  typed: attempt({}),
  noSize: attempt({ "asm-bolt-size": " " }),
  noAxial: attempt({ "asm-bolt-axial": "" }),
  noGrip: attempt({ "asm-bolt-grip": "" }),
};
ui.assembly = null;
report.noAssembly = attempt({});
done();
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(source + "\n" + driver, sandbox, { filename: "app.js" });
"""


def _probe(tmp_path) -> dict:
    node = shutil.which("node")
    assert node
    probe = tmp_path / "bolt-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    static = ROOT / "src" / "aicad" / "app_static"
    result = subprocess.run(
        [node, str(probe), str(static / "project_path.js"), str(static / "app.js"), str(static / "tools.js")],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_assembly_bolt_check_sends_the_typed_bolt(tmp_path):
    report = _probe(tmp_path)
    for field in ("asm-bolt-size", "asm-bolt-grade", "asm-bolt-condition", "asm-bolt-axial",
                  "asm-bolt-shear", "asm-bolt-grip", "asm-bolt-friction", "asm-bolt-vibration"):
        assert f'id="{field}"' in report["html"]
    assert report["typed"] == {
        "assembly": "arm",
        "size": "M10",
        "grade": "10.9",
        "condition": "lubricated",
        "axial_n": 1500,
        "shear_n": 400,
        "grip_mm": 25,
        "friction": 0.2,
        "vibration": "moderate",
    }
    assert report["noSize"] == "Enter the bolt size, such as M8."
    assert report["noAxial"] == "Enter the axial load in newtons."
    assert report["noGrip"] == "Enter the grip length in millimeters."
    assert report["noAssembly"] == "Select an assembly first."


def test_the_page_no_longer_checks_bolts_written_into_it():
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert "contrast M12" not in script
    assert '$("assembly-bolts").onclick' not in script


def test_the_typed_bolt_is_checked_for_the_assembly(tmp_path):
    workspace = Workspace(tmp_path / "bolted")
    spec = {
        "assembly": "arm",
        "size": "M10",
        "grade": "10.9",
        "condition": "lubricated",
        "axial_n": 1500,
        "shear_n": 400,
        "grip_mm": 25,
        "friction": 0.2,
        "vibration": "moderate",
    }
    checked = workspace.execute("check_bolts", {"spec": spec})
    assert checked["assembly"] == "arm"
    assert checked["size"] == "M10"
    assert checked["grade"] == "10.9"
