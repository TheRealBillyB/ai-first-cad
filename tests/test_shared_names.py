"""A project file with another name is not opened.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT, outside the project.
A hard link inside the project still names that file. Reading the project
name would read the other one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aicad.blueprint import BlueprintError, open_blueprint
from aicad.document import Document
from aicad.errors import DocumentError, GeometryError, PlanError
from aicad.plan import PlanStore
from aicad.workspace import Workspace

_CANARY_NAME = "SANDBOX_CANARY.txt"
_CANARY_TEXT = "CANARY_HIT\n"


def _canary(directory: Path) -> Path:
    path = directory / _CANARY_NAME
    path.write_text(_CANARY_TEXT, encoding="utf-8")
    return path


def _link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError as exc:
        pytest.skip(f"hard links are not available: {exc}")


def test_a_session_with_another_name_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    _link(canary, project / "session.json")
    with pytest.raises(DocumentError, match="another name"):
        Document(project)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert canary.stat().st_nlink > 1


def test_a_step_file_with_another_name_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    _link(canary, project / "imports" / "bracket.step")
    session = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "bracket",
                "source": "step",
                "step_file": "imports/bracket.step",
                "placement": {"position": [0, 0, 0], "rotation_deg": [0, 0, 0]},
            }
        ],
    }
    (project / "session.json").write_text(json.dumps(session), encoding="utf-8")
    document = Document(project)
    text = "\n".join(document.problems)
    assert "another name" in text
    assert "CANARY_HIT" not in text
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_note_and_requirements_with_another_name_are_not_read(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    workspace = Workspace(project)
    note = project / "documents" / "decisions.md"
    note.unlink()
    _link(canary, note)
    with pytest.raises(GeometryError, match="another name"):
        workspace.execute("read_document", {"name": "decisions.md"})
    requirements = project / "requirements.json"
    requirements.unlink()
    _link(canary, requirements)
    with pytest.raises(GeometryError, match="another name"):
        Workspace(project)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_blueprint_with_another_name_is_not_opened(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    drawing = project / "drawings" / "plan.png"
    _link(canary, drawing)
    with pytest.raises(BlueprintError, match="another name"):
        open_blueprint(drawing, project)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_plan_with_another_name_is_not_opened(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    _link(canary, project / "plans" / "bracket.json")
    with pytest.raises(PlanError, match="another name"):
        PlanStore(project).get("bracket")
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
