"""A turned part's mesh takes the box its move already found.

``place`` works out the box of a turned solid and keeps it on the moved solid.
The mesh of that solid then asked OpenCascade for the same box again.
"""

from __future__ import annotations

import pytest
from build123d.topology import shape_core

from aicad import kernel, mesh_cache
from aicad.workspace import Workspace

_BRACKET = [
    {"op": "add", "shape": "box", "size": [40, 20, 6], "center": [10, 5, 3]},
    {"op": "cut", "shape": "cylinder", "radius": 3, "height": 20, "center": [4, 2, 0]},
    {"op": "add", "shape": "cylinder", "radius": 4, "height": 9, "center": [22, 6, 8]},
]
_WIDER = [{"op": "add", "shape": "box", "size": [64, 20, 6], "center": [10, 5, 3]}]


@pytest.fixture
def boxes(monkeypatch):
    """Each box asked of the geometry library."""
    asked: list[int] = []
    real = shape_core.Shape.bounding_box

    def counted(self, *args, **kwargs):
        asked.append(id(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(shape_core.Shape, "bounding_box", counted)
    return asked


def _fresh_box(shape: kernel.Shape) -> dict:
    """The moved solid's own box, read again from the solid with nothing kept."""
    box = kernel.Shape(shape.impl, features=shape.features).impl.bounding_box()
    return {"min": [box.min.X, box.min.Y, box.min.Z], "max": [box.max.X, box.max.Y, box.max.Z]}


def _same_box(mesh: dict, shape: kernel.Shape) -> None:
    fresh = _fresh_box(shape)
    assert mesh["bounding_box"]["min"] == pytest.approx(fresh["min"], abs=2e-6)
    assert mesh["bounding_box"]["max"] == pytest.approx(fresh["max"], abs=2e-6)


@pytest.mark.parametrize("rotation", [(0, 0, 90), (20, 35, 110), (0, 45, 0)])
def test_a_turned_mesh_asks_for_no_box(rotation, boxes):
    shape, errors = kernel.prefix_from_ops(_BRACKET)
    assert shape is not None, errors
    mesh_cache.viewport_mesh(shape)
    placed = kernel.place(shape, (30, -12.5, 7.25), rotation)
    boxes.clear()
    carried = mesh_cache.viewport_mesh(placed)
    assert boxes == []
    _same_box(carried, placed)


def test_a_part_turned_twice_takes_the_box_of_both_turns(boxes):
    shape, _errors = kernel.prefix_from_ops(_BRACKET)
    mesh_cache.viewport_mesh(shape)
    once = kernel.place(shape, (10, 0, 0), (0, 0, 90))
    twice = kernel.place(once, (0, 5, 2), (30, 0, 0))
    boxes.clear()
    carried = mesh_cache.viewport_mesh(twice)
    assert boxes == []
    _same_box(carried, twice)


def _workspace(tmp_path, monkeypatch) -> Workspace:
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_MESH_CACHE", str(tmp_path / "meshes"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "bracket", "ops": _BRACKET}, source="ui")
    workspace.mesh("bracket")
    return workspace


def _turn(workspace, x: float, turn: float) -> None:
    workspace.execute("place_part", {"name": "bracket", "position": [x, 0, 3], "rotation_deg": [0, 0, turn]}, source="ui")


def test_each_turn_of_a_part_shows_its_own_box(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    seen = []
    for x, turn in ((5, 30), (5, 60), (40, 60), (40, 0)):
        _turn(workspace, x, turn)
        mesh = workspace.mesh("bracket")
        _same_box(mesh, workspace.document._get("bracket").shape)  # noqa: SLF001
        seen.append(mesh["bounding_box"])
    # Every move gave a different box. None was the box of the turn before.
    assert len({repr(box) for box in seen}) == len(seen)


def test_a_rebuilt_part_turned_shows_the_box_of_the_new_solid(tmp_path, monkeypatch):
    """The box is kept on the solid it was found for. A rebuilt part is a new solid."""
    workspace = _workspace(tmp_path, monkeypatch)
    _turn(workspace, 5, 90)
    narrow = workspace.mesh("bracket")["bounding_box"]
    workspace.execute("update_part_ops", {"name": "bracket", "ops": _WIDER}, source="ui")
    _turn(workspace, 5, 90)
    mesh = workspace.mesh("bracket")
    _same_box(mesh, workspace.document._get("bracket").shape)  # noqa: SLF001
    # Turned a quarter, the 64 mm plate is 64 mm along Y.
    assert mesh["bounding_box"]["max"][1] - mesh["bounding_box"]["min"][1] == pytest.approx(64, abs=1e-6)
    assert mesh["bounding_box"] != narrow


def test_undoing_a_turn_shows_the_box_of_the_turn_before(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    _turn(workspace, 5, 30)
    before = workspace.mesh("bracket")["bounding_box"]
    _turn(workspace, 5, 75)
    assert workspace.mesh("bracket")["bounding_box"] != before
    workspace.execute("undo", {}, source="ui")
    assert workspace.mesh("bracket")["bounding_box"] == before
    _same_box(workspace.mesh("bracket"), workspace.document._get("bracket").shape)  # noqa: SLF001
