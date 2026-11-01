"""The recent-project list only keeps folders the app recorded."""

from __future__ import annotations

import json

import pytest

from aicad.paths import file_is_user_private
from aicad.studio import list_recent, recent_key_path, remember_project
from aicad.workspace import Workspace


@pytest.fixture
def recent(tmp_path, monkeypatch):
    path = tmp_path / "recent.json"
    monkeypatch.setenv("AICAD_RECENT", str(path))
    return path


def test_an_old_list_is_signed_once_and_a_later_edit_is_left_out(recent, tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    extra = tmp_path / "extra"
    for folder in (first, second, extra):
        folder.mkdir()
    recent.write_text(
        json.dumps(
            {
                "projects": [
                    {"path": str(first), "name": "nope"},
                    {"path": str(second), "name": "also"},
                    {"path": str(tmp_path / "missing"), "name": "gone"},
                ]
            }
        ),
        encoding="utf-8",
    )
    shown = [item["path"] for item in list_recent()["projects"]]
    assert shown == [str(first), str(second)]
    assert file_is_user_private(recent_key_path())
    stored = json.loads(recent.read_text(encoding="utf-8"))
    assert [item["path"] for item in stored["projects"]] == [str(first), str(second)]
    assert all(len(item["mac"]) == 64 for item in stored["projects"])

    stored["projects"].append({"path": str(extra), "name": extra.name})
    recent.write_text(json.dumps(stored), encoding="utf-8")
    again = [item["path"] for item in list_recent()["projects"]]
    assert again == [str(first), str(second)]
    assert str(extra) not in recent.read_text(encoding="utf-8")


def test_a_changed_authentication_is_left_out(recent, tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    remember_project(folder)
    other = tmp_path / "other"
    other.mkdir()
    payload = json.loads(recent.read_text(encoding="utf-8"))
    payload["projects"][0]["mac"] = "0" * 64
    payload["projects"].append({"path": str(other), "mac": payload["projects"][0]["mac"]})
    recent.write_text(json.dumps(payload), encoding="utf-8")
    assert list_recent()["projects"] == []


def test_a_broken_list_does_not_crash(recent, tmp_path):
    recent.write_text("{", encoding="utf-8")
    assert list_recent()["projects"] == []
    recent.write_text(json.dumps(["nope"]), encoding="utf-8")
    assert list_recent()["projects"] == []
    folder = tmp_path / "project"
    folder.mkdir()
    remember_project(folder)
    assert [item["path"] for item in list_recent()["projects"]] == [str(folder.resolve())]


def test_opening_a_project_records_it_and_a_file_edit_does_not(recent, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = Workspace(project)
    workspace.execute("open_project", {"path": str(project)}, source="ui")
    payload = json.loads(recent.read_text(encoding="utf-8"))
    payload["projects"].insert(0, {"path": str(outside.resolve()), "name": outside.name})
    recent.write_text(json.dumps(payload), encoding="utf-8")
    listed = workspace.execute("list_recent_projects", {}, source="ui")
    paths = [item["path"] for item in listed["projects"]]
    assert str(project.resolve()) in paths
    assert str(outside.resolve()) not in paths
    workspace.execute("open_project", {"path": str(outside)}, source="ui")
    listed = workspace.execute("list_recent_projects", {}, source="ui")
    paths = [item["path"] for item in listed["projects"]]
    assert paths[0] == str(outside.resolve())
    assert str(project.resolve()) in paths
    assert file_is_user_private(recent_key_path())
