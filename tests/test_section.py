"""Planar sections and the section-backed wall estimate."""

import math
from pathlib import Path

import pytest

from aicad import Document


def test_axis_section_of_a_plate_reports_area_and_thickness(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("plate", "part = Pos(0, 0, 1) * Box(10, 10, 2)\n")
    report = document.section("plate", {"axis": "y", "offset": 0})

    assert report.area_mm2 == pytest.approx(20)
    assert report.face_count == 1
    assert report.min_thickness_mm == pytest.approx(2)
    assert sorted((report.width_mm, report.height_mm)) == pytest.approx([2, 10])
    assert report.view is not None
    assert Path(report.view.path).read_bytes().startswith(b"\x89PNG")
    assert any(loop.kind == "outer" for loop in report.loops)


def test_section_keeps_a_hole_and_an_arbitrary_plane(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script(
        "plate",
        "part = Box(10, 10, 4) - Cylinder(2, 8)\n",
    )
    report = document.section("plate", {"axis": "z", "offset": 0})
    assert report.area_mm2 == pytest.approx(100 - math.pi * 4, rel=1e-3)
    assert any(loop.kind == "hole" for loop in report.loops)

    same = document.section("plate", {"origin": [0, 0, 0], "normal": [0, 0, 1]})
    assert same.area_mm2 == pytest.approx(report.area_mm2)


def test_a_plane_that_misses_is_an_empty_section(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("box", "part = Box(4, 4, 4)\n")
    report = document.section("box", {"axis": "z", "offset": 100})
    assert report.area_mm2 == 0
    assert report.face_count == 0
    assert report.min_thickness_mm is None
    assert Path(report.view.path).is_file()


def test_wall_report_names_the_method(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("plate", "part = Pos(0, 0, 1) * Box(10, 10, 2)\n")
    wall = document.check_wall_thickness("plate", min_wall_mm=1.2, samples=3)
    assert wall.estimated_min_mm == pytest.approx(2.0)
    assert wall.ray_estimate_mm == pytest.approx(2.0)
    assert wall.section_estimate_mm == pytest.approx(2.0)
    assert wall.section_count == 9
    assert wall.method == "ray+section"
    assert wall.passed is True
    assert "section" in wall.note
