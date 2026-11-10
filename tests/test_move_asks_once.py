"""A move asks the geometry library for a bounding box at most once, and for the volume never.

Placing a part checked the moved solid's volume and bounding box, and the
measurement that followed asked for the same box again. On a 38-part finger
rig that was 0.10 s of a 0.14 s move. A move changes neither the volume nor
the number of solids, a shift carries the box with it, and a turn needs one
new box that the measurement can share.
"""

from __future__ import annotations

import pytest
from build123d.topology import shape_core

from aicad import kernel
from aicad.kernel import GeometryError
from aicad.workspace import Workspace

_PLATE = [
    {"op": "add", "shape": "box", "size": [40, 20, 6], "center": [10, 5, 3]},
    {"op": "cut", "shape": "cylinder", "radius": 3, "height": 20, "center": [0, 0, 0]},
    {"op": "add", "shape": "cylinder", "radius": 7, "height": 9, "center": [30, 5, 3]},
]
_LONGER = [{"op": "add", "shape": "box", "size": [90, 20, 6], "center": [10, 5, 3]}]


@pytest.fixture
def asked(monkeypatch):
    """How often the geometry library was asked for a bounding box and for a volume."""
    calls = {"box": 0, "volume": 0}
    real_box = shape_core.Shape.bounding_box
    real_volume = kernel._volume

    def bounding_box(self, *args, **kwargs):
        calls["box"] += 1
        return real_box(self, *args, **kwargs)

    def volume(obj):
        calls["volume"] += 1
        return real_volume(obj)

    monkeypatch.setattr(shape_core.Shape, "bounding_box", bounding_box)
    monkeypatch.setattr(kernel, "_volume", volume)
    return calls


def _plate() -> kernel.Shape:
    shape, errors = kernel.prefix_from_ops(_PLATE)
    assert shape is not None, errors
    return shape


def _unknown(shape: kernel.Shape) -> kernel.Shape:
    """The same solid with nothing known about it."""
    return kernel.Shape(shape.impl, features=shape.features)


def _box(shape: kernel.Shape) -> list[float]:
    low, high = kernel.aabb_bounds(shape)
    return [*low, *high]


def _workspace(tmp_path, monkeypatch) -> Workspace:
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "plate", "ops": _PLATE}, source="ui")
    workspace.state()
    return workspace


def _listed_box(workspace: Workspace, name: str = "plate") -> dict:
    listed = {part.name: part for part in workspace.document.list_parts()}
    return listed[name].to_dict()["bounding_box"]


@pytest.mark.parametrize(
    "position, rotation",
    [
        ((30, -12, 7), (0, 0, 0)),
        ((0, 0, 0), (0, 0, 90)),
        ((30, -12, 7), (20, 35, 110)),
        ((-0.25, 400, 1e-3), (0, 90, 0)),
    ],
)
def test_a_moved_solid_has_the_box_the_geometry_library_gives_it(position, rotation):
    plate = _plate()
    moved = kernel.place(plate, position, rotation)
    assert _box(moved) == pytest.approx(_box(_unknown(moved)), abs=1e-9)
    assert kernel.measure(moved).to_dict()["bounding_box"] == kernel.measure(_unknown(moved)).to_dict()["bounding_box"]


def test_shifting_a_solid_asks_for_no_box_and_no_volume(asked):
    plate = _plate()
    kernel.measure(plate)
    asked.update(box=0, volume=0)
    moved = kernel.place(plate, (30, -12, 7), (0, 0, 0))
    kernel.measure(moved)
    assert asked == {"box": 0, "volume": 0}
    assert _box(moved) == pytest.approx([20, -17, 5.5, 67, 3, 14.5], abs=1e-9)


def test_a_turn_asks_for_one_box_and_the_same_turn_shifted_asks_for_none(asked):
    plate = _plate()
    kernel.measure(plate)
    asked.update(box=0, volume=0)
    first = kernel.place(plate, (5, 0, 0), (0, 0, 90))
    kernel.measure(first)
    assert asked == {"box": 1, "volume": 0}
    second = kernel.place(plate, (5, 40, -3), (0, 0, 90))
    kernel.measure(second)
    assert asked == {"box": 1, "volume": 0}
    assert _box(second) == pytest.approx(_box(_unknown(second)), abs=1e-9)


def test_a_different_turn_does_not_take_the_box_of_the_last_one(asked):
    plate = _plate()
    quarter = kernel.place(plate, (5, 0, 0), (0, 0, 90))
    asked.update(box=0, volume=0)
    tilted = kernel.place(plate, (5, 0, 0), (0, 35, 90))
    assert asked["box"] == 1
    assert _box(tilted) != pytest.approx(_box(quarter), abs=1e-3)
    assert _box(tilted) == pytest.approx(_box(_unknown(tilted)), abs=1e-9)
    # Back to the first turn is a new turn again, not the one kept two moves ago.
    again = kernel.place(plate, (5, 0, 0), (0, 0, 90))
    assert _box(again) == pytest.approx(_box(_unknown(quarter)), abs=1e-9)


def test_a_solid_placed_from_a_placed_solid_carries_both_moves():
    plate = _plate()
    first = kernel.place(plate, (10, 0, 0), (0, 0, 90))
    second = kernel.place(first, (0, 5, 2), (45, 0, 0))
    assert _box(second) == pytest.approx(_box(_unknown(second)), abs=1e-9)


def test_a_shift_past_the_size_limit_is_refused_before_the_part_moves():
    plate = _plate()
    kernel.measure(plate)
    with pytest.raises(GeometryError, match="past the 1e\\+06 mm limit"):
        kernel.place(plate, (999_990, 0, 0), (0, 0, 0))
    # The plate reaches x = 37, so this shift ends just inside the limit.
    assert _box(kernel.place(plate, (999_960, 0, 0), (0, 0, 0)))[3] == pytest.approx(999_997)


def test_a_turn_that_swings_the_solid_past_the_size_limit_is_refused():
    plate = _plate()
    # Along X the plate reaches 37 mm. Turned to point down Y it reaches only 10.
    assert _box(kernel.place(plate, (999_980, 0, 0), (0, 0, 90)))[3] == pytest.approx(999_985)
    with pytest.raises(GeometryError, match="past the 1e\\+06 mm limit"):
        kernel.place(plate, (999_980, 0, 0), (0, 0, 0))
    # And about Y the 37 mm swings to +Z... which a z shift then carries past the limit.
    with pytest.raises(GeometryError, match="past the 1e\\+06 mm limit"):
        kernel.place(plate, (0, 0, 999_980), (0, -90, 0))


def test_a_value_with_no_volume_is_still_refused():
    from build123d import Rectangle

    flat = kernel.Shape(Rectangle(10, 10).face())
    with pytest.raises(GeometryError, match="no solid volume"):
        kernel.place(flat, (5, 0, 0), (0, 0, 0))
    # Asked again, it is refused again: only a solid that passed is remembered.
    with pytest.raises(GeometryError, match="no solid volume"):
        kernel.place(flat, (5, 0, 0), (0, 0, 0))


def test_a_solid_never_measured_is_checked_once_and_then_moves_freely(asked):
    plate = _plate()
    asked.update(box=0, volume=0)
    kernel.place(plate, (5, 0, 0), (0, 0, 0))
    assert asked == {"box": 1, "volume": 1}
    kernel.place(plate, (9, 0, 0), (0, 0, 0))
    assert asked == {"box": 1, "volume": 1}


def test_moving_a_part_asks_for_no_box(tmp_path, monkeypatch, asked):
    workspace = _workspace(tmp_path, monkeypatch)
    asked.update(box=0, volume=0)
    workspace.execute("place_part", {"name": "plate", "position": [5, 0, 3], "rotation_deg": [0, 0, 0]}, source="ui")
    box = _listed_box(workspace)
    assert asked == {"box": 0, "volume": 0}
    assert box["min"] == {"x": -5.0, "y": -5.0, "z": 1.5}
    assert box["max"] == {"x": 42.0, "y": 15.0, "z": 10.5}


def test_a_rebuilt_part_does_not_keep_the_box_of_the_solid_it_replaced(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    workspace.execute("place_part", {"name": "plate", "position": [5, 0, 3], "rotation_deg": [0, 0, 90]}, source="ui")
    _listed_box(workspace)
    workspace.execute("update_part_ops", {"name": "plate", "ops": _LONGER}, source="ui")
    # New operations put the part back at the origin, 90 mm long.
    box = _listed_box(workspace)
    assert box["min"] == {"x": -35.0, "y": -5.0, "z": 0.0}
    assert box["max"] == {"x": 55.0, "y": 15.0, "z": 6.0}
    workspace.execute("place_part", {"name": "plate", "position": [5, 0, 3], "rotation_deg": [0, 0, 90]}, source="ui")
    # The same turn as before, on the new solid: 90 mm now runs along Y.
    box = _listed_box(workspace)
    assert box["size"] == {"x": 20.0, "y": 90.0, "z": 6.0}
    assert box["min"] == {"x": -10.0, "y": -35.0, "z": 3.0}


def test_undoing_a_move_measures_the_part_where_it_was(tmp_path, monkeypatch, asked):
    workspace = _workspace(tmp_path, monkeypatch)
    before = _listed_box(workspace)
    workspace.execute("place_part", {"name": "plate", "position": [5, 0, 3], "rotation_deg": [0, 0, 0]}, source="ui")
    moved = _listed_box(workspace)
    asked.update(box=0, volume=0)
    workspace.undo()
    assert _listed_box(workspace) == before
    workspace.redo()
    assert _listed_box(workspace) == moved
    assert asked == {"box": 0, "volume": 0}
