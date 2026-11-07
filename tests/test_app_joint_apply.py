"""Apply on the Assembly page sets a joint only to a number that was typed.

An empty box used to read as Number("") and moved the joint to zero.
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
const sent = [];
command = async (name, args) => { sent.push([name, args]); return {}; };
refresh = async () => {};
ui.assembly = "arm";
async function attempt(text) {
  try { await applyJoint("shoulder", text); return ""; } catch (error) { return error.message; }
}
async function probe() {
  const empty = await attempt("");
  const blank = await attempt("   ");
  const typed = await attempt("30");
  const zero = await attempt("0");
  const negative = await attempt("-12.5");
  return { empty, blank, typed, zero, negative, sent };
}
probe().then((value) => { report = value; done(); }, (error) => { report = { error: String(error) }; done(); });
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\n" + driver, sandbox, { filename: "app.js" });
"""


def test_an_empty_joint_box_does_not_move_the_joint(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "joint-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(ROOT / "src" / "aicad" / "app_static" / "app.js")],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    report = json.loads(result.stdout)
    assert "error" not in report, report
    assert report["empty"] == "Enter a number for joint shoulder."
    assert report["blank"] == "Enter a number for joint shoulder."
    assert report["typed"] == report["zero"] == report["negative"] == ""
    # Only the three typed numbers reach the server, and zero is still allowed.
    assert report["sent"] == [
        ["set_joint", {"name": "arm", "joint": "shoulder", "value": 30}],
        ["set_joint", {"name": "arm", "joint": "shoulder", "value": 0}],
        ["set_joint", {"name": "arm", "joint": "shoulder", "value": -12.5}],
    ]
