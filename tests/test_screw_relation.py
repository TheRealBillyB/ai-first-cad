"""A lead-screw relation, and the order a fixed joint's rotation_deg turns in."""

import math

import pytest

from aicad.errors import GeometryError
from aicad.kinematics import _euler, normalize, solve
from aicad.power import _load_shift


def _spec(relation: dict, *, screw_axis=(0, 0, 1), nut_axis=(0, 0, 1), turn=720.0) -> dict:
    """A screw that turns in place on the frame, and a nut that slides on the frame."""
    return {
        "name": "lift",
        "instances": [{"name": "frame", "part": "frame"}, {"name": "screw", "part": "screw"}, {"name": "nut", "part": "nut"}],
        "joints": [
            {"name": "spin", "type": "revolute", "parent": "frame", "child": "screw", "axis": list(screw_axis), "value": turn},
            {"name": "travel", "type": "prismatic", "parent": "frame", "child": "nut", "axis": list(nut_axis), "value": 0},
        ],
        "relations": [{"name": "lead", "type": "screw", "driver": "spin", "driven": "travel", **relation}],
    }


def _vise(relation: dict, *, turn=720.0) -> dict:
    """A screw that runs through a nut fixed in the frame: the carriage slides and carries the turning screw."""
    return {
        "name": "vise",
        "instances": [{"name": "frame", "part": "frame"}, {"name": "carriage", "part": "carriage"}, {"name": "screw", "part": "screw"}],
        "joints": [
            {"name": "travel", "type": "prismatic", "parent": "frame", "child": "carriage", "axis": [0, 0, 1], "value": 0},
            {"name": "spin", "type": "revolute", "parent": "carriage", "child": "screw", "axis": [0, 0, 1], "value": turn},
        ],
        "relations": [{"name": "lead", "type": "screw", "driver": "spin", "driven": "travel", **relation}],
    }


def _value(solved: dict, name: str) -> float:
    return next(joint["value"] for joint in solved["joints"] if joint["name"] == name)


def _z(solved: dict, instance: str) -> float:
    return next(item for item in solved["instances"] if item["name"] == instance)["matrix"][2][3]


def test_a_right_hand_screw_turning_in_place_moves_its_nut_toward_minus_axis():
    """Two positive turns of a 2 mm right-hand lead pull the nut 4 mm down the screw's axis."""
    solved = solve(_spec({"lead_mm": 2.0}))
    assert _value(solved, "travel") == pytest.approx(-4.0)
    assert _z(solved, "nut") == pytest.approx(-4.0)
    assert _z(solved, "screw") == pytest.approx(0.0)
    row = solved["relations"][0]
    assert (row["type"], row["lead_mm"], row["hand"], row["slide_turns"]) == ("screw", 2.0, "right", False)


def test_a_right_hand_screw_through_a_fixed_nut_moves_itself_toward_plus_axis():
    solved = solve(_vise({"lead_mm": 2.0}))
    assert _value(solved, "travel") == pytest.approx(4.0)
    assert _z(solved, "screw") == pytest.approx(4.0)
    assert solved["relations"][0]["slide_turns"] is True


def test_a_left_hand_thread_flips_both_cases():
    assert _value(solve(_spec({"lead_mm": 2.0, "hand": "left"})), "travel") == pytest.approx(4.0)
    assert _value(solve(_vise({"lead_mm": 2.0, "hand": "left"})), "travel") == pytest.approx(-4.0)


def test_a_left_hand_screw_takes_an_offset():
    solved = solve(_spec({"lead_mm": 8.0, "hand": "left", "offset": 1.5}, turn=90.0))
    assert _value(solved, "travel") == pytest.approx(2.0 + 1.5)


def test_a_nut_turning_in_place_drives_a_screw_that_cannot_turn_like_a_traveling_nut():
    """The thread is the same pair of bodies seen from the other side: right-hand still moves the slide toward -axis."""
    spec = _spec({"lead_mm": 2.0})
    spec["instances"] = [{"name": "frame", "part": "frame"}, {"name": "nut", "part": "nut"}, {"name": "screw", "part": "screw"}]
    spec["joints"][0]["child"], spec["joints"][1]["child"] = "nut", "screw"
    assert _value(solve(spec), "travel") == pytest.approx(-4.0)


def test_a_slide_axis_pointing_the_other_way_flips_the_sign_of_its_value():
    """The nut moves the same way in space: -4 mm along +Z is +4 mm along -Z."""
    solved = solve(_spec({"lead_mm": 2.0}, nut_axis=(0, 0, -1)))
    assert _value(solved, "travel") == pytest.approx(4.0)
    assert _z(solved, "nut") == pytest.approx(-4.0)


def test_fixed_joints_between_still_classify_and_their_turn_counts():
    """The screw sits on a bracket turned 180 degrees about X, so its +Z is the frame's -Z."""
    spec = _spec({"lead_mm": 2.0}, screw_axis=(0, 0, 1))
    spec["instances"].append({"name": "bracket", "part": "bracket"})
    spec["joints"].insert(0, {"name": "mount", "type": "fixed", "parent": "frame", "child": "bracket", "rotation_deg": [180, 0, 0]})
    spec["joints"][1]["parent"] = "bracket"
    solved = solve(spec)
    # A positive turn about the frame's -Z is a negative turn about +Z, so a
    # right-hand thread moves the nut toward the frame's +Z.
    assert _value(solved, "travel") == pytest.approx(4.0)


def test_a_moving_joint_between_the_turn_and_the_slide_is_refused_by_name():
    spec = _spec({"lead_mm": 2.0})
    spec["instances"].append({"name": "arm", "part": "arm"})
    spec["joints"].insert(0, {"name": "swing", "type": "revolute", "parent": "frame", "child": "arm", "axis": [1, 0, 0]})
    spec["joints"][1]["parent"] = "arm"
    with pytest.raises(GeometryError, match="cannot tell which part carries the thread: 'swing' also moves between 'spin' and 'travel'"):
        normalize(spec)


def test_axes_that_are_not_parallel_are_refused():
    with pytest.raises(GeometryError, match="'spin' and 'travel' on one axis. Their axes are 90.0 degrees apart"):
        normalize(_spec({"lead_mm": 2.0}, nut_axis=(1, 0, 0)))


def test_the_slide_stops_at_its_limit():
    spec = _vise({"lead_mm": 5.0}, turn=3600.0)
    spec["joints"][0]["max"] = 20.0
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
