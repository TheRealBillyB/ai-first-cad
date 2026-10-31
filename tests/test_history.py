"""The feature list is the ordered build history."""

import json

import pytest

from aicad.document import Document
from aicad.errors import EdgeOpError
from aicad.workspace import Workspace


def _history(workspace: Workspace, name: str) -> list[dict]:
    part = next(item for item in workspace.state()["parts"] if item["name"] == name)
    return part["history"]


def test_history_lists_steps_and_a_broken_cut_keeps_the_earlier_solid(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "create_part_from_ops",
        {
            "name": "block",
            "ops": [
                {"op": "add", "shape": "box", "size": [10, 10, 10], "name": "base"},
                {"op": "cut", "shape": "box", "size": [4, 4, 4], "name": "pocket"},
            ],
        },
        source="ui",
    )
    rows = _history(workspace, "block")
    assert [row["id"] for row in rows] == ["op/0", "op/1"]
    assert rows[0]["name"] == "base"
    assert rows[1]["kind"] == "cut"
    assert all(row["error"] is None for row in rows)
    listed = workspace.execute("list_parameters", {"name": "block"})
    by_id = {item["id"]: item for item in listed["parameters"]}
    assert by_id["op/0/size.x"]["step"] == "op/0"
    assert by_id["op/1/size.z"]["step"] == "op/1"
    before = workspace.document.measure("block").volume_mm3
    workspace.execute("set_parameter", {"name": "block", "parameter": "op/1/size.x", "value": 30}, source="ui")
    workspace.execute("set_parameter", {"name": "block", "parameter": "op/1/size.y", "value": 30}, source="ui")
    workspace.execute("set_parameter", {"name": "block", "parameter": "op/1/size.z", "value": 30}, source="ui")
    broken = _history(workspace, "block")
    assert broken[0]["error"] is None
    assert "removed the entire part" in broken[1]["error"]
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(1000)
    workspace.execute("undo")
    assert workspace.document.measure("block").volume_mm3 != pytest.approx(1000)
    assert _history(workspace, "block")[1]["error"] is None
    workspace.execute("redo")
    assert "removed the entire part" in _history(workspace, "block")[1]["error"]
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(1000)
    reopened = Document(tmp_path / "project")
    assert reopened.measure("block").volume_mm3 == pytest.approx(1000)
    again = Workspace(tmp_path / "project")
    assert "removed the entire part" in _history(again, "block")[1]["error"]
    assert before < 1000


def test_a_later_fillet_goes_red_when_the_radius_no_longer_fits(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "create_part_from_ops",
        {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [20, 20, 20], "name": "base"}]},
        source="ui",
    )
    workspace.execute("add_fillet", {"name": "block", "radius": 2, "edges": [0], "step_name": "round"}, source="ui")
    assert [row["kind"] for row in _history(workspace, "block")] == ["add", "fillet"]
    assert workspace.execute("list_parameters", {"name": "block"})["parameters"][-1]["step"] == "edge/0"
    with pytest.raises(EdgeOpError, match="too big"):
        workspace.execute("set_parameter", {"name": "block", "parameter": "edge/0/radius", "value": 100})
    assert _history(workspace, "block")[1]["error"] is None
    workspace.execute("set_parameter", {"name": "block", "parameter": "op/0/size.x", "value": 2}, source="ui")
    rows = _history(workspace, "block")
    assert rows[0]["error"] is None
    assert rows[1]["error"]
    assert "too big" in rows[1]["error"]
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(2 * 20 * 20)
    workspace.execute("set_parameter", {"name": "block", "parameter": "edge/0/radius", "value": 0.4}, source="ui")
    healed = _history(workspace, "block")
    assert healed[1]["error"] is None
    assert workspace.document.measure("block").volume_mm3 < 2 * 20 * 20


def test_sketch_history_includes_the_sketch_the_extrude_and_the_fillet(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "create_sketch",
        {
            "name": "plate",
            "spec": {
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
            },
        },
    )
    workspace.execute(
        "create_part_from_sketch",
        {
            "name": "plate",
            "sketch": "plate",
            "steps": [{"op": "extrude", "points": ["p0", "p1", "p2", "p3"], "height": 5, "name": "slab"}],
        },
    )
    workspace.execute("add_fillet", {"name": "plate", "radius": 1, "edges": [0]})
    rows = _history(workspace, "plate")
    assert [row["id"] for row in rows] == ["sketch", "step/0", "edge/0"]
    assert rows[1]["name"] == "slab"
    widths = [
        item
        for item in workspace.execute("list_parameters", {"name": "plate"})["parameters"]
        if item["id"] == "dimension/width"
    ]
    assert widths[0]["step"] == "sketch"


def test_an_old_project_without_history_still_opens(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "session.json").write_text(
        json.dumps(
            {
                "units": "mm",
                "sketches": [],
                "assemblies": [],
                "parts": [
                    {
                        "name": "block",
                        "source": "ops",
                        "ops": [{"op": "add", "shape": "box", "size": [2, 3, 4], "name": "block"}],
                        "placement": {"position": [0, 0, 0], "rotation_deg": [0, 0, 0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    workspace = Workspace(folder)
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(24)
    rows = _history(workspace, "block")
    assert rows == [{"id": "op/0", "kind": "add", "name": "block", "error": None}]
    assert workspace.document.problems == []
