"""glTF, OBJ, and COLLADA imports do not open a path outside the file.

The marker is CANARY_HIT. Embedded data and one same-folder file are read
through the shared file reader. Any other buffer, image, or material library
is refused.
"""

from __future__ import annotations

import base64
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


def _gltf(buffers: list[dict], images: list[dict] | None = None) -> dict:
    positions = 48
    document = {
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
    if images is not None:
        document["images"] = images
    return document


def _obj() -> str:
    lines = [f"v {x} {y} {z}" for x, y, z in _TETRA]
    lines.append("mtllib mat.mtl")
    for face in _FACES:
        lines.append("f " + " ".join(str(index + 1) for index in face))
    return "\n".join(lines) + "\n"


def _collada(image: str | None = None, library: str | None = None) -> str:
    points = " ".join(str(value) for point in _TETRA for value in point)
    corners = " ".join(str(index) for face in _FACES for index in face)
    image_xml = ""
    if image is not None:
        image_xml = f"<library_images><image id='pix'><init_from>{image}</init_from></image></library_images>"
    library_xml = ""
    if library is not None:
        library_xml = f"<library_effects><effect id='e'><instance_effect url='{library}'/></effect></library_effects>"
    return (
        "<COLLADA>"
        f"{image_xml}{library_xml}"
        "<library_geometries><geometry id='tetra'><mesh>"
        f"<source id='pos'><float_array id='pos-array' count='12'>{points}</float_array>"
        "<technique_common><accessor source='#pos-array' count='4' stride='3'/></technique_common></source>"
        "<vertices id='verts'><input semantic='POSITION' source='#pos'/></vertices>"
        f"<triangles count='4'><input semantic='VERTEX' source='#verts' offset='0'/><p>{corners}</p></triangles>"
        "</mesh></geometry></library_geometries></COLLADA>"
    )


def test_a_gltf_same_folder_buffer_is_read_by_the_helper(tmp_path):
    project, marker = _project(tmp_path)
    (project / "positions.bin").write_bytes(_tetra_bytes())
    (project / "part.gltf").write_text(
        json.dumps(_gltf([{"uri": "positions.bin", "byteLength": 72}])),
        encoding="utf-8",
    )
    assert _volume(project / "part.gltf") == pytest.approx(1 / 6)
    assert marker.read_text(encoding="utf-8") == _MARKER


def test_a_gltf_buffer_outside_the_folder_is_not_opened(tmp_path, monkeypatch):
    project, marker = _project(tmp_path)
    opened = _watch(monkeypatch)
    mesh = project / "part.gltf"
    for uri in (
        str(marker),
        "../outside/marker.bin",
        "textures/marker.bin",
        "./positions.bin",
        "http://example.test/marker.bin",
        "C:/marker.bin",
        "%2e%2e/outside/marker.bin",
        "positions.bin ",
        "NUL.bin",
    ):
        mesh.write_text(json.dumps(_gltf([{"uri": uri, "byteLength": 4}])), encoding="utf-8")
        with pytest.raises(GeometryError) as raised:
            import_step_file(mesh)
        assert "CANARY_HIT" not in str(raised.value)
        assert "outside its folder" in str(raised.value)
    _assert_closed(marker, opened)


def test_an_embedded_gltf_does_not_open_a_sibling(tmp_path, monkeypatch):
    project, marker = _project(tmp_path)
    opened = _watch(monkeypatch)
    payload = base64.b64encode(_tetra_bytes()).decode("ascii")
    (project / "positions.bin").write_bytes(b"not-the-mesh")
    mesh = project / "part.gltf"
    mesh.write_text(
        json.dumps(_gltf([{"uri": f"data:application/octet-stream;base64,{payload}", "byteLength": 72}])),
        encoding="utf-8",
    )
    assert _volume(mesh) == pytest.approx(1 / 6)
    assert all(not item.endswith("positions.bin") for item in opened)
    _assert_closed(marker, opened)


def test_a_glb_uses_its_own_buffer(tmp_path, monkeypatch):
    project, marker = _project(tmp_path)
    opened = _watch(monkeypatch)
    (project / "positions.bin").symlink_to(marker)
    document = json.dumps(_gltf([{"byteLength": 72}])).encode("utf-8")
    document += b" " * ((4 - len(document) % 4) % 4)
    binary = _tetra_bytes()
    blob = bytearray(b"glTF")
    blob += struct.pack("<II", 2, 12 + 8 + len(document) + 8 + len(binary))
    blob += struct.pack("<II", len(document), 0x4E4F534A)
    blob += document
    blob += struct.pack("<II", len(binary), 0x004E4942)
    blob += binary
    (project / "part.glb").write_bytes(blob)
    assert _volume(project / "part.glb") == pytest.approx(1 / 6)
    assert all("positions.bin" not in item for item in opened)
    _assert_closed(marker, opened)


def test_a_gltf_sibling_link_is_not_followed(tmp_path):
    project, marker = _project(tmp_path)
    (project / "positions.bin").symlink_to(marker)
    (project / "part.gltf").write_text(
        json.dumps(_gltf([{"uri": "positions.bin", "byteLength": 4}])),
        encoding="utf-8",
    )
    with pytest.raises(GeometryError) as raised:
        import_step_file(project / "part.gltf")
    assert "link" in str(raised.value)
    assert marker.read_text(encoding="utf-8") == _MARKER


def test_an_obj_material_library_stays_in_the_folder(tmp_path, monkeypatch):
    project, marker = _project(tmp_path)
    (project / "mat.mtl").write_text("newmtl clay\nmap_Kd -s 1 1 1 clay.png\n", encoding="utf-8")
    (project / "clay.png").write_bytes(b"png")
    (project / "part.obj").write_text(_obj(), encoding="utf-8")
    assert _volume(project / "part.obj") == pytest.approx(1 / 6)
    (project / "clay.png").unlink()
    with pytest.raises(GeometryError) as missing:
        import_step_file(project / "part.obj")
    assert "could not be read" in str(missing.value)
    opened = _watch(monkeypatch)
    (project / "mat.mtl").write_text(f"map_Kd {marker.as_posix()}\n", encoding="utf-8")
    with pytest.raises(GeometryError) as outside:
        import_step_file(project / "part.obj")
    assert "outside its folder" in str(outside.value)
    (project / "part.obj").write_text(_obj().replace("mtllib mat.mtl", f"mtllib {marker.as_posix()}"), encoding="utf-8")
    with pytest.raises(GeometryError) as library:
        import_step_file(project / "part.obj")
    assert "outside its folder" in str(library.value)
    _assert_closed(marker, opened)


def test_a_collada_image_and_library_stay_in_the_folder(tmp_path, monkeypatch):
    project, marker = _project(tmp_path)
    (project / "pix.bin").write_bytes(b"pix")
    (project / "lib.dae").write_text("<COLLADA></COLLADA>", encoding="utf-8")
    mesh = project / "part.dae"
    mesh.write_text(_collada(image="pix.bin", library="lib.dae#effect"), encoding="utf-8")
    assert _volume(mesh) == pytest.approx(1 / 6)
    (project / "pix.bin").unlink()
    with pytest.raises(GeometryError) as missing:
        import_step_file(mesh)
    assert "could not be read" in str(missing.value)
    opened = _watch(monkeypatch)
    mesh.write_text(_collada(image=str(marker)), encoding="utf-8")
    with pytest.raises(GeometryError) as image:
        import_step_file(mesh)
    assert "outside its folder" in str(image.value)
    mesh.write_text(_collada(library="../outside/lib.dae#effect"), encoding="utf-8")
    with pytest.raises(GeometryError) as library:
        import_step_file(mesh)
    assert "outside its folder" in str(library.value)
    (project / "pix.bin").symlink_to(marker)
    mesh.write_text(_collada(image="pix.bin"), encoding="utf-8")
    with pytest.raises(GeometryError) as linked:
        import_step_file(mesh)
    assert "link" in str(linked.value)
    _assert_closed(marker, opened)


def test_a_collada_library_that_points_outside_is_not_followed(tmp_path, monkeypatch):
    project, marker = _project(tmp_path)
    opened = _watch(monkeypatch)
    (project / "lib.dae").write_text(
        f"<COLLADA><library_images><image><init_from>{marker.as_posix()}</init_from></image></library_images></COLLADA>",
        encoding="utf-8",
    )
    (project / "part.dae").write_text(_collada(library="lib.dae#effect"), encoding="utf-8")
    with pytest.raises(GeometryError) as raised:
        import_step_file(project / "part.dae")
    assert "outside its folder" in str(raised.value)
    _assert_closed(marker, opened)
