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
}));
"""


def test_the_sketch_canvas_uses_the_solved_sketch(tmp_path):
    page = (ROOT / "src" / "aicad" / "app_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert page.index("/static/sketch_geom.js") < page.index("/static/app.js")
    assert "sketchSolvedPoint" in script
    assert "sketchSelectionAfter" in script
    assert "sketchArcSamples" in script
    assert "sketchProfile" in script
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
