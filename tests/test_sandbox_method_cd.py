"""The assistant lists folders only inside the open project.

The marker is CANARY_HIT. A person can list a folder they chose. The
assistant cannot, and that marker stays where it was written.
"""

from __future__ import annotations

import pytest

from aicad.errors import GeometryError
from aicad.workspace import Workspace

_MARKER = "CANARY_HIT"


def test_the_assistant_lists_only_the_open_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    nested = project / "parts"
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    workspace = Workspace(project)
    nested.mkdir()
    listed = workspace.execute("list_folders", {}, source="agent")
    assert Path_eq(listed["path"], project)
    assert listed["parent"] is None
    assert "parts" in [item["name"] for item in listed["folders"]]
    assert "outside" not in [item["name"] for item in listed["folders"]]
    with pytest.raises(GeometryError, match="outside the project"):
        workspace.execute("list_folders", {"path": str(outside)}, source="chat")
    with pytest.raises(GeometryError, match="outside the project"):
        workspace.execute("list_folders", {"path": str(tmp_path)}, source="agent")
    chosen = workspace.execute("list_folders", {"path": str(tmp_path)}, source="ui")
    assert Path_eq(chosen["path"], tmp_path)
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
    assert _MARKER not in str(listed)


def test_a_design_script_cannot_list_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    workspace = Workspace(project)
    monkeypatch.setattr("aicad.sandbox_state.in_sandbox", lambda: True)
    with pytest.raises(GeometryError, match="outside the project"):
        workspace.execute("list_folders", {"path": str(outside)}, source="ui")
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"


def Path_eq(shown: str, folder) -> bool:
    from pathlib import Path

    return Path(shown) == Path(folder).resolve()
