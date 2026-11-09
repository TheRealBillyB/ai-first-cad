"""The plan summary and the plan list are drawn again only when they change, without polling.

Both were rewritten every 2.5 seconds, which replaced the "Open the plan"
button and the plan rows under the pointer. A click that straddled a redraw
was lost, and keyboard focus fell off the button. A project with no plans also
kept the previous project's summary in the sidebar.
"""

import json
import shutil
import subprocess
from pathlib import Path

PLAN = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static" / "plan.js"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");

const writes = {};
class Node {
  constructor(tag, id) {
    this.tag = tag;
    this.id = id || "";
    this.hidden = false;
    this.children = [];
    this.listeners = {};
    this.classes = new Set();
    this.html = "";
    const self = this;
    this.classList = {
      add: (name) => self.classes.add(name),
      remove: (name) => self.classes.delete(name),
      toggle: (name, on) => (on ? self.classes.add(name) : self.classes.delete(name)),
    };
    this.style = { setProperty() {}, removeProperty() {}, set top(_value) {} };
  }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  addEventListener(name, listener) { (this.listeners[name] = this.listeners[name] || []).push(listener); }
  contains() { return false; }
  getBoundingClientRect() { return { bottom: 96 }; }
  querySelector() { return null; }
  set innerHTML(value) {
    this.html = value;
    writes[this.id] = (writes[this.id] || 0) + 1;
    this.children = [];
    for (const match of value.matchAll(/id="([^"]+)"/g)) this.appendChild(new Node("div", match[1]));
  }
  get innerHTML() { return this.html; }
}

const body = new Node("body");
const header = body.appendChild(new Node("header"));
const nav = header.appendChild(new Node("nav", "page-nav"));
const mount = body.appendChild(new Node("section", "plan-mount"));
const slot = mount.appendChild(new Node("div", "plan-body"));

function everyNode(node = body) {
  return [node, ...node.children.flatMap((child) => everyNode(child))];
}

const documentStandIn = {
  readyState: "complete",
  get activeElement() { return null; },
  createElement: (tag) => new Node(tag),
  getElementById: (id) => everyNode().find((node) => node.id === id) || null,
  body,
  querySelector(selector) {
    if (selector === "header") return header;
    if (selector === "header nav") return nav;
    if (selector === "header nav button.on") return null;
    if (selector.startsWith("meta")) return null;
    throw new Error("unknown selector " + selector);
  },
  querySelectorAll() { return []; },
};

let plans = [{
  id: "shelf", title: "Wall shelf", status: "draft", status_text: "draft", updated_at: "1",
  current_revision: 1, open_questions: 2, modeling: "closed",
}];
const plan = {
  id: "shelf", title: "Wall shelf", status: "draft", status_text: "draft", current_revision: 1,
  modeling: "closed", modeling_note: "", revisions: [{ version: 1, sheets: [], activity: [] }],
  revision: { version: 1, sheets: [], activity: [] },
};
const reply = (result) => Promise.resolve({ json: () => Promise.resolve({ ok: true, result }) });
let tick = null;
let stateReads = 0;
const sandbox = {
  document: documentStandIn,
  window: { addEventListener() {}, confirm: () => false },
  fetch: (url) => {
    if (url !== "/api/state") return reply(plan);
    stateReads += 1;
    return reply({ plans });
  },
  setInterval(callback) { tick = callback; },
  setTimeout(callback) { callback(); },
  console,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "plan.js" });

const settle = () => new Promise((resolve) => setImmediate(resolve));
(async () => {
  for (let index = 0; index < 5; index += 1) await settle();
  const first = { ...writes };
  const readsAtStart = stateReads;
  // With the Plan page closed, the timer does not read the state.
  for (let index = 0; index < 3; index += 1) {
    tick();
    for (let step = 0; step < 5; step += 1) await settle();
  }
  const polled = stateReads - readsAtStart;
  const button = documentStandIn.getElementById("plan-mount-open");
  // The workspace page hands over the plans from each state it reads.
  for (let index = 0; index < 3; index += 1) {
    sandbox.window.aicadPlanSummaries(plans);
    for (let step = 0; step < 5; step += 1) await settle();
  }
  const steady = { ...writes };
  const same = documentStandIn.getElementById("plan-mount-open") === button;
  plans = [];
  sandbox.window.aicadPlanSummaries(plans);
  for (let step = 0; step < 5; step += 1) await settle();
  process.stdout.write(JSON.stringify({ first, steady, same, polled, emptied: slot.html }));
})();
"""


def test_an_unchanged_plan_summary_is_not_drawn_again(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "plan-mount.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run([node, str(probe), str(PLAN)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["first"]["plan-body"] == 1
    assert report["first"]["plan-rows"] == 1
    # The sidebar summary does not poll. Only the open Plan page asks every 2.5 seconds.
    assert report["polled"] == 0
    # Three more hand-overs with the same plans leave both alone.
    assert report["steady"]["plan-body"] == 1
    assert report["steady"]["plan-rows"] == 1
    assert report["same"] is True
    # A project with no plans shows that in the sidebar too.
    assert "Nothing planned yet" in report["emptied"]


_APP_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const elements = {};
function make(id) {
  const node = {
    id, value: "", textContent: "", hidden: false, innerHTML: "", className: "", style: {}, dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    setAttribute() {}, removeAttribute() {}, getAttribute() { return ""; }, appendChild() {}, append() {},
    replaceChildren() {}, focus() {}, click() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 300 }; },
  };
  elements[id] = node;
  return node;
}
const handed = [];
const sandbox = {
  console, setTimeout, clearTimeout,
  document: {
    getElementById(id) { return elements[id] || make(id); },
    querySelector() { return { getAttribute() { return ""; } }; },
    querySelectorAll() { return []; },
    addEventListener() {},
    createElement() { return make("el"); },
    documentElement: { style: { setProperty() {} } },
  },
  sessionStorage: { getItem() { return null; }, setItem() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  navigator: { userAgent: "test" },
  location: { origin: "http://127.0.0.1:8765", href: "http://127.0.0.1:8765/" },
  fetch: () => Promise.reject(new Error("no fetch in this probe")),
  WebSocket: function () {},
  requestAnimationFrame() {}, cancelAnimationFrame() {},
  aicadPlanSummaries: (plans) => handed.push(plans),
  // The sketch helpers come from another page script.
  sketchPendingPoint: () => null,
  sketchWithPendingPoint: (sketch) => sketch,
  sketchOpenSpline: () => null,
};
sandbox.window = sandbox;
sandbox.injectedState = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
vm.createContext(sandbox);
const driver = `
const STATE = injectedState;
apiFetch = async () => ({ status: 200, headers: { get: () => null }, json: async () => ({ ok: true, result: STATE }) });
loadMesh = async () => {};
renderShape = async () => {};
ui.page = "documents";
refresh().then(() => { done(); }, (error) => { failure = String(error && error.stack || error); done(); });
`;
sandbox.done = () => process.stdout.write(JSON.stringify({ handed, failure: sandbox.failure || null }));
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\n" + driver, sandbox, { filename: "app.js" });
"""


def test_the_workspace_page_hands_each_state_s_plans_to_the_plan_panel(tmp_path, monkeypatch):
    from aicad.workspace import Workspace

    node = shutil.which("node")
    assert node
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    state = Workspace(tmp_path / "project").state()
    plans = [{"id": "shelf", "title": "Wall shelf", "status": "draft", "current_revision": 1}]
    state["plans"] = plans
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    probe = tmp_path / "app-plans.js"
    probe.write_text(_APP_PROBE, encoding="utf-8")
    app = PLAN.parent / "app.js"
    result = subprocess.run([node, str(probe), str(app), str(state_file)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["failure"] is None, report["failure"]
    assert report["handed"] == [plans]
