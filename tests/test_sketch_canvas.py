"""The workspace sketch canvas uses the solved sketch.

A click is a screen distance, not a fixed number of millimetres. A second
click keeps the first selection so a distance or a coincident constraint has
two entities. A circle radius uses the solved point. An arc is the
counterclockwise sweep. A closed profile is kept when a longer open chain is
also in the sketch.
"""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEOM = ROOT / "src" / "aicad" / "app_static" / "sketch_geom.js"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "sketch_geom.js" });

const tolerance = sandbox.sketchSnapTolerance(4);
const pointsOnly = sandbox.sketchPointHit(
  { points: { a: { x: 0, y: 0 }, b: { x: 100, y: 0 } } },
  1.5,
  0,
  tolerance,
);
const missed = sandbox.sketchPointHit(
  { points: { a: { x: 0, y: 0 } } },
  3,
  0,
  tolerance,
);
const line = sandbox.sketchEntityHit(
  {
    points: { a: { x: 0, y: 0 }, b: { x: 100, y: 0 } },
    lines: { bottom: { start: { x: 0, y: 0 }, end: { x: 100, y: 0 } } },
  },
  50,
  0.5,
  tolerance,
);
const endpoint = sandbox.sketchEntityHit(
  {
    points: { a: { x: 0, y: 0 } },
    lines: { bottom: { start: { x: 0, y: 0 }, end: { x: 100, y: 0 } } },
  },
  0,
  0.4,
  tolerance,
);
const circle = sandbox.sketchEntityHit(
  {
    points: { center: { x: 0, y: 0 } },
    circles: { hole: { center: { x: 0, y: 0 }, radius: 10 } },
  },
  10,
  0,
  tolerance,
);
const pair = sandbox.sketchSelectionAfter(
  sandbox.sketchSelectionAfter([], "a", false),
  "b",
  false,
);
const replaced = sandbox.sketchSelectionAfter(pair, "c", false);
const toggled = sandbox.sketchSelectionAfter(pair, "a", true);
const cleared = sandbox.sketchSelectionAfter(pair, null, false);
const solved = sandbox.sketchSolvedPoint(
  { entities: [{ id: "p", type: "point", at: [0, 0] }] },
  { points: { p: { x: 40, y: 0 } } },
  "p",
);
const stale = Math.hypot(40 - 0, 10 - 0);
const radius = Math.hypot(40 - solved[0], 10 - solved[1]);
const quarter = sandbox.sketchArcSamples(
  {
    center: { x: 0, y: 0 },
    start: { x: 10, y: 0 },
    end: { x: 0, y: 10 },
  },
  8,
);
const mid = quarter[4];
const rectangle = {
  entities: [
    { id: "p0", type: "point", at: [0, 0] },
    { id: "p1", type: "point", at: [40, 0] },
    { id: "p2", type: "point", at: [40, 20] },
    { id: "p3", type: "point", at: [0, 20] },
    { id: "q0", type: "point", at: [0, 0] },
    { id: "q1", type: "point", at: [5, 0] },
    { id: "q2", type: "point", at: [10, 1] },
    { id: "q3", type: "point", at: [15, 0] },
    { id: "q4", type: "point", at: [20, 1] },
    { id: "bottom", type: "line", start: "p0", end: "p1" },
    { id: "right", type: "line", start: "p1", end: "p2" },
    { id: "top", type: "line", start: "p2", end: "p3" },
    { id: "left", type: "line", start: "p3", end: "p0" },
    { id: "s0", type: "line", start: "q0", end: "q1" },
    { id: "s1", type: "line", start: "q1", end: "q2" },
    { id: "s2", type: "line", start: "q2", end: "q3" },
    { id: "s3", type: "line", start: "q3", end: "q4" },
  ],
};
const profile = sandbox.sketchProfile(rectangle);
const blank = { plane: "xy", entities: [{ id: "origin", type: "point", at: [0, 0] }], constraints: [] };
const started = sandbox.sketchLineClick(blank, { points: { origin: { x: 0, y: 0 } } }, null, 12, 4, 2);
const joined = sandbox.sketchLineClick(
  started.spec,
  { points: { origin: { x: 0, y: 0 }, [started.lineStart]: { x: 12, y: 4 } } },
  started.lineStart,
  30,
  4,
  2,
);
const lost = sandbox.sketchLineClick(blank, { points: { origin: { x: 0, y: 0 } } }, "missing", 8, 8, 2);
const same = sandbox.sketchLineClick(
  started.spec,
  { points: { origin: { x: 0, y: 0 }, [started.lineStart]: { x: 12, y: 4 } } },
  started.lineStart,
  12,
  4,
  2,
);
const center = sandbox.sketchCircleClick(blank, { points: { origin: { x: 0, y: 0 } } }, null, 5, 0, 2);
const drawn = sandbox.sketchCircleClick(
  center.spec,
  { points: { origin: { x: 0, y: 0 }, [center.circleCenter]: { x: 5, y: 0 } } },
  center.circleCenter,
  5,
  8,
  2,
);
process.stdout.write(JSON.stringify({
  tolerance,
  pointsOnly,
  missed,
  line,
  endpoint,
  circle,
  pair,
  replaced,
  toggled,
  cleared,
  solved,
  stale,
  radius,
  mid,
  profile,
  started: {
    commit: started.commit,
    finished: started.finished,
    id: started.lineStart,
    count: started.spec.entities.length,
  },
  joined: {
    commit: joined.commit,
    finished: joined.finished,
    lineStart: joined.lineStart,
    line: joined.spec.entities[joined.spec.entities.length - 1],
  },
  lost: { commit: lost.commit, finished: lost.finished, id: lost.lineStart },
  same: { commit: same.commit, finished: same.finished, count: same.spec.entities.length },
  center: { commit: center.commit, id: center.circleCenter },
  drawn: {
    commit: drawn.commit,
    center: drawn.circleCenter,
    entity: drawn.spec.entities[drawn.spec.entities.length - 1],
    name: drawn.spec.constraints[0] && drawn.spec.constraints[0].name,
    value: drawn.spec.constraints[0] && drawn.spec.constraints[0].value,
  },
}));
"""


def test_the_sketch_canvas_uses_the_solved_sketch(tmp_path):
    page = (ROOT / "src" / "aicad" / "app_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert page.index("/static/sketch_geom.js") < page.index("/static/app.js")
    assert "sketchSolvedPoint" in GEOM.read_text(encoding="utf-8")
    assert "sketchSelectionAfter" in script
    assert "sketchArcSamples" in script
    assert "sketchProfile" in GEOM.read_text(encoding="utf-8")
    assert "sketchProfile" in script
    assert "sketchSolidSteps" in script
    assert "sketchLineClick" in script
    assert "sketchCircleClick" in script
    assert 'uniqueConstraintName(spec, "distance")' in script
    assert 'uniqueConstraintName(spec, "diameter")' in script
    node = shutil.which("node")
    assert node
    probe = tmp_path / "sketch-canvas.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(GEOM)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["tolerance"] == 2
    assert report["pointsOnly"] == "a"
    assert report["missed"] is None
    assert report["line"] == "bottom"
    assert report["endpoint"] == "a"
    assert report["circle"] == "hole"
    assert report["pair"] == ["a", "b"]
    assert report["replaced"] == ["c"]
    assert report["toggled"] == ["b"]
    assert report["cleared"] == []
    assert report["solved"] == [40, 0]
    assert report["radius"] == 10
    assert report["stale"] > 40
    assert abs(report["mid"][0] - 7.0710678118654755) < 1e-6
    assert abs(report["mid"][1] - 7.0710678118654755) < 1e-6
    assert report["profile"] == ["p0", "p1", "p2", "p3"]
    assert report["started"]["commit"] is True
    assert report["started"]["finished"] is False
    assert report["started"]["count"] == 2
    assert report["joined"]["finished"] is True
    assert report["joined"]["lineStart"] is None
    assert report["joined"]["line"]["type"] == "line"
    assert report["joined"]["line"]["start"] == report["started"]["id"]
    assert report["lost"]["finished"] is False
    assert report["lost"]["id"] != "missing"
    assert report["same"]["finished"] is False
    assert report["same"]["count"] == report["started"]["count"]
    assert report["center"]["commit"] is True
    assert report["drawn"]["commit"] is True
    assert report["drawn"]["center"] is None
    assert report["drawn"]["entity"]["type"] == "circle"
    assert report["drawn"]["entity"]["center"] == report["center"]["id"]
    assert report["drawn"]["value"] == 16
    assert report["drawn"]["name"].endswith("_diameter")


_STEPS = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "sketch_geom.js" });
const plate = {
  entities: [
    { id: "p0", type: "point", at: [0, 0] },
    { id: "p1", type: "point", at: [40, 0] },
    { id: "p2", type: "point", at: [40, 20] },
    { id: "p3", type: "point", at: [0, 20] },
    { id: "c", type: "circle", center: "o", radius: 3 },
    { id: "bottom", type: "line", start: "p0", end: "p1" },
    { id: "right", type: "line", start: "p1", end: "p2" },
    { id: "top", type: "line", start: "p2", end: "p3" },
    { id: "left", type: "line", start: "p3", end: "p0" },
  ],
};
const extruded = sandbox.sketchSolidSteps("extrude", plate, 4, null);
const bare = sandbox.sketchSolidSteps("cut", plate, 4, null);
const cut = sandbox.sketchSolidSteps("cut", plate, 4, extruded.steps);
const again = sandbox.sketchSolidSteps("cut", plate, 4, cut.steps);
process.stdout.write(JSON.stringify({ extruded, bare, cut, again }));
"""


def test_a_sketch_cut_keeps_the_solid_it_cuts(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "sketch-steps.js"
    probe.write_text(_STEPS, encoding="utf-8")
    result = subprocess.run([node, str(probe), str(GEOM)], check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert [step["op"] for step in report["extruded"]["steps"]] == ["extrude", "cut"]
    assert report["extruded"]["steps"][1]["z"] == -1
    assert report["extruded"]["steps"][1]["height"] == 6
    assert "solid" in report["bare"]["error"].lower()
    assert [step["op"] for step in report["cut"]["steps"]] == ["extrude", "cut", "cut"]
    assert report["cut"]["steps"][0]["points"] == ["p0", "p1", "p2", "p3"]
    assert report["cut"]["steps"][2]["circle"] == "c"
    assert report["cut"]["steps"][2]["name"] == "cut"
    assert report["again"]["steps"][-1]["name"] == "cut2"
