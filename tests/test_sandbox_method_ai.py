"""A name inside a container or a session frame does not open another file.

The marker is CANARY_HIT. A frame path, a 3MF relationship, and a DXF image
path stay inside the project.
"""

from __future__ import annotations

import zipfile

import pytest

from aicad.errors import GeometryError
from aicad.kernel import import_step_file
from aicad.session_record import RecordRefused, _load_png

_MARKER = "CANARY_HIT\n"


def _marker(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker.bin"
    marker.write_text(_MARKER, encoding="utf-8")
    return marker


def test_a_frame_path_does_not_read_outside_the_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    marker = _marker(tmp_path)
    for relative in (
        "../outside/marker.bin",
        str(marker),
        "records/session/frames/FOO~1.png",
        "records/session/frames/hole\u202e.png",
        "records/session/frames/pic:secret.png",
    ):
        with pytest.raises((RecordRefused, GeometryError)):
            _load_png(project, relative)
    assert marker.read_text(encoding="utf-8") == _MARKER


def test_a_container_name_does_not_open_another_file(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    marker = _marker(tmp_path)
    package = project / "part.3mf"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr(
            "_rels/.rels",
            f'<Relationships><Relationship Target="{marker.as_posix()}"/></Relationships>',
        )
    drawing = project / "sheet.dxf"
    drawing.write_text(f"0\nIMAGE\n1\n{marker.as_posix()}\n", encoding="utf-8")
    for path in (package, drawing):
        with pytest.raises(GeometryError) as raised:
            import_step_file(path)
        assert "CANARY_HIT" not in str(raised.value)
    assert marker.read_text(encoding="utf-8") == _MARKER
