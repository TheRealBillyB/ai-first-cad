"""An assistant's command does not move the person off the page they are on.

Live mode used to follow every framed edit by switching to the Model page,
which shows every part at its own origin. On the Assembly page that piled
the parts together and dropped the assembly view. Live now follows only on
the Model page's solid view, and on a command that opens another project.
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
vm.createContext(sandbox);
const source = process.argv.slice(2).map((path) => fs.readFileSync(path, "utf8")).join("\n");
const driver = `
const calls = [];
refresh = (options) => { calls.push(options); return Promise.resolve(); };
const edit = { source: "mcp", command: "update_part", summary: "Edited bracket", parts: ["bracket"], follow: true, frame: "part" };
const undo = { source: "mcp", command: "undo", summary: "Undo", parts: [], follow: true, frame: "scene" };
const opened = { source: "mcp", command: "open_project", summary: "Opened vise", parts: [], follow: true, frame: "scene" };
function run(page, stage, viewAssembly, event) {
  calls.length = 0;
  ui.page = page;
  ui.stage = stage;
  ui.viewAssembly = viewAssembly;
  handleLiveEvent(event);
  const options = calls[0];
  return { page: ui.page, focus: options.focus || null, fit: options.fit, solid: options.solid };
}
ui.live = true;
report = {
  assemblyEdit: run("assembly", "solid", "vise", edit),
  assemblyUndo: run("assembly", "solid", "vise", undo),
  assemblyOpen: run("assembly", "solid", "vise", opened),
  documentsEdit: run("documents", "solid", null, edit),
  sketchEdit: run("model", "sketch", null, edit),
  modelEdit: run("model", "solid", null, edit),
  modelUndo: run("model", "solid", null, undo),
};
ui.live = false;
report.liveOff = run("model", "solid", null, edit);
done();
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(source + "\n" + driver, sandbox, { filename: "app.js" });
"""


def _report(tmp_path) -> dict:
    node = shutil.which("node")
    assert node
    probe = tmp_path / "live-probe.js"
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


def test_the_assembly_page_stays_on_an_assistant_edit_and_undo(tmp_path):
    report = _report(tmp_path)
    for key in ("assemblyEdit", "assemblyUndo"):
        assert report[key] == {"page": "assembly", "focus": None, "fit": None, "solid": False}, key


def test_documents_and_a_sketch_stay_put(tmp_path):
    report = _report(tmp_path)
    assert report["documentsEdit"] == {"page": "documents", "focus": None, "fit": None, "solid": False}
    assert report["sketchEdit"] == {"page": "model", "focus": None, "fit": None, "solid": False}


def test_the_model_view_still_follows_the_edited_part(tmp_path):
    report = _report(tmp_path)
    assert report["modelEdit"] == {"page": "model", "focus": "bracket", "fit": "part", "solid": True}
    assert report["modelUndo"] == {"page": "model", "focus": None, "fit": "scene", "solid": True}


def test_opening_another_project_goes_to_its_model(tmp_path):
    report = _report(tmp_path)
    assert report["assemblyOpen"]["solid"] is True
    assert report["assemblyOpen"]["fit"] == "scene"


def test_live_off_never_follows(tmp_path):
    assert _report(tmp_path)["liveOff"] == {"page": "model", "focus": None, "fit": None, "solid": False}
