"""A mesh import may read a subfolder of the mesh file's folder.

The marker is CANARY_HIT. A relative path stays inside that folder. A link
is not followed. The shared file reader opens the file.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from aicad.errors import GeometryError
from aicad.kernel import import_step_file

_MARKER = "CANARY_HIT\n"
_TETRA = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
_FACES = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]


def _project(tmp_path: Path) -> tuple[Path, Path]:
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker.bin"
    marker.write_text(_MARKER, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    return project, marker


def _assert_closed(marker: Path, opened: list[str]) -> None:
    assert marker.read_text(encoding="utf-8") == _MARKER
    assert all(str(marker) not in item for item in opened)


def _watch(monkeypatch) -> list[str]:
    import os

    opened: list[str] = []
    real = os.open

    def spy(path, flags, *args, **kwargs):
        opened.append(os.fspath(path))
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    return opened


def _volume(path: Path) -> float:
    shape = import_step_file(path)
    return float(shape.impl.volume)


def _tetra_bytes() -> bytes:
    blob = bytearray()
    for point in _TETRA:
        blob += struct.pack("<3f", *point)
    for triangle in _FACES:
        blob += struct.pack("<3H", *triangle)
    return bytes(blob)


def _gltf(buffers: list[dict]) -> dict:
    positions = 48
    return {
        "buffers": buffers,
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": positions},
            {"buffer": 0, "byteOffset": positions, "byteLength": 24},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 12, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
    }


def _obj() -> str:
    lines = [f"v {x} {y} {z}" for x, y, z in _TETRA]
    lines.append("mtllib mat.mtl")
    for face in _FACES:
        lines.append("f " + " ".join(str(index + 1) for index in face))
    return "\n".join(lines) + "\n"


def test_a_gltf_buffer_in_a_subfolder_is_read(tmp_path):
    project, marker = _project(tmp_path)
    (project / "textures").mkdir()
    (project / "textures" / "positions.bin").write_bytes(_tetra_bytes())
    (project / "part.gltf").write_text(
        json.dumps(_gltf([{"uri": "textures/positions.bin", "byteLength": 72}])),
        encoding="utf-8",
    )
    assert _volume(project / "part.gltf") == pytest.approx(1 / 6)
    (project / "nested.gltf").write_text(
        json.dumps(_gltf([{"uri": "textures\\positions.bin", "byteLength": 72}])),
        encoding="utf-8",
    )
    assert _volume(project / "nested.gltf") == pytest.approx(1 / 6)
    assert marker.read_text(encoding="utf-8") == _MARKER


def test_a_gltf_subfolder_link_is_not_followed(tmp_path):
    project, marker = _project(tmp_path)
    try:
        (project / "textures").symlink_to(marker.parent, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"this machine cannot create a directory symlink: {exc}")
    (project / "part.gltf").write_text(
        json.dumps(_gltf([{"uri": "textures/marker.bin", "byteLength": 4}])),
        encoding="utf-8",
    )
    with pytest.raises(GeometryError) as raised:
        import_step_file(project / "part.gltf")
    assert "link" in str(raised.value)
    assert marker.read_text(encoding="utf-8") == _MARKER


def test_a_texture_path_with_too_many_folders_is_refused(tmp_path, monkeypatch):
    project, marker = _project(tmp_path)
    opened = _watch(monkeypatch)
    uri = "/".join(["textures"] * 9)
    (project / "part.gltf").write_text(
        json.dumps(_gltf([{"uri": uri, "byteLength": 4}])),
        encoding="utf-8",
    )
    with pytest.raises(GeometryError) as raised:
        import_step_file(project / "part.gltf")
    assert "outside its folder" in str(raised.value)
    _assert_closed(marker, opened)


def test_an_obj_texture_in_a_subfolder_is_read(tmp_path):
    project, marker = _project(tmp_path)
    (project / "maps").mkdir()
    (project / "maps" / "clay.png").write_bytes(b"png")
    (project / "mat.mtl").write_text("newmtl clay\nmap_Kd maps/clay.png\n", encoding="utf-8")
    (project / "part.obj").write_text(_obj(), encoding="utf-8")
    assert _volume(project / "part.obj") == pytest.approx(1 / 6)
    assert marker.read_text(encoding="utf-8") == _MARKER
