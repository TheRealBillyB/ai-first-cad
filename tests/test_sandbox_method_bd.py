"""The OpenCascade glTF reader does not open a file.

The marker is CANARY_HIT. A glTF import still uses the shared file reader.
"""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path

import pytest

from aicad.errors import GeometryError
from aicad.kernel import import_step_file
from aicad.paths import lock_unused_gltf_reader

_MARKER = "CANARY_HIT"
_TETRA = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
_FACES = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]


def _tetra_bytes() -> bytes:
    blob = bytearray()
    for point in _TETRA:
        blob += struct.pack("<3f", *point)
    for triangle in _FACES:
        blob += struct.pack("<3H", *triangle)
    return bytes(blob)


def test_the_opencascade_gltf_reader_does_not_open_a_file(tmp_path):
    rwgltf = pytest.importorskip("OCP.RWGltf")
    from OCP.Message import Message_ProgressRange
    from OCP.TCollection import TCollection_AsciiString

    lock_unused_gltf_reader()
    canary = tmp_path / "canary.glb"
    canary.write_bytes(_MARKER.encode("utf-8"))
    reader = rwgltf.RWGltf_CafReader()
    with pytest.raises(GeometryError, match="not used"):
        reader.Perform(TCollection_AsciiString(str(canary)), Message_ProgressRange())
    with pytest.raises(GeometryError, match="not used"):
        reader.ProbeHeader(TCollection_AsciiString(str(canary)))
    assert getattr(rwgltf.RWGltf_CafWriter.Perform, "__name__", "") == "blocked"
    assert canary.read_bytes() == _MARKER.encode("utf-8")


def test_a_gltf_import_still_reads_embedded_bytes(tmp_path):
    payload = base64.b64encode(_tetra_bytes()).decode("ascii")
    positions = 48
    document = {
        "buffers": [{"uri": f"data:application/octet-stream;base64,{payload}", "byteLength": 72}],
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
    mesh = tmp_path / "part.gltf"
    mesh.write_text(json.dumps(document), encoding="utf-8")
    shape = import_step_file(mesh)
    assert float(shape.impl.volume) == pytest.approx(1 / 6)
