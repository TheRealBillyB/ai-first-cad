"""A joint whose solids overlap is recorded, and is not called a collision."""

from pathlib import Path

import pytest

from aicad.document import Document
from aicad.workspace import Workspace


def _hinge(document: Document, pin_radius: float, tab: bool) -> None:
    plate = [
        {"op": "add", "shape": "box", "size": [30, 30, 6], "center": [0, 0, 0]},
        {"op": "cut", "shape": "cylinder", "radius": 4, "height": 10, "center": [0, 0, 0]},
    ]
    if tab:
        plate.append({"op": "cut", "shape": "box", "size": [16, 4, 10], "center": [8, 0, 0]})
    document.create_part_from_ops("plate", plate)
    pin = [{"op": "add", "shape": "cylinder", "radius": pin_radius, "height": 4, "center": [0, 0, 0]}]
    if tab:
        pin.append({"op": "add", "shape": "box", "size": [6, 2, 2], "center": [6, 0, 0]})
    document.create_part_from_ops("pin", pin)
    document.create_assembly(
        {
            "name": "hinge",
            "instances": [{"name": "plate", "part": "plate"}, {"name": "pin", "part": "pin"}],
            "joints": [
                {
                    "name": "spin",
                    "type": "revolute",
                    "parent": "plate",
                    "child": "pin",
                    "axis": [0, 0, 1],
                    "min": 0,
                    "max": 90,
                }
            ],
            "studies": {
                "quarter": {
                    "duration_s": 1,
                    "steps": 3,
                    "drivers": [{"joint": "spin", "expression": "90 * t"}],
                }
            },
        }
    )


def test_an_overlapping_pin_is_fused_and_not_a_collision(tmp_path: Path):
    document = Document(tmp_path)
    _hinge(document, 4.2, tab=False)
    report = document.run_motion_study("hinge", sequence="quarter")
    assert report["status"] == "clear"
    assert report["collision_count"] == 0
    assert [row["joint"] for row in report["fused_joints"]] == ["spin"]
    assert report["fused_joints"][0]["steps"] == [0, 1, 2]
    assert report["fused_joints"][0]["max_overlap_mm3"] > 0
    assert "spin" in report["note"]
    for step in report["steps"]:
        seat = step["joint_seats"][0]
        assert seat["fused"] is True
        assert seat["gap_mm"] == 0


def test_a_clearance_pin_keeps_its_gap_through_the_turn(tmp_path: Path):
    document = Document(tmp_path)
    _hinge(document, 3.0, tab=False)
    report = document.run_motion_study("hinge", sequence="quarter")
    assert report["status"] == "clear"
    assert report["fused_joints"] == []
    for step in report["steps"]:
        seat = step["joint_seats"][0]
        assert seat["fused"] is False
        assert seat["gap_mm"] == pytest.approx(1.0, abs=0.05)


def test_a_tab_is_clear_in_its_slot_and_fused_when_it_turns(tmp_path: Path):
    document = Document(tmp_path)
    _hinge(document, 3.0, tab=True)
    report = document.run_motion_study("hinge", sequence="quarter")
    assert report["status"] == "clear"
    assert report["collision_count"] == 0
    assert report["steps"][0]["joint_seats"][0]["fused"] is False
    assert report["steps"][0]["joint_seats"][0]["gap_mm"] > 0.2
    assert report["steps"][2]["joints"][0]["value"] == pytest.approx(90)
    assert report["steps"][2]["joint_seats"][0]["fused"] is True
    assert report["fused_joints"][0]["joint"] == "spin"
    assert 2 in report["fused_joints"][0]["steps"]
    assert 0 not in report["fused_joints"][0]["steps"]

    motion = document.run_motion(
        "hinge",
        motion={"name": "turn", "sweep": {"joint": "spin", "start": 0, "end": 90, "frames": 3}},
        render=False,
    )
    assert motion["status"] == "clear"
    assert motion["collision_count"] == 0
    assert motion["fused_joints"] == ["spin"]
    assert motion["frames"][0]["joint_seats"][0]["fused"] is False
    assert motion["frames"][2]["joint_seats"][0]["fused"] is True
    workspace = Workspace(tmp_path)
    again = workspace.execute("run_motion_study", {"name": "hinge", "sequence": "quarter"})
    assert again["fused_joints"][0]["joint"] == "spin"
