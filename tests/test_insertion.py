"""Each part can go in along a straight path, in order, past the parts already there.

A simplified vise: a body with a fixed jaw and two rails, and a moving jaw
that rides on the rail tops and slides in from the open end. In the first
version the cap-pin cheeks sit on the rail tops at the open end, inside the
jaw's width, so the jaw has no way on even though its seated pose is clear.
In the second the cheeks are outboard of the rails and the jaw slides on.
"""

import pytest

from aicad.errors import GeometryError
from aicad.workspace import Workspace

_BASE = [
    {"op": "add", "shape": "box", "size": [100, 60, 5], "center": [50, 0, 2.5]},
    # Rails along X, their tops at z = 20.
    {"op": "add", "shape": "box", "size": [100, 6, 15], "center": [50, -27, 12.5]},
    {"op": "add", "shape": "box", "size": [100, 6, 15], "center": [50, 27, 12.5]},
    # The fixed jaw closes the -X end.
    {"op": "add", "shape": "box", "size": [8, 60, 35], "center": [4, 0, 22.5]},
]
# v1: cheeks on the rail tops at the open end, inside the moving jaw's width.
_CHEEKS_ON_RAILS = [
    {"op": "add", "shape": "box", "size": [10, 6, 12], "center": [95, -27, 26]},
    {"op": "add", "shape": "box", "size": [10, 6, 12], "center": [95, 27, 26]},
]
# v2: cheeks outboard of the rails, full height, clear of the jaw's sides.
_CHEEKS_OUTBOARD = [
    {"op": "add", "shape": "box", "size": [10, 5, 32], "center": [95, -32.8, 16]},
    {"op": "add", "shape": "box", "size": [10, 5, 32], "center": [95, 32.8, 16]},
]
# The moving jaw sits on the rails with 0.3 mm under it, seated at x = 10..30.
_JAW = [{"op": "add", "shape": "box", "size": [20, 60, 15], "center": [20, 0, 27.8]}]
# An end cap over the open end, past the rails.
_CAP = [{"op": "add", "shape": "box", "size": [6, 60, 30], "center": [103, 0, 15]}]


def _vise(tmp_path, cheeks, insertion=None):
    workspace = Workspace(tmp_path / "project")
    for name, ops in (("body", _BASE + cheeks), ("jaw", _JAW), ("cap", _CAP)):
        workspace.execute("create_part_from_ops", {"name": name, "ops": ops}, source="agent")
    spec = {
        "name": "vise",
        "instances": [
            {"name": "body", "part": "body"},
            {"name": "jaw", "part": "jaw"},
            {"name": "cap", "part": "cap"},
        ],
        "joints": [
            {"name": "slide", "type": "prismatic", "parent": "body", "child": "jaw", "axis": [1, 0, 0], "min": -5, "max": 60},
            {"name": "cap_on", "type": "fixed", "parent": "body", "child": "cap"},
        ],
    }
    if insertion is not None:
        spec["insertion"] = insertion
    workspace.execute("create_assembly", {"spec": spec}, source="agent")
    return workspace


_JAW_THEN_CAP = [{"instance": "jaw", "direction": [-1, 0, 0]}, {"instance": "cap", "direction": [0, 0, -1]}]


def test_a_jaw_blocked_by_cheeks_on_its_rails_is_flagged_with_the_right_pair(tmp_path):
    workspace = _vise(tmp_path, _CHEEKS_ON_RAILS, _JAW_THEN_CAP)
    report = workspace.execute("check_insertion", {"name": "vise"}, source="agent")
    assert report["status"] == "blocked"
    assert report["plan_from"] == "assembly"
    jaw = report["results"][0]
    assert jaw["instance"] == "jaw" and jaw["status"] == "blocked"
    first = jaw["first_collision"]
    assert (first["moving"], first["other"]) == ("jaw", "body")
    # The jaw starts past the body's end at x = 100 and meets the cheeks there.
    # Both cheeks, on the rail tops: the overlap is between them, above the rails.
    assert 90 <= first["at_mm"][0] <= 100
    assert 20 <= first["at_mm"][2] <= 32
    assert first["distance_from_seated_mm"] == pytest.approx(90.0, abs=0.06)
    assert first["starts_blocked"] is False
    assert "jaw cannot go in: it hits body" in report["note"]
    assert report["result_label"] == "Verified check"


def test_the_same_jaw_slides_on_when_the_cheeks_are_outboard(tmp_path):
    workspace = _vise(tmp_path, _CHEEKS_OUTBOARD, _JAW_THEN_CAP)
    report = workspace.execute("check_insertion", {"name": "vise"}, source="agent")
    assert report["status"] == "clear"
    jaw, cap = report["results"]
    assert jaw["status"] == "clear"
    assert jaw["direction"] == [-1.0, 0.0, 0.0]
    assert jaw["positions_checked"] >= 90
    assert jaw["seated_overlaps"] == []
    assert cap["status"] == "clear"
    assert report["order"] == ["jaw", "cap"]
    assert report["in_place_at_start"] == ["body"]


def test_the_order_matters_a_cap_already_on_blocks_the_jaw(tmp_path):
    workspace = _vise(tmp_path, _CHEEKS_OUTBOARD)
    # Only the jaw is planned, so the cap is in place from the start.
    report = workspace.execute(
        "check_insertion", {"name": "vise", "insertion": [{"instance": "jaw", "direction": [-1, 0, 0]}]}, source="agent"
    )
    assert report["plan_from"] == "call"
    first = report["results"][0]["first_collision"]
    assert first["other"] == "cap"
    assert report["in_place_at_start"] == ["body", "cap"]


def test_without_a_plan_the_prismatic_joint_is_tried_both_ways(tmp_path):
    workspace = _vise(tmp_path, _CHEEKS_OUTBOARD)
    report = workspace.execute("check_insertion", {"name": "vise"}, source="agent")
    assert report["plan_from"] == "prismatic joints"
    jaw = report["results"][0]
    assert jaw["direction_from"] == "prismatic joint axis, both senses"
    # The cap closes the open end and the fixed jaw closes the other: both senses are blocked.
    assert jaw["status"] == "blocked"
    assert jaw["other_sense"]["status"] == "blocked"


def test_a_part_that_overlaps_when_seated_is_listed_and_not_counted_on_the_way_in(tmp_path):
    workspace = _vise(tmp_path, _CHEEKS_OUTBOARD)
    # A press fit: the jaw is 0.5 mm into the fixed jaw's face when seated at x = 7.5.
    report = workspace.execute(
        "check_insertion",
        {"name": "vise", "pose": {"slide": -2.5}, "insertion": _JAW_THEN_CAP},
        source="agent",
    )
    jaw = report["results"][0]
    assert [(item["moving"], item["other"]) for item in jaw["seated_overlaps"]] == [("jaw", "body")]
    assert jaw["status"] == "clear"


def test_check_false_puts_a_part_in_without_a_path(tmp_path):
    workspace = _vise(tmp_path, _CHEEKS_ON_RAILS)
    plan = [{"instance": "cap", "check": False}, {"instance": "jaw", "direction": [-1, 0, 0]}]
    report = workspace.execute("check_insertion", {"name": "vise", "insertion": plan}, source="agent")
    cap, jaw = report["results"]
    assert cap["status"] == "not_checked"
    # The cap went in first, so it blocks the jaw before the cheeks do.
    assert jaw["first_collision"]["other"] == "cap"


def test_the_plan_is_stored_on_the_assembly(tmp_path):
    workspace = _vise(tmp_path, _CHEEKS_OUTBOARD, [{"instance": "jaw", "direction": [-2, 0, 0], "step_mm": 2}, "cap"])
    expected = [{"instance": "jaw", "direction": [-1.0, 0.0, 0.0], "step_mm": 2.0}, {"instance": "cap"}]
    stored = workspace.execute("get_assembly", {"name": "vise"}, source="agent")["assembly"]
    assert stored["insertion"] == expected
    # It is saved with the project.
    reopened = Workspace(tmp_path / "project")
    assert reopened.execute("get_assembly", {"name": "vise"}, source="agent")["assembly"]["insertion"] == expected


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        ([{"instance": "nope"}], "not an instance"),
        ([{"instance": "jaw"}, {"instance": "jaw"}], "listed twice"),
        ([{"instance": "jaw", "direction": [0, 0, 0]}], "must not be zero"),
        ([{"instance": "jaw", "step_mm": 0}], "positive"),
        ([{"instance": "cap"}], "no stated direction and no prismatic joint"),
    ],
)
def test_a_bad_plan_is_refused_in_words(tmp_path, plan, message):
    workspace = _vise(tmp_path, _CHEEKS_OUTBOARD)
    with pytest.raises(GeometryError, match=message):
        workspace.execute("check_insertion", {"name": "vise", "insertion": plan}, source="agent")
