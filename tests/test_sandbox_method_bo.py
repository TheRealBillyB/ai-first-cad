"""An assistant STEP import stays inside the project.

The marker is CANARY_HIT. A command from the assistant does not read a file
outside the project. Nothing is sent to another machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError
from aicad.workspace import Workspace

_MARKER = "CANARY_HIT\n"


def _ready(tmp_path: Path) -> tuple[Workspace, Path, Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER, encoding="utf-8")
    document = Document(project)
    document.create_part_from_ops(
        "box",
        [{"op": "add", "shape": "box", "size": [2, 3, 4]}],
    )
    seed = document.export("box", outside / "seed.step", "step")
    inside = document.export("box", project / "exports" / "box.step", "step")
    return Workspace(project), seed, inside, marker


def test_an_assistant_import_does_not_read_outside_the_project(tmp_path: Path):
    workspace, seed, inside, marker = _ready(tmp_path)
    before = seed.read_bytes()
    with pytest.raises(GeometryError, match="outside the project"):
        workspace.execute("import_step", {"name": "stolen", "path": str(seed)}, source="agent")
    with pytest.raises(GeometryError, match="outside the project"):
        workspace.execute("import_step", {"name": "stolen", "path": str(seed)}, source="chat")
    assert not (workspace.root / "imports" / "stolen.step").exists()
    assert seed.read_bytes() == before
    assert marker.read_text(encoding="utf-8") == _MARKER
    imported = workspace.execute(
        "import_step",
        {"name": "again", "path": "exports/box.step"},
        source="agent",
    )
    assert imported["part"]["name"] == "again"
    absolute = workspace.execute(
        "import_step",
        {"name": "local", "path": str(inside)},
        source="agent",
    )
    assert absolute["part"]["name"] == "local"
    assert marker.read_text(encoding="utf-8") == _MARKER


def test_the_page_can_import_a_file_it_names(tmp_path: Path):
    workspace, seed, _inside, marker = _ready(tmp_path)
    imported = workspace.execute(
        "import_step",
        {"name": "chosen", "path": str(seed)},
        source="ui",
    )
    assert imported["part"]["name"] == "chosen"
    assert (workspace.root / "imports" / "chosen.step").is_file()
    assert marker.read_text(encoding="utf-8") == _MARKER
