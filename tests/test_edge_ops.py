"""Fillet and chamfer steps on shape and sketch parts."""

import pytest

from aicad.document import Document
from aicad.errors import EdgeOpError, GeometryError
from aicad.llm import tool_specs
from aicad.workspace import Workspace


def _box(workspace: Workspace, name: str = "block"):
    workspace.execute(
        "create_part_from_ops",
        {"name": name, "ops": [{"op": "add", "shape": "box", "size": [10, 20, 30], "name": "block"}]},
        source="ui",
    )


def _volume(workspace: Workspace, name: str = "block") -> float:
    return workspace.document.measure(name).volume_mm3


def test_fillet_and_chamfer_are_editable_and_undo(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _box(workspace)
    plain = _volume(workspace)
    filleted = workspace.execute(
        "add_fillet",
        {"name": "block", "radius": 1, "edges": [0], "step_name": "round"},
        source="ui",
    )
    assert filleted["part"]["name"] == "block"
    rounded = _volume(workspace)
    assert rounded < plain
    listed = workspace.execute("list_parameters", {"name": "block"})
    radius = next(item for item in listed["parameters"] if item["id"] == "edge/0/radius")
    assert radius["value"] == 1
    assert radius["label"] == "round radius"
    workspace.execute("set_parameter", {"name": "block", "parameter": "edge/0/radius", "value": 1.5}, source="ui")
    tighter = _volume(workspace)
    assert tighter < rounded
    workspace.execute(
        "add_chamfer",
        {"name": "block", "distance": 0.4, "edges": [2]},
        source="ui",
    )
    broken = _volume(workspace)
    assert broken < tighter
    distance = next(
        item
        for item in workspace.execute("list_parameters", {"name": "block"})["parameters"]
        if item["id"] == "edge/1/distance"
    )
    assert distance["value"] == 0.4
    workspace.execute("undo")
    assert _volume(workspace) == pytest.approx(tighter)
    workspace.execute("undo")
    assert _volume(workspace) == pytest.approx(rounded)
    workspace.execute("undo")
    assert _volume(workspace) == pytest.approx(plain)
    assert workspace.document.get_source("block")["edge_ops"] == []


def test_a_bad_radius_or_a_lost_edge_keeps_the_solid(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _box(workspace)
    plain = _volume(workspace)
    with pytest.raises(EdgeOpError, match="was lost") as lost:
        workspace.execute("add_fillet", {"name": "block", "radius": 1, "edges": [40]})
    assert lost.value.code == "edge_lost"
    with pytest.raises(EdgeOpError, match="was lost") as missing:
        workspace.execute(
            "add_chamfer",
            {"name": "block", "distance": 0.5, "edges": ["edge/circle/0,0,0"]},
        )
    assert missing.value.code == "edge_lost"
    assert _volume(workspace) == pytest.approx(plain)
    assert workspace.document.get_source("block")["edge_ops"] == []
    workspace.execute("add_fillet", {"name": "block", "radius": 1, "edges": [0]}, source="ui")
    rounded = _volume(workspace)
    with pytest.raises(EdgeOpError, match="too big") as huge:
        workspace.execute("set_parameter", {"name": "block", "parameter": "edge/0/radius", "value": 100})
    assert huge.value.code == "radius_too_big"
    assert _volume(workspace) == pytest.approx(rounded)
    assert workspace.document.get_source("block")["edge_ops"][0]["radius"] == 1
    with pytest.raises(EdgeOpError, match="too big") as wide:
        workspace.execute("add_chamfer", {"name": "block", "distance": 100, "edges": [1]})
    assert wide.value.code == "distance_too_big"
    assert len(workspace.document.get_source("block")["edge_ops"]) == 1
    assert _volume(workspace) == pytest.approx(rounded)


def test_fillet_follows_a_size_change_and_a_saved_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _box(workspace)
    workspace.execute("place_part", {"name": "block", "position": [5, 0, 0]})
    edges = workspace.execute("describe_edges", {"name": "block"})["edges"]
    assert edges
    workspace.execute("add_fillet", {"name": "block", "radius": 1, "edges": [edges[0]["ref"]]})
    rounded = _volume(workspace)
    workspace.execute("set_parameter", {"name": "block", "parameter": "op/0/size.x", "value": 16})
    grown = _volume(workspace)
    assert grown > rounded
    assert grown < 16 * 20 * 30
    assert workspace.document.get_source("block")["placement"]["position"] == [5.0, 0.0, 0.0]
    reopened = Document(tmp_path / "project")
    assert reopened.measure("block").volume_mm3 == pytest.approx(grown)
    assert reopened.get_source("block")["edge_ops"][0]["op"] == "fillet"
    assert reopened.get_source("block")["edge_ops"][0]["radius"] == 1


def test_a_sketch_part_can_take_a_fillet(tmp_path, monkeypatch):
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
            "steps": [{"op": "extrude", "points": ["p0", "p1", "p2", "p3"], "height": 5, "name": "extrude"}],
        },
    )
    plain = _volume(workspace, "plate")
    assert plain == pytest.approx(4000)
    workspace.execute("add_fillet", {"name": "plate", "radius": 1, "edges": [0]})
    assert _volume(workspace, "plate") < plain
    workspace.execute("set_parameter", {"name": "plate", "parameter": "step/0/height", "value": 8})
    assert _volume(workspace, "plate") < 40 * 20 * 8
    assert any(
        item["id"] == "edge/0/radius"
        for item in workspace.execute("list_parameters", {"name": "plate"})["parameters"]
    )


def test_a_catalog_part_has_no_fillet_step(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "insert_standard_part",
        {"name": "screw", "kind": "socket_head_cap_screw", "size": "M3-0.5", "length": 8},
    )
    plain = _volume(workspace, "screw")
    with pytest.raises(GeometryError, match="shapes or a sketch"):
        workspace.execute("add_fillet", {"name": "screw", "radius": 1, "edges": [0]})
    assert _volume(workspace, "screw") == pytest.approx(plain)


def test_fillet_tools_are_on_the_shared_list():
    names = {spec["name"] for spec in tool_specs()}
    assert {"add_fillet", "add_chamfer"} <= names
