"""The recent-project list names only a folder the person opened.

The marker is CANARY_HIT. A link is not named. The assistant is not given a
folder outside the open project, and that marker stays where it was written.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from aicad.errors import GeometryError
from aicad.workspace import Workspace

_MARKER = "CANARY_HIT"
_LINE = "\u2028"


def _write_recent(path, projects: list[dict]) -> None:
    path.write_text(json.dumps({"projects": projects}) + "\n", encoding="utf-8")


def _link_directory(link, target) -> None:
    """A directory link. Windows uses a junction, which does not need extra rights."""

    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a directory link")


def test_the_assistant_is_not_given_a_folder_outside_the_project(tmp_path, monkeypatch):
    recent = tmp_path / "recent.json"
    monkeypatch.setenv("AICAD_RECENT", str(recent))
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    workspace = Workspace(project)
    from aicad.studio import remember_project

    remember_project(project)
    payload = json.loads(recent.read_text(encoding="utf-8"))
    payload["projects"].append({"path": str(outside.resolve()), "name": f"outside{_LINE}{_MARKER}"})
    for item in payload["projects"]:
        if item.get("path") == str(project.resolve()):
            item["name"] = f"project{_LINE}{_MARKER}"
    recent.write_text(json.dumps(payload), encoding="utf-8")
    listed = workspace.execute("list_recent_projects", {}, source="agent")
    shown = json.dumps(listed)
    assert str(outside.resolve()) not in shown
    assert _MARKER not in shown
    assert _LINE not in shown
    assert any(item["path"] == str(project.resolve()) for item in listed["projects"])
    chosen = workspace.execute("list_recent_projects", {}, source="ui")
    paths = [item["path"] for item in chosen["projects"]]
    assert str(outside.resolve()) not in paths
    assert str(project.resolve()) in paths
    assert all(_LINE not in item["name"] and _MARKER not in item["name"] for item in chosen["projects"])
    project_row = next(item for item in chosen["projects"] if item["path"] == str(project.resolve()))
    assert project_row["name"] == project.name
    with pytest.raises(GeometryError, match="Only the person"):
        workspace.execute("open_project", {"path": str(outside)}, source="agent")
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
    assert workspace.root == project.resolve()


def test_a_linked_folder_is_not_named_or_opened_from_the_recent_list(tmp_path, monkeypatch):
    recent = tmp_path / "recent.json"
    monkeypatch.setenv("AICAD_RECENT", str(recent))
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    link = tmp_path / "linked-project"
    _link_directory(link, outside)
    workspace = Workspace(project)
    _write_recent(recent, [{"path": str(link), "name": "chosen"}])
    listed = workspace.execute("list_recent_projects", {}, source="ui")
    shown = json.dumps(listed)
    assert str(link) not in shown
    assert str(outside.resolve()) not in shown
    assert "chosen" not in shown
    assert _MARKER not in shown
    assert listed["projects"] == []
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"


def test_a_design_script_is_not_given_a_folder_outside_the_project(tmp_path, monkeypatch):
    recent = tmp_path / "recent.json"
    monkeypatch.setenv("AICAD_RECENT", str(recent))
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    workspace = Workspace(project)
    from aicad.studio import remember_project

    remember_project(project)
    _write_recent(recent, [{"path": str(outside.resolve()), "name": outside.name}])
    monkeypatch.setattr("aicad.sandbox_state.in_sandbox", lambda: True)
    listed = workspace.execute("list_recent_projects", {}, source="ui")
    assert str(outside.resolve()) not in json.dumps(listed)
    from aicad.studio import list_recent

    assert str(outside.resolve()) not in json.dumps(list_recent(person=True))
    assert _MARKER not in json.dumps(listed)
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
