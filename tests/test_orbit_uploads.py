"""An orbit frame sends the GPU nothing it already holds.

Every orbit and pan frame used to make the positions, normals, colors, face
highlight and edge arrays again and send each one with ``gl.bufferData``. On
the 38-part finger rig that is about 257 thousand triangles, and a frame took
about 24 ms of page time before the camera moved. Now each array is made again
only when what it is made from changes, and sent only when it is new.
"""

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
    width: 0, height: 0,
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    setAttribute() {}, removeAttribute() {}, getAttribute() { return ""; }, appendChild() {}, focus() {}, click() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 300 }; },
  };
  node.parentElement = { getBoundingClientRect: node.getBoundingClientRect };
  elements[id] = node;
  return node;
}
make("overlay").hidden = true;

// A stand-in WebGL context. It draws nothing and keeps each bufferData call.
const uploads = [];
let bound = null;
const calls = {
  createBuffer: () => ({}),
  createProgram: () => ({}),
  createShader: () => ({}),
  bindBuffer: (target, buffer) => { bound = buffer; },
  bufferData: (target, data) => { uploads.push({ buffer: bound, data: Array.from(data) }); },
  getAttribLocation: () => 0,
  getUniformLocation: () => ({}),
  getShaderParameter: () => true,
  getProgramParameter: () => true,
};
const gl = new Proxy({}, {
  get(target, name) {
    if (name in calls) return calls[name];
    if (typeof name === "string" && /^[A-Z_0-9]+$/.test(name)) return name;
    return () => {};
  },
});

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
  devicePixelRatio: 1,
  gl, uploads,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
const driver = `
view.gl = gl;
view.program = { name: "solid" };
view.edgeProgram = { name: "edge" };
ui.state = { parts: [{ name: "a" }, { name: "b" }], sketches: [], panels: {}, assemblies: [] };
ui.stage = "solid";
ui.part = "a";
// Two parts, one triangle each, one edge each.
const rig = () => ({
  positions: [0, 0, 0, 1, 0, 0, 0, 1, 0, 5, 0, 0, 6, 0, 0, 5, 1, 0],
  normals: [0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1],
  colors: [255, 0, 0, 255, 0, 0, 255, 0, 0, 0, 0, 255, 0, 0, 255, 0, 0, 255],
  triangle_face: [0, 1],
  triangle_part: ["a", "b"],
  faces: [{ ref: "face/1", part: "a" }, { ref: "face/1", part: "b" }],
  edges: [
    { ref: "edge/1", part: "a", points: [[0, 0, 0], [1, 0, 0]] },
    { ref: "edge/1", part: "b", points: [[5, 0, 0], [6, 0, 0], [6, 1, 0]] },
  ],
  bounding_box: { min: [0, 0, 0], max: [6, 1, 0] },
});
const named = () => new Map(Array.from(view.buffers, ([key, buffer]) => [buffer, key]));
// Draw, as an orbit frame does, and say which buffers were sent what.
function frames(count) {
  uploads.length = 0;
  for (let i = 0; i < count; i += 1) {
    view.yaw += 0.05;
    view.target = [view.target[0] + 0.1, view.target[1], view.target[2]];
    drawSolid();
  }
  const names = named();
  const sent = {};
  uploads.forEach((upload) => {
    const key = names.get(upload.buffer) || "other";
    if (key === "other") return;
    sent[key] = upload.data;
  });
  return { keys: uploads.map((upload) => names.get(upload.buffer) || "other").filter((key) => key !== "other").sort(), sent };
}
const out = {};
ui.mesh = rig();
out.first = frames(1);
out.orbit = frames(60);
ui.selectedFaces = ["face/1"];
out.face = frames(3);
ui.selectedFaces = ["face/1"];
out.sameFace = frames(3);
ui.selectedEdges = ["edge/1"];
out.edge = frames(3);
ui.part = "b";
out.part = frames(3);
ui.live = true;
ui.livePart = "a";
out.live = frames(3);
ui.live = false;
out.liveOff = frames(3);
ui.dragOffset = { part: "b", delta: [10, 0, 0] };
out.drag = frames(1);
ui.dragOffset = { part: "b", delta: [10, 0, 0] };
out.dragHeld = frames(3);
ui.dragOffset = null;
out.dropped = frames(1);
ui.mesh = rig();
out.newMesh = frames(3);
ui.mesh = null;
out.empty = frames(1);
ui.mesh = rig();
out.back = frames(1);
report = out;
`;
vm.runInContext(
  fs.readFileSync(process.argv[2], "utf8") + "\n" + fs.readFileSync(process.argv[3], "utf8") + "\n" + driver,
  sandbox,
  { filename: "app.js" },
);
process.stdout.write(JSON.stringify(sandbox.report));
"""

_ALL = ["edge:aPos", "edge:aSelect", "solid:aColor", "solid:aNormal", "solid:aPos", "solid:aSelect"]


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    node = shutil.which("node")
    assert node
    probe = tmp_path_factory.mktemp("orbit") / "orbit-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(STATIC / "viewport_guides.js"), str(STATIC / "app.js")],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_the_first_draw_sends_every_array(page):
    first = page["first"]
    assert first["keys"] == _ALL
    sent = first["sent"]
    assert sent["solid:aPos"] == [0, 0, 0, 1, 0, 0, 0, 1, 0, 5, 0, 0, 6, 0, 0, 5, 1, 0]
    assert sent["solid:aColor"] == [1, 0, 0] * 3 + [0, 0, 1] * 3
    assert sent["solid:aSelect"] == [0] * 6
    assert sent["edge:aPos"] == [0, 0, 0, 1, 0, 0, 5, 0, 0, 6, 0, 0, 6, 0, 0, 6, 1, 0]
    assert sent["edge:aSelect"] == [0] * 6


def test_orbit_frames_with_nothing_else_changed_send_nothing(page):
    assert page["orbit"]["keys"] == []


def test_a_face_pick_sends_only_the_face_highlight(page):
    face = page["face"]
    assert face["keys"] == ["solid:aSelect"]
    # Only the selected part's face lights up, not the other part's face of the same name.
    assert face["sent"]["solid:aSelect"] == [1, 1, 1, 0, 0, 0]


def test_the_same_selection_in_a_new_list_sends_nothing(page):
    assert page["sameFace"]["keys"] == []


def test_an_edge_pick_sends_only_the_edge_highlight(page):
    edge = page["edge"]
    assert edge["keys"] == ["edge:aSelect"]
    assert edge["sent"]["edge:aSelect"] == [1, 1, 0, 0, 0, 0]


def test_another_part_moves_both_highlights(page):
    part = page["part"]
    assert part["keys"] == ["edge:aSelect", "solid:aSelect"]
    assert part["sent"]["solid:aSelect"] == [0, 0, 0, 1, 1, 1]
    assert part["sent"]["edge:aSelect"] == [0, 0, 1, 1, 1, 1]


def test_live_mode_lights_its_part_and_goes_off_again(page):
    assert page["live"]["keys"] == ["solid:aSelect"]
    assert page["live"]["sent"]["solid:aSelect"] == pytest.approx([0.55] * 3 + [1] * 3)
    assert page["liveOff"]["keys"] == ["solid:aSelect"]
    assert page["liveOff"]["sent"]["solid:aSelect"] == [0, 0, 0, 1, 1, 1]


def test_a_drag_moves_only_the_dragged_part_and_its_edges(page):
    drag = page["drag"]
    assert drag["keys"] == ["edge:aPos", "solid:aPos"]
    assert drag["sent"]["solid:aPos"] == [0, 0, 0, 1, 0, 0, 0, 1, 0, 15, 0, 0, 16, 0, 0, 15, 1, 0]
    assert drag["sent"]["edge:aPos"] == [0, 0, 0, 1, 0, 0, 15, 0, 0, 16, 0, 0, 16, 0, 0, 16, 1, 0]
    # The same offset again, as a held mouse sends it, sends nothing.
    assert page["dragHeld"]["keys"] == []
    dropped = page["dropped"]
    assert dropped["keys"] == ["edge:aPos", "solid:aPos"]
    assert dropped["sent"]["solid:aPos"][9] == 5


def test_a_new_mesh_sends_every_array_once(page):
    assert page["newMesh"]["keys"] == _ALL


def test_a_mesh_back_after_an_empty_view_is_sent_again(page):
    assert page["empty"]["keys"] == []
    assert page["back"]["keys"] == _ALL
