"""One project open in two places: the app and a script, or two scripts.

Each holds its own copy of the model. Before this, the one that saved next
wrote its older copy back over session.json, so a part the other had deleted
came back on the next load.
"""

from __future__ import annotations

import json

import pytest

from aicad.errors import DocumentError
from aicad.workspace import Workspace

_BOX = [{"op": "add", "shape": "box", "size": [10, 10, 10]}]


def _saved_parts(root) -> list[str]:
    payload = json.loads((root / "session.json").read_text(encoding="utf-8"))
    return sorted(part["name"] for part in payload["parts"])


def _names(workspace: Workspace) -> list[str]:
    return sorted(part.name for part in workspace.document.list_parts())


def test_a_deleted_part_stays_deleted_when_another_copy_of_the_project_saves(tmp_path):
    first = Workspace(tmp_path)
    first.execute("create_part_from_ops", {"name": "kept", "ops": _BOX}, source="agent")
    first.execute("create_part_from_ops", {"name": "gone", "ops": _BOX}, source="agent")
    second = Workspace(tmp_path)
    kept_before = second.document._parts["kept"]

    assert first.execute("delete_part", {"name": "gone"}, source="agent") == {"deleted": "gone"}
    assert _saved_parts(tmp_path) == ["kept"]

    second.execute("create_part_from_ops", {"name": "new", "ops": _BOX}, source="agent")
    assert _saved_parts(tmp_path) == ["kept", "new"]
    assert _names(second) == ["kept", "new"]
    assert _names(Workspace(tmp_path)) == ["kept", "new"]
    # A part whose saved record did not change keeps its solid; it is not built again.
    assert second.document._parts["kept"] is kept_before


def test_a_state_read_shows_what_another_copy_saved(tmp_path):
    first = Workspace(tmp_path)
    first.execute("create_part_from_ops", {"name": "a", "ops": _BOX}, source="agent")
    second = Workspace(tmp_path)
    revision = second.geometry_revision
    first.execute("create_part_from_ops", {"name": "b", "ops": _BOX}, source="agent")
    state = second.state()
    assert sorted(part["name"] for part in state["parts"]) == ["a", "b"]
    assert second.geometry_revision > revision


def test_a_document_refuses_to_write_over_a_newer_session(tmp_path):
    first = Workspace(tmp_path)
    first.execute("create_part_from_ops", {"name": "a", "ops": _BOX}, source="agent")
    first.execute("create_part_from_ops", {"name": "b", "ops": _BOX}, source="agent")
    second = Workspace(tmp_path)
    first.execute("delete_part", {"name": "b"}, source="agent")

    # Straight to the document, past the workspace's check before each command.
    with pytest.raises(DocumentError, match="changed by another program"):
        second.document.delete_part("a")
    assert _saved_parts(tmp_path) == ["a"]
    assert second.document.reload_if_changed() is True
    assert _names(second) == ["a"]
    assert second.document.reload_if_changed() is False


def test_undo_after_another_copy_saved_is_not_refused(tmp_path):
    first = Workspace(tmp_path)
    first.execute("create_part_from_ops", {"name": "a", "ops": _BOX}, source="agent")
    second = Workspace(tmp_path)
    second.execute("create_part_from_ops", {"name": "b", "ops": _BOX}, source="agent")
    first.execute("create_part_from_ops", {"name": "c", "ops": _BOX}, source="agent")
    second.undo("agent")
    assert _saved_parts(tmp_path) == _names(second)
