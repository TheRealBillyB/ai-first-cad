"""STL import is a mesh-backed part with no editable features."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from aicad import Document, GeometryError
from aicad.errors import AICADError
from aicad.studio import list_parameters
from aicad.workspace import Workspace

_TETRA = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
_FACES = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]


def _ascii_stl() -> str:
    lines = ["solid tetra"]
    for face in _FACES:
        lines.append("  facet normal 0 0 0")
        lines.append("    outer loop")
        for index in face:
            x, y, z = _TETRA[index]
            lines.append(f"      vertex {x} {y} {z}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid tetra")
    return "\n".join(lines) + "\n"


def _binary_stl() -> bytes:
    blob = bytearray(b"solid-header".ljust(80, b"\0"))
    blob += struct.pack("<I", len(_FACES))
    for face in _FACES:
        numbers = [0.0, 0.0, 0.0]
        for index in face:
            numbers.extend(_TETRA[index])
        blob += struct.pack("<12fH", *numbers, 0)
    return bytes(blob)


def test_ascii_and_binary_stl_import_as_a_closed_mesh(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tetra.stl").write_text(_ascii_stl(), encoding="utf-8")
    (project / "tetra-bin.stl").write_bytes(_binary_stl())
    document = Document(project)
    info = document.import_stl("tetra", "tetra.stl")
    assert info.source == "mesh"
    assert info.volume_mm3 == pytest.approx(1 / 6, abs=1e-5)
    assert "no editable features" in document.feature_report("tetra").note
    binary = document.import_stl("binary", project / "tetra-bin.stl")
    assert binary.volume_mm3 == pytest.approx(1 / 6, abs=1e-5)
    reopened = Document(project)
    assert reopened.measure("tetra").volume_mm3 == pytest.approx(1 / 6, abs=1e-5)
    assert "no editable features" in reopened.feature_report("binary").note
    note = list_parameters(Workspace(project), "tetra")["note"]
    assert "no editable features" in note
    assert list_parameters(Workspace(project), "tetra")["parameters"] == []


def test_an_open_mesh_and_a_file_outside_the_project_are_refused(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "open.stl").write_text(
        "solid open\nfacet normal 0 0 0\nouter loop\n"
        "vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid open\n",
        encoding="utf-8",
    )
    document = Document(project)
    with pytest.raises(GeometryError, match="closed solid"):
        document.import_stl("open", "open.stl")
    outside = tmp_path / "outside.stl"
    outside.write_text(_ascii_stl(), encoding="utf-8")
    with pytest.raises(AICADError, match="outside the project"):
        document.import_stl("remote", "../outside.stl")
    (project / "notes.txt").write_text("solid nope\n", encoding="utf-8")
    with pytest.raises(GeometryError, match="not an .stl file"):
        document.import_stl("notes", "notes.txt")
    assert "open" not in {part.name for part in document.list_parts()}


def test_the_import_panel_offers_stl_beside_step():
    script = Path("src/aicad/app_static/tools.js").read_text(encoding="utf-8")
    assert "Import STL" in script
    assert "import_stl" in script
    assert "no editable features" in script
