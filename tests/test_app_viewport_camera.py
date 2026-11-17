"""The 3D view turns on a turntable, plays a camera path, and records itself.

A turntable turns about the model's centre at the person's distance and
height. A path passes through the stored views and turns the short way
between them. A recording of one turn, or of the path, stops when the move
ends and is saved as MP4 where the browser can write it, else WebM.
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
const clicks = [];
const copies = [];
function make(id, value) {
  const node = {
    id,
    value: value || "",
    textContent: "",
    hidden: false,
    disabled: false,
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
    removeAttribute() {},
    appendChild() {},
    focus() {},
    click() { clicks.push({ href: this.href, download: this.download }); },
    getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; },
  };
  elements[id] = node;
  return node;
}
class FakeRecorder {
  constructor(stream, options) {
    this.options = options;
    this.mimeType = options.mimeType;
    this.state = "inactive";
    FakeRecorder.made.push(this);
  }
  static isTypeSupported(type) { return FakeRecorder.supported.includes(type); }
  start() { this.state = "recording"; }
  stop() {
    this.state = "inactive";
    this.ondataavailable({ data: { size: 3 } });
    this.onstop();
  }
}
FakeRecorder.made = [];
FakeRecorder.supported = ["video/mp4;codecs=avc1", "video/mp4", "video/webm"];
const sandbox = {
  console,
  setTimeout() {},
  document: {
    getElementById(id) { return elements[id] || make(id); },
    querySelector() { return { getAttribute() { return ""; } }; },
    querySelectorAll() { return []; },
    addEventListener() {},
    createElement(tag) {
      const node = make(tag);
      if (tag === "canvas") {
        node.getContext = () => ({ drawImage(source, x, y, w, h) { copies.push([source.id, w, h]); } });
        node.captureStream = () => ({ stream: true });
      }
      return node;
    },
  },
  sessionStorage: { getItem() { return null; }, setItem() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  navigator: { userAgent: "test" },
  location: { origin: "http://127.0.0.1:8765", href: "http://127.0.0.1:8765/" },
  fetch: () => Promise.reject(new Error("no")),
  WebSocket: function () {},
  performance: { now: () => 0 },
  requestAnimationFrame() { return 7; },
  cancelAnimationFrame() {},
  MediaRecorder: FakeRecorder,
  Blob: class { constructor(parts, options) { this.type = options.type; } },
  URL: { createObjectURL: () => "blob:clip", revokeObjectURL() {} },
};
sandbox.window = sandbox;
sandbox.FakeRecorder = FakeRecorder;
sandbox.clicks = clicks;
sandbox.copies = copies;
vm.createContext(sandbox);
const source = process.argv.slice(2).map((path) => fs.readFileSync(path, "utf8")).join("\n");
const driver = `
const out = {};
$("view").width = 1281;
$("view").height = 721;
ui.mesh = { positions: [0], bounding_box: { min: [0, 0, 0], max: [100, 40, 20] } };
ui.viewAssembly = "clamp vise";
view.yaw = 0.5; view.pitch = 0.3; view.distance = 250; view.target = [0, 0, 0];
bindCameraControls();
startTurntable();
stepCamera(3000);
out.turn = { mode: cameraMotion.mode, yaw: view.yaw, target: view.target, distance: view.distance, pitch: view.pitch, label: $("camera-turntable").textContent };
stopCameraMotion();
out.stopped = { mode: cameraMotion.mode, label: $("camera-turntable").textContent };

view.target = [0, 0, 0];
applyCamera({ yaw: 3.0, pitch: 0.2, distance: 100, target: [0, 0, 0] });
addCameraKey();
applyCamera({ yaw: -3.0, pitch: 0.6, distance: 300, target: [10, 0, 0] });
addCameraKey();
out.playLabel = $("camera-play").textContent;
playCameraPath();
out.pathStart = { yaw: view.yaw, distance: view.distance };
stepCamera(1250);
out.pathMiddle = { yaw: view.yaw, distance: view.distance, mode: cameraMotion.mode };
stepCamera(2600);
out.pathEnd = { yaw: view.yaw, distance: view.distance, target: view.target, mode: cameraMotion.mode };

view.yaw = 1.0;
$("camera-record-what").value = "turn";
$("camera-record").onclick();
out.recording = { type: FakeRecorder.made[0].mimeType, label: $("camera-record").textContent, mode: cameraMotion.mode, size: [cameraMotion.copy.width, cameraMotion.copy.height] };
copies.length = 0;
copyDrawnView($("view"));
out.copied = copies.slice();
stepCamera(12001);
out.recorded = { yaw: view.yaw, mode: cameraMotion.mode, recorder: cameraMotion.recorder === null, clicks: clicks.slice(), readout: $("camera-readout").textContent };

FakeRecorder.supported = ["video/webm"];
$("camera-record-what").value = "free";
$("camera-record").onclick();
$("camera-record").onclick();
out.webm = { type: FakeRecorder.made[1].mimeType, download: clicks[clicks.length - 1].download, readout: $("camera-readout").textContent };

clearCameraKeys();
$("camera-record-what").value = "path";
$("camera-record").onclick();
out.noPath = $("camera-readout").textContent;
globalThis.report = out;
done();
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(source + "\n" + driver, sandbox, { filename: "app.js" });
"""


def _report(tmp_path) -> dict:
    node = shutil.which("node")
    assert node
    probe = tmp_path / "camera-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    static = ROOT / "src" / "aicad" / "app_static"
    result = subprocess.run(
        [node, str(probe), str(static / "project_path.js"), str(static / "app.js"), str(static / "viewport_camera.js")],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_turntable_turns_about_the_model_centre_and_keeps_distance_and_height(tmp_path):
    report = _report(tmp_path)
    turn = report["turn"]
    assert turn["mode"] == "turntable"
    # 3 s of a 12 s turn is a quarter turn.
    assert abs(turn["yaw"] - (0.5 + 3.14159265 / 2)) < 1e-6
    assert turn["target"] == [50, 20, 10]
    assert turn["distance"] == 250 and turn["pitch"] == 0.3
    assert turn["label"] == "Stop turning"
    assert report["stopped"] == {"mode": None, "label": "Turntable"}


def test_a_path_passes_through_the_views_the_short_way(tmp_path):
    report = _report(tmp_path)
    assert report["playLabel"] == "Play path (2)"
    assert report["pathStart"] == {"yaw": 3.0, "distance": 100}
    middle = report["pathMiddle"]
    assert middle["mode"] == "path"
    # From 3.0 to -3.0 the short way is through pi, not back through 0.
    assert 3.0 < middle["yaw"] < 3.0 + 2 * 3.14159265 - 6.0 + 1e-9
    assert 100 < middle["distance"] < 300
    end = report["pathEnd"]
    assert end["mode"] is None
    assert abs(end["yaw"] - (-3.0 + 2 * 3.14159265358979)) < 1e-6
    assert end["distance"] == 300 and end["target"] == [10, 0, 0]


def test_recording_one_turn_stops_with_the_turn_and_saves_an_mp4(tmp_path):
    report = _report(tmp_path)
    assert report["recording"] == {
        "type": "video/mp4;codecs=avc1",
        "label": "Stop recording",
        "mode": "turntable",
        # The view's size, rounded down to even numbers for H.264.
        "size": [1280, 720],
    }
    # Each drawn view is copied into the recorded canvas, scaled to its size.
    assert report["copied"] == [["view", 1280, 720]]
    recorded = report["recorded"]
    assert abs(recorded["yaw"] - (1.0 + 2 * 3.14159265358979)) < 1e-6
    assert recorded["mode"] is None and recorded["recorder"] is True
    assert recorded["clicks"] == [{"href": "blob:clip", "download": "clamp_vise_view.mp4"}]
    assert recorded["readout"] == "Saved clamp_vise_view.mp4 to the browser's downloads."


def test_a_browser_without_mp4_saves_webm_and_says_so(tmp_path):
    webm = _report(tmp_path)["webm"]
    assert webm["type"] == "video/webm"
    assert webm["download"] == "clamp_vise_view.webm"
    assert "This browser records WebM. Chrome, Edge, and Safari record MP4." in webm["readout"]


def test_recording_a_path_needs_two_views(tmp_path):
    assert _report(tmp_path)["noPath"] == "Add at least two views to record a path."


def test_the_page_loads_the_camera_script_after_app_js():
    page = (ROOT / "src" / "aicad" / "app_static" / "index.html").read_text(encoding="utf-8")
    assert page.index("/static/app.js") < page.index("/static/viewport_camera.js")
    assert 'id="camera-record"' in page
