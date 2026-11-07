"""Save as starts in the folder that holds the project.

The dialog used to start inside the open project. Save as refuses a copy
inside the project, so OK with the suggested folder and name always failed.
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
    showModal() {},
    close() {},
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
const parents = { "C:/work/bracket": "C:/work", "C:/work": "C:/" };
let listed = [];
command = async (name, args) => {
  if (name === "list_folders") {
    listed.push(args.path || "");
    return { path: args.path, parent: parents[args.path] || null, folders: [] };
  }
  return { projects: [] };
};
async function probe() {
  ui.state = { project: { path: "C:/work/bracket", name: "bracket" } };
  await browseProjects("save");
  const save = { listed, body: elements["modal-body"].innerHTML };
  listed = [];
  await browseProjects("open");
  const open = { listed, body: elements["modal-body"].innerHTML };
  return { save, open };
}
probe().then((value) => { report = value; done(); }, (error) => { report = { error: String(error) }; done(); });
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\n" + driver, sandbox, { filename: "app.js" });
"""


def test_save_as_lists_the_folder_beside_the_project_and_open_lists_the_project(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "save-as-probe.js"
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
    save = report["save"]
    assert save["listed"][-1] == "C:/work"
    assert 'id="folder-path" value="C:/work"' in save["body"]
    assert 'id="save-name" value="bracket-copy"' in save["body"]
    open_ = report["open"]
    assert open_["listed"] == ["C:/work/bracket"]
    assert 'id="folder-path" value="C:/work/bracket"' in open_["body"]


def test_the_suggested_save_as_folder_and_name_make_a_copy(tmp_path):
    from aicad.workspace import Workspace

    project = tmp_path / "bracket"
    workspace = Workspace(project)
    workspace.execute(
        "create_part_from_ops",
        {"name": "plate", "ops": [{"op": "add", "shape": "box", "size": [10, 10, 2]}]},
    )
    listed = workspace.execute("list_folders", {"path": str(project)})
    saved = workspace.execute("save_project_as", {"path": listed["parent"], "name": "bracket-copy"})
    assert Path(saved["path"]) == (tmp_path / "bracket-copy").resolve()
    assert (tmp_path / "bracket-copy" / "session.json").is_file()
