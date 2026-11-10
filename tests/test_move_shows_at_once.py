"""A moved part is drawn in its new place at once, and stays there until the model answers.

Letting go of a dragged part dropped the drag's shift without drawing again, so
the part looked moved only because nothing drew. Any frame before the moved
mesh arrived, such as an orbit, put it back where it had been. A typed position
showed nothing until the server answered, 0.8 s later on a 38-part finger rig.
Now the shift is held until the moved mesh is drawn, and dropped at once when
the move fails.
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
// The placement editor's six number fields.
const form = { place: ["0", "0", "0"], spin: ["0", "0", "0"] };
make("shape-editor").querySelector = (selector) => {
  const found = /data-(place|spin)="(\d)"/.exec(selector);
  return found ? { value: form[found[1]][Number(found[2])] } : null;
};

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
  gl, uploads, form,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
const driver = `
view.gl = gl;
view.program = { name: "solid" };
view.edgeProgram = { name: "edge" };
// Two parts, one triangle and one edge each. Part b starts at x = 5 + bx.
const rig = (bx = 0) => ({
  positions: [0, 0, 0, 1, 0, 0, 0, 1, 0, 5 + bx, 0, 0, 6 + bx, 0, 0, 5 + bx, 1, 0],
  normals: [0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1],
  colors: [255, 0, 0, 255, 0, 0, 255, 0, 0, 0, 0, 255, 0, 0, 255, 0, 0, 255],
  triangle_face: [0, 1],
  triangle_part: ["a", "b"],
  faces: [{ ref: "face/1", part: "a" }, { ref: "face/1", part: "b" }],
  edges: [
    { ref: "edge/1", part: "a", points: [[0, 0, 0], [1, 0, 0]] },
    { ref: "edge/1", part: "b", points: [[5 + bx, 0, 0], [6 + bx, 0, 0]] },
  ],
  bounding_box: { min: [0, 0, 0], max: [6 + bx, 1, 0] },
});
const named = () => new Map(Array.from(view.buffers, ([key, buffer]) => [buffer, key]));
// Where part b's first corner and its edge start were sent to the GPU since the last look.
function sent() {
  const names = named();
  const out = { keys: [], bx: null, edge: null };
  uploads.forEach((upload) => {
    const key = names.get(upload.buffer);
    if (!key) return;
    out.keys.push(key);
    if (key === "solid:aPos") out.bx = upload.data[9];
    if (key === "edge:aPos") out.edge = upload.data[6];
  });
  out.keys.sort();
  uploads.length = 0;
  return out;
}
// An orbit frame: the camera turns and the solid is drawn again.
function orbit() {
  view.yaw += 0.05;
  drawSolid();
  return sent();
}
function start(placement = { position: [0, 0, 0], rotation_deg: [0, 0, 0] }) {
  ui.page = "model";
  ui.stage = "solid";
  ui.viewAssembly = null;
  ui.part = "b";
  ui.state = { revision: 7, parts: [{ name: "a" }, { name: "b", placement }], sketches: [], panels: {}, assemblies: [] };
  ui.parameters = { placement };
  ui.parameterKey = "b:7";
  ui.dragOffset = null;
  ui.mesh = rig();
  drawSolid();
  sent();
}
// The server, as the test plays it: each command waits until the test answers it.
let pending = [];
let asked = [];
command = (name, args) => {
  asked.push(name);
  if (name === "list_parameters") return Promise.resolve(ui.parameters);
  return new Promise((resolve, reject) => pending.push({ name, args, resolve, reject }));
};
let arriving = null;
refresh = async () => {
  if (arriving) ui.mesh = arriving;
  arriving = null;
  drawSolid();
};
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
// Drag part b by ten millimetres along X and let go.
function dragAndDrop() {
  pending = [];
  view.dragging = { x: 0, y: 0, moved: true, button: 0, mode: "move", part: "b", origin: [0, 0, 0], rotation: [0, 0, 0] };
  ui.dragOffset = { part: "b", delta: [10, 0, 0] };
  drawSolid();
  const during = sent();
  const done = onPointerUp({ clientX: 0, clientY: 0 });
  return { during, done };
}
function type(place, spin = ["0", "0", "0"]) {
  form.place = place;
  form.spin = spin;
  pending = [];
  asked = [];
  return commitPlacement();
}

main = (async () => {
  const out = {};

  // A drag let go, an orbit before the server answers, then the moved mesh.
  start();
  let drop = dragAndDrop();
  out.dragging = drop.during;
  out.letGo = sent();
  out.orbitWhileSending = orbit();
  out.sentPosition = pending[0].args.position;
  arriving = rig(10);
  pending[0].resolve({});
  await drop.done;
  out.movedMesh = sent();
  out.heldAfter = ui.dragOffset;
  out.orbitAfter = orbit();

  // The move fails.
  start();
  drop = dragAndDrop();
  sent();
  pending[0].reject(new Error("That position is past the limit."));
  out.failed = await drop.done.then(() => "moved", (error) => error.message);
  out.afterFailure = sent();
  out.heldAfterFailure = ui.dragOffset;

  // The move is taken, and the same mesh stays on screen.
  start();
  drop = dragAndDrop();
  sent();
  pending[0].resolve({});
  await drop.done;
  out.sameMesh = sent();

  // Another change puts a new mesh on screen before the move is answered.
  start();
  drop = dragAndDrop();
  sent();
  ui.mesh = rig(0);
  drawSolid();
  out.otherMesh = sent();
  arriving = rig(10);
  pending[0].resolve({});
  await drop.done;
  out.otherMeshThenMoved = sent();

  // A typed position that keeps the part's turn.
  start();
  let typed = type(["12", "0", "0"]);
  await settle();
  out.typed = sent();
  out.typedOrbit = orbit();
  arriving = rig(12);
  pending[0].resolve({});
  await typed;
  out.typedMoved = sent();
  out.typedOrbitAfter = orbit();

  // A typed position on a part that is turned keeps the turn too.
  start({ position: [3, 0, 0], rotation_deg: [0, 0, 90] });
  typed = type(["7.5", "0", "0"], ["0", "0", "90"]);
  await settle();
  out.typedTurned = sent();
  arriving = rig(4.5);
  pending[0].resolve({});
  await typed;

  // A typed turn is more than a shift, so the page waits for the model.
  start();
  typed = type(["12", "0", "0"], ["0", "0", "45"]);
  await settle();
  out.typedTurn = sent();
  out.typedTurnOrbit = orbit();
  arriving = rig(12);
  pending[0].resolve({});
  await typed;
  out.typedTurnMoved = sent();

  // A mounted part gives up its mount when a position is typed.
  start({ position: [0, 0, 0], rotation_deg: [0, 0, 0], mount: { part: "a", ref: "face/1" } });
  typed = type(["12", "0", "0"]);
  await settle();
  out.typedMounted = sent();
  pending[0].resolve({});
  await typed;

  // The editor holds another revision's numbers.
  start();
  ui.parameterKey = "b:6";
  typed = type(["12", "0", "0"]);
  await settle();
  out.typedStale = sent();
  pending[0].resolve({});
  await typed;

  // A typed position the server refuses.
  start();
  typed = type(["2000000", "0", "0"]);
  await settle();
  out.typedRefusedShown = sent();
  pending[0].reject(new Error("That position is past the limit."));
  out.typedRefused = await typed.then(() => "moved", (error) => error.message);
  out.typedRefusedAfter = sent();
  out.typedRefusedHeld = ui.dragOffset;
  out.typedRefusedAsked = asked.slice();

  report = out;
})();
`;
vm.runInContext(
  fs.readFileSync(process.argv[2], "utf8") + "\n" + fs.readFileSync(process.argv[3], "utf8") + "\n" + driver,
  sandbox,
  { filename: "app.js" },
);
sandbox.main.then(
  () => process.stdout.write(JSON.stringify(sandbox.report)),
  (error) => { console.error(error); process.exit(1); },
);
"""

_MOVED = ["edge:aPos", "solid:aPos"]
_ALL = ["edge:aPos", "edge:aSelect", "solid:aColor", "solid:aNormal", "solid:aPos", "solid:aSelect"]


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    node = shutil.which("node")
    assert node
    probe = tmp_path_factory.mktemp("move") / "move-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(STATIC / "viewport_guides.js"), str(STATIC / "app.js")],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_a_part_let_go_stays_where_it_was_dropped_while_the_move_is_sent(page):
    assert page["dragging"] == {"keys": _MOVED, "bx": 15, "edge": 15}
    # Letting go draws nothing new, and an orbit frame before the answer leaves the part there.
    assert page["letGo"]["keys"] == []
    assert page["orbitWhileSending"]["keys"] == []
    assert page["sentPosition"] == [10, 0, 0]


def test_the_moved_mesh_is_drawn_as_the_server_sent_it(page):
    # The server's mesh has the part at 15. A shift left on would draw it at 25.
    assert page["movedMesh"] == {"keys": _ALL, "bx": 15, "edge": 15}
    assert page["heldAfter"] is None
    assert page["orbitAfter"]["keys"] == []


def test_a_move_that_fails_puts_the_part_back_at_once(page):
    assert page["failed"] == "That position is past the limit."
    assert page["afterFailure"] == {"keys": _MOVED, "bx": 5, "edge": 5}
    assert page["heldAfterFailure"] is None


def test_a_move_that_leaves_the_same_mesh_draws_the_part_where_the_model_has_it(page):
    assert page["sameMesh"] == {"keys": _MOVED, "bx": 5, "edge": 5}


def test_a_mesh_from_another_change_is_not_shifted(page):
    assert page["otherMesh"] == {"keys": _ALL, "bx": 5, "edge": 5}
    assert page["otherMeshThenMoved"] == {"keys": _ALL, "bx": 15, "edge": 15}


def test_a_typed_position_is_drawn_before_the_server_answers(page):
    assert page["typed"] == {"keys": _MOVED, "bx": 17, "edge": 17}
    assert page["typedOrbit"]["keys"] == []
    assert page["typedMoved"] == {"keys": _ALL, "bx": 17, "edge": 17}
    assert page["typedOrbitAfter"]["keys"] == []


def test_a_typed_position_on_a_turned_part_shifts_it_from_where_it_is(page):
    # The part stood at 3 and goes to 7.5, so it is drawn 4.5 further along.
    assert page["typedTurned"] == {"keys": _MOVED, "bx": 9.5, "edge": 9.5}


def test_a_typed_turn_waits_for_the_model(page):
    assert page["typedTurn"]["keys"] == []
    assert page["typedTurnOrbit"]["keys"] == []
    assert page["typedTurnMoved"] == {"keys": _ALL, "bx": 17, "edge": 17}


def test_a_typed_position_on_a_mounted_part_waits_for_the_model(page):
    assert page["typedMounted"]["keys"] == []


def test_numbers_from_another_revision_are_not_drawn_from(page):
    assert page["typedStale"]["keys"] == []


def test_a_typed_position_the_server_refuses_goes_back_at_once(page):
    assert page["typedRefusedShown"]["bx"] == 2000005
    assert page["typedRefused"] == "That position is past the limit."
    assert page["typedRefusedAfter"] == {"keys": _MOVED, "bx": 5, "edge": 5}
    assert page["typedRefusedHeld"] is None
    # The editor is filled again from the model, so it does not keep the refused number.
    assert page["typedRefusedAsked"] == ["place_part", "list_parameters"]
