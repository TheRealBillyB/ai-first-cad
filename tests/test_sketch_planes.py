"""Sketches on the principal planes, a datum, and a planar face of a solid."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aicad import Document, GeometryError, Sketch, SketchPlaneError
from aicad.kernel import solid_from_sketch
from aicad.sketch import solve_sketch
from aicad.sketch_plane import sketch_frame

ROOT = Path(__file__).resolve().parents[1]
GEOM = ROOT / "src" / "aicad" / "app_static" / "sketch_geom.js"


def _plate(plane):
    return (
        Sketch(plane)
        .point("a", 0, 0)
        .fix("a")
        .point("b", 10, 0)
        .fix("b")
        .point("c", 10, 8)
        .fix("c")
        .point("d", 0, 8)
        .fix("d")
        .line("ab", "a", "b")
        .line("bc", "b", "c")
        .line("cd", "c", "d")
        .line("da", "d", "a")
    )


def _solid(plane):
    report = solve_sketch(_plate(plane))
    assert report.status == "fully_constrained"
    frame = sketch_frame(report and plane, "plate")
    return solid_from_sketch(
        report.geometry,
        [{"op": "extrude", "points": ["a", "b", "c", "d"], "height": 6, "name": "pad"}],
        frame=frame,
    )


def _box(shape):
    return shape.impl.bounding_box()


def test_yz_and_xz_extrude_along_their_normals():
    yz = _box(_solid("yz"))
    assert yz.min.X == pytest.approx(0, abs=1e-6)
    assert yz.max.X == pytest.approx(6, abs=1e-6)
    assert yz.max.Y - yz.min.Y == pytest.approx(10, abs=1e-6)
    assert yz.max.Z - yz.min.Z == pytest.approx(8, abs=1e-6)
    xz = _box(_solid("xz"))
    assert xz.min.Y == pytest.approx(-6, abs=1e-6)
    assert xz.max.Y == pytest.approx(0, abs=1e-6)
    assert xz.max.X - xz.min.X == pytest.approx(10, abs=1e-6)
    assert xz.max.Z - xz.min.Z == pytest.approx(8, abs=1e-6)


def test_an_offset_and_an_angled_datum_move_the_solid():
    offset = _box(_solid({"kind": "datum", "base": "xy", "offset": 12}))
    assert offset.min.Z == pytest.approx(12, abs=1e-6)
    assert offset.max.Z == pytest.approx(18, abs=1e-6)
    tilted = _box(_solid({"kind": "datum", "base": "xy", "angle": 90, "axis": "x"}))
    assert tilted.min.Y == pytest.approx(-6, abs=1e-6)
    assert tilted.max.Y == pytest.approx(0, abs=1e-6)
    assert tilted.max.Z - tilted.min.Z == pytest.approx(8, abs=1e-6)


def test_a_bad_plane_is_refused_before_a_face_is_looked_up():
    with pytest.raises(GeometryError, match="xy, yz, xz"):
        solve_sketch(
            {
                "plane": "ab",
                "entities": [{"id": "a", "type": "point", "at": [0, 0]}],
                "constraints": [],
            }
        )


def test_a_sketch_on_a_face_follows_that_face_and_reloads(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "block",
        [{"op": "add", "shape": "box", "size": [30, 20, 10], "center": [0, 0, 5], "name": "block"}],
    )
    faces = document.describe_faces("block")["faces"]
    top = next(face for face in faces if face["normal"] == [0, 0, 1])
    plane = {"kind": "face", "part": "block", "ref": top["ref"]}
    document.create_sketch("lid", _plate(plane))
    info = document.create_part_from_sketch(
        "pad",
        "lid",
        [{"op": "extrude", "points": ["a", "b", "c", "d"], "height": 4, "name": "pad"}],
    )
    assert info.bounding_box.min.z == pytest.approx(10, abs=0.05)
    assert info.bounding_box.max.z == pytest.approx(14, abs=0.05)

    document.update_part_ops(
        "block",
        [{"op": "add", "shape": "box", "size": [30, 20, 20], "center": [0, 0, 10], "name": "block"}],
    )
    moved = document.measure("pad")
    assert moved.bounding_box.min.z == pytest.approx(20, abs=0.05)
    assert moved.bounding_box.max.z == pytest.approx(24, abs=0.05)

    document.place_part("block", [0, 0, 5])
    shifted = document.measure("pad")
    assert shifted.bounding_box.min.z == pytest.approx(25, abs=0.05)

    reloaded = Document(tmp_path)
    again = reloaded.measure("pad")
    assert again.bounding_box.min.z == pytest.approx(25, abs=0.05)
    assert reloaded.get_source("pad")["problem"] is None


def test_a_missing_face_raises_a_typed_error(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "block",
        [{"op": "add", "shape": "box", "size": [30, 20, 10], "center": [0, 0, 5], "name": "block"}],
    )
    top = next(face for face in document.describe_faces("block")["faces"] if face["normal"] == [0, 0, 1])
    document.create_sketch("lid", _plate({"kind": "face", "part": "block", "ref": top["ref"]}))
    document.create_part_from_sketch(
        "pad",
        "lid",
        [{"op": "extrude", "points": ["a", "b", "c", "d"], "height": 4, "name": "pad"}],
    )
    with pytest.raises(SketchPlaneError, match="no longer there"):
        document.update_part_ops(
            "block",
            [{
                "op": "add",
                "shape": "cylinder",
                "radius": 8,
                "height": 20,
                "center": [0, 0, 0],
                "axis": [1, 0, 0],
                "name": "pin",
            }],
        )
    assert document.measure("block").bounding_box.max.z == pytest.approx(8, abs=0.05)
    assert document.get_source("pad")["built"] is False
    assert "no longer there" in document.get_source("pad")["problem"]
    missing = _plate({"kind": "face", "part": "absent", "ref": top["ref"]})
    document.create_sketch("loose", missing)
    with pytest.raises(SketchPlaneError, match="not in this model"):
        document.create_part_from_sketch(
            "other",
            "loose",
            [{"op": "extrude", "points": ["a", "b", "c", "d"], "height": 4, "name": "pad"}],
        )


def test_a_sketch_on_a_face_moves_and_turns_with_that_part(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "block",
        [{"op": "add", "shape": "box", "size": [30, 20, 10], "center": [0, 0, 5], "name": "block"}],
    )
    top = next(face for face in document.describe_faces("block")["faces"] if face["normal"] == [0, 0, 1])
    document.create_sketch("lid", _plate({"kind": "face", "part": "block", "ref": top["ref"]}))
    document.create_part_from_sketch(
        "pad",
        "lid",
        [{"op": "extrude", "points": ["a", "b", "c", "d"], "height": 4, "name": "pad"}],
    )
    before = document.measure("pad").bounding_box
    document.place_part("block", [40, 0, 0])
    moved = document.measure("pad").bounding_box
    assert moved.min.x == pytest.approx(before.min.x + 40, abs=0.05)
    assert moved.max.x == pytest.approx(before.max.x + 40, abs=0.05)
    assert moved.min.y == pytest.approx(before.min.y, abs=0.05)
    assert moved.min.z == pytest.approx(before.min.z, abs=0.05)

    document.place_part("block", [0, 0, 0], [0, 0, 90])
    turned = document.measure("pad").bounding_box
    assert turned.min.x == pytest.approx(-8, abs=0.05)
    assert turned.max.x == pytest.approx(0, abs=0.05)
    assert turned.min.y == pytest.approx(0, abs=0.05)
    assert turned.max.y == pytest.approx(10, abs=0.05)
    assert turned.min.z == pytest.approx(10, abs=0.05)
    assert turned.max.z == pytest.approx(14, abs=0.05)
    reloaded = Document(tmp_path)
    again = reloaded.measure("pad").bounding_box
    assert again.min.x == pytest.approx(turned.min.x, abs=0.05)
    assert again.max.y == pytest.approx(turned.max.y, abs=0.05)
    assert again.min.z == pytest.approx(turned.min.z, abs=0.05)


def test_a_sketch_follows_a_face_that_moves_in_the_part(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "block",
        [{"op": "add", "shape": "box", "size": [30, 20, 10], "center": [0, 0, 5], "name": "block"}],
    )
    top = next(face for face in document.describe_faces("block")["faces"] if face["normal"] == [0, 0, 1])
    document.create_sketch("lid", _plate({"kind": "face", "part": "block", "ref": top["ref"]}))
    document.create_part_from_sketch(
        "pad",
        "lid",
        [{"op": "extrude", "points": ["a", "b", "c", "d"], "height": 4, "name": "pad"}],
    )
    document.update_part_ops(
        "block",
        [{"op": "add", "shape": "box", "size": [30, 20, 10], "center": [30, 0, 5], "name": "block"}],
    )
    moved = document.measure("pad").bounding_box
    assert moved.min.x == pytest.approx(30, abs=0.05)
    assert moved.max.x == pytest.approx(40, abs=0.05)
    assert moved.min.y == pytest.approx(0, abs=0.05)
    assert moved.min.z == pytest.approx(10, abs=0.05)
    assert moved.max.z == pytest.approx(14, abs=0.05)


def test_a_sketch_started_on_a_moved_face_stays_on_it(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "block",
        [{"op": "add", "shape": "box", "size": [30, 20, 10], "center": [0, 0, 5], "name": "block"}],
    )
    document.place_part("block", [40, 0, 0])
    top = next(face for face in document.describe_faces("block")["faces"] if face["normal"] == [0, 0, 1])
    document.create_sketch("lid", _plate({"kind": "face", "part": "block", "ref": top["ref"]}))
    document.create_part_from_sketch(
        "pad",
        "lid",
        [{"op": "extrude", "points": ["a", "b", "c", "d"], "height": 4, "name": "pad"}],
    )
    placed = document.measure("pad").bounding_box
    assert placed.min.x == pytest.approx(40, abs=0.05)
    assert placed.min.z == pytest.approx(10, abs=0.05)
    document.place_part("block", [0, 0, 0])
    home = document.measure("pad").bounding_box
    assert home.min.x == pytest.approx(0, abs=0.05)
    assert home.max.x == pytest.approx(10, abs=0.05)
    assert home.min.z == pytest.approx(10, abs=0.05)


def test_a_sketch_cannot_use_a_face_of_the_solid_it_builds(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "block",
        [{"op": "add", "shape": "box", "size": [20, 20, 10], "center": [0, 0, 5], "name": "block"}],
    )
    top = next(face for face in document.describe_faces("block")["faces"] if face["normal"] == [0, 0, 1])
    document.create_sketch("block", _plate({"kind": "face", "part": "block", "ref": top["ref"]}))
    with pytest.raises(SketchPlaneError, match="the solid that sketch builds"):
        document.update_part_from_sketch(
            "block",
            "block",
            [{"op": "extrude", "points": ["a", "b", "c", "d"], "height": 4, "name": "pad"}],
        )


_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "sketch_geom.js" });
process.stdout.write(JSON.stringify({
  plain: sandbox.sketchPlaneSpec({ plane: "yz" }, {}),
  offset: sandbox.sketchPlaneSpec({ plane: "xy", offset: "12", angle: "", axis: "x" }, {}),
  face: sandbox.sketchPlaneSpec({ plane: "face" }, { part: "block", ref: "plane/z+/0,0,10" }),
  missing: sandbox.sketchPlaneSpec({ plane: "face" }, {}),
  label: sandbox.sketchPlaneLabel({ kind: "datum", base: "xz", angle: 30, axis: "y" }),
}));
"""


def test_the_new_sketch_dialog_can_name_a_face_or_a_datum(tmp_path):
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert "sketchPlaneSpec" in script
    assert "sketchPlaneLabel" in script
    node = shutil.which("node")
    assert node
    probe = tmp_path / "plane.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run([node, str(probe), str(GEOM)], check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert report["plain"]["plane"] == "yz"
    assert report["offset"]["plane"] == {"kind": "datum", "base": "xy", "offset": 12}
    assert report["face"]["plane"]["part"] == "block"
    assert "planar face" in report["missing"]["error"]
    assert report["label"] == "XZ, 30 deg about y"
