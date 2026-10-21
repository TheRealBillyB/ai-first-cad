"""Geometry measurements, scripts, operations, and checks."""

import math

import pytest

from aicad import Document
from aicad.errors import GeometryError, ScriptError

BOX = "part = Box(10, 20, 30)\n"


def test_box_measurements(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("box", BOX)
    measured = document.measure("box")

    assert measured.units == "mm"
    assert measured.volume_mm3 == pytest.approx(6000)
    assert measured.area_mm2 == pytest.approx(2200)
    assert measured.valid is True
    assert measured.watertight is True
    assert measured.solid_count == 1
    assert (measured.face_count, measured.edge_count, measured.vertex_count) == (6, 12, 8)
    assert measured.bounding_box.size.x == pytest.approx(10)
    assert measured.bounding_box.size.y == pytest.approx(20)
    assert measured.bounding_box.size.z == pytest.approx(30)
    assert measured.bounding_box.min.z == pytest.approx(-15)
    assert measured.center_of_mass_mm.x == pytest.approx(0, abs=1e-6)
    assert measured.center_of_mass_mm.y == pytest.approx(0, abs=1e-6)
    assert measured.center_of_mass_mm.z == pytest.approx(0, abs=1e-6)


def test_hole_reduces_volume_and_stays_closed(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script(
        "plate",
        "part = Box(10, 10, 4) - Cylinder(1.5, 8)\n",
    )
    measured = document.measure("plate")
    removed = math.pi * 1.5**2 * 4
    assert measured.volume_mm3 == pytest.approx(400 - removed, rel=1e-6)
    assert measured.watertight is True
    assert measured.valid is True
    assert measured.face_count > 6


def test_script_must_assign_a_solid(tmp_path):
    document = Document(tmp_path)
    with pytest.raises(ScriptError, match="assign"):
        document.create_part_from_script("empty", "x = 1\n")
    with pytest.raises(ScriptError, match="no solid volume"):
        document.create_part_from_script("sketch", "part = Circle(5)\n")


def test_script_error_names_the_line(tmp_path):
    document = Document(tmp_path)
    with pytest.raises(ScriptError, match=r"line 2"):
        document.create_part_from_script("bad", "part = Box(1, 1, 1)\npart = missing\n")


def test_failed_update_keeps_the_previous_solid(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("box", BOX)
    with pytest.raises(ScriptError):
        document.update_part_script("box", "part = no_such_name\n")
    assert document.measure("box").volume_mm3 == pytest.approx(6000)


def test_ops_box_with_hole_and_horizontal_cylinder(tmp_path):
    document = Document(tmp_path)
    info = document.create_part_from_ops(
        "plate",
        [
            {"op": "add", "shape": "box", "size": [10, 20, 4], "center": [0, 0, 2]},
            {"op": "cut", "shape": "cylinder", "radius": 1.5, "height": 10, "center": [0, 0, 2]},
        ],
    )
    removed = math.pi * 1.5**2 * 4
    assert info.volume_mm3 == pytest.approx(800 - removed, rel=1e-6)

    document.create_part_from_ops(
        "pin",
        [{"op": "add", "shape": "cylinder", "radius": 1, "height": 10, "axis": [1, 0, 0]}],
    )
    size = document.measure("pin").bounding_box.size
    assert size.x == pytest.approx(10)
    assert size.y == pytest.approx(2)
    assert size.z == pytest.approx(2)


def test_ops_reject_a_cut_of_nothing(tmp_path):
    document = Document(tmp_path)
    with pytest.raises(GeometryError, match="no solid yet"):
        document.create_part_from_ops(
            "bad",
            [{"op": "cut", "shape": "box", "size": [1, 1, 1]}],
        )


def test_plate_and_tube_wall_thickness(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("plate", "part = Box(10, 10, 2)\n")
    plate = document.check_wall_thickness("plate", min_wall_mm=1.2)
    assert plate.estimated_min_mm == pytest.approx(2.0, abs=0.05)
    assert plate.passed is True
    assert plate.sample_count > 0
    assert plate.thinnest_point_mm is not None

    too_thick = document.check_wall_thickness("plate", min_wall_mm=5)
    assert too_thick.passed is False

    document.create_part_from_script(
        "tube",
        "part = Cylinder(10, 20) - Cylinder(8, 24)\n",
    )
    tube = document.check_wall_thickness("tube", min_wall_mm=1.5)
    assert tube.estimated_min_mm == pytest.approx(2.0, abs=0.05)
    assert tube.passed is True


def test_interference_and_clearance(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("left", "part = Box(10, 10, 10)\n")
    document.create_part_from_script("overlap", "part = Pos(5, 0, 0) * Box(10, 10, 10)\n")
    document.create_part_from_script("apart", "part = Pos(30, 0, 0) * Box(10, 10, 10)\n")

    hit = document.check_clearance("left", "overlap", min_clearance_mm=0.2)
    assert hit.intersecting is True
    assert hit.interference_volume_mm3 == pytest.approx(500, rel=1e-4)
    assert hit.min_distance_mm == 0
    assert hit.passed is False

    gap = document.check_clearance("left", "apart", min_clearance_mm=0)
    assert gap.intersecting is False
    assert gap.min_distance_mm == pytest.approx(20)
    assert gap.passed is True
    assert gap.closest_point_a_mm is not None

    # 10 mm plate with a 5 mm radius hole, and a 4.8 mm radius pin centered in it.
    document.create_part_from_script(
        "block",
        "part = Box(20, 20, 10) - Cylinder(5, 14)\n",
    )
    document.create_part_from_script("pin", "part = Cylinder(4.8, 8)\n")
    fit = document.check_clearance("block", "pin", min_clearance_mm=0.15)
    assert fit.intersecting is False
    assert fit.min_distance_mm == pytest.approx(0.2, abs=0.02)
    assert fit.passed is True
    tight = document.check_clearance("block", "pin", min_clearance_mm=0.5)
    assert tight.passed is False
