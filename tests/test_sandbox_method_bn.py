"""A design script stays inside the project when it names a file.

The marker is CANARY_HIT. A call the script asks for does not read or write
that file. Nothing is sent to another machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad.design_calls import script_document
from aicad.document import Document
from aicad.errors import ScriptError

_MARKER = "CANARY_HIT\n"


def _project(tmp_path: Path) -> tuple[Document, Path, Path]:
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
    return document, outside, marker


def test_a_design_script_export_does_not_leave_the_project(tmp_path: Path):
    document, outside, marker = _project(tmp_path)
    target = outside / "leak.step"
    proxy = script_document(document, [])
    with pytest.raises(ScriptError, match="outside the project"):
        proxy.export("box", str(target), "step")
    assert not target.exists()
    assert marker.read_text(encoding="utf-8") == _MARKER
    kept = proxy.export("box", "exports/box.step", "step")
    assert Path(kept).is_file()
    assert Path(kept).resolve().is_relative_to(document.workspace.resolve())
    assert marker.read_text(encoding="utf-8") == _MARKER


def test_a_design_script_import_does_not_read_outside_the_project(tmp_path: Path):
    document, outside, marker = _project(tmp_path)
    seed = document.export("box", outside / "seed.step", "step")
    before = seed.read_bytes()
    proxy = script_document(document, [])
    with pytest.raises(ScriptError, match="outside the project"):
        proxy.import_step("stolen", str(seed))
    assert not (document.workspace / "imports" / "stolen.step").exists()
    assert seed.read_bytes() == before
    assert marker.read_text(encoding="utf-8") == _MARKER
    inside = proxy.export("box", "exports/box.step", "step")
    imported = proxy.import_step("again", str(inside))
    assert imported.name == "again"
    assert marker.read_text(encoding="utf-8") == _MARKER


def test_a_design_script_does_not_follow_a_link_out_of_the_project(tmp_path: Path):
    document, outside, marker = _project(tmp_path)
    link = document.workspace / "leak.step"
    link.symlink_to(outside / "via-link.step")
    proxy = script_document(document, [])
    with pytest.raises(ScriptError, match="outside the project"):
        proxy.export("box", str(link), "step")
    assert not (outside / "via-link.step").exists()
    assert marker.read_text(encoding="utf-8") == _MARKER
