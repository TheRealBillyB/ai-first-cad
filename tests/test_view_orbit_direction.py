"""Orbiting the model view works like grabbing the model.

A drag to the right turns the front of the model to the right, and a drag up
tilts it up, as in Fusion, Onshape, and FreeCAD. The horizontal turn used to go
against the mouse. Pan and zoom keep their directions. CI has no browser, so
the real pointer handler from app.js runs in Node, and the test projects the
point on the front of the model onto the screen before and after each drag.
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
const source = fs.readFileSync(process.argv[2], "utf8").replace(/\r\n/g, "\n");

function block(start, end) {
  const from = source.indexOf(start);
  if (from < 0) throw new Error(`app.js no longer has ${start}`);
  const to = source.indexOf(end, from);
  if (to < 0) throw new Error(`app.js: no end for ${start}`);
  return source.slice(from, to + end.length);
}
const fn = (name) => block(`function ${name}(`, "\n}\n");
const line = (start) => block(start, "\n");

const code = [
  block("const view = {", "\n};\n"),
  line("const ORBIT_RADIANS_PER_PIXEL"),
  ...["add", "sub", "dot", "cross", "scaleV", "normalize"].map((name) => line(`function ${name}(`)),
  fn("eyePosition"),
  fn("cameraUp"),
  fn("orbitAfterDrag"),
  fn("onPointerMove"),
  fn("onWheel"),
].join("\n");

const sandbox = { Math, ui: {}, drawSolid: () => {}, out: {} };
vm.createContext(sandbox);
vm.runInContext(code + "\nthis.view = view;", sandbox);
const view = sandbox.view;
const { add, sub, dot, cross, scaleV, normalize } = vm.runInContext("({ add, sub, dot, cross, scaleV, normalize })", sandbox);
const eyePosition = vm.runInContext("eyePosition", sandbox);
const cameraUp = vm.runInContext("cameraUp", sandbox);
const onPointerMove = vm.runInContext("onPointerMove", sandbox);
const onWheel = vm.runInContext("onWheel", sandbox);

function reset() {
  Object.assign(view, { yaw: 0.7, pitch: 0.55, distance: 80, target: [0, 0, 0], direction: null, up: [0, 0, 1], dragging: null });
}

// Where a world point lands on screen: right is +x, up is +y.
function screen(point) {
  const eye = eyePosition();
  const forward = normalize(sub(view.target, eye));
  const right = normalize(cross(forward, cameraUp()));
  const up = cross(right, forward);
  const offset = sub(point, eye);
  const depth = dot(offset, forward);
  return [dot(offset, right) / depth, dot(offset, up) / depth];
}

function front() {
  return add(view.target, scaleV(normalize(sub(eyePosition(), view.target)), 10));
}

function drag(dx, dy, extra = {}) {
  view.dragging = { x: 100, y: 100, yaw: view.yaw, pitch: view.pitch, target: view.target.slice(), moved: false, button: extra.button || 0, mode: "orbit" };
  onPointerMove({ clientX: 100 + dx, clientY: 100 + dy, shiftKey: Boolean(extra.shiftKey), pointerType: extra.pointerType || "mouse" });
  view.dragging = null;
}

const result = {};
for (const pointerType of ["mouse", "pen", "touch"]) {
  reset();
  const point = front();
  const before = screen(point);
  const yaw = view.yaw;
  drag(60, 0, { pointerType });
  result[`right_${pointerType}`] = { before, after: screen(point), yaw_before: yaw, yaw_after: view.yaw };
}

reset();
let point = front();
let before = screen(point);
drag(0, -60);
result.up = { before, after: screen(point) };

reset();
point = front();
before = screen(point);
drag(0, 60);
result.down = { before, after: screen(point) };

reset();
point = front();
before = screen(point);
drag(-60, 0);
result.left = { before, after: screen(point) };

for (const [name, extra] of [["pan_right_button", { button: 2 }], ["pan_shift", { shiftKey: true }]]) {
  reset();
  const yaw = view.yaw;
  const pitch = view.pitch;
  const origin = [0, 0, 0];
  const start = screen(origin);
  drag(60, -40, extra);
  result[name] = { before: start, after: screen(origin), yaw_same: view.yaw === yaw, pitch_same: view.pitch === pitch };
}

reset();
onWheel({ deltaY: 100, preventDefault() {} });
result.wheel_down = view.distance;
reset();
onWheel({ deltaY: -100, preventDefault() {} });
result.wheel_up = view.distance;

process.stdout.write(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def drags(tmp_path_factory):
    node = shutil.which("node")
    assert node, "Node runs the page scripts in these tests"
    folder = tmp_path_factory.mktemp("orbit")
    probe = folder / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    done = subprocess.run([node, str(probe), str(STATIC / "app.js")], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, f"node exited {done.returncode}\nstdout:\n{done.stdout}\nstderr:\n{done.stderr}"
    return json.loads(done.stdout)


@pytest.mark.parametrize("pointer", ["mouse", "pen", "touch"])
def test_a_rightward_drag_turns_the_front_of_the_model_to_the_right(drags, pointer):
    turned = drags[f"right_{pointer}"]
    assert turned["before"][0] == pytest.approx(0, abs=1e-9)
    assert turned["after"][0] > 0.05, turned
    assert abs(turned["after"][1]) < 0.02, turned
    # The camera swings the other way round the model.
    assert turned["yaw_after"] < turned["yaw_before"]


def test_a_leftward_drag_turns_it_to_the_left(drags):
    assert drags["left"]["after"][0] < -0.05


def test_an_upward_drag_tilts_the_front_up_and_a_downward_drag_tilts_it_down(drags):
    assert drags["up"]["after"][1] > 0.05, drags["up"]
    assert drags["down"]["after"][1] < -0.05, drags["down"]
    assert abs(drags["up"]["after"][0]) < 0.02


@pytest.mark.parametrize("name", ["pan_right_button", "pan_shift"])
def test_pan_still_moves_the_model_with_the_mouse_and_does_not_orbit(drags, name):
    panned = drags[name]
    assert panned["after"][0] > panned["before"][0]
    assert panned["after"][1] > panned["before"][1]
    assert panned["yaw_same"] and panned["pitch_same"]


def test_the_wheel_still_zooms_out_when_scrolled_down_and_in_when_scrolled_up(drags):
    assert drags["wheel_down"] == pytest.approx(80 * 1.08)
    assert drags["wheel_up"] == pytest.approx(80 * 0.92)
