"""The Modes button uses the drill speed that was typed.

A blank, zero, or unreadable box used to become 120 rpm, so the resonance
check ran against a speed nobody entered. A negative speed was refused only
after the whole modal solve.
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
const driver = `
function attempt(text) {
  try { return drillRpm(text); } catch (error) { return error.message; }
}
report = {
  typed: attempt("90"),
  spaced: attempt(" 1500 "),
  fraction: attempt("0.5"),
  empty: attempt(""),
  zero: attempt("0"),
  negative: attempt("-50"),
};
done();
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\n" + driver, sandbox, { filename: "app.js" });
"""


def test_the_drill_speed_is_the_number_typed(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "rpm-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(ROOT / "src" / "aicad" / "app_static" / "app.js")],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    report = json.loads(result.stdout)
    assert report["typed"] == 90
    assert report["spaced"] == 1500
    assert report["fraction"] == 0.5
    assert report["empty"] == "Enter the drill speed in rpm."
    assert report["zero"] == "Drill rpm must be greater than zero."
    assert report["negative"] == "Drill rpm must be greater than zero."
