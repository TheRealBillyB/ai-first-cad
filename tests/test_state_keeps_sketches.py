"""The state solves and draws a sketch again only when that sketch's spec has changed.

Every change to any part made the next state solve and draw every sketch
again. On a 38-part finger rig that was 55 ms of each 66 ms state after a
move, for a sketch the move did not touch. A solved sketch depends on its
name and its stored spec, so the state keeps each one until its spec differs.
"""

from __future__ import annotations

import copy

import pytest

from aicad.document import Document
from aicad.workspace import Workspace


def _rectangle(width=40, height=20, plane="xy"):
    return {
        "plane": plane,
        "entities": [
            {"id": "p0", "type": "point", "at": [0, 0]},
            {"id": "p1", "type": "point", "at": [width, 0]},
            {"id": "p2", "type": "point", "at": [width, height]},
            {"id": "p3", "type": "point", "at": [0, height]},
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
            {"type": "distance", "a": "p0", "b": "p1", "value": width, "name": "width"},
            {"type": "distance", "a": "p0", "b": "p3", "value": height, "name": "height"},
        ],
    }


_EXTRUDE = [{"op": "extrude", "points": ["p0", "p1", "p2", "p3"], "height": 5, "name": "extrude"}]


@pytest.fixture
def solved(monkeypatch):
    """The name of each sketch the state solved, in order."""
    names: list[str] = []
    real = Document.get_sketch

    def get_sketch(self, name):
        names.append(name)
        return real(self, name)

    monkeypatch.setattr(Document, "get_sketch", get_sketch)
    return names


def _workspace(tmp_path, monkeypatch) -> Workspace:
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_sketch", {"name": "plate", "spec": _rectangle()}, source="ui")
    workspace.execute("create_part_from_sketch", {"name": "plate", "sketch": "plate", "steps": _EXTRUDE}, source="ui")
    workspace.execute("create_sketch", {"name": "tab", "spec": _rectangle(12, 8)}, source="ui")
    workspace.execute(
        "create_part_from_ops",
        {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [30, 20, 10], "center": [0, 0, 5]}]},
        source="ui",
    )
    return workspace


def _sketch(workspace: Workspace, name: str) -> dict:
    return next(entry for entry in workspace.state()["sketches"] if entry["name"] == name)


def _fresh(workspace: Workspace, name: str) -> dict:
    """What the state said about a sketch before any of it was kept."""
    report = workspace.document.get_sketch(name)
    return {
        "name": name,
        "status": report.status,
        "dof": report.dof,
        "free": report.free,
        "conflicts": report.conflicts,
        "dimensions": report.dimensions,
        "geometry": report.geometry,
        "note": report.note,
        "spec": copy.deepcopy(workspace.document._get_sketch(name).spec),  # noqa: SLF001
    }


def test_moving_a_part_solves_no_sketch_again(tmp_path, monkeypatch, solved):
    workspace = _workspace(tmp_path, monkeypatch)
    before = copy.deepcopy(workspace.state()["sketches"])
    solved.clear()
    workspace.execute("place_part", {"name": "block", "position": [5, 0, 3], "rotation_deg": [0, 0, 0]}, source="ui")
    after = workspace.state()
    assert solved == []
    assert after["sketches"] == before
    assert after["sketches"] == [_fresh(workspace, "plate"), _fresh(workspace, "tab")]


def test_a_changed_dimension_solves_that_sketch_and_no_other(tmp_path, monkeypatch, solved):
    workspace = _workspace(tmp_path, monkeypatch)
    workspace.state()
    workspace.execute("set_dimension", {"sketch": "plate", "dimension": "width", "value": 50}, source="ui")
    solved.clear()
    state = workspace.state()
    assert solved == ["plate"]
    plate = next(entry for entry in state["sketches"] if entry["name"] == "plate")
    assert plate["geometry"]["points"]["p1"]["x"] == 50
    assert next(item for item in plate["dimensions"] if item["name"] == "width")["value"] == 50
    assert plate == _fresh(workspace, "plate")
    assert _sketch(workspace, "tab") == _fresh(workspace, "tab")


def test_a_rewritten_sketch_is_solved_again(tmp_path, monkeypatch, solved):
    workspace = _workspace(tmp_path, monkeypatch)
    workspace.state()
    loose = _rectangle(12, 8)
    loose["constraints"] = [item for item in loose["constraints"] if item.get("name") != "height"]
    workspace.execute("update_sketch", {"name": "tab", "spec": loose}, source="ui")
    solved.clear()
    tab = _sketch(workspace, "tab")
    assert solved == ["tab"]
    assert tab["status"] == "under_constrained"
    assert tab["dof"] > 0
    assert tab == _fresh(workspace, "tab")


def test_undoing_a_dimension_shows_the_sketch_as_it_was(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    before = copy.deepcopy(_sketch(workspace, "plate"))
    workspace.execute("set_dimension", {"sketch": "plate", "dimension": "width", "value": 50}, source="ui")
    assert _sketch(workspace, "plate")["geometry"]["points"]["p1"]["x"] == 50
    workspace.undo()
    assert _sketch(workspace, "plate") == before
    workspace.redo()
    assert _sketch(workspace, "plate")["geometry"]["points"]["p1"]["x"] == 50
    assert _sketch(workspace, "plate") == _fresh(workspace, "plate")


def test_a_dimension_that_fails_keeps_the_sketch_still_stored(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    before = copy.deepcopy(_sketch(workspace, "plate"))
    with pytest.raises(Exception, match="greater than 0|cannot be"):
        workspace.execute("set_dimension", {"sketch": "plate", "dimension": "width", "value": -4}, source="ui")
    assert _sketch(workspace, "plate") == before


def test_a_sketch_undone_out_of_the_document_leaves_the_state(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    workspace.execute("create_sketch", {"name": "extra", "spec": _rectangle(7, 7)}, source="ui")
    assert [entry["name"] for entry in workspace.state()["sketches"]] == ["plate", "tab", "extra"]
    workspace.undo()
    assert [entry["name"] for entry in workspace.state()["sketches"]] == ["plate", "tab"]
    # The same name comes back with another shape, and the first one is not shown for it.
    workspace.execute("create_sketch", {"name": "extra", "spec": _rectangle(9, 3)}, source="ui")
    extra = _sketch(workspace, "extra")
    assert extra["geometry"]["points"]["p2"] == pytest.approx({"x": 9, "y": 3})
    assert extra == _fresh(workspace, "extra")


def test_another_project_does_not_show_a_sketch_kept_from_the_last_one(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    workspace.state()
    other = Workspace(tmp_path / "other")
    other.execute("create_sketch", {"name": "plate", "spec": _rectangle(90, 10)}, source="ui")
    del other
    workspace.open_project(str(tmp_path / "other"))
    plate = _sketch(workspace, "plate")
    assert plate["geometry"]["points"]["p1"]["x"] == 90
    assert [entry["name"] for entry in workspace.state()["sketches"]] == ["plate"]
