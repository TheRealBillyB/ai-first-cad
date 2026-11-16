"""A clearance check says where two parts overlap, and checks two instances in one pose.

It said only that the parts overlapped and by how much volume. Where, and
in how many places, had to be found by hand. Two instances in one pose of an
assembly needed a full motion study.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError


def _box(document: Document, name: str, size, center) -> None:
    document.create_part_from_ops(name, [{"op": "add", "shape": "box", "size": size, "center": center}])


def test_an_overlap_says_where_it_is(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "a", [10, 10, 10], [0, 0, 0])
    _box(document, "b", [10, 10, 10], [8, 0, 0])
    report = document.check_clearance("a", "b").to_dict()
    assert report["intersecting"] is True
    where = report["overlap"]
    assert where["region_count"] == 1
    assert where["center_mm"] == pytest.approx({"x": 4.0, "y": 0.0, "z": 0.0}, abs=1e-6)
    assert where["box_min_mm"] == pytest.approx([3, -5, -5], abs=1e-6)
    assert where["box_max_mm"] == pytest.approx([5, 5, 5], abs=1e-6)
    assert where["regions"][0]["volume_mm3"] == pytest.approx(200, abs=1e-3)
    assert where["penetration_depth_mm"] == pytest.approx(2.0, abs=1e-6)


def test_each_separate_overlap_is_listed_largest_first(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "plate", [40, 10, 4], [0, 0, 0])
    document.create_part_from_ops(
        "fork",
        [
            {"op": "add", "shape": "box", "size": [4, 10, 10], "center": [-10, 0, 3]},
            {"op": "add", "shape": "box", "size": [8, 10, 10], "center": [12, 0, 3]},
            {"op": "add", "shape": "box", "size": [30, 10, 2], "center": [1, 0, 9]},
        ],
    )
    where = document.check_clearance("plate", "fork").to_dict()["overlap"]
    assert where["region_count"] == 2
    first, second = where["regions"]
    assert first["volume_mm3"] > second["volume_mm3"]
    assert first["center_mm"]["x"] == pytest.approx(12, abs=1e-6)
    assert second["center_mm"]["x"] == pytest.approx(-10, abs=1e-6)


def test_parts_that_do_not_overlap_have_no_overlap(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "a", [10, 10, 10], [0, 0, 0])
    _box(document, "b", [10, 10, 10], [20, 0, 0])
    report = document.check_clearance("a", "b").to_dict()
    assert report["intersecting"] is False and report["overlap"] is None
    assert report["min_distance_mm"] == pytest.approx(10.0)


def _hinge(document: Document) -> None:
    # The arm's corner is at local (100, 10). At angle t its height is 100 sin t + 10 cos t,
    # and the wall's face is at y = 70: clear at 20 degrees (43.6), into the wall at 40 (71.9).
    _box(document, "base", [10, 10, 10], [0, 0, 5])
    _box(document, "arm", [100, 20, 40], [50, 0, 20])
    _box(document, "wall", [400, 40, 80], [0, 90, 20])
    document.create_assembly(
        {
            "name": "hinge",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "link", "part": "arm"},
                {"name": "stop", "part": "wall"},
            ],
            "joints": [
                {"name": "hinge", "type": "revolute", "parent": "base", "child": "link", "axis": [0, 0, 1], "min": 0, "max": 90},
                {"name": "mount", "type": "fixed", "parent": "base", "child": "stop"},
            ],
        }
    )


def test_two_instances_are_checked_in_one_pose(tmp_path: Path):
    document = Document(tmp_path)
    _hinge(document)
    clear = document.check_clearance("link", "stop", assembly="hinge", joints={"hinge": 20}).to_dict()
    assert clear["intersecting"] is False
    assert clear["min_distance_mm"] == pytest.approx(70 - (100 * 0.3420201 + 10 * 0.9396926), abs=0.01)
    assert clear["assembly"] == "hinge" and clear["pose"]["joints"] == {"hinge": 20}
    hit = document.check_clearance("link", "stop", assembly="hinge", joints={"hinge": 40}).to_dict()
    assert hit["intersecting"] is True
    assert hit["overlap"]["box_min_mm"][1] == pytest.approx(70, abs=1e-3)
    # The stored assembly is unchanged.
    joint = next(j for j in document.get_assembly("hinge")["assembly"]["joints"] if j["name"] == "hinge")
    assert joint.get("value", 0) == 0


def test_a_pose_without_an_assembly_or_a_missing_instance_is_refused(tmp_path: Path):
    document = Document(tmp_path)
    _hinge(document)
    with pytest.raises(GeometryError, match="need an assembly"):
        document.check_clearance("arm", "wall", joints={"hinge": 20})
    with pytest.raises(GeometryError, match="No instance 'nope'"):
        document.check_clearance("link", "nope", assembly="hinge")
