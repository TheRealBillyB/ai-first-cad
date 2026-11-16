"""A lead-screw relation, and the order a fixed joint's rotation_deg turns in."""

import math

import pytest

from aicad.errors import GeometryError
from aicad.kinematics import _euler, normalize, solve
from aicad.power import _load_shift


def _spec(relation: dict, *, screw_axis=(0, 0, 1), nut_axis=(0, 0, 1), turn=720.0) -> dict:
    return {
        "name": "lift",
        "instances": [{"name": "frame", "part": "frame"}, {"name": "screw", "part": "screw"}, {"name": "nut", "part": "nut"}],
        "joints": [
            {"name": "spin", "type": "revolute", "parent": "frame", "child": "screw", "axis": list(screw_axis), "value": turn},
            {"name": "travel", "type": "prismatic", "parent": "frame", "child": "nut", "axis": list(nut_axis), "value": 0},
        ],
        "relations": [{"name": "lead", "type": "screw", "driver": "spin", "driven": "travel", **relation}],
    }


def _value(solved: dict, name: str) -> float:
    return next(joint["value"] for joint in solved["joints"] if joint["name"] == name)


def test_two_turns_of_a_2mm_lead_move_the_nut_4mm():
    solved = solve(_spec({"lead_mm": 2.0}))
    assert _value(solved, "travel") == pytest.approx(4.0)
    nut = next(item for item in solved["instances"] if item["name"] == "nut")
    assert nut["matrix"][2][3] == pytest.approx(4.0)
    row = solved["relations"][0]
    assert (row["type"], row["lead_mm"], row["hand"]) == ("screw", 2.0, "right")


def test_a_left_hand_screw_moves_the_other_way_and_takes_an_offset():
    solved = solve(_spec({"lead_mm": 8.0, "hand": "left", "offset": 1.5}, turn=90.0))
    assert _value(solved, "travel") == pytest.approx(-2.0 + 1.5)


def test_the_slide_stops_at_its_limit():
    spec = _spec({"lead_mm": 5.0}, turn=3600.0)
    spec["joints"][1]["max"] = 20.0
    solved = solve(spec)
    assert _value(solved, "travel") == pytest.approx(20.0)
    assert solved["warnings"]


@pytest.mark.parametrize(
    ("relation", "message"),
    [
        ({}, "needs lead_mm"),
        ({"lead_mm": 0}, "positive"),
        ({"lead_mm": -2}, "positive"),
        ({"lead_mm": float("nan")}, "positive"),
        ({"lead_mm": "two"}, "must be a number"),
        ({"lead_mm": 2, "hand": "both"}, "right or left"),
    ],
)
def test_a_bad_screw_is_refused(relation, message):
    with pytest.raises(GeometryError, match=message):
        normalize(_spec(relation))


def test_a_screw_turns_a_rotary_joint_into_a_slide_only():
    spec = _spec({"lead_mm": 2})
    spec["relations"][0]["driver"], spec["relations"][0]["driven"] = "travel", "spin"
    with pytest.raises(GeometryError, match="revolute or cylindrical driver"):
        normalize(spec)


def test_a_stored_screw_normalizes_to_the_same_record():
    once = normalize(_spec({"lead_mm": 3, "hand": "left"}))
    twice = normalize(once)
    assert twice["relations"] == once["relations"]


def test_the_slide_force_becomes_the_ideal_screw_torque():
    """T = F * lead / (2 pi). 1000 N on a 2 mm lead needs about 0.318 N m."""
    ratio = normalize(_spec({"lead_mm": 2.0}))["relations"][0]["ratio"]
    torque, force = _load_shift({"type": "revolute"}, {"type": "prismatic"}, 0.0, 1000.0, ratio)
    assert abs(torque) == pytest.approx(1000.0 * 0.002 / (2 * math.pi))
    assert force == 0.0


def test_rotation_deg_turns_about_x_then_the_turned_y_then_the_twice_turned_z():
    """About the child's own axes: X first. About fixed axes the same matrix is Z first."""
    matrix = _euler(90.0, 0.0, 90.0)
    # X by 90 takes the child's Z to world -Y. A turn about that axis then takes
    # the child's X to world +Z.
    x_axis = [row[0] for row in matrix]
    assert x_axis == pytest.approx([0.0, 0.0, 1.0], abs=1e-12)
