"""Undo and redo write back only the notes and requirements that differ.

Undoing a move wrote every note and the requirements file again, with the
same text, and checked the notes folder once per note. On a 38-part finger
rig that was about 40 ms of a 160 ms undo.
"""

from __future__ import annotations

import json
import os

import pytest

from aicad import paths
from aicad.errors import GeometryError
from aicad.workspace import Workspace

_BOX = [{"op": "add", "shape": "box", "size": [10, 6, 4]}]
_LIGHT = [{"id": "light", "kind": "max_mass", "part": "arm", "material": "pla", "max_grams": 10}]
_HEAVY = [{"id": "heavy", "kind": "max_mass", "part": "arm", "material": "pla", "max_grams": 500}]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_MESH_CACHE", str(tmp_path / "meshes"))
    opened = Workspace(tmp_path / "project")
    opened.execute("create_part_from_ops", {"name": "arm", "ops": _BOX}, source="ui")
    opened.execute("write_document", {"name": "decisions.md", "markdown": "# Decisions\n\nThe arm is 10 mm long.\n"}, source="ui")
    opened.execute("write_document", {"name": "loads.md", "markdown": "# Loads\n\n5 N at the tip.\n"}, source="ui")
    opened.execute("set_requirements", {"requirements": _LIGHT}, source="ui")
    return opened


@pytest.fixture
def written(monkeypatch):
    """The name of each file written through the checked write, in order."""
    names: list[str] = []
    real = paths.publish_text

    def counted(path, text, **kwargs):
        names.append(os.path.basename(str(path)))
        return real(path, text, **kwargs)

    monkeypatch.setattr(paths, "publish_text", counted)
    return names


def _move(workspace, x: float) -> None:
    workspace.execute("place_part", {"name": "arm", "position": [x, 0, 0], "rotation_deg": [0, 0, 0]}, source="ui")


def _note(workspace, name: str) -> str:
    return (workspace.root / "documents" / name).read_text(encoding="utf-8")


def _requirements_on_disk(workspace) -> list:
    return json.loads(workspace.requirements_path.read_text(encoding="utf-8"))["requirements"]


def _position(workspace) -> list[float]:
    return list(workspace.document._get("arm").placement["position"])  # noqa: SLF001


def test_undoing_a_move_writes_the_session_and_nothing_else(workspace, written):
    _move(workspace, 30)
    written.clear()
    workspace.execute("undo", {}, source="ui")
    assert written == ["session.json"]
    assert _position(workspace) == [0, 0, 0]
    written.clear()
    workspace.execute("redo", {}, source="ui")
    assert written == ["session.json"]
    assert _position(workspace) == [30, 0, 0]
    # What was not written is still what the model holds.
    assert _note(workspace, "decisions.md") == "# Decisions\n\nThe arm is 10 mm long.\n"
    assert _requirements_on_disk(workspace) == workspace.execute("get_requirements")["requirements"]


def test_a_changed_note_is_put_back_and_the_other_is_left(workspace, written):
    workspace.execute("write_document", {"name": "decisions.md", "markdown": "# Decisions\n\nThe arm is 12 mm long.\n"}, source="ui")
    written.clear()
    workspace.execute("undo", {}, source="ui")
    assert _note(workspace, "decisions.md") == "# Decisions\n\nThe arm is 10 mm long.\n"
    assert sorted(written) == ["decisions.md", "session.json"]
    written.clear()
    workspace.execute("redo", {}, source="ui")
    assert _note(workspace, "decisions.md") == "# Decisions\n\nThe arm is 12 mm long.\n"
    assert sorted(written) == ["decisions.md", "session.json"]
    assert _note(workspace, "loads.md") == "# Loads\n\n5 N at the tip.\n"


def test_a_note_that_was_made_is_removed_and_made_again(workspace):
    workspace.execute("write_document", {"name": "tests.md", "markdown": "# Tests\n"}, source="ui")
    workspace.execute("undo", {}, source="ui")
    assert not (workspace.root / "documents" / "tests.md").exists()
    assert "tests.md" not in workspace.execute("list_documents")["documents"]
    workspace.execute("redo", {}, source="ui")
    assert _note(workspace, "tests.md") == "# Tests\n"


def test_a_deleted_note_is_put_back(workspace):
    workspace.execute("delete_document", {"name": "loads.md"}, source="ui")
    assert not (workspace.root / "documents" / "loads.md").exists()
    workspace.execute("undo", {}, source="ui")
    assert _note(workspace, "loads.md") == "# Loads\n\n5 N at the tip.\n"
    workspace.execute("redo", {}, source="ui")
    assert not (workspace.root / "documents" / "loads.md").exists()


def test_a_note_edited_outside_the_app_is_put_back_as_the_undo_step_holds_it(workspace):
    _move(workspace, 30)
    # Another program changes one note and deletes the other after the move.
    (workspace.root / "documents" / "decisions.md").write_text("# Decisions\n\nEdited in an editor.\n", encoding="utf-8")
    (workspace.root / "documents" / "loads.md").unlink()
    workspace.execute("undo", {}, source="ui")
    assert _note(workspace, "decisions.md") == "# Decisions\n\nThe arm is 10 mm long.\n"
    assert _note(workspace, "loads.md") == "# Loads\n\n5 N at the tip.\n"
    # Redo goes back to the files as they were when undo was pressed.
    workspace.execute("redo", {}, source="ui")
    assert _note(workspace, "decisions.md") == "# Decisions\n\nEdited in an editor.\n"
    assert not (workspace.root / "documents" / "loads.md").exists()
    assert _position(workspace) == [30, 0, 0]


def test_changed_requirements_are_put_back_in_the_model_and_the_file(workspace, written):
    workspace.execute("set_requirements", {"requirements": _HEAVY}, source="ui")
    assert [item["id"] for item in _requirements_on_disk(workspace)] == ["heavy"]
    written.clear()
    workspace.execute("undo", {}, source="ui")
    assert [item["id"] for item in workspace.execute("get_requirements")["requirements"]] == ["light"]
    assert [item["id"] for item in _requirements_on_disk(workspace)] == ["light"]
    assert sorted(written) == ["requirements.json", "session.json"]
    workspace.execute("redo", {}, source="ui")
    assert [item["id"] for item in workspace.execute("get_requirements")["requirements"]] == ["heavy"]
    assert [item["id"] for item in _requirements_on_disk(workspace)] == ["heavy"]


def test_a_reopened_project_has_what_undo_left(workspace):
    workspace.execute("write_document", {"name": "decisions.md", "markdown": "# Decisions\n\nThe arm is 12 mm long.\n"}, source="ui")
    workspace.execute("set_requirements", {"requirements": _HEAVY}, source="ui")
    _move(workspace, 30)
    for _ in range(3):
        workspace.execute("undo", {}, source="ui")
    again = Workspace(workspace.root)
    assert again.execute("read_document", {"name": "decisions.md"}, source="ui")["markdown"] == "# Decisions\n\nThe arm is 10 mm long.\n"
    assert [item["id"] for item in again.execute("get_requirements")["requirements"]] == ["light"]
    assert list(again.document._get("arm").placement["position"]) == [0, 0, 0]  # noqa: SLF001


def test_the_notes_folder_is_checked_once_for_an_undo_copy(workspace, monkeypatch):
    checked: list[str] = []
    real = paths.output_dir

    def counted(root, path, label, error):
        checked.append(label)
        return real(root, path, label, error)

    monkeypatch.setattr(paths, "output_dir", counted)
    _move(workspace, 30)
    # It was once to list the notes and once more for each note.
    assert checked.count("Notes") == 1


def test_a_note_with_another_name_is_still_refused(workspace, tmp_path):
    # The folder is checked once, and each note is still read through its own check.
    outside = tmp_path / "outside.md"
    outside.write_text("# Not a note of this project\n", encoding="utf-8")
    try:
        os.link(outside, workspace.root / "documents" / "linked.md")
    except OSError:
        pytest.skip("This file system has no hard links.")
    with pytest.raises(GeometryError, match="another name"):
        _move(workspace, 30)
    assert _position(workspace) == [0, 0, 0]
