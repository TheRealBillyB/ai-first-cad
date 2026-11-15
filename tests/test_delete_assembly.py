"""An assembly can be deleted. Its parts stay, and undo brings it back.

There was no way to remove an assembly, and a part an assembly used could
not be deleted either, so a wrong assembly stayed in the project for good.
"""

from __future__ import annotations

import json

import pytest

from aicad.errors import GeometryError
from aicad.workspace import Workspace

_BOX = [{"op": "add", "shape": "box", "size": [10, 10, 10]}]


def _project(tmp_path) -> Workspace:
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "base", "ops": _BOX}, source="agent")
    workspace.execute("create_part_from_ops", {"name": "lid", "ops": _BOX}, source="agent")
    workspace.execute(
        "create_assembly",
        {
            "spec": {
                "name": "stack",
                "instances": [{"name": "base", "part": "base"}, {"name": "lid", "part": "lid"}],
                "joints": [{"name": "sit", "type": "fixed", "parent": "base", "child": "lid"}],
            }
        },
        source="agent",
    )
    return workspace


def _saved_assemblies(workspace: Workspace) -> list[str]:
    payload = json.loads((workspace.root / "session.json").read_text(encoding="utf-8"))
    return [spec["name"] for spec in payload.get("assemblies") or []]


def test_an_assembly_is_deleted_and_its_parts_stay(tmp_path):
    workspace = _project(tmp_path)
    assert workspace.execute("delete_assembly", {"name": "stack"}, source="agent")["deleted"] == "stack"
    assert workspace.document.list_assemblies() == []
    assert _saved_assemblies(workspace) == []
    assert sorted(part.name for part in workspace.document.list_parts()) == ["base", "lid"]
    assert Workspace(workspace.root).document.list_assemblies() == []


def test_a_part_of_a_deleted_assembly_can_be_deleted(tmp_path):
    workspace = _project(tmp_path)
    with pytest.raises(GeometryError, match="used by assembly stack"):
        workspace.execute("delete_part", {"name": "lid"}, source="agent")
    workspace.execute("delete_assembly", {"name": "stack"}, source="agent")
    workspace.execute("delete_part", {"name": "lid"}, source="agent")
    assert [part.name for part in workspace.document.list_parts()] == ["base"]


def test_undo_brings_the_assembly_back(tmp_path):
    workspace = _project(tmp_path)
    workspace.execute("delete_assembly", {"name": "stack"}, source="agent")
    workspace.undo("agent")
    assert [spec["name"] for spec in workspace.document.list_assemblies()] == ["stack"]
    assert _saved_assemblies(workspace) == ["stack"]


def test_an_unknown_assembly_is_named(tmp_path):
    workspace = _project(tmp_path)
    with pytest.raises(GeometryError, match="No assembly named 'nope'"):
        workspace.execute("delete_assembly", {"name": "nope"}, source="agent")
    assert _saved_assemblies(workspace) == ["stack"]


def test_the_assembly_page_has_a_delete_button_that_asks_first():
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"
    assert 'id="assembly-delete"' in (static / "index.html").read_text(encoding="utf-8")
    script = (static / "app.js").read_text(encoding="utf-8")
    handler = script[script.index('$("assembly-delete").onclick') :][:600]
    assert "confirm(" in handler and 'command("delete_assembly"' in handler
