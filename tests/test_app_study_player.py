"""A motion study plays in the 3D view, not only as a panel.

The study's steps say where each instance sits. The player places the
assembly's pieces at each step, draws an instance in a collision red, and
keeps the stored pose to go back to.
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
    setAttribute(name, value) { this[name] = value; },
    getAttribute() { return ""; },
    removeAttribute() {},
    appendChild() {},
    focus() {},
    click() {},
  };
  elements[id] = node;
  return node;
}
let clock = 0;
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
  performance: { now: () => clock },
  requestAnimationFrame() { return 1; },
  cancelAnimationFrame() {},
};
sandbox.window = sandbox;
sandbox.setClock = (value) => { clock = value; };
vm.createContext(sandbox);
const source = process.argv.slice(2).map((path) => fs.readFileSync(path, "utf8")).join("\n");
const driver = `
const eye = (x) => [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
const triangle = { vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0], normals: [0, 0, 1, 0, 0, 1, 0, 0, 1], triangles: [0, 1, 2], edges: [] };
const pieces = { pieces: { base: triangle, slider: triangle } };
const pose = {
  assembly: "rig",
  instances: [
    { name: "base", part: "base", matrix: eye(0), color: [100, 100, 100], origin_mm: [0, 0, 0] },
    { name: "slider", part: "slider", matrix: eye(0), color: [50, 60, 70], origin_mm: [0, 0, 0] },
  ],
};
const steps = [0, 1, 2].map((index) => ({
  index,
  time_s: index * 0.5,
  poses: { base: { matrix: eye(0), origin_mm: [0, 0, 0] }, slider: { matrix: eye(10 * index), origin_mm: [10 * index, 0, 0] } },
  collisions: index === 2 ? [{ a: "slider", b: "base" }] : [],
  limits: [],
}));
const studyRun = { assembly: "rig", study: { name: "push" }, status: "collision", duration_s: 1, steps, collisions: [], limit_hits: [] };
ui.page = "assembly";
ui.viewAssembly = "rig";
ui.mesh = assembledMesh(pieces, pose);
ui.mesh._key = "rig";
showStudyReport(studyRun);
const out = { html: $("assembly-report").innerHTML.includes('id="study-play"') };
const sliderX = () => ui.mesh.positions[9];
const sliderRed = () => ui.mesh.colors[9];
$("study-step").oninput({ target: { value: "1" } });
out.step1 = { x: sliderX(), red: sliderRed(), readout: $("study-readout").textContent, pose: ui.mesh._pose === pose };
$("study-step").oninput({ target: { value: "2" } });
out.step2 = { x: sliderX(), red: sliderRed(), readout: $("study-readout").textContent };
$("study-rest").onclick();
out.rest = { x: sliderX(), red: sliderRed(), readout: $("study-readout").textContent };
setClock(1000);
$("study-play").onclick();
out.playing = { on: studyPlayer.playing, label: $("study-play").textContent, x: sliderX() };
tickStudy(1000 + 600);
out.tick = { index: studyPlayer.index, x: sliderX() };
tickStudy(1000 + 1600);
out.loop = { index: studyPlayer.index };
$("study-play").onclick();
out.paused = { on: studyPlayer.playing, label: $("study-play").textContent };
ui.page = "model";
$("study-step").oninput({ target: { value: "1" } });
out.otherPage = { readout: $("study-readout").textContent };
globalThis.report = out;
done();
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(source + "\n" + driver, sandbox, { filename: "app.js" });
"""


def _report(tmp_path) -> dict:
    node = shutil.which("node")
    assert node
    probe = tmp_path / "study-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    static = ROOT / "src" / "aicad" / "app_static"
    result = subprocess.run(
        [node, str(probe), str(static / "project_path.js"), str(static / "app.js"), str(static / "tools.js")],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_report_has_a_player_and_a_step_places_the_pieces(tmp_path):
    report = _report(tmp_path)
    assert report["html"] is True
    assert report["step1"]["x"] == 10
    assert report["step1"]["red"] == 50
    assert report["step1"]["readout"].startswith("Step 2 of 3 · t = 0.50 s")
    # A refresh that reads the same stored pose keeps the step on screen.
    assert report["step1"]["pose"] is True


def test_a_colliding_step_draws_the_pair_red_and_names_it(tmp_path):
    step = _report(tmp_path)["step2"]
    assert step["x"] == 20
    assert step["red"] == 214
    assert "collision slider × base" in step["readout"]


def test_stored_pose_goes_back(tmp_path):
    rest = _report(tmp_path)["rest"]
    assert rest["x"] == 0 and rest["red"] == 50
    assert rest["readout"].startswith("Stored pose")


def test_play_runs_on_the_study_clock_and_loops(tmp_path):
    report = _report(tmp_path)
    assert report["playing"] == {"on": True, "label": "Pause", "x": 0}
    # 0.5 s a step: 0.6 s in is step 2, 1.6 s in is step 4, which loops to step 1.
    assert report["tick"] == {"index": 1, "x": 10}
    assert report["loop"] == {"index": 0}
    assert report["paused"] == {"on": False, "label": "Play"}


def test_the_player_needs_the_assembly_view(tmp_path):
    assert _report(tmp_path)["otherPage"]["readout"] == "Open this assembly's view to play the study."
