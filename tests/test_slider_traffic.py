"""A slider release asks for what it needs once.

A page switch and a refresh each loaded the mesh at the same time. Both asked
with the same old tag, so both read and parsed the new pose. Assembly also
read the hidden shape editor's numbers after every change.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const elements = {};
function make(id) {
  const node = {
    id, value: "", textContent: "", hidden: false, innerHTML: "", className: "", style: {}, dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    setAttribute() {}, removeAttribute() {}, getAttribute() { return ""; }, appendChild() {}, focus() {}, click() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 300 }; },
  };
  elements[id] = node;
  return node;
}
const sandbox = {
  console, setTimeout,
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
  fetch: () => Promise.reject(new Error("no fetch in this probe")),
  WebSocket: function () {},
  requestAnimationFrame() {}, cancelAnimationFrame() {},
};
sandbox.window = sandbox;
vm.createContext(sandbox);
const driver = `
const PIECES = { assembly: "rig", pieces: { link: {
  vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0], normals: [0, 0, 1, 0, 0, 1, 0, 0, 1], triangles: [0, 1, 2], edges: [] } } };
const posed = (dx) => ({ assembly: "rig", instances: [
  { name: "a", part: "link", matrix: [[1, 0, 0, dx], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], origin_mm: [dx, 0, 0], color: [255, 0, 0] },
] });
let pose = { tag: '"pose-1"', body: posed(0) };
const asked = [];
const parsed = [];
const sent = [];
const later = (value) => new Promise((resolve) => setTimeout(() => resolve(value), 5));
apiFetch = async (url, options) => {
  const held = (options && options.headers && options.headers["If-None-Match"]) || null;
  const answer = url.includes("assembly=") ? { tag: '"pieces-1"', body: PIECES } : pose;
  asked.push(url);
  // The server answers a moment later, so two loads overlap as they do in the page.
  return later({
    status: held === answer.tag ? 304 : 200,
    headers: { get: (name) => (name === "ETag" ? answer.tag : null) },
    json: async () => { parsed.push(url); return { ok: true, result: answer.body }; },
  });
};
command = async (name, args) => { sent.push(name); return { placement: { position: [0, 0, 0], rotation_deg: [0, 0, 0] }, parameters: [] }; };
ui.state = { parts: [{ name: "palm" }], sketches: [], panels: {}, revision: 1,
  assemblies: [{ name: "rig", instances: [], joints: [], poses: [], motions: [], studies: [] }] };
ui.part = "palm";
ui.assembly = "rig";
async function probe() {
  const out = {};
  showPage("assembly");
  await loadMesh();
  asked.splice(0); parsed.splice(0); sent.splice(0);
  // A joint changed. Three loads asked at the same moment are one load.
  pose = { tag: '"pose-2"', body: posed(7) };
  await Promise.all([loadMesh(), loadMesh(), loadMesh({ fit: "scene" })]);
  out.together = { asked: asked.splice(0), parsed: parsed.splice(0), x: ui.mesh.positions[0] };
  // Another change. A load asked while one is running waits for it, then sends the new tag.
  pose = { tag: '"pose-3"', body: posed(9) };
  const running = loadMesh();
  await new Promise((resolve) => setTimeout(resolve, 1));
  await Promise.all([running, loadMesh(), loadMesh()]);
  out.overlapping = { asked: asked.splice(0), parsed: parsed.splice(0), x: ui.mesh.positions[0] };
  // Assembly hides the shape editor, so a change there does not read its numbers.
  ui.state = { ...ui.state, revision: 2 };
  await renderShape();
  out.assemblySent = sent.splice(0);
  showPage("model");
  await new Promise((resolve) => setTimeout(resolve, 20));
  out.modelSent = sent.splice(0);
  return out;
}
probe().then((value) => { report = value; done(); }, (error) => { report = { error: String(error && error.stack || error) }; done(); });
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\n" + driver, sandbox, { filename: "app.js" });
"""


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    node = shutil.which("node")
    assert node
    probe = tmp_path_factory.mktemp("page") / "traffic-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(STATIC / "app.js")], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    report = json.loads(result.stdout)
    assert "error" not in report, report
    return report


def test_loads_asked_together_are_one_load(page):
    together = page["together"]
    assert together["asked"] == ["/api/mesh?assembly=rig", "/api/mesh?pose=rig"]
    assert together["parsed"] == ["/api/mesh?pose=rig"]
    assert together["x"] == 7


def test_loads_asked_during_a_load_read_the_new_pose_once(page):
    overlapping = page["overlapping"]
    # The running load reads the new pose. The two asked while it ran become one load,
    # which runs after it, sends the new tag, and gets 304 for both.
    assert overlapping["parsed"] == ["/api/mesh?pose=rig"]
    assert overlapping["asked"] == ["/api/mesh?assembly=rig", "/api/mesh?pose=rig"] * 2
    assert overlapping["x"] == 9


def test_assembly_does_not_read_the_hidden_shape_editor(page):
    assert page["assemblySent"] == []
    # Back in Model, the editor is read once for the change it missed.
    assert page["modelSent"] == ["list_parameters"]
