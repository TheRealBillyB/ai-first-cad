"""Only a STEP exchange is given to the STEP reader.

Other formats and a STEP list nested past the limit are refused first. The
marker file outside the project stays as it was written. Nothing is sent to
another machine.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from aicad.errors import GeometryError
from aicad.kernel import export_step_file, import_step_bytes, import_step_file, make_from_ops
from aicad.mesh import volume_mm3

_MARKER = "CANARY_HIT\n"


def _marker(tmp_path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text("untouched\n", encoding="utf-8")
    return marker


def _box_bytes(folder: Path) -> bytes:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "source.step"
    export_step_file(make_from_ops([{"op": "add", "shape": "box", "size": [2, 3, 4]}]), path)
    return path.read_bytes()


def _payloads() -> list[tuple[str, bytes]]:
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as inner:
        inner.writestr("../outside/SANDBOX_CANARY.txt", _MARKER)
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as outer:
        outer.writestr("3D/3dmodel.model", "<model/>")
        outer.writestr("../outside/SANDBOX_CANARY.txt", _MARKER)
        outer.writestr("nested.zip", nested.getvalue())
    return [
        ("part.stl", b"solid marker\nendsolid marker\n"),
        ("part.3mf", package.getvalue()),
        ("part.gltf", b'{"asset":{"version":"2.0"},"buffers":[{"uri":"file:///tmp/marker"}]}'),
        ("part.obj", b"mtllib marker.mtl\nv 0 0 0\n"),
        ("part.dae", b'<?xml version="1.0"?><COLLADA><asset/></COLLADA>'),
        ("part.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'),
        ("part.pdf", b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"),
        ("part.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32),
    ]


def test_other_formats_do_not_reach_the_step_reader(tmp_path, monkeypatch):
    marker = _marker(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    called = False

    def reader(path, data):
        nonlocal called
        called = True
        marker.write_text(_MARKER, encoding="utf-8")
        raise GeometryError(f"Could not read STEP file {path}.")

    monkeypatch.setattr("aicad.kernel._read_step_bytes", reader)
    for name, payload in _payloads():
        path = project / name
        path.write_bytes(payload)
        with pytest.raises(GeometryError) as raised:
            import_step_file(path)
        assert "CANARY_HIT" not in str(raised.value)
        assert _MARKER.strip() not in path.name
    assert called is False
    assert marker.read_text(encoding="utf-8") == "untouched\n"


def test_a_deep_step_list_does_not_reach_the_reader(tmp_path, monkeypatch):
    marker = _marker(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    called = False

    def reader(path, data):
        nonlocal called
        called = True
        marker.write_text(_MARKER, encoding="utf-8")
        raise GeometryError(f"Could not read STEP file {path}.")

    monkeypatch.setattr("aicad.kernel._read_step_bytes", reader)
    deep = project / "deep.step"
    deep.write_bytes(b"ISO-10303-21;\nDATA;\n" + b"(" * 33 + b");\nEND-ISO-10303-21;\n")
    with pytest.raises(GeometryError) as raised:
        import_step_file(deep)
    assert "CANARY_HIT" not in str(raised.value)
    assert "(" not in str(raised.value)
    assert called is False
    assert marker.read_text(encoding="utf-8") == "untouched\n"

    def counted(path, data):
        nonlocal called
        called = True
        raise GeometryError(f"Could not read STEP file {path}.")

    monkeypatch.setattr("aicad.kernel._read_step_bytes", counted)
    quoted = project / "quoted.step"
    quoted.write_bytes(b"ISO-10303-21;\nDATA;\n#1=X('" + b"(" * 40 + b"');\nEND-ISO-10303-21;\n")
    with pytest.raises(GeometryError) as raised:
        import_step_file(quoted)
    assert called is True
    assert "CANARY_HIT" not in str(raised.value)
    assert marker.read_text(encoding="utf-8") == "untouched\n"


def test_a_step_exchange_with_a_leading_mark_still_imports(tmp_path):
    marker = _marker(tmp_path)
    data = _box_bytes(tmp_path / "source")
    solid = import_step_bytes(tmp_path / "project" / "box.step", b"\xef\xbb\xbf\n" + data)
    assert abs(volume_mm3(solid) - 24) < 1e-6
    assert marker.read_text(encoding="utf-8") == "untouched\n"
