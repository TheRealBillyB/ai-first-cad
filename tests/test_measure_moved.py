"""A solid is measured once, and a moved solid takes what a move does not change.

Measuring checked validity twice per solid, and moving a part measured it
again from scratch: on a 38-part finger rig that was 0.35 s of a 0.56 s move,
for a validity, closed-shell check, volume, and area a rigid move cannot change.
"""

from __future__ import annotations

import pytest
from build123d.topology import shape_core

from aicad import kernel
from aicad.workspace import Workspace

_PLATE = [
    {"op": "add", "shape": "box", "size": [40, 20, 6], "center": [10, 5, 3]},
    {"op": "cut", "shape": "cylinder", "radius": 3, "height": 20, "center": [0, 0, 0]},
]


@pytest.fixture
def counted(monkeypatch):
    calls = {"valid": 0, "direct": 0}
    real_valid = shape_core.Shape.is_valid
    real_direct = kernel._measure_solid

    def is_valid(self):
        calls["valid"] += 1
        return real_valid.fget(self)

    def direct(impl):
        calls["direct"] += 1
        return real_direct(impl)

    monkeypatch.setattr(shape_core.Shape, "is_valid", property(is_valid))
    monkeypatch.setattr(kernel, "_measure_solid", direct)
    return calls


def _plate() -> kernel.Shape:
    shape, errors = kernel.prefix_from_ops(_PLATE)
    assert shape is not None, errors
    return shape


def _unknown(shape: kernel.Shape) -> kernel.Shape:
    """The same solid with nothing known about it."""
    return kernel.Shape(shape.impl, features=shape.features)


def _same(kept: kernel.Measurement, fresh: kernel.Measurement) -> None:
    kept_dict = kept.to_dict()
    fresh_dict = fresh.to_dict()
    for key in ("valid", "watertight", "solid_count", "face_count", "edge_count", "vertex_count", "volume_mm3", "area_mm2"):
        assert kept_dict[key] == fresh_dict[key], key
    for axis in ("x", "y", "z"):
        assert kept_dict["center_of_mass_mm"][axis] == pytest.approx(fresh_dict["center_of_mass_mm"][axis], abs=2e-3)
    assert kept_dict["bounding_box"] == fresh_dict["bounding_box"]


def test_a_measurement_checks_validity_once(counted):
    kernel.measure(_plate())
    assert counted["valid"] == 1


def test_a_moved_solid_takes_the_facts_of_the_solid_it_was_moved_from(counted):
    plate = _plate()
    kernel.measure(plate)
    counted["direct"] = 0
    moved = kernel.place(plate, (30, -12, 7), (0, 35, 90))
    kept = kernel.measure(moved)
    assert counted["direct"] == 0
    _same(kept, kernel.measure(_unknown(moved)))


def test_a_second_placement_takes_the_facts_found_on_the_first(counted):
    """A part away from the origin is measured placed. Its next move needs no full measurement either."""
    plate = _plate()
    first = kernel.place(plate, (5, 5, 5), (10, 0, 0))
    kernel.measure(first)
    assert counted["direct"] == 1
    second = kernel.place(plate, (-40, 3, 0), (0, 0, 120))
    kept = kernel.measure(second)
    assert counted["direct"] == 1
    _same(kept, kernel.measure(_unknown(second)))


def test_mass_uses_the_kept_volume():
    plate = _plate()
    kernel.measure(plate)
    moved = kernel.place(plate, (1, 2, 3), (0, 0, 45))
    assert kernel.measure(moved, "steel").mass_g == pytest.approx(kernel.measure(_unknown(moved), "steel").mass_g)


def test_moving_a_part_does_not_measure_it_again_from_scratch(tmp_path, monkeypatch, counted):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "plate", "ops": _PLATE}, source="ui")
    workspace.state()
    counted["direct"] = 0
    counted["valid"] = 0
    workspace.execute("place_part", {"name": "plate", "position": [0, 0, 25], "rotation_deg": [0, 90, 0]}, source="ui")
    listed = workspace.document.list_parts()[0]
    assert counted["direct"] == 0
    assert counted["valid"] == 0
    # Turned a quarter about Y, the 40 mm length now runs along Z around z = 25.
    assert listed.bounding_box.size.z == pytest.approx(40, abs=1e-3)
    assert listed.valid and listed.watertight
