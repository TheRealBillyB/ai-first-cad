"""Opening the Plan page leaves the page buttons on screen."""

import json
import shutil
import subprocess
from pathlib import Path

PLAN = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static" / "plan.js"
PAGE = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static" / "index.html"

# The real plan.js against a small stand-in for the page. A selector the
# stand-in does not know fails the test, so it cannot quietly match nothing.
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
    this.styles = {};
    const self = this;
    this.classList = {
      add: (name) => self.classes.add(name),
      remove: (name) => self.classes.delete(name),
      toggle: (name, on) => (on ? self.classes.add(name) : self.classes.delete(name)),
    };
    this.style = {
      setProperty: (name, value) => { self.styles[name] = value; },
      removeProperty: (name) => { delete self.styles[name]; },
      set top(value) { self.styles.top = value; },
    };
  }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  addEventListener(name, listener) { (this.listeners[name] = this.listeners[name] || []).push(listener); }
  closest(tag) { return this.tag === tag ? this : null; }
  getBoundingClientRect() { return { bottom: 96 }; }
  set innerHTML(_value) {}
}

const body = new Node("body");
const header = body.appendChild(new Node("header"));
const nav = header.appendChild(new Node("nav", "page-nav"));
const navModel = nav.appendChild(new Node("button", "nav-model"));
navModel.classes.add("on");
const navAssembly = nav.appendChild(new Node("button", "nav-assembly"));
const open = header.appendChild(new Node("button", "open-project"));
// Model and Assembly are one page: the Assembly button shows page-model in assembly mode.
const model = body.appendChild(new Node("div", "page-model"));

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
    if (selector === "header nav button.on") return nav.children.find((item) => item.classes.has("on")) || null;
    if (selector.startsWith("meta")) return null;
    throw new Error("unknown selector " + selector);
  },
  querySelectorAll(selector) {
    if (selector === "header nav button") return nav.children;
    if (selector === "[id^='page-']") return everyNode().filter((node) => node.id.startsWith("page-"));
    if (selector === "body > [id^='page-']") return body.children.filter((node) => node.id.startsWith("page-"));
    throw new Error("unknown selector " + selector);
  },
};

const sandbox = {
  document: documentStandIn,
  window: { addEventListener() {} },
  fetch: () => new Promise(() => {}),
  setInterval() {},
  setTimeout() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "plan.js" });

function click(button) {
  (button.listeners.click || []).forEach((listener) => listener({ target: button }));
  if (button.onPageClick) button.onPageClick();
  (header.listeners.click || []).forEach((listener) => listener({ target: button }));
}

// What showPage does for a page button in app.js.
navAssembly.onPageClick = () => {
  navModel.classes.delete("on");
  navAssembly.classes.add("on");
};

const snapshot = () => ({
  navDisplay: nav.styles.display || null,
  modelDisplay: model.styles.display || null,
  planHidden: documentStandIn.getElementById("page-plan").hidden,
  planTop: documentStandIn.getElementById("page-plan").styles.top || null,
  marked: nav.children.filter((item) => item.classes.has("on")).map((item) => item.id),
});

const planButton = documentStandIn.getElementById("nav-plan");
const steps = {};
click(planButton);
steps.opened = snapshot();
click(open);
steps.leftWithOpen = snapshot();
click(planButton);
click(navAssembly);
steps.leftWithPageButton = snapshot();
process.stdout.write(JSON.stringify(steps));
"""


def _steps(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "plan-nav.js"
    probe.write_text(_PROBE, encoding="utf-8")
    answer = subprocess.run([node, str(probe), str(PLAN)], check=True, capture_output=True, text=True)
    return json.loads(answer.stdout)


def test_the_page_buttons_stay_when_plan_opens(tmp_path):
    opened = _steps(tmp_path)["opened"]
    assert opened["navDisplay"] is None
    assert opened["modelDisplay"] == "none"
    assert opened["planHidden"] is False
    assert opened["marked"] == ["nav-plan"]


def test_the_plan_page_starts_below_a_wrapped_header(tmp_path):
    assert _steps(tmp_path)["opened"]["planTop"] == "96px"


def test_leaving_with_open_marks_the_page_you_came_from(tmp_path):
    left = _steps(tmp_path)["leftWithOpen"]
    assert left["planHidden"] is True
    assert left["modelDisplay"] is None
    assert left["marked"] == ["nav-model"]


def test_leaving_with_a_page_button_marks_only_that_page(tmp_path):
    left = _steps(tmp_path)["leftWithPageButton"]
    assert left["planHidden"] is True
    assert left["modelDisplay"] is None
    assert left["marked"] == ["nav-assembly"]


def test_each_page_is_a_direct_child_of_the_body():
    """The Plan page hides `body > [id^='page-']`, so a page nested deeper would stay visible."""
    page = PAGE.read_text(encoding="utf-8")
    for name in ("page-model", "page-docs"):
        assert f'\n  <div id="{name}"' in page
    # The assembly is a mode of page-model, not a page of its own.
    assert 'id="page-assembly"' not in page
