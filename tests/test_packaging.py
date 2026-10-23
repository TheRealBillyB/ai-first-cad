"""Packaging against envelopes whose protrusion and clearance can be computed by hand."""

import json
import math
import subprocess
import sys

import pytest

from aicad import Document, GeometryError
from aicad.workspace import Workspace

# Box(10, 10, 10) corners sit at radius sqrt(50) from the axis.
_CORNER_10 = math.sqrt(50)
# Box(6, 6, 6) corners sit at radius sqrt(18).
_CORNER_6 = math.sqrt(18)
# Box(16, 16, 16) corners sit at radius sqrt(192) from the origin.
_CORNER_16 = math.sqrt(192)
# Box(10, 10, 10) corners sit at radius sqrt(75).
_CORNER_BOX_10 = math.sqrt(75)


def _ops(document: Document, name: str, shape: str, **fields) -> None:
    document.create_part_from_ops(name, [{"op": "add", "shape": shape, **fields}])


def _part(report, name: str) -> dict:
    return next(item for item in report["parts"] if item["name"] == name)


def test_a_box_inside_a_box_reports_the_face_gap(tmp_path):
    document = Document(tmp_path)
    _ops(document, "bay", "box", size=[20, 20, 20])
    # 10 x 8 x 6, centered. Gaps to a 20 mm cube are 5, 6, and 7.
    _ops(document, "brick", "box", size=[10, 8, 6])
    report = document.check_packaging("bay", ["brick"], min_clearance_mm=5).to_dict()
    assert report["result_kind"] == "verified_check"
    assert report["passed"] is True
    assert report["min_clearance_mm"] == pytest.approx(5)
    assert report["min_clearance_part"] == "brick"
    assert report["protruding"] == []
    assert report["overlaps"] == []
    brick = _part(report, "brick")
    assert brick["passed"] is True
    assert brick["contained"] is True
    assert brick["protrudes"] is False
    assert brick["max_protrusion_mm"] == 0
    assert brick["protrusion_at_mm"] is None
    assert brick["method"] == "boundary"
    point = brick["clearance_at_mm"]
    assert abs(point["x"]) == pytest.approx(5)
    assert abs(point["y"]) <= 4 + 1e-6
    assert abs(point["z"]) <= 3 + 1e-6
    tight = document.check_packaging("bay", ["brick"], min_clearance_mm=5.1).to_dict()
    assert tight["passed"] is False
    assert _part(tight, "brick")["protrudes"] is False
    assert _part(tight, "brick")["passed"] is False


def test_a_box_that_sticks_through_one_face_reports_the_protrusion(tmp_path):
    document = Document(tmp_path)
    # Envelope occupies [0, 20] on each axis.
    _ops(document, "bay", "box", size=[20, 20, 20], center=[10, 10, 10])
    # Brick occupies x [0, 10], y [0, 10], z [10, 22].
    _ops(document, "brick", "box", size=[10, 10, 12], center=[5, 5, 16])
    report = document.check_packaging("bay", ["brick"]).to_dict()
    assert report["passed"] is False
    assert report["protruding"] == ["brick"]
    assert report["min_clearance_mm"] == pytest.approx(-2)
    brick = _part(report, "brick")
    assert brick["passed"] is False
    assert brick["max_protrusion_mm"] == pytest.approx(2)
    assert brick["protrusion_at_mm"]["z"] == pytest.approx(22)
    assert 0 <= brick["protrusion_at_mm"]["x"] <= 10
    assert 0 <= brick["protrusion_at_mm"]["y"] <= 10
    assert brick["method"] == "vertices"
    assert brick["outside_volume_mm3"] == pytest.approx(200)


def test_the_minimum_clearance_is_the_smaller_part(tmp_path):
    document = Document(tmp_path)
    _ops(document, "bay", "box", size=[40, 40, 40])
    _ops(document, "inner", "box", size=[10, 10, 10])
    # Extends to x = 11, so the gap to x = 20 is 9. The centered block's gap is 15.
    _ops(document, "offset", "box", size=[10, 10, 10], center=[6, 0, 0])
    report = document.check_packaging("bay", ["inner", "offset"], min_clearance_mm=10).to_dict()
    assert report["min_clearance_mm"] == pytest.approx(9)
    assert report["min_clearance_part"] == "offset"
    assert _part(report, "inner")["passed"] is True
    assert _part(report, "inner")["min_clearance_mm"] == pytest.approx(15)
    assert _part(report, "offset")["passed"] is False
    assert _part(report, "offset")["protrudes"] is False
    assert report["passed"] is False


def test_cylinders_report_radial_clearance_not_the_bounding_gap(tmp_path):
    document = Document(tmp_path)
    _ops(document, "sleeve", "cylinder", radius=10, height=20)
    _ops(document, "pin", "cylinder", radius=7, height=10)
    report = document.check_packaging(
        {"shape": "cylinder", "radius": 10, "height": 20},
        ["pin"],
    ).to_dict()
    assert report["envelope"]["kind"] == "primitive"
    assert report["envelope"]["shape"] == "cylinder"
    pin = _part(report, "pin")
    # Axial gap is 5. The wall gap is the radius difference, 3.
    assert pin["min_clearance_mm"] == pytest.approx(3)
    assert pin["passed"] is True
    point = pin["clearance_at_mm"]
    assert math.hypot(point["x"], point["y"]) == pytest.approx(7)
    assert abs(point["z"]) <= 5 + 1e-6
    # The same numbers against a stored solid, not only a primitive.
    stored = document.check_packaging("sleeve", ["pin"]).to_dict()
    assert _part(stored, "pin")["min_clearance_mm"] == pytest.approx(3)


def test_a_square_inside_a_round_envelope_uses_the_corners(tmp_path):
    document = Document(tmp_path)
    _ops(document, "block", "box", size=[10, 10, 10])
    report = document.check_packaging(
        {"shape": "cylinder", "radius": 6, "height": 20},
        ["block"],
    ).to_dict()
    # The bounding box of the cylinder is 12 mm across, so a box check would
    # say the 10 mm block fits. The corners are sqrt(50) mm from the axis.
    block = _part(report, "block")
    assert block["max_protrusion_mm"] == pytest.approx(_CORNER_10 - 6)
    assert block["min_clearance_mm"] == pytest.approx(6 - _CORNER_10)
    assert block["protrudes"] is True
    point = block["protrusion_at_mm"]
    assert math.hypot(point["x"], point["y"]) == pytest.approx(_CORNER_10)
    assert abs(point["z"]) == pytest.approx(5)

    wider = document.check_packaging(
        {"shape": "cylinder", "radius": 10, "height": 20},
        ["block"],
    ).to_dict()
    # The same 10 mm block clears a radius-10 cylinder by 10 - sqrt(50).
    assert _part(wider, "block")["min_clearance_mm"] == pytest.approx(10 - _CORNER_10)
    assert _part(wider, "block")["protrudes"] is False
    assert wider["passed"] is True
    assert report["passed"] is False

    # A 6 mm block has corners at sqrt(18), so the wall gap is 10 - sqrt(18).
    small = Document(tmp_path / "small")
    _ops(small, "block", "box", size=[6, 6, 6])
    fit = small.check_packaging(
        {"shape": "cylinder", "radius": 10, "height": 20},
        ["block"],
    ).to_dict()
    assert _part(fit, "block")["min_clearance_mm"] == pytest.approx(10 - _CORNER_6)
    assert _part(fit, "block")["protrudes"] is False


def test_a_cylinder_past_a_box_corner_is_not_the_box_diagonal(tmp_path):
    document = Document(tmp_path)
    # Envelope is [0, 10]³. Cylinder radius 5 centered on the corner (10, 10).
    _ops(document, "peg", "cylinder", radius=5, height=4, center=[10, 10, 5])
    report = document.check_packaging(
        {"shape": "box", "size": [10, 10, 10], "center": [5, 5, 5]},
        ["peg"],
    ).to_dict()
    peg = _part(report, "peg")
    # Every point on the outside quarter-circle is 5 mm from the corner.
    # The cylinder's bounding box reaches (15, 15), which is 7.071 mm out.
    assert peg["max_protrusion_mm"] == pytest.approx(5)
    point = peg["protrusion_at_mm"]
    assert math.hypot(point["x"] - 10, point["y"] - 10) == pytest.approx(5)
    assert point["x"] >= 10 - 1e-6
    assert point["y"] >= 10 - 1e-6


def test_a_rotated_cylinder_protrudes_past_its_vertices(tmp_path):
    document = Document(tmp_path)
    _ops(document, "peg", "cylinder", radius=5, height=10)
    document.place_part("peg", [12, 0, 0], [0, 0, 30])
    # Envelope occupies x = [-10, 14]. The solid reaches x = 17.
    # A seam vertex after a 30° turn is at x = 12 + 5*cos(30°) = 16.330.
    report = document.check_packaging(
        {"shape": "box", "size": [24, 40, 40], "center": [2, 0, 0]},
        ["peg"],
    ).to_dict()
    peg = _part(report, "peg")
    assert peg["max_protrusion_mm"] == pytest.approx(3)
    assert peg["method"] == "surface"
    point = peg["protrusion_at_mm"]
    assert point["x"] == pytest.approx(17)
    assert point["y"] == pytest.approx(0, abs=1e-4)
    assert abs(point["z"]) <= 5 + 1e-6


def test_a_horizontal_cylinder_protrudes_from_the_top_of_the_wall(tmp_path):
    document = Document(tmp_path)
    _ops(document, "rod", "cylinder", radius=3, height=10)
    document.place_part("rod", [0, 0, 8], [0, 90, 0])
    report = document.check_packaging(
        {"shape": "box", "size": [20, 20, 10], "center": [0, 0, 5]},
        ["rod"],
    ).to_dict()
    rod = _part(report, "rod")
    assert rod["max_protrusion_mm"] == pytest.approx(1)
    point = rod["protrusion_at_mm"]
    assert point["z"] == pytest.approx(11)
    assert abs(point["y"]) == pytest.approx(0, abs=1e-4)
    assert abs(point["x"]) <= 5 + 1e-4


def test_a_sphere_protrudes_along_the_offset_not_at_the_poles(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script(
        "ball",
        "part = Pos(4, 0, 0) * Sphere(5)\n",
    )
    report = document.check_packaging(
        {"shape": "box", "size": [6, 6, 6]},
        ["ball"],
    ).to_dict()
    ball = _part(report, "ball")
    # The +X point is (9, 0, 0), 6 mm past the wall at x = 3.
    # The poles are only about 2.236 mm outside.
    assert ball["max_protrusion_mm"] == pytest.approx(6)
    point = ball["protrusion_at_mm"]
    assert point["x"] == pytest.approx(9)
    assert point["y"] == pytest.approx(0, abs=1e-4)
    assert point["z"] == pytest.approx(0, abs=1e-4)


def test_a_box_in_a_sphere_uses_the_corner_radius(tmp_path):
    document = Document(tmp_path)
    _ops(document, "block", "box", size=[16, 16, 16])
    outside = document.check_packaging(
        {"shape": "sphere", "radius": 10},
        ["block"],
    ).to_dict()
    # The sphere's bounding box is 20 mm, so a box check would pass a 16 mm block.
    assert _part(outside, "block")["max_protrusion_mm"] == pytest.approx(_CORNER_16 - 10)
    assert _part(outside, "block")["method"] == "vertices"

    _ops(document, "small", "box", size=[10, 10, 10])
    inside = document.check_packaging(
        {"shape": "sphere", "radius": 10},
        ["small"],
        min_clearance_mm=1,
    ).to_dict()
    assert _part(inside, "small")["min_clearance_mm"] == pytest.approx(10 - _CORNER_BOX_10)
    assert _part(inside, "small")["passed"] is True
    assert inside["passed"] is True


def test_an_outline_extrusion_is_the_polygon_not_its_bounding_box(tmp_path):
    document = Document(tmp_path)
    outline = {
        "outline": [[0, 0], [40, 0], [0, 30]],
        "axis": [0, 0, 1],
        "limit": [0, 20],
    }
    # Corner (10, 25) is 2 mm past the hypotenuse 3x + 4y = 120, and inside
    # the triangle's bounding box [0, 40] x [0, 30].
    _ops(document, "chip", "box", size=[2, 2, 2], center=[9, 24, 6])
    report = document.check_packaging(outline, ["chip"]).to_dict()
    assert report["envelope"]["kind"] == "outline"
    chip = _part(report, "chip")
    assert chip["max_protrusion_mm"] == pytest.approx(2)
    point = chip["protrusion_at_mm"]
    assert point["x"] == pytest.approx(10)
    assert point["y"] == pytest.approx(25)
    assert point["z"] == pytest.approx(5) or point["z"] == pytest.approx(7)

    _ops(document, "inset", "box", size=[2, 2, 2], center=[4, 4, 4])
    fit = document.check_packaging(outline, ["inset"]).to_dict()
    # Three millimeters from the x = 0, y = 0, and z = 0 walls. The slant is farther.
    assert _part(fit, "inset")["min_clearance_mm"] == pytest.approx(3)
    assert _part(fit, "inset")["passed"] is True


def test_an_outline_can_be_limited_along_an_axis_other_than_z(tmp_path):
    document = Document(tmp_path)
    # Local X is world Y and local Y is world Z when the axis is +X.
    # The solid is x [0, 30], y [0, 20], z [0, 10].
    outline = {
        "outline": [[0, 0], [20, 0], [20, 10], [0, 10]],
        "axis": [1, 0, 0],
        "origin": [0, 0, 0],
        "length": 30,
    }
    _ops(document, "brick", "box", size=[10, 10, 6], center=[15, 10, 5])
    report = document.check_packaging(outline, ["brick"], min_clearance_mm=2).to_dict()
    brick = _part(report, "brick")
    assert brick["min_clearance_mm"] == pytest.approx(2)
    assert brick["passed"] is True
    assert brick["clearance_at_mm"]["z"] == pytest.approx(2)


def test_part_overlap_inside_the_envelope_is_reported(tmp_path):
    document = Document(tmp_path)
    _ops(document, "bay", "box", size=[40, 40, 40])
    _ops(document, "left", "box", size=[10, 10, 10])
    _ops(document, "right", "box", size=[10, 10, 10], center=[6, 0, 0])
    report = document.check_packaging("bay", ["left", "right"]).to_dict()
    assert report["passed"] is False
    assert _part(report, "left")["passed"] is True
    assert _part(report, "right")["passed"] is True
    assert len(report["overlaps"]) == 1
    overlap = report["overlaps"][0]
    assert overlap["part_a"] == "left"
    assert overlap["part_b"] == "right"
    assert overlap["volume_mm3"] == pytest.approx(400)
    assert overlap["at_mm"]["x"] == pytest.approx(3)
    assert overlap["at_mm"]["y"] == pytest.approx(0)
    assert overlap["at_mm"]["z"] == pytest.approx(0)


def test_overlap_outside_the_envelope_is_not_counted(tmp_path):
    document = Document(tmp_path)
    _ops(document, "left", "box", size=[10, 10, 10], center=[5, 5, 5])
    _ops(document, "right", "box", size=[10, 10, 10], center=[13, 5, 5])
    # Envelope x is [0, 9], so only 1 mm of the 2 mm overlap is inside.
    report = document.check_packaging(
        {"shape": "box", "size": [9, 12, 12], "center": [4.5, 5, 5]},
        ["left", "right"],
    ).to_dict()
    assert report["overlaps"][0]["volume_mm3"] == pytest.approx(100)
    assert report["overlaps"][0]["at_mm"]["x"] == pytest.approx(8.5)

    missed = document.check_packaging(
        {"shape": "box", "size": [10, 10, 10], "center": [100, 0, 0]},
        ["left", "right"],
    ).to_dict()
    assert missed["overlaps"] == []
    assert missed["passed"] is False
    assert _part(missed, "left")["protrudes"] is True


def test_an_imported_step_envelope_matches_the_modeled_solid(tmp_path):
    document = Document(tmp_path)
    _ops(document, "housing", "box", size=[20, 20, 20])
    _ops(document, "brick", "box", size=[10, 8, 6])
    step = document.export("housing", tmp_path / "housing.step", "step")
    document.import_step("bay", step)
    modeled = document.check_packaging("housing", ["brick"]).to_dict()
    imported = document.check_packaging("bay", ["brick"]).to_dict()
    assert imported["envelope"]["kind"] == "part"
    assert imported["min_clearance_mm"] == pytest.approx(modeled["min_clearance_mm"])
    assert imported["min_clearance_mm"] == pytest.approx(5)


def test_a_posed_assembly_can_be_checked_without_changing_the_pose(tmp_path):
    document = Document(tmp_path)
    _ops(document, "block", "box", size=[10, 10, 10])
    _ops(document, "ground", "box", size=[4, 4, 4])
    document.create_assembly(
        {
            "name": "slide",
            "instances": [
                {"name": "base", "part": "ground"},
                {"name": "slider", "part": "block"},
            ],
            "joints": [
                {
                    "name": "travel",
                    "type": "prismatic",
                    "parent": "base",
                    "child": "slider",
                    "axis": [1, 0, 0],
                    "value": 0,
                }
            ],
            "poses": {"home": {"travel": 0}, "out": {"travel": 18}},
        }
    )
    envelope = {"shape": "box", "size": [40, 40, 40]}
    home = document.check_packaging(envelope, ["slider"], assembly="slide", pose="home").to_dict()
    assert home["assembly"] == "slide"
    assert home["pose"] == "home"
    assert home["joints"]["travel"] == pytest.approx(0)
    assert _part(home, "slider")["min_clearance_mm"] == pytest.approx(15)
    assert home["passed"] is True

    out = document.check_packaging(envelope, ["slider"], assembly="slide", pose="out").to_dict()
    slider = _part(out, "slider")
    # Travel 18 moves the centered 10 mm block to x = [13, 23]. The wall is at 20.
    assert slider["max_protrusion_mm"] == pytest.approx(3)
    assert slider["protrusion_at_mm"]["x"] == pytest.approx(23)
    assert out["passed"] is False

    nudged = document.check_packaging(
        envelope,
        ["slider"],
        assembly="slide",
        joints={"travel": 18},
    ).to_dict()
    assert _part(nudged, "slider")["max_protrusion_mm"] == pytest.approx(3)
    stored = document.get_assembly("slide")["solved"]["joints"]
    assert stored[0]["value"] == pytest.approx(0)


def test_touching_the_wall_passes_a_zero_gap_and_fails_a_required_gap(tmp_path):
    document = Document(tmp_path)
    _ops(document, "bay", "box", size=[20, 20, 20])
    _ops(document, "fill", "box", size=[20, 20, 20])
    touch = document.check_packaging("bay", ["fill"], min_clearance_mm=0).to_dict()
    assert _part(touch, "fill")["min_clearance_mm"] == pytest.approx(0)
    assert _part(touch, "fill")["protrudes"] is False
    assert touch["passed"] is True
    gap = document.check_packaging("bay", ["fill"], min_clearance_mm=0.1).to_dict()
    assert gap["passed"] is False
    assert _part(gap, "fill")["protrudes"] is False


def test_a_part_entirely_outside_reports_the_farthest_point(tmp_path):
    document = Document(tmp_path)
    _ops(document, "bay", "box", size=[20, 20, 20])
    _ops(document, "away", "box", size=[10, 10, 10], center=[50, 0, 0])
    report = document.check_packaging("bay", ["away"]).to_dict()
    away = _part(report, "away")
    assert away["max_protrusion_mm"] == pytest.approx(45)
    assert away["min_clearance_mm"] == pytest.approx(-45)
    assert away["protrusion_at_mm"]["x"] == pytest.approx(55)


def test_packaging_rejects_bad_requests(tmp_path):
    document = Document(tmp_path)
    _ops(document, "bay", "box", size=[20, 20, 20])
    _ops(document, "brick", "box", size=[4, 4, 4])
    with pytest.raises(GeometryError, match="cannot be negative"):
        document.check_packaging("bay", ["brick"], min_clearance_mm=-1)
    with pytest.raises(GeometryError, match="envelope"):
        document.check_packaging("bay", ["bay", "brick"])
    with pytest.raises(GeometryError, match="no area"):
        document.check_packaging({"outline": [[0, 0], [1, 0], [2, 0]], "length": 5}, ["brick"])
    with pytest.raises(GeometryError, match="twice"):
        document.check_packaging("bay", ["brick", "brick"])
    document.create_assembly(
        {"name": "only", "instances": [{"name": "base", "part": "brick"}], "joints": []}
    )
    with pytest.raises(GeometryError, match="pose"):
        document.check_packaging("bay", assembly="only", pose="missing")


def test_the_command_line_and_workspace_return_the_same_check(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.execute(
        "create_part_from_ops",
        {"name": "brick", "ops": [{"op": "add", "shape": "box", "size": [10, 8, 6]}]},
    )
    report = workspace.execute(
        "check_packaging",
        {
            "envelope": {"shape": "box", "size": [20, 20, 20]},
            "parts": ["brick"],
            "min_clearance_mm": 5,
        },
    )
    assert report["result_label"] == "Verified check"
    assert report["passed"] is True
    assert report["min_clearance_mm"] == pytest.approx(5)
    assert report["sheet"]["path"]

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "aicad",
            "check-packaging",
            "--envelope",
            json.dumps({"shape": "box", "size": [20, 20, 20]}),
            "--parts",
            "brick",
            "--min",
            "5",
            "--workspace",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["passed"] is True
    assert payload["min_clearance_mm"] == pytest.approx(5)
    assert payload["result_kind"] == "verified_check"
