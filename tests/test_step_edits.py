"""Appending a step, and script sizes, leave the rest of the part in place."""

import pytest

from aicad.errors import GeometryError, ScriptError
from aicad.llm import _SYSTEM, tool_specs
from aicad.script_params import rewrite_script_size, script_sizes
from aicad.workspace import Workspace


def _history(workspace: Workspace, name: str) -> list[dict]:
    part = next(item for item in workspace.state()["parts"] if item["name"] == name)
    return part["history"]


def test_a_script_size_is_a_top_level_number():
    script = "width = 10\nheight = 20.5\npart = Box(width, height, 4)\n"
    sizes = script_sizes(script)
    assert [item["name"] for item in sizes] == ["width", "height"]
    assert sizes[0]["value"] == 10
    assert rewrite_script_size(script, "width", 16) == "width = 16\nheight = 20.5\npart = Box(width, height, 4)\n"
    text = "width = 1\nwidth = 4  # mm\npart = Box(width, 2, 2)\n"
    assert rewrite_script_size(text, "width", 8) == "width = 1\nwidth = 8  # mm\npart = Box(width, 2, 2)\n"
    assert rewrite_script_size("gap = -2\npart = Box(1, 1, 1)\n", "gap", 3) == "gap = 3\npart = Box(1, 1, 1)\n"
    assert script_sizes("part = 5\n") == []
    with pytest.raises(GeometryError, match="No size"):
        rewrite_script_size(script, "depth", 1)


def test_add_step_appends_a_cut_and_a_fillet(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "create_part_from_ops",
        {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [10, 20, 30], "name": "base"}]},
        source="ui",
    )
    workspace.execute("place_part", {"name": "block", "position": [4, 0, 0]})
    plain = workspace.document.measure("block").volume_mm3
    workspace.execute(
        "add_step",
        {"name": "block", "step": {"op": "cut", "shape": "box", "size": [4, 4, 40], "name": "slot"}},
        source="ui",
    )
    assert workspace.document.measure("block").volume_mm3 < plain
    assert [row["name"] for row in _history(workspace, "block")] == ["base", "slot"]
    assert workspace.document.get_source("block")["placement"]["position"] == [4.0, 0.0, 0.0]
    workspace.execute(
        "add_step",
        {"name": "block", "step": {"op": "fillet", "radius": 1, "edges": [0], "name": "round"}},
        source="ui",
    )
    assert _history(workspace, "block")[-1]["kind"] == "fillet"
    assert workspace.document.measure("block").volume_mm3 < plain
    workspace.execute("undo")
    assert [row["name"] for row in _history(workspace, "block")] == ["base", "slot"]
    workspace.execute("undo")
    assert [row["name"] for row in _history(workspace, "block")] == ["base"]
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(plain)
    workspace.execute(
        "insert_standard_part",
        {"name": "screw", "kind": "socket_head_cap_screw", "size": "M3-0.5", "length": 8},
    )
    with pytest.raises(GeometryError, match="named sizes"):
        workspace.execute("add_step", {"name": "screw", "step": {"op": "cut", "shape": "box", "size": [1, 1, 1]}})


def test_add_step_appends_a_sketch_cut(tmp_path, monkeypatch):
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
    before = workspace.document.measure("plate").volume_mm3
    workspace.execute(
        "add_step",
        {
            "name": "plate",
            "step": {"op": "cut_box", "center": [20, 10, 2.5], "size": [4, 4, 8], "name": "slot"},
        },
    )
    assert workspace.document.measure("plate").volume_mm3 < before
    assert [row["id"] for row in _history(workspace, "plate")] == ["sketch", "step/0", "step/1"]
    assert workspace.document.get_source("plate")["source"] == "sketch"


def test_a_script_size_rebuilds_through_the_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "create_part_from_script",
        {"name": "block", "script": "width = 10\nheight = 20\npart = Box(width, height, 5)\n"},
        source="ui",
    )
    listed = workspace.execute("list_parameters", {"name": "block"})
    width = next(item for item in listed["parameters"] if item["id"] == "script/width")
    assert width["value"] == 10
    assert width["step"] == "script"
    assert _history(workspace, "block")[0]["id"] == "script"
    workspace.execute("place_part", {"name": "block", "position": [3, 0, 0]})
    workspace.execute("set_parameter", {"name": "block", "parameter": "script/width", "value": 16}, source="ui")
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(1600)
    assert "width = 16" in workspace.document.get_source("block")["script"]
    assert workspace.document.get_source("block")["placement"]["position"] == [3.0, 0.0, 0.0]
    workspace.execute("undo")
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(1000)
    with pytest.raises(ScriptError):
        workspace.execute("set_parameter", {"name": "block", "parameter": "script/width", "value": 0})
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(1000)


def test_the_assistant_is_told_to_edit_a_step():
    names = {spec["name"] for spec in tool_specs()}
    assert "add_step" in names
    assert "set_parameter" in names
    assert "add_step" in _SYSTEM
    assert "update_part_ops" in _SYSTEM
    assert "width = 40" in _SYSTEM
