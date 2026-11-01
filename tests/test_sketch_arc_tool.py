"""Arc tools: three points, center-start-end, and a tangent continuation.

The canvas snaps to a point or a grid intersection, keeps an unfinished arc
across a refresh, and writes the same entities the solver accepts.
"""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError
from aicad.kernel import solid_from_sketch
from aicad.sketch import Sketch, solve_sketch
from aicad.sketch_arc import circle_through, tangent_continuation

ROOT = Path(__file__).resolve().parents[1]
GEOM = ROOT / "src" / "aicad" / "app_static" / "sketch_geom.js"


def test_a_three_point_arc_follows_the_circle_and_its_radius():
    sketch = (
        Sketch()
        .point("a", 10, 0)
        .point("b", 0, 10)
        .point("c", -10, 0)
        .three_point_arc("rim", "a", "b", "c")
        .fix("a")
        .fix("b")
        .fix("c")
    )
    report = solve_sketch(sketch)
    assert report.status == "fully_constrained"
    arc = report.geometry["arcs"]["rim"]
    center = arc["center"]
    assert center["x"] == pytest.approx(0, abs=1e-3)
    assert center["y"] == pytest.approx(0, abs=1e-3)
    assert math.hypot(arc["start"]["x"] - center["x"], arc["start"]["y"] - center["y"]) == pytest.approx(10)
    middle = report.geometry["points"]["b"]
    assert middle["x"] == pytest.approx(0, abs=1e-3)
    assert middle["y"] == pytest.approx(10, abs=1e-3)
    assert report.dimensions == []
    shape = solid_from_sketch(
        report.geometry,
        [{"op": "extrude", "points": ["rim_center", "a", "c"], "height": 1, "name": "half"}],
    )
    assert shape.impl.volume == pytest.approx(math.pi * 100 / 2, rel=1e-2)


def test_colinear_points_do_not_invent_an_arc():
    sketch = Sketch().point("a", 0, 0).point("b", 5, 0).point("c", 10, 0)
    with pytest.raises(GeometryError, match="not in a line"):
        sketch.three_point_arc("rim", "a", "b", "c")


def test_center_start_end_extrudes_the_quarter():
    sketch = (
        Sketch()
        .point("o", 0, 0)
        .fix("o")
        .point("a", 8, 0)
        .point("b", 0, 8)
        .line("oa", "o", "a")
        .horizontal("oa")
        .line("ob", "o", "b")
        .vertical("ob")
        .arc("q", "o", "a", "b")
        .radius("q", 8, name="q_radius")
    )
    report = solve_sketch(sketch)
    assert report.status == "fully_constrained"
    assert any(item["name"] == "q_radius" and item["value"] == pytest.approx(8) for item in report.dimensions)
    shape = solid_from_sketch(
        report.geometry,
        [{"op": "extrude", "points": ["o", "a", "b"], "height": 2, "name": "quarter"}],
    )
    assert shape.impl.volume == pytest.approx(math.pi * 64 / 4 * 2, rel=1e-3)


def test_a_tangent_arc_leaves_in_the_line_direction():
    frame = tangent_continuation((0.0, 0.0), (10.0, 0.0), True, (10.0, 10.0))
    assert frame["start_at_line"] is True
    assert frame["radius"] == pytest.approx(5)
    assert frame["center"] == pytest.approx((10, 5))
    right = tangent_continuation((0.0, 0.0), (10.0, 0.0), True, (10.0, -10.0))
    assert right["start_at_line"] is False
    assert right["center"] == pytest.approx((10, -5))
    assert tangent_continuation((0.0, 0.0), (10.0, 0.0), True, (20.0, 0.0)) is None
    found = circle_through((10.0, 0.0), (0.0, 10.0), (-10.0, 0.0))
    assert found[0] == pytest.approx((0, 0))
    assert found[1] == pytest.approx(10)


def test_a_tangent_arc_continues_the_line():
    sketch = (
        Sketch()
        .point("s", 0, 0)
        .point("e", 10, 0)
        .point("p", 10, 10)
        .line("edge", "s", "e")
        .fix("s")
        .fix("e")
        .tangent_arc("turn", "edge", "p")
    )
    report = solve_sketch(sketch)
    arc = report.geometry["arcs"]["turn"]
    center = arc["center"]
    assert center["x"] == pytest.approx(10, abs=1e-3)
    assert center["y"] == pytest.approx(5, abs=1e-3)
    end = report.geometry["points"]["p"]
    assert end["x"] == pytest.approx(10, abs=1e-2)
    assert end["y"] == pytest.approx(10, abs=1e-2)
    on_line = min(
        math.hypot(arc["start"]["x"] - 10, arc["start"]["y"]),
        math.hypot(arc["end"]["x"] - 10, arc["end"]["y"]),
    )
    assert on_line == pytest.approx(0, abs=1e-3)


def test_changing_the_tangent_radius_moves_the_end(tmp_path):
    document = Document(tmp_path)
    sketch = (
        Sketch()
        .point("s", 0, 0)
        .point("e", 10, 0)
        .point("p", 10, 10)
        .line("edge", "s", "e")
        .fix("s")
        .fix("e")
        .tangent_arc("turn", "edge", "p")
    )
    document.create_sketch("hook", sketch)
    report = document.set_dimension("hook", "turn_radius", 8)
    end = report.geometry["points"]["p"]
    center = report.geometry["arcs"]["turn"]["center"]
    assert math.hypot(end["x"] - center["x"], end["y"] - center["y"]) == pytest.approx(8, abs=1e-2)
    assert math.hypot(center["x"] - 10, center["y"]) == pytest.approx(8, abs=1e-2)


def test_a_tangent_or_three_point_arc_stays_in_the_extruded_profile(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "arc-profile.js"
    probe.write_text(_PROFILE, encoding="utf-8")
    result = subprocess.run([node, str(probe), str(GEOM)], check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert report["tangent"] == ["a", "b", "c", "d"]
    assert None not in report["tangent"]
    assert report["three"] == ["a", "b", "c", "d"]
    assert "m" not in report["three"]
    assert report["rounded"] == ["a", "b", "c"]
    assert report["center"] == ["o", "a", "b"]

    tangent = (
        Sketch()
        .point("a", 0, 0)
        .fix("a")
        .point("b", 10, 0)
        .fix("b")
        .point("c", 10, 8)
        .point("d", 0, 8)
        .fix("d")
        .line("ab", "a", "b")
        .tangent_arc("turn", "ab", "c")
        .line("cd", "c", "d")
        .line("da", "d", "a")
    )
    solved = solve_sketch(tangent)
    shape = solid_from_sketch(
        solved.geometry,
        [{"op": "extrude", "points": report["tangent"], "height": 4, "name": "pad"}],
    )
    assert shape.impl.volume > 0
    assert shape.impl.volume != pytest.approx(10 * 8 * 4, rel=1e-3)

    rounded = (
        Sketch()
        .point("a", 0, 0)
        .fix("a")
        .point("m1", 5, -3)
        .fix("m1")
        .point("b", 10, 0)
        .fix("b")
        .point("m2", 13, 5)
        .fix("m2")
        .point("c", 5, 10)
        .fix("c")
        .point("m3", -3, 5)
        .fix("m3")
        .three_point_arc("ab", "a", "m1", "b")
        .three_point_arc("bc", "b", "m2", "c")
        .three_point_arc("ca", "c", "m3", "a")
    )
    round_report = solve_sketch(rounded)
    round_shape = solid_from_sketch(
        round_report.geometry,
        [{"op": "extrude", "points": report["rounded"], "height": 2, "name": "pad"}],
    )
    straight = solid_from_sketch(
        {"points": round_report.geometry["points"], "lines": {}, "circles": {}, "arcs": {}, "splines": {}},
        [{"op": "extrude", "points": report["rounded"], "height": 2, "name": "flat"}],
    )
    assert round_shape.impl.volume > 0
    assert abs(round_shape.impl.volume - straight.impl.volume) > 1


_PROFILE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "sketch_geom.js" });

const tangent = {
  entities: [
    { id: "a", type: "point", at: [0, 0] },
    { id: "b", type: "point", at: [10, 0] },
    { id: "c", type: "point", at: [10, 8] },
    { id: "d", type: "point", at: [0, 8] },
    { id: "ab", type: "line", start: "a", end: "b" },
    { id: "turn", type: "arc", tangent_to: "ab", end: "c", from: "end" },
    { id: "cd", type: "line", start: "c", end: "d" },
    { id: "da", type: "line", start: "d", end: "a" },
  ],
};
const three = {
  entities: [
    { id: "a", type: "point", at: [0, 0] },
    { id: "b", type: "point", at: [10, 0] },
    { id: "m", type: "point", at: [5, 4] },
    { id: "c", type: "point", at: [10, 8] },
    { id: "d", type: "point", at: [0, 8] },
    { id: "ab", type: "line", start: "a", end: "b" },
    { id: "arc", type: "arc", through: ["b", "m", "c"] },
    { id: "cd", type: "line", start: "c", end: "d" },
    { id: "da", type: "line", start: "d", end: "a" },
  ],
};
const rounded = {
  entities: [
    { id: "a", type: "point" },
    { id: "b", type: "point" },
    { id: "c", type: "point" },
    { id: "m1", type: "point" },
    { id: "m2", type: "point" },
    { id: "m3", type: "point" },
    { id: "ab", type: "arc", through: ["a", "m1", "b"] },
    { id: "bc", type: "arc", through: ["b", "m2", "c"] },
    { id: "ca", type: "arc", through: ["c", "m3", "a"] },
  ],
};
const center = {
  entities: [
    { id: "o", type: "point" },
    { id: "a", type: "point" },
    { id: "b", type: "point" },
    { id: "oa", type: "line", start: "o", end: "a" },
    { id: "arc", type: "arc", center: "o", start: "a", end: "b" },
    { id: "bo", type: "line", start: "b", end: "o" },
  ],
};
process.stdout.write(JSON.stringify({
  tangent: sandbox.sketchProfile(tangent),
  three: sandbox.sketchProfile(three),
  rounded: sandbox.sketchProfile(rounded),
  center: sandbox.sketchProfile(center),
}));
"""


def test_a_point_on_the_line_is_refused():
    sketch = (
        Sketch()
        .point("s", 0, 0)
        .point("e", 10, 0)
        .point("p", 16, 0)
        .line("edge", "s", "e")
    )
    with pytest.raises(GeometryError, match="on the line"):
        sketch.tangent_arc("turn", "edge", "p")


_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "sketch_geom.js" });

const blank = {
  plane: "xy",
  entities: [{ id: "origin", type: "point", at: [0, 0] }],
  constraints: [{ type: "fix", entity: "origin" }],
};
const geometry = { points: { origin: { x: 0, y: 0 } }, lines: {}, circles: {}, arcs: {} };
const first = sandbox.sketchArcClick(blank, geometry, "three", [], 10, 0, 2);
const second = sandbox.sketchArcClick(first.spec, geometry, "three", first.pending, 0, 10, 2);
const third = sandbox.sketchArcClick(second.spec, geometry, "three", second.pending, -10, 0, 2);
const arc = third.spec.entities.find((entity) => entity.type === "arc");
const radius = third.spec.constraints.find((item) => item.type === "radius");
const flat = sandbox.sketchArcClick(second.spec, geometry, "three", second.pending, 20, -10, 2);

const centerSpec = {
  plane: "xy",
  entities: [{ id: "o", type: "point", at: [0, 0] }],
  constraints: [],
};
const centerGeometry = { points: { o: { x: 0, y: 0 } } };
const center = sandbox.sketchArcClick(centerSpec, centerGeometry, "center", [], 0, 0, 2);
const started = sandbox.sketchArcClick(center.spec, centerGeometry, "center", center.pending, 6, 0, 2);
const finished = sandbox.sketchArcClick(started.spec, centerGeometry, "center", started.pending, 0, 6, 2);
const centerArc = finished.spec.entities.find((entity) => entity.type === "arc");

const lineSpec = {
  plane: "xy",
  entities: [
    { id: "s", type: "point", at: [0, 0] },
    { id: "e", type: "point", at: [10, 0] },
    { id: "edge", type: "line", start: "s", end: "e" },
  ],
  constraints: [],
};
const lineGeometry = {
  points: { s: { x: 0, y: 0 }, e: { x: 10, y: 0 } },
  lines: { edge: { start: { x: 0, y: 0 }, end: { x: 10, y: 0 } } },
};
const missed = sandbox.sketchArcClick(lineSpec, lineGeometry, "tangent", [], 3, 3, 1);
const armed = sandbox.sketchArcClick(lineSpec, lineGeometry, "tangent", [], 10, 0, 2);
const turned = sandbox.sketchArcClick(armed.spec, lineGeometry, "tangent", armed.pending, 10, 10, 2);
const tangentArc = turned.spec.entities.find((entity) => entity.type === "arc");
const onLine = sandbox.sketchArcClick(armed.spec, lineGeometry, "tangent", armed.pending, 20, 0, 2);

const server = { spec: blank, geometry };
const pending = first.pending.map((id) => sandbox.sketchPendingPoint(first, id));
let kept = server;
pending.forEach((item) => { kept = sandbox.sketchWithPendingPoint(kept, item); });
const resumed = sandbox.sketchArcClick(kept.spec, kept.geometry, "three", first.pending, 0, 10, 2);

const snapped = sandbox.sketchSnap(geometry, 1.2, 0.4, 2, 10);
const grid = sandbox.sketchSnap({ points: {} }, 12, 4, 8, 10);
const free = sandbox.sketchSnap({ points: {} }, 14, 6, 1, 10);

process.stdout.write(JSON.stringify({
  firstPending: first.pending,
  thirdPending: third.pending,
  arc,
  radius: radius || null,
  flat: flat.error,
  centerArc,
  centerRadius: finished.spec.constraints.find((item) => item.type === "radius"),
  missed: missed.error,
  armed: armed.pending,
  tangentArc,
  tangentRadius: turned.spec.constraints.find((item) => item.type === "radius"),
  onLine: onLine.error,
  resumed: resumed.spec.entities.filter((entity) => entity.type === "point").map((entity) => entity.id),
  snapped,
  grid,
  free,
}));
"""


def test_the_canvas_arc_tool_matches_the_solver_and_survives_a_refresh(tmp_path):
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    page = (ROOT / "src" / "aicad" / "app_static" / "index.html").read_text(encoding="utf-8")
    assert 'ui.tool === "arc"' in script
    assert "sketchArcClick" in script
    assert "sketchSnap(" in script
    assert "sketchPendingPoint(ui.sketch, id)" in script
    assert "sketchWithPendingPoint(sketch, pending)" in script
    assert 'data-tool="arc"' in page
    assert 'data-constraint="radius"' in page
    assert 'data-constraint="tangent"' in page
    node = shutil.which("node")
    assert node
    probe = tmp_path / "arc-tool.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run([node, str(probe), str(GEOM)], check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert report["arc"]["through"][0] == report["firstPending"][0]
    assert len(report["arc"]["through"]) == 3
    assert report["arc"]["through"][0] != report["arc"]["through"][2]
    assert report["radius"] is None
    assert report["thirdPending"] == []
    assert "line" in report["flat"]
    assert report["centerArc"]["type"] == "arc"
    assert report["centerArc"]["center"] == "o"
    assert report["centerRadius"]["value"] == 6
    assert "end of a line" in report["missed"]
    assert report["tangentArc"]["tangent_to"] == "edge"
    assert report["tangentArc"]["from"] == "end"
    assert report["tangentRadius"]["value"] == 5
    assert "on the line" in report["onLine"]
    assert report["firstPending"][0] in report["resumed"]
    assert report["snapped"]["point"] == "origin"
    assert report["grid"] == {"x": 10, "y": 0, "point": None}
    assert report["free"]["point"] is None
    assert report["free"]["x"] == 14
