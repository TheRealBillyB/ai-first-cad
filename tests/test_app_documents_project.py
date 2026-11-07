"""The Documents editor holds a document of the project that is open.

Opening another project used to leave the old project's text in the editor,
and Save then wrote it over the new project's file of the same name.
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
const files = {
  "C:/one": { "decisions.md": "# One", "requirements.md": "# One needs" },
  "C:/two": { "decisions.md": "# Two keeps this", "requirements.md": "# Two needs" },
};
const written = [];
command = async (name, args) => {
  const folder = ui.state.project.path;
  if (name === "read_document") return { markdown: files[folder][args.name] };
  if (name === "write_document") { written.push([folder, args.name, args.markdown]); return {}; }
  return {};
};
refresh = async () => {};
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const docs = ["decisions.md", "requirements.md"];
async function probe() {
  ui.page = "documents";
  ui.state = { project: { path: "C:/one" }, documents: docs };
  let refused = "";
  try { await saveDoc(); } catch (error) { refused = error.message; }
  await loadDoc("decisions.md");
  const first = elements["doc-editor"].value;

  ui.page = "model";
  ui.state = { project: { path: "C:/two" }, documents: docs };
  forgetDocumentOfOtherProject();
  const cleared = elements["doc-editor"].value;
  let stale = "";
  try { await saveDoc(); } catch (error) { stale = error.message; }

  ui.page = "documents";
  ui.state = { project: { path: "C:/one" }, documents: docs };
  await loadDoc("decisions.md");
  ui.state = { project: { path: "C:/two" }, documents: docs };
  forgetDocumentOfOtherProject();
  await settle();
  const reloaded = elements["doc-editor"].value;
  elements["doc-editor"].value = reloaded + " and an edit";
  await saveDoc();
  return { refused, first, cleared, stale, reloaded, written };
}
probe().then((value) => { report = value; done(); }, (error) => { report = { error: String(error) }; done(); });
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\n" + driver, sandbox, { filename: "app.js" });
"""


def test_opening_another_project_drops_the_old_document_before_save(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "documents-probe.js"
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
    # Save before any document is read writes nothing.
    assert "Pick one from the list" in report["refused"]
    assert report["first"] == "# One"
    # The other project's text leaves the editor, and Save refuses it.
    assert report["cleared"] == ""
    assert "Pick one from the list" in report["stale"]
    # On the Documents page the same document is read from the new project.
    assert report["reloaded"] == "# Two keeps this"
    assert report["written"] == [["C:/two", "decisions.md", "# Two keeps this and an edit"]]
