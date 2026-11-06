"""Clicking a report picture opens it over the app at full size, zoomable, and closable.

CI has no browser, so a small stand-in for the page runs the real
picture_viewer.js in Node: open, close (X, Esc, click outside), wheel and
pinch zoom, and drag.
"""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");

class Node {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parent = null;
    this.attributes = {};
    this.style = {};
    this.listeners = {};
    this.hidden = false;
    this.className = "";
    this.textContent = "";
    this.complete = false;
  }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  removeAttribute(name) { delete this.attributes[name]; }
  set src(value) { this.setAttribute("src", value); }
  get src() { return this.getAttribute("src") || ""; }
  addEventListener(name, listener, options) {
    const capture = options === true || Boolean(options && options.capture);
    (this.listeners[name] = this.listeners[name] || []).push({ listener, capture });
  }
  closest(selector) {
    for (let node = this; node; node = node.parent) {
      if (selector === "img" && node.tagName === "IMG") return node;
      if (selector.startsWith(".") && String(node.className).split(" ").includes(selector.slice(1))) return node;
    }
    return null;
  }
  focus() { document.activeElement = this; }
}

const document = new Node("#document");
document.body = document.appendChild(new Node("body"));
document.createElement = (tag) => new Node(tag);
document.activeElement = document.body;
const window = { innerWidth: 1000, innerHeight: 800, addEventListener() {} };

// Capture from the document down, then bubble back up, as a browser does.
function dispatch(target, type, fields = {}) {
  const path = [];
  for (let node = target; node; node = node.parent) path.push(node);
  let stopped = false;
  const event = {
    type, target, defaultPrevented: false, ...fields,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { stopped = true; },
  };
  const run = (node, capture) => {
    for (const entry of node.listeners[type] || []) {
      if (entry.capture === capture && !stopped) entry.listener.call(node, event);
    }
  };
  for (const node of [...path].reverse()) { if (!stopped) run(node, true); }
  for (const node of path) { if (!stopped) run(node, false); }
  return event;
}

const sandbox = { document, window, Math, Map, String, Number, Boolean };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "picture_viewer.js" });
const viewerOf = vm.runInContext("installPictureViewer(document, window)", sandbox);

const panel = document.body.appendChild(new Node("div"));
panel.className = "panel";
const report = panel.appendChild(new Node("img"));
report.setAttribute("src", "/api/file?path=reports%2Fstress.png&token=t");
report.alt = "von Mises";
const icon = panel.appendChild(new Node("img"));
icon.setAttribute("src", "/static/favicon.png");

let appSawEscape = false;
document.addEventListener("keydown", (event) => { if (event.key === "Escape") appSawEscape = true; });

const out = {};
const snapshot = () => {
  const viewer = viewerOf();
  if (!viewer) return null;
  const overlay = viewer.element;
  const [picture, close] = overlay.children;
  return {
    open: viewer.isOpen(),
    hidden: overlay.hidden,
    role: overlay.getAttribute("role"),
    modal: overlay.getAttribute("aria-modal"),
    src: picture.getAttribute("src"),
    alt: picture.alt,
    view: viewer.view(),
    fit: viewer.fit(),
    transform: picture.style.transform || "",
    focusIsClose: document.activeElement === close,
    focusIsReport: document.activeElement === report,
  };
};
const parts = () => {
  const [picture, close, hint] = viewerOf().element.children;
  return { overlay: viewerOf().element, picture, close, hint };
};
const openReport = () => {
  dispatch(report, "click", { clientX: 5, clientY: 5 });
  const { picture } = parts();
  picture.naturalWidth = 500;
  picture.naturalHeight = 400;
  picture.onload();
};

dispatch(icon, "click");
out.iconOpened = snapshot();

openReport();
out.opened = snapshot();
out.closeLabel = parts().close.getAttribute("aria-label");
out.closeText = parts().close.textContent;

dispatch(parts().close, "click");
out.afterX = snapshot();

openReport();
dispatch(document.body, "keydown", { key: "Escape" });
out.afterEscape = snapshot();
out.appSawEscape = appSawEscape;
appSawEscape = false;
dispatch(document.body, "keydown", { key: "Escape" });
out.appSawEscapeWhenClosed = appSawEscape;

openReport();
dispatch(parts().picture, "click");
out.afterClickOnPicture = snapshot();
dispatch(parts().overlay, "click");
out.afterClickOutside = snapshot();

openReport();
dispatch(parts().hint, "click");
out.afterClickOnHint = snapshot();

// Wheel zoom about a point keeps that point of the picture under the pointer.
openReport();
out.beforeWheel = snapshot().view;
const wheel = dispatch(parts().picture, "wheel", { deltaY: -100, clientX: 300, clientY: 250 });
out.wheelPrevented = wheel.defaultPrevented;
out.afterWheelIn = snapshot().view;
for (let i = 0; i < 60; i++) dispatch(parts().overlay, "wheel", { deltaY: -100, clientX: 300, clientY: 250 });
out.afterWheelMax = snapshot().view;
for (let i = 0; i < 80; i++) dispatch(parts().overlay, "wheel", { deltaY: 100, clientX: 300, clientY: 250 });
out.afterWheelMin = snapshot().view;
out.transformAfterWheel = snapshot().transform;

// A drag moves the picture, and its release is not a click outside.
const before = snapshot().view;
dispatch(parts().picture, "pointerdown", { pointerId: 1, clientX: 100, clientY: 100 });
dispatch(parts().overlay, "pointermove", { pointerId: 1, clientX: 150, clientY: 130 });
dispatch(parts().overlay, "pointerup", { pointerId: 1, clientX: 150, clientY: 130 });
dispatch(parts().overlay, "click", { clientX: 150, clientY: 130 });
out.dragFrom = before;
out.afterDrag = snapshot();

// Two fingers moving apart zoom in about the point between them.
dispatch(parts().picture, "pointerdown", { pointerId: 7, clientX: 400, clientY: 400 });
dispatch(parts().picture, "pointerdown", { pointerId: 8, clientX: 600, clientY: 400 });
out.beforePinch = snapshot().view;
dispatch(parts().overlay, "pointermove", { pointerId: 8, clientX: 800, clientY: 400 });
out.afterPinch = snapshot().view;
dispatch(parts().overlay, "pointerup", { pointerId: 7 });
dispatch(parts().overlay, "pointerup", { pointerId: 8 });

// Reopening starts fitted again.
dispatch(parts().close, "click");
openReport();
out.reopened = snapshot();
out.overlays = document.body.children.filter((node) => node.className === "picture-viewer").length;

process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    node = shutil.which("node")
    assert node, "Node runs the page scripts in these tests"
    folder = tmp_path_factory.mktemp("picture_viewer")
    probe = folder / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    done = subprocess.run(
        [node, str(probe), str(STATIC / "picture_viewer.js")], capture_output=True, text=True, encoding="utf-8"
    )
    assert done.returncode == 0, f"node exited {done.returncode}\nstdout:\n{done.stdout}\nstderr:\n{done.stderr}"
    return json.loads(done.stdout)


# 500 x 400 in a 1000 x 800 window, with a margin.
FIT = min(1000 * 0.94 / 500, 800 * 0.88 / 400, 2)


def test_clicking_a_report_picture_opens_it_fitted_to_the_window(run):
    opened = run["opened"]
    assert opened["open"] and not opened["hidden"]
    assert opened["src"] == "/api/file?path=reports%2Fstress.png&token=t"
    assert opened["alt"] == "von Mises"
    assert (opened["role"], opened["modal"]) == ("dialog", "true")
    assert opened["fit"] == pytest.approx(FIT)
    assert opened["view"] == pytest.approx({"scale": FIT, "x": (1000 - 500 * FIT) / 2, "y": (800 - 400 * FIT) / 2})
    assert opened["transform"].startswith("translate(") and f"scale({FIT})" in opened["transform"]
    assert opened["focusIsClose"]
    assert (run["closeLabel"], run["closeText"]) == ("Close picture", "×")


def test_a_picture_that_is_not_a_report_does_not_open(run):
    assert run["iconOpened"] is None


def test_the_x_button_closes_it_and_returns_focus(run):
    closed = run["afterX"]
    assert not closed["open"] and closed["hidden"]
    assert closed["src"] is None
    assert closed["focusIsReport"]


def test_esc_closes_it_and_nothing_behind_it_sees_the_key(run):
    assert not run["afterEscape"]["open"]
    assert run["appSawEscape"] is False
    assert run["appSawEscapeWhenClosed"] is True


def test_a_click_outside_the_picture_closes_it_and_a_click_on_it_does_not(run):
    assert run["afterClickOnPicture"]["open"]
    assert not run["afterClickOutside"]["open"]
    assert not run["afterClickOnHint"]["open"]


def test_the_wheel_zooms_about_the_pointer_within_limits(run):
    before, after = run["beforeWheel"], run["afterWheelIn"]
    assert run["wheelPrevented"]
    assert after["scale"] == pytest.approx(before["scale"] * math.exp(0.15))
    # The picture point under the pointer stays under it.
    for axis, pointer in (("x", 300), ("y", 250)):
        assert (pointer - after[axis]) / after["scale"] == pytest.approx((pointer - before[axis]) / before["scale"])
    assert run["afterWheelMax"]["scale"] == pytest.approx(FIT * 8)
    assert run["afterWheelMin"]["scale"] == pytest.approx(FIT)
    assert f"scale({run['afterWheelMin']['scale']})" in run["transformAfterWheel"]


def test_a_drag_moves_it_and_its_release_does_not_close_it(run):
    start, moved = run["dragFrom"], run["afterDrag"]
    assert moved["open"]
    assert moved["view"]["x"] == pytest.approx(start["x"] + 50)
    assert moved["view"]["y"] == pytest.approx(start["y"] + 30)
    assert moved["view"]["scale"] == pytest.approx(start["scale"])


def test_a_pinch_zooms_about_the_point_between_the_fingers(run):
    before, after = run["beforePinch"], run["afterPinch"]
    assert after["scale"] == pytest.approx(before["scale"] * 2)
    middle = (600, 400)
    assert (middle[0] - after["x"]) / after["scale"] == pytest.approx((middle[0] - before["x"]) / before["scale"])
    assert (middle[1] - after["y"]) / after["scale"] == pytest.approx((middle[1] - before["y"]) / before["scale"])


def test_reopening_starts_fitted_and_reuses_one_overlay(run):
    reopened = run["reopened"]
    assert reopened["open"]
    assert reopened["view"]["scale"] == pytest.approx(FIT)
    assert run["overlays"] == 1


def test_the_page_installs_it_at_start_and_every_panel_picture_qualifies():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert page.index("/static/picture_viewer.js") < page.index("/static/app.js")
    boot = script[script.index("async function boot()") :]
    assert boot.index("installPictureViewer(document, window);") < boot.index("initView();")
    # Report pictures come from fileUrl, which is what the viewer looks for.
    assert 'return "/api/file?path=" + encodeURIComponent(' in script
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".picture-viewer[hidden] { display: none; }" in css
    assert 'img[src^="/api/file?"] { cursor: zoom-in; }' in css
