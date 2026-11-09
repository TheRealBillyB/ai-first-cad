"""A Plan action shows in the sidebar and on Model at once, without polling.

The sidebar summary stopped polling while the Plan page is closed. A Plan
action only read the plan again, and the workspace page skips the page's own
events. Approving a plan and going straight back to Model left the sidebar
saying "draft" and "Modeling stays off until you approve." while the server
said approved.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");

class Node {
  constructor(tag, id) {
    this.tag = tag;
    this.id = id || "";
    this.hidden = false;
    this.children = [];
    this.listeners = {};
    this.classes = new Set();
    this.html = "";
    this.value = "";
    this.textContent = "";
    this.className = "";
    this.dataset = {};
    const self = this;
    this.classList = {
      add: (name) => self.classes.add(name),
      remove: (name) => self.classes.delete(name),
      toggle: (name, on) => (on ? self.classes.add(name) : self.classes.delete(name)),
      contains: (name) => self.classes.has(name),
    };
    this.style = { setProperty() {}, removeProperty() {}, set top(_value) {} };
  }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  append() {}
  replaceChildren() {}
  addEventListener(name, listener) { (this.listeners[name] = this.listeners[name] || []).push(listener); }
  removeEventListener() {}
  contains() { return false; }
  closest() { return null; }
  matches() { return false; }
  focus() {}
  click() {}
  setAttribute() {}
  removeAttribute() {}
  getAttribute() { return ""; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 300, bottom: 96 }; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  set innerHTML(value) {
    this.html = value;
    this.children = [];
    for (const match of value.matchAll(/id="([^"]+)"/g)) this.appendChild(new Node("div", match[1]));
  }
  get innerHTML() { return this.html; }
}

const body = new Node("body");
const header = body.appendChild(new Node("header"));
const nav = header.appendChild(new Node("nav", "page-nav"));
const mount = body.appendChild(new Node("section", "plan-mount"));
mount.appendChild(new Node("div", "plan-body"));
const loose = {};
function everyNode(node = body) {
  return [node, ...node.children.flatMap((child) => everyNode(child))];
}

// The server: one draft plan that approve_plan opens for modeling.
const injected = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
let plans = [{
  id: "shelf", title: "Wall shelf", status: "draft", status_text: "draft", updated_at: "1",
  current_revision: 1, open_questions: 0, modeling: "closed",
}];
const revision = { version: 1, sheets: [], activity: [] };
const plan = () => ({ ...plans[0], modeling_note: "", revisions: [revision], revision });
const sent = [];
let stateReads = 0;
function serve(url, options) {
  if (url === "/api/state") {
    stateReads += 1;
    return { ok: true, result: { ...injected, plans: JSON.parse(JSON.stringify(plans)) } };
  }
  const command = JSON.parse(options.body);
  sent.push(command.name);
  if (command.name === "approve_plan") {
    plans = [{ ...plans[0], status: "approved", status_text: "approved", updated_at: "2", modeling: "open" }];
    return { ok: true, result: { note: "Approved." } };
  }
  return { ok: true, result: plan() };
}
const answer = (payload) => ({
  ok: true, status: 200, headers: { get: () => null }, json: async () => payload,
});

let tick = null;
const sandbox = {
  console, clearTimeout,
  setTimeout: (callback) => setTimeout(callback, 0),
  setInterval(callback) { tick = callback; },
  document: {
    readyState: "complete",
    get activeElement() { return null; },
    body,
    documentElement: { style: { setProperty() {} } },
    createElement: (tag) => new Node(tag),
    getElementById(id) {
      const found = everyNode().find((node) => node.id === id);
      if (found) return found;
      // The plan page makes its own nodes. Any other node the workspace page asks for is a stand-in.
      if (id.includes("plan")) return null;
      return loose[id] || (loose[id] = new Node("div", id));
    },
    querySelector(selector) {
      if (selector === "header") return header;
      if (selector === "header nav") return nav;
      if (selector === "header nav button.on") return null;
      return { getAttribute() { return ""; } };
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  },
  sessionStorage: { getItem() { return null; }, setItem() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  navigator: { userAgent: "test" },
  location: { origin: "http://127.0.0.1:8765", href: "http://127.0.0.1:8765/" },
  fetch: async (url, options) => answer(serve(url, options)),
  WebSocket: function () {},
  requestAnimationFrame() {}, cancelAnimationFrame() {},
  confirm: () => true,
  addEventListener() {},
  // The sketch helpers come from another page script.
  sketchPendingPoint: () => null,
  sketchWithPendingPoint: (sketch) => sketch,
  sketchOpenSpline: () => null,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
// The workspace page, as the browser loads it, then the plan page.
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + `
apiFetch = async (url, options) => fetch(url, options);
loadMesh = async () => {};
renderShape = async () => {};
`, sandbox, { filename: "app.js" });
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), sandbox, { filename: "plan.js" });

const settle = async () => {
  for (let step = 0; step < 20; step += 1) await new Promise((resolve) => setImmediate(resolve));
};
const summary = () => sandbox.document.getElementById("plan-body").html;
(async () => {
  const out = {};
  await vm.runInContext("refresh()", sandbox);
  vm.runInContext("showPage('model')", sandbox);
  await settle();
  out.before = summary();
  // Open the Plan page, approve, and go straight back to Model.
  nav.children.find((node) => node.id === "nav-plan").listeners.click[0]();
  await settle();
  const root = sandbox.document.getElementById("page-plan");
  const approve = new Node("button", "plan-approve");
  root.listeners.click[0]({ target: approve });
  header.listeners.click[0]({ target: { closest: () => ({ id: "nav-model" }) } });
  vm.runInContext("showPage('model')", sandbox);
  await settle();
  out.sent = sent.splice(0);
  out.after = summary();
  out.sharedStatus = vm.runInContext("ui.state.plans[0].status", sandbox);
  out.planHidden = root.hidden;
  // Back on Model, the summary does not poll.
  const reads = stateReads;
  for (let index = 0; index < 3; index += 1) {
    tick();
    await settle();
  }
  out.idleReads = stateReads - reads;
  process.stdout.write(JSON.stringify(out));
})().catch((error) => {
  process.stdout.write(JSON.stringify({ error: String(error && error.stack || error) }));
});
"""


def test_approving_then_returning_to_model_shows_the_approved_plan(tmp_path, monkeypatch):
    from aicad.workspace import Workspace

    node = shutil.which("node")
    assert node
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(Workspace(tmp_path / "project").state()), encoding="utf-8")
    probe = tmp_path / "plan-action.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(STATIC / "app.js"), str(STATIC / "plan.js"), str(state_file)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert "error" not in report, report["error"]
    assert "Modeling stays off until you approve." in report["before"]

    assert "approve_plan" in report["sent"]
    assert report["planHidden"] is True
    # The workspace page read the approved plan, and the sidebar summary shows it.
    assert report["sharedStatus"] == "approved"
    assert "approved" in report["after"]
    assert "Modeling may start from this revision." in report["after"]
    assert "Modeling stays off" not in report["after"]
    # Nothing polls on Model.
    assert report["idleReads"] == 0
