"""A size written under one name in several part scripts is one shared parameter."""

import pytest

from aicad.errors import AICADError, GeometryError
from aicad.sandbox import sandbox_supported
from aicad.workspace import Workspace


def _sandbox_or_skip() -> None:
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)


def _script(name: str, body: str, workspace: Workspace) -> None:
    workspace.execute("create_part_from_script", {"name": name, "script": body}, source="agent")


def _size_x(workspace: Workspace, name: str) -> float:
    listed = {part["name"]: part for part in workspace.execute("list_parts", {}, source="agent")["parts"]}
    return round(listed[name]["bounding_box"]["size"]["x"], 6)


@pytest.fixture
def two_parts(tmp_path):
    _sandbox_or_skip()
    workspace = Workspace(tmp_path)
    _script("lid", "wall = 2\nwidth = 30\npart = Box(width + 2 * wall, 10, wall)\n", workspace)
    _script("tray", "wall = 2\npart = Box(40 + 2 * wall, 20, 10)\n", workspace)
    _script("pin", "length = 12\npart = Cylinder(2, length)\n", workspace)
    return workspace


def test_a_size_in_two_scripts_is_listed_as_shared(two_parts):
    listed = two_parts.execute("list_shared_parameters", {}, source="agent")["parameters"]
    by_name = {item["name"]: item for item in listed}
    assert by_name["wall"]["shared"] is True
    assert by_name["wall"]["parts"] == ["lid", "tray"]
    assert by_name["wall"]["value"] == 2
    assert by_name["length"]["shared"] is False
    # Shared ones come first.
    assert listed[0]["name"] == "wall"


def test_setting_a_shared_size_rebuilds_every_part_that_has_it(two_parts):
    result = two_parts.execute("set_shared_parameter", {"parameter": "wall", "value": 3}, source="agent")
    assert result["parts"] == ["lid", "tray"]
    assert _size_x(two_parts, "lid") == 36
    assert _size_x(two_parts, "tray") == 46
    assert "wall = 3\n" in two_parts.document.get_source("lid")["script"]
    listed = {item["name"]: item for item in two_parts.execute("list_shared_parameters", {}, source="agent")["parameters"]}
    assert listed["wall"]["value"] == 3


def test_parts_limits_the_change(two_parts):
    two_parts.execute("set_shared_parameter", {"parameter": "wall", "value": 5, "parts": ["tray"]}, source="agent")
    assert _size_x(two_parts, "lid") == 34
    assert _size_x(two_parts, "tray") == 50
    listed = {item["name"]: item for item in two_parts.execute("list_shared_parameters", {}, source="agent")["parameters"]}
    assert listed["wall"]["same_everywhere"] is False
    assert listed["wall"]["value"] is None
    assert listed["wall"]["values"] == {"lid": 2, "tray": 5}


def test_one_failed_rebuild_changes_none_of_the_parts(two_parts):
    # A wall of -15 passes the number check and rebuilds the lid, a box 0 wide
    # in X, which fails. The tray, rebuilt first or not, must keep its wall.
    with pytest.raises(AICADError):
        two_parts.execute("set_shared_parameter", {"parameter": "wall", "value": -15}, source="agent")
    assert "wall = 2\n" in two_parts.document.get_source("lid")["script"]
    assert "wall = 2\n" in two_parts.document.get_source("tray")["script"]
    assert _size_x(two_parts, "lid") == 34
    assert _size_x(two_parts, "tray") == 44


def test_one_undo_puts_every_part_back(two_parts):
    two_parts.execute("set_shared_parameter", {"parameter": "wall", "value": 4}, source="agent")
    two_parts.execute("undo", {}, source="agent")
    assert _size_x(two_parts, "lid") == 34
    assert _size_x(two_parts, "tray") == 44


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"parameter": "depth", "value": 1}, "No part script has a top-level size 'depth'"),
        ({"parameter": "wall", "value": 1, "parts": ["pin"]}, "'pin' has no top-level size 'wall'"),
        ({"parameter": "wall", "value": 1, "parts": []}, "parts is a list of part names"),
    ],
)
def test_a_bad_request_is_refused(two_parts, arguments, message):
    with pytest.raises(GeometryError, match=message):
        two_parts.execute("set_shared_parameter", arguments, source="agent")


def test_a_part_changed_before_a_failed_one_gets_its_script_back(tmp_path):
    _sandbox_or_skip()
    workspace = Workspace(tmp_path)
    _script("wide", "wall = 2\npart = Box(60 + 2 * wall, 10, 10)\n", workspace)
    _script("narrow", "wall = 2\npart = Box(30 + 2 * wall, 10, 10)\n", workspace)
    workspace.execute("place_part", {"name": "wide", "position": [5, 0, 0]}, source="agent")
    # wall = -15: wide rebuilds 30 mm long, then narrow is 0 mm long and fails.
    with pytest.raises(AICADError):
        workspace.execute("set_shared_parameter", {"parameter": "wall", "value": -15}, source="agent")
    assert "wall = 2\n" in workspace.document.get_source("wide")["script"]
    assert _size_x(workspace, "wide") == 64
    assert workspace.document.get_source("wide")["placement"]["position"] == [5, 0, 0]
