"""A link that is already outside the project is not what gets read.

The marker is SANDBOX_CANARY.txt containing CANARY_HIT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad.blueprint import open_blueprint
from aicad.document import Document
from aicad.errors import AICADError
from aicad.workspace import Workspace

_CANARY_TEXT = "CANARY_HIT\n"


def _canary(directory: Path) -> Path:
    path = directory / "SANDBOX_CANARY.txt"
    path.write_text(_CANARY_TEXT, encoding="utf-8")
    return path


def _link(path: Path, target: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"this system cannot create a symlink ({exc})")


def test_a_linked_session_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    _link(project / "session.json", canary)
    with pytest.raises(AICADError) as caught:
        Document(project)
    assert _CANARY_TEXT not in str(caught.value)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_linked_note_and_requirements_file_are_not_read(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    workspace = Workspace(project)
    note = workspace.root / "documents" / "decisions.md"
    note.unlink()
    _link(note, canary)
    with pytest.raises(AICADError) as caught:
        workspace.execute("read_document", {"name": "decisions.md"})
    assert _CANARY_TEXT not in str(caught.value)

    other = tmp_path / "requirements-project"
    other.mkdir()
    _link(other / "requirements.json", canary)
    with pytest.raises(AICADError) as caught:
        Workspace(other)
    assert _CANARY_TEXT not in str(caught.value)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_linked_blueprint_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    _link(project / "sheet.png", canary)
    with pytest.raises(AICADError) as caught:
        open_blueprint(project / "sheet.png", project)
    assert _CANARY_TEXT not in str(caught.value)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
