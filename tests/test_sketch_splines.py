"""Splines through sketch points, including a closed profile that extrudes."""

import copy
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

ROOT = Path(__file__).resolve().parents[1]
GEOM = ROOT / "src" / "aicad" / "app_static" / "sketch_geom.js"


def _loop(bulge: float = 25) -> Sketch:
    return (
        Sketch()
        .point("a", 15, 0)
        .fix("a")
        .point("b", bulge, 10)
        .fix("b")
        .point("c", 15, 20)
        .fix("c")
        .point("d", 5, 10)
        .fix("d")
        .spline("curve", ["a", "b", "c", "d"], closed=True)
    )


def test_a_closed_spline_extrudes_the_curve_and_can_be_revolved():
    report = solve_sketch(_loop())
    assert report.status == "fully_constrained"
    samples = report.geometry["splines"]["curve"]["samples"]
    assert len(samples) > 8
    assert samples[0]["x"] == pytest.approx(15)
    shape = solid_from_sketch(
        report.geometry,
        [{"op": "extrude", "points": ["a", "b", "c", "d"], "height": 2, "name": "pad"}],
    )
    polygon = solid_from_sketch(
        {"points": report.geometry["points"], "lines": {}, "circles": {}, "arcs": {}},
        [{"op": "extrude", "points": ["a", "b", "c", "d"], "height": 2, "name": "flat"}],
    )
    assert shape.impl.volume > 0
    assert abs(shape.impl.volume - polygon.impl.volume) > 1
    spun = solid_from_sketch(
        report.geometry,
        [{"op": "revolve", "points": ["a", "b", "c", "d"], "axis": "y", "name": "ring"}],
    )
    assert spun.impl.volume > shape.impl.volume


def test_moving_a_point_changes_the_solid(tmp_path):
    document = Document(tmp_path)
    document.create_sketch("loop", _loop(6))
    first = document.create_part_from_sketch(
        "pad",
        "loop",
        [{"op": "extrude", "points": ["a", "b", "c", "d"], "height": 2, "name": "pad"}],
    )
    stored = copy.deepcopy(document._get_sketch("loop").spec)  # noqa: SLF001
    for entity in stored["entities"]:
        if entity["id"] == "b":
            entity["at"] = [18, 10]
    document.update_sketch("loop", stored)
    second = document.measure("pad")
    assert second.volume_mm3 != pytest.approx(first.volume_mm3)


def test_an_open_spline_is_not_a_profile_until_it_closes():
    sketch = (
        Sketch()
        .point("a", 0, 0)
        .point("b", 10, 4)
        .point("c", 20, 0)
        .spline("curve", ["a", "b", "c"], closed=False)
    )
    report = solve_sketch(sketch)
    assert report.geometry["splines"]["curve"]["closed"] is False
    with pytest.raises(GeometryError, match="three or more"):
        solid_from_sketch(
            report.geometry,
            [{"op": "extrude", "points": ["a", "b"], "height": 1, "name": "open"}],
        )


def test_a_spline_refuses_a_repeated_point():
    sketch = Sketch().point("a", 0, 0).point("b", 10, 0)
    with pytest.raises(GeometryError, match="at least 3"):
        sketch.spline("curve", ["a", "b"], closed=True)
    sketch.point("c", 10, 10)
    sketch.spline("curve", ["a", "b", "a"], closed=True)
    with pytest.raises(GeometryError, match="repeats"):
        solve_sketch(sketch)


_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "sketch_geom.js" });

const blank = { plane: "xy", entities: [], constraints: [] };
function geometryOf(spec) {
  const points = {};
  (spec.entities || []).forEach((entity) => {
    if (entity && entity.type === "point" && Array.isArray(entity.at)) {
      points[entity.id] = { x: entity.at[0], y: entity.at[1] };
    }
  });
  return { points };
}
const first = sandbox.sketchSplineClick(blank, geometryOf(blank), null, null, 0, 0, 2);
const second = sandbox.sketchSplineClick(first.spec, geometryOf(first.spec), first.splineId, first.pending, 10, 6, 2);
const third = sandbox.sketchSplineClick(second.spec, geometryOf(second.spec), second.splineId, second.pending, 10, 0, 2);
const fourth = sandbox.sketchSplineClick(third.spec, geometryOf(third.spec), third.splineId, third.pending, 0, 8, 2);
const fifth = sandbox.sketchSplineClick(fourth.spec, geometryOf(fourth.spec), fourth.splineId, fourth.pending, 0, 0, 2);
const curve = fifth.spec.entities.find((entity) => entity.type === "spline");
const profile = sandbox.sketchProfile(fifth.spec);
const server = { spec: blank, geometry: geometryOf(blank) };
const pending = sandbox.sketchPendingPoint(first, first.pending);
const kept = sandbox.sketchWithPendingPoint(server, pending);
const resumed = sandbox.sketchSplineClick(kept.spec, kept.geometry, null, first.pending, 10, 6, 2);

process.stdout.write(JSON.stringify({
  pending: first.pending,
  splineId: second.splineId,
  closed: curve.closed,
  points: curve.points.length,
  profile,
  resumed: resumed.spec.entities.filter((entity) => entity.type === "spline").length,
  resumedPoints: resumed.spec.entities.find((entity) => entity.type === "spline").points,
}));
"""


def test_the_canvas_spline_closes_and_keeps_its_first_point(tmp_path):
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    page = (ROOT / "src" / "aicad" / "app_static" / "index.html").read_text(encoding="utf-8")
    assert 'ui.tool === "spline"' in script
    assert "sketchSplineClick" in script
    assert "sketchPendingPoint(ui.sketch, ui.splinePoint)" in script
    assert "sketchOpenSpline" in script
    assert 'data-tool="spline"' in page
    node = shutil.which("node")
    assert node
    probe = tmp_path / "spline.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run([node, str(probe), str(GEOM)], check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert report["points"] == 4
    assert report["closed"] is True
    assert report["profile"][0] == report["pending"]
    assert len(report["profile"]) == 4
    assert report["resumed"] == 1
    assert report["pending"] in report["resumedPoints"]
    assert math.isfinite(report["points"])
