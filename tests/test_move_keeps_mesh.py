"""Moving a part carries its mesh to the new place instead of meshing the part again.

On a 38-part finger rig, one drag of the proximal link spent 0.52 s of its
0.8 s meshing and describing a solid whose faces had not changed.
"""

from __future__ import annotations

import numpy as np
import pytest

from aicad import kernel, mesh_cache
from aicad.workspace import Workspace

_BRACKET = [
    {"op": "add", "shape": "box", "size": [40, 20, 6], "center": [10, 5, 3]},
    {"op": "cut", "shape": "cylinder", "radius": 3, "height": 20, "center": [4, 2, 0]},
    {"op": "add", "shape": "cylinder", "radius": 4, "height": 9, "center": [22, 6, 8]},
]
_WIDER = [{"op": "add", "shape": "box", "size": [64, 20, 6], "center": [10, 5, 3]}]


def _bracket() -> kernel.Shape:
    shape, errors = kernel.prefix_from_ops(_BRACKET)
    assert shape is not None, errors
    return shape


def _unknown(shape: kernel.Shape) -> kernel.Shape:
    """The same solid with nothing kept about it and no record of the move that made it."""
    return kernel.Shape(shape.impl, features=shape.features)


@pytest.fixture
def counted(monkeypatch):
    calls = {"meshed": 0, "faces": 0, "edges": 0}
    real_mesh, real_faces, real_edges = kernel.viewport_mesh, kernel.describe_faces, kernel.describe_edges

    def viewport_mesh(shape):
        calls["meshed"] += 1
        return real_mesh(shape)

    def describe_faces(shape):
        calls["faces"] += 1
        return real_faces(shape)

    def describe_edges(shape):
        calls["edges"] += 1
        return real_edges(shape)

    monkeypatch.setattr(kernel, "viewport_mesh", viewport_mesh)
    monkeypatch.setattr(kernel, "describe_faces", describe_faces)
    monkeypatch.setattr(kernel, "describe_edges", describe_edges)
    return calls


def _carried_points(base: kernel.Shape, placed: kernel.Shape, flat: list[float]) -> np.ndarray:
    """``flat`` points of the first solid, moved by OpenCascade's own transform."""
    location = placed._placed_from[1]
    points = np.asarray(flat, dtype=np.float64).reshape(-1, 3)
    return np.array([kernel._moved_point(location, tuple(point)) for point in points])


def _same_records(carried: dict, placed: kernel.Shape) -> None:
    """The face and edge records are the ones read off the moved solid, to the rounding of a record."""
    faces = kernel.describe_faces(_unknown(placed))
    assert len(carried["faces"]) == len(faces)
    for kept, fresh in zip(carried["faces"], faces):
        assert kept.keys() == fresh.keys()
        for key in ("index", "kind", "area_mm2", "radius_mm", "ref"):
            assert kept[key] == fresh[key], key
        for key in ("center_mm", "normal", "axis_origin_mm", "axis_direction"):
            if fresh[key] is None:
                assert kept[key] is None, key
            else:
                assert kept[key] == pytest.approx(fresh[key], abs=2e-6), key
    edges = kernel.describe_edges(_unknown(placed))
    assert len(carried["edges"]) == len(edges)
    for kept, fresh in zip(carried["edges"], edges):
        for key in ("index", "kind", "length_mm", "ref"):
            assert kept[key] == fresh[key], key
        assert kept["midpoint_mm"] == pytest.approx(fresh["midpoint_mm"], abs=2e-6)
    box = placed.impl.bounding_box()
    assert carried["bounding_box"]["min"] == pytest.approx([box.min.X, box.min.Y, box.min.Z], abs=2e-6)
    assert carried["bounding_box"]["max"] == pytest.approx([box.max.X, box.max.Y, box.max.Z], abs=2e-6)


@pytest.mark.parametrize("rotation", [(0, 0, 0), (0, 0, 90), (20, 35, 110)])
def test_a_placed_solid_shows_the_first_mesh_carried_by_the_move(rotation, counted):
    base = _bracket()
    first = mesh_cache.viewport_mesh(base)
    placed = kernel.place(base, (30, -12.5, 7.25), rotation)
    counted.update(meshed=0, faces=0, edges=0)
    carried = mesh_cache.viewport_mesh(placed)
    assert counted == {"meshed": 0, "faces": 0, "edges": 0}

    assert carried.keys() == first.keys()
    assert carried["triangle_face"] == first["triangle_face"]
    # A corner is the first mesh's corner moved, to one step of the mesh's own rounding.
    moved = _carried_points(base, placed, first["positions"])
    assert np.abs(np.asarray(carried["positions"]).reshape(-1, 3) - moved).max() <= 1.0e-4
    for kept, was in zip(carried["edges"], first["edges"]):
        assert len(kept["points"]) == len(was["points"])
    lines = [value for edge in first["edges"] for point in edge["points"] for value in point]
    carried_lines = [value for edge in carried["edges"] for point in edge["points"] for value in point]
    assert np.abs(np.asarray(carried_lines).reshape(-1, 3) - _carried_points(base, placed, lines)).max() <= 1.0e-4
    # Each flat normal still is the normal of its own moved triangle.
    corners = np.asarray(carried["positions"]).reshape(-1, 3, 3)
    across = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    size = np.linalg.norm(across, axis=1)
    kept_normals = np.asarray(carried["normals"]).reshape(-1, 3, 3)[:, 0]
    solid = size > 1e-3
    assert np.abs(across[solid] / size[solid][:, None] - kept_normals[solid]).max() < 2e-2
    _same_records(carried, placed)


def test_a_shift_alone_keeps_every_normal_and_axis():
    base = _bracket()
    first = mesh_cache.viewport_mesh(base)
    carried = mesh_cache.viewport_mesh(kernel.place(base, (3, 4, 5), (0, 0, 0)))
    assert carried["normals"] == first["normals"]
    for kept, was in zip(carried["faces"], first["faces"]):
        assert kept["normal"] == was["normal"]
        assert kept["axis_direction"] == was["axis_direction"]


def test_an_axis_turned_evenly_between_two_axes_is_read_from_the_moved_solid(counted):
    """Half-way between X and Y, which of the two a reference names is rounding noise. It is not guessed."""
    base = _bracket()
    mesh_cache.viewport_mesh(base)
    placed = kernel.place(base, (1, 2, 3), (0, 0, 45))
    counted.update(meshed=0, faces=0, edges=0)
    carried = mesh_cache.viewport_mesh(placed)
    assert counted["meshed"] == 0
    assert counted["faces"] == 1
    assert carried["faces"] == kernel.describe_faces(_unknown(placed))


def test_a_solid_placed_from_a_placed_solid_carries_both_moves():
    base = _bracket()
    once = kernel.place(base, (10, 0, 0), (0, 0, 90))
    twice = kernel.place(once, (0, 5, 2), (30, 0, 0))
    carried = mesh_cache.viewport_mesh(twice)
    first = mesh_cache.viewport_mesh(base)
    middle = _carried_points(base, once, first["positions"])
    moved = _carried_points(once, twice, middle.reshape(-1).tolist())
    assert np.abs(np.asarray(carried["positions"]).reshape(-1, 3) - moved).max() <= 1.0e-4
    _same_records(carried, twice)


def _workspace(tmp_path, monkeypatch) -> Workspace:
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "bracket", "ops": _BRACKET}, source="ui")
    workspace.execute("place_part", {"name": "bracket", "position": [5, 0, 3], "rotation_deg": [0, 0, 0]}, source="ui")
    return workspace


def test_moving_a_part_meshes_nothing(tmp_path, monkeypatch, counted):
    workspace = _workspace(tmp_path, monkeypatch)
    tag, _body = workspace.mesh_answer("part", "bracket")
    counted.update(meshed=0, faces=0, edges=0)
    workspace.execute("place_part", {"name": "bracket", "position": [-20, 8, 3], "rotation_deg": [0, 90, 0]}, source="ui")
    moved_tag, _body = workspace.mesh_answer("part", "bracket")
    mesh = workspace.mesh("bracket")
    assert counted["meshed"] == 0
    assert moved_tag != tag
    _same_records(mesh, workspace.document._get("bracket").shape)


def test_a_rebuilt_part_does_not_show_the_mesh_of_the_solid_it_replaced(tmp_path, monkeypatch, counted):
    """The carried mesh is kept on the solid it was made from. A rebuilt part is a new solid."""
    workspace = _workspace(tmp_path, monkeypatch)
    before = workspace.mesh("bracket")
    counted.update(meshed=0, faces=0, edges=0)
    workspace.execute("update_part_ops", {"name": "bracket", "ops": _WIDER}, source="ui")
    after = workspace.mesh("bracket")
    assert counted["meshed"] == 1
    # New steps put the part back at the origin, and it is the wider plate that is there.
    assert after["bounding_box"]["min"] == pytest.approx([-22, -5, 0], abs=1e-6)
    assert after["bounding_box"]["max"] == pytest.approx([42, 15, 6], abs=1e-6)
    assert len(after["faces"]) == 6 and len(before["faces"]) > 6
    _same_records(after, workspace.document._get("bracket").shape)
    # Moved, it is the wider plate that moves, and it is not meshed a second time.
    workspace.execute("place_part", {"name": "bracket", "position": [5, 0, 3], "rotation_deg": [0, 0, 0]}, source="ui")
    moved = workspace.mesh("bracket")
    assert counted["meshed"] == 1
    assert moved["bounding_box"]["min"] == pytest.approx([-17, -5, 3], abs=1e-6)
    assert moved["bounding_box"]["max"] == pytest.approx([47, 15, 9], abs=1e-6)
    assert len(moved["faces"]) == 6
    _same_records(moved, workspace.document._get("bracket").shape)


def test_a_catalog_part_made_longer_in_place_shows_the_longer_solid(tmp_path, monkeypatch):
    """Here the placement is kept and only the solid under it changes."""
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    document = workspace.document
    document.insert_standard_part("screw", "socket_head_cap_screw", "M5-0.8", length=16)
    document.place_part("screw", [12, -4, 7], [90, 0, 0])
    short = mesh_cache.viewport_mesh(document._get("screw").shape)
    document._set_catalog_length("screw", 25)
    shape = document._get("screw").shape
    long = mesh_cache.viewport_mesh(shape)
    assert long["bounding_box"] != short["bounding_box"]
    _same_records(long, shape)


def test_a_rebuild_that_fails_keeps_the_mesh_of_the_solid_still_stored(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    before = workspace.mesh_answer("part", "bracket")
    with pytest.raises(Exception):
        workspace.execute(
            "update_part_ops",
            {"name": "bracket", "ops": [{"op": "add", "shape": "box", "size": [0, 20, 6]}]},
            source="ui",
        )
    shape = workspace.document._get("bracket").shape
    assert workspace.mesh_answer("part", "bracket") == before
    _same_records(workspace.mesh("bracket"), shape)


def test_undoing_a_move_shows_the_part_where_it_was(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    before = workspace.mesh("bracket")["bounding_box"]
    workspace.execute("place_part", {"name": "bracket", "position": [50, 0, 3], "rotation_deg": [0, 0, 30]}, source="ui")
    assert workspace.mesh("bracket")["bounding_box"] != before
    workspace.execute("undo", {}, source="ui")
    assert workspace.mesh("bracket")["bounding_box"] == before
    _same_records(workspace.mesh("bracket"), workspace.document._get("bracket").shape)


def test_a_shift_leaves_a_coordinate_it_does_not_move_exactly_as_stored():
    """A cylinder on the Z axis has a centre at -0.0 in places, and a reference prints the sign."""
    pin, errors = kernel.prefix_from_ops([{"op": "add", "shape": "cylinder", "radius": 2, "height": 12}])
    assert pin is not None, errors
    placed = kernel.place(pin, (30, 0, 0), (0, 0, 0))
    _same_records(mesh_cache.viewport_mesh(placed), placed)
