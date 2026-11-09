"""Opening a project again reads its meshes from disk instead of meshing every part.

On a 38-part finger rig, meshing every part for the model view, and every
part again for the assembly view, was most of the wait before the model showed.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from aicad import kernel, mesh_cache
from aicad.workspace import Workspace

_PLATE = [
    {"op": "add", "shape": "box", "size": [30, 20, 6]},
    {"op": "cut", "shape": "cylinder", "radius": 4, "height": 20},
]
_PIN = [{"op": "add", "shape": "cylinder", "radius": 2, "height": 14}]


def _project(path, monkeypatch) -> Workspace:
    monkeypatch.setenv("AICAD_RECENT", str(path.parent / "recent.json"))
    workspace = Workspace(path)
    workspace.execute("create_part_from_ops", {"name": "plate", "ops": _PLATE}, source="ui")
    workspace.execute("create_part_from_ops", {"name": "pin", "ops": _PIN}, source="ui")
    workspace.execute("place_part", {"name": "pin", "position": [5, 0, 3], "rotation_deg": [0, 0, 0]}, source="ui")
    workspace.execute(
        "create_assembly",
        {
            "spec": {
                "name": "rig",
                "instances": [{"name": "plate", "part": "plate"}, {"name": "pin", "part": "pin"}],
                "joints": [{"name": "spin", "type": "revolute", "parent": "plate", "child": "pin", "axis": [0, 0, 1]}],
            }
        },
        source="ui",
    )
    return workspace


@pytest.fixture
def counted(monkeypatch):
    calls = {"viewport": 0, "tessellate": 0}
    real_viewport = kernel.viewport_mesh
    real_tessellate = kernel.tessellate

    def viewport(shape):
        calls["viewport"] += 1
        return real_viewport(shape)

    def tessellate(shape):
        calls["tessellate"] += 1
        return real_tessellate(shape)

    monkeypatch.setattr(kernel, "viewport_mesh", viewport)
    monkeypatch.setattr(kernel, "tessellate", tessellate)
    return calls


def _meshes(workspace: Workspace) -> dict:
    return {name: json.dumps(workspace.mesh(name)) for name in workspace.document.part_names()}


def _pieces(workspace: Workspace) -> bytes:
    return workspace.mesh_answer("pieces", "rig")[1]


def test_opening_the_project_again_meshes_nothing(tmp_path, monkeypatch, counted):
    first = _project(tmp_path / "project", monkeypatch)
    meshes, pieces = _meshes(first), _pieces(first)
    assert counted["viewport"] >= 2 and counted["tessellate"] >= 2
    counted.update(viewport=0, tessellate=0)

    reopened = Workspace(tmp_path / "project")
    assert _meshes(reopened) == meshes
    assert _pieces(reopened) == pieces
    assert counted == {"viewport": 0, "tessellate": 0}


def test_a_moved_part_is_meshed_where_it_now_is(tmp_path, monkeypatch, counted):
    workspace = _project(tmp_path / "project", monkeypatch)
    _meshes(workspace)
    counted.update(viewport=0, tessellate=0)
    workspace.execute("place_part", {"name": "pin", "position": [-9, 0, 3], "rotation_deg": [0, 0, 0]}, source="ui")
    moved = workspace.mesh("pin")
    assert counted["viewport"] >= 1
    assert moved["bounding_box"]["min"][0] == pytest.approx(-11)
    # Moved back, it is the solid that was meshed before.
    counted.update(viewport=0, tessellate=0)
    workspace.execute("place_part", {"name": "pin", "position": [5, 0, 3], "rotation_deg": [0, 0, 0]}, source="ui")
    assert workspace.mesh("pin")["bounding_box"]["min"][0] == pytest.approx(3)
    assert counted["viewport"] == 0


def test_a_changed_solid_is_meshed_again(tmp_path, monkeypatch, counted):
    workspace = _project(tmp_path / "project", monkeypatch)
    before = workspace.mesh("plate")
    workspace.execute("update_part_ops", {"name": "plate", "ops": [{"op": "add", "shape": "box", "size": [40, 20, 6]}]}, source="ui")
    after = workspace.mesh("plate")
    assert after["bounding_box"]["max"][0] == pytest.approx(20)
    assert after != before


def test_the_key_is_taken_before_the_solid_is_meshed(tmp_path, monkeypatch):
    shape, _errors = kernel.prefix_from_ops(_PLATE)
    key = mesh_cache.content_key(shape)
    kernel.viewport_mesh(shape)
    assert mesh_cache.content_key(shape) == key
    same, _errors = kernel.prefix_from_ops(_PLATE)
    assert mesh_cache.content_key(same) == key
    other, _errors = kernel.prefix_from_ops(_PIN)
    assert mesh_cache.content_key(other) != key


def test_meshing_a_placed_copy_does_not_change_the_key_of_the_part(tmp_path, monkeypatch):
    """A placed part shares its faces with the unplaced one, and meshing it changes their flags."""
    base, _errors = kernel.prefix_from_ops(_PIN)
    placed = kernel.place(base, (5, 0, 3), (0, 0, 0))
    kernel.viewport_mesh(placed)
    fresh, _errors = kernel.prefix_from_ops(_PIN)
    assert mesh_cache.content_key(base) == mesh_cache.content_key(fresh)
    again = kernel.place(base, (5, 0, 3), (0, 0, 0))
    first_placement = kernel.place(fresh, (5, 0, 3), (0, 0, 0))
    assert mesh_cache.content_key(again) == mesh_cache.content_key(first_placement)


def test_a_damaged_entry_is_meshed_again(tmp_path, monkeypatch, counted):
    shape, _errors = kernel.prefix_from_ops(_PLATE)
    first = mesh_cache.viewport_mesh(shape)
    path = mesh_cache.folder() / f"{mesh_cache.content_key(shape)}.viewport.json"
    path.write_text("{ not json", encoding="utf-8")
    again, _errors = kernel.prefix_from_ops(_PLATE)
    assert mesh_cache.viewport_mesh(again) == first
    assert counted["viewport"] == 2
    tessellated = mesh_cache.tessellate(shape)
    (mesh_cache.folder() / f"{mesh_cache.content_key(shape)}.tessellation.npz").write_bytes(b"not an archive")
    third, _errors = kernel.prefix_from_ops(_PLATE)
    assert np.array_equal(mesh_cache.tessellate(third).triangles, tessellated.triangles)
    assert counted["tessellate"] == 2


def test_a_folder_that_cannot_be_written_still_meshes(tmp_path, monkeypatch):
    blocked = tmp_path / "a file, not a folder"
    blocked.write_text("", encoding="utf-8")
    monkeypatch.setenv("AICAD_MESH_CACHE", str(blocked))
    shape, _errors = kernel.prefix_from_ops(_PIN)
    assert mesh_cache.viewport_mesh(shape)["triangle_face"]
    assert len(mesh_cache.tessellate(shape).triangles)


def test_the_oldest_meshes_go_once_the_folder_is_full(tmp_path, monkeypatch):
    folder = tmp_path / "meshes"
    monkeypatch.setenv("AICAD_MESH_CACHE", str(folder))
    monkeypatch.setattr(mesh_cache, "_LIMIT_BYTES", 1000)
    folder.mkdir()
    for index in range(5):
        old = folder / f"old{index}.viewport.json"
        old.write_bytes(b"x" * 400)
        os.utime(old, (1_000_000 + index, 1_000_000 + index))
    mesh_cache._prune(folder)
    left = sorted(path.name for path in folder.iterdir())
    # 2000 bytes is past 1000; the oldest go until 800 or less is left.
    assert left == ["old3.viewport.json", "old4.viewport.json"]
