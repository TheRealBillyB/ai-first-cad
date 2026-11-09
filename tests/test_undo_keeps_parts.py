"""Undo builds only the parts whose saved state changed.

Undo used to throw every part away and build it again from the saved
session. On a 38-part finger rig, undoing one move imported 35 STEP files,
rebuilt two catalog bearings, measured every part again, and meshed every
part again: about 17 seconds for one part that moved.

The meshes counted are the ones asked for per part, from ``mesh_cache``. A
solid meshed before, such as the one an Undo restores, is read from disk then,
and ``kernel`` does not mesh it again.
"""

from __future__ import annotations

import json

import pytest

from aicad import kernel, mesh_cache
from aicad.workspace import Workspace

_BOX = [{"op": "add", "shape": "box", "size": [10, 6, 4]}]

_PLATE = {
    "plane": "xy",
    "entities": [
        {"id": "p0", "type": "point", "at": [0, 0]},
        {"id": "p1", "type": "point", "at": [40, 0]},
        {"id": "p2", "type": "point", "at": [40, 20]},
        {"id": "p3", "type": "point", "at": [0, 20]},
        {"id": "bottom", "type": "line", "start": "p0", "end": "p1"},
        {"id": "right", "type": "line", "start": "p1", "end": "p2"},
        {"id": "top", "type": "line", "start": "p2", "end": "p3"},
        {"id": "left", "type": "line", "start": "p3", "end": "p0"},
    ],
    "constraints": [
        {"type": "fix", "entity": "p0"},
        {"type": "horizontal", "entity": "bottom"},
        {"type": "horizontal", "entity": "top"},
        {"type": "vertical", "entity": "right"},
        {"type": "vertical", "entity": "left"},
        {"type": "distance", "a": "p0", "b": "p1", "value": 40, "name": "width"},
        {"type": "distance", "a": "p0", "b": "p3", "value": 20, "name": "height"},
    ],
}


class _Counted:
    """Counts each call that builds a solid, or asks for or makes its mesh.

    ``viewport`` and ``tessellate`` are the meshes asked for per part, from the
    mesh folder or made fresh. ``meshed`` is a model-view mesh actually made.
    """

    def __init__(self, monkeypatch):
        self.calls: dict[str, list[int]] = {"viewport": [], "tessellate": [], "meshed": [], "ops": [], "measure": []}
        for module, attribute, key in (
            (mesh_cache, "viewport_mesh", "viewport"),
            (mesh_cache, "tessellate", "tessellate"),
            (kernel, "viewport_mesh", "meshed"),
            (kernel, "prefix_from_ops", "ops"),
            (kernel, "measure", "measure"),
        ):
            real = getattr(module, attribute)

            def counted(*args, _real=real, _key=key, **kwargs):
                self.calls[_key].append(id(args[0]))
                return _real(*args, **kwargs)

            monkeypatch.setattr(module, attribute, counted)

    def reset(self) -> None:
        for calls in self.calls.values():
            calls.clear()


def _look(workspace: Workspace) -> None:
    """What the page asks for after a change: the state, the scene list, and each part's mesh."""
    workspace.state()
    workspace.mesh_answer("scene_parts")
    workspace.mesh_answer("pieces", "rig")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    for name in ("frame", "arm", "lid"):
        workspace.execute("create_part_from_ops", {"name": name, "ops": _BOX}, source="ui")
    workspace.execute("create_sketch", {"name": "plate", "spec": _PLATE}, source="ui")
    workspace.execute(
        "create_part_from_sketch",
        {"name": "plate", "sketch": "plate", "steps": [{"op": "extrude", "points": ["p0", "p1", "p2", "p3"], "height": 5}]},
        source="ui",
    )
    workspace.execute(
        "create_assembly",
        {
            "spec": {
                "name": "rig",
                "instances": [{"name": "frame", "part": "frame"}, {"name": "arm", "part": "arm"}],
                "joints": [{"name": "hinge", "type": "revolute", "parent": "frame", "child": "arm", "axis": [0, 0, 1]}],
            }
        },
        source="ui",
    )
    _look(workspace)
    counted = _Counted(monkeypatch)
    return workspace, counted


def _parts(workspace: Workspace) -> dict:
    return dict(workspace.document._parts)  # noqa: SLF001


def test_undoing_a_move_builds_and_meshes_only_the_moved_part(project):
    workspace, counted = project
    before = _parts(workspace)
    saved = workspace.document.session_payload()
    workspace.execute("place_part", {"name": "arm", "position": [30, 0, 0], "rotation_deg": [0, 0, 0]}, source="ui")
    _look(workspace)
    counted.reset()

    workspace.execute("undo", {}, source="ui")
    _look(workspace)

    after = _parts(workspace)
    # Every part is the same object, so its solid, measurement, and mesh are kept.
    assert all(after[name] is before[name] for name in before)
    assert after["frame"].shape is before["frame"].shape
    # The moved part is placed again from the solid it already held. Nothing is built from its ops.
    assert counted.calls["ops"] == []
    assert counted.calls["viewport"] == [id(after["arm"].shape)]
    assert counted.calls["measure"] == [id(after["arm"].shape)]
    # The assembly places each part's own solid, which a move leaves alone.
    assert counted.calls["tessellate"] == []
    assert workspace.document.session_payload() == saved
    assert workspace.document.list_parts()[1].bounding_box.max.x == pytest.approx(5)


def test_undoing_a_geometry_change_builds_that_part_again_and_no_other(project):
    workspace, counted = project
    before = _parts(workspace)
    workspace.execute("update_part_ops", {"name": "lid", "ops": [{"op": "add", "shape": "box", "size": [12, 6, 4]}]}, source="ui")
    _look(workspace)
    counted.reset()

    workspace.execute("undo", {}, source="ui")
    _look(workspace)

    after = _parts(workspace)
    assert after["lid"] is not before["lid"]
    assert all(after[name] is before[name] for name in ("frame", "arm", "plate"))
    assert len(counted.calls["ops"]) == 1
    assert counted.calls["viewport"] == [id(after["lid"].shape)]
    assert workspace.document.list_parts()[2].bounding_box.size.x == pytest.approx(10)


def test_undoing_a_sketch_change_builds_the_part_made_from_it(project):
    """The part's saved steps are the same, but the sketch they extrude is not. It is built again."""
    workspace, counted = project
    workspace.execute("set_dimension", {"sketch": "plate", "dimension": "width", "value": 60}, source="ui")
    assert workspace.document.list_parts()[3].volume_mm3 == pytest.approx(6000)
    _look(workspace)
    before = _parts(workspace)
    counted.reset()

    workspace.execute("undo", {}, source="ui")
    _look(workspace)

    after = _parts(workspace)
    assert after["plate"] is not before["plate"]
    assert workspace.document.list_parts()[3].volume_mm3 == pytest.approx(4000)
    assert all(after[name] is before[name] for name in ("frame", "arm", "lid"))
    assert counted.calls["viewport"] == [id(after["plate"].shape)]


def test_redo_after_undo_also_keeps_the_parts_it_can(project):
    workspace, counted = project
    workspace.execute("place_part", {"name": "frame", "position": [0, 0, 7], "rotation_deg": [0, 0, 90]}, source="ui")
    moved = workspace.document.session_payload()
    workspace.execute("undo", {}, source="ui")
    _look(workspace)
    before = _parts(workspace)
    counted.reset()

    workspace.execute("redo", {}, source="ui")
    _look(workspace)

    after = _parts(workspace)
    assert all(after[name] is before[name] for name in before)
    assert counted.calls["ops"] == []
    assert counted.calls["viewport"] == [id(after["frame"].shape)]
    assert workspace.document.session_payload() == moved


def test_undo_matches_opening_the_restored_session(project, tmp_path):
    """A kept part is what building it from the saved session would give."""
    from aicad.document import Document

    workspace, _counted = project
    workspace.execute("place_part", {"name": "arm", "position": [3, 4, 5], "rotation_deg": [10, 0, 30]}, source="ui")
    workspace.execute("place_part", {"name": "arm", "position": [-8, 0, 2], "rotation_deg": [0, 45, 0]}, source="ui")
    workspace.execute("undo", {}, source="ui")
    reopened = Document(workspace.root)
    for kept, built in zip(workspace.document.list_parts(), reopened.list_parts()):
        assert kept.name == built.name
        assert kept.volume_mm3 == pytest.approx(built.volume_mm3)
        assert kept.bounding_box.min.x == pytest.approx(built.bounding_box.min.x, abs=1e-6)
        assert kept.bounding_box.max.z == pytest.approx(built.bounding_box.max.z, abs=1e-6)


def test_undoing_a_move_reads_the_restored_mesh_from_disk(project):
    """The solid an Undo restores was meshed before. Its mesh is read back, the same as meshing it."""
    workspace, counted = project
    workspace.execute("place_part", {"name": "arm", "position": [30, 0, 0], "rotation_deg": [0, 0, 0]}, source="ui")
    _look(workspace)
    counted.reset()

    workspace.execute("undo", {}, source="ui")
    _look(workspace)

    restored = workspace.document._get("arm").shape  # noqa: SLF001
    assert counted.calls["viewport"] == [id(restored)]
    # Nothing was meshed: the restored solid's mesh was on disk under its key.
    assert counted.calls["meshed"] == []
    assert (mesh_cache.folder() / f"{mesh_cache.content_key(restored)}.viewport.json").is_file()
    _tag, body = workspace.mesh_answer("part", "arm")
    fresh = {**kernel.viewport_mesh(restored), "part": "arm"}
    assert json.loads(body)["result"] == json.loads(json.dumps(fresh))
