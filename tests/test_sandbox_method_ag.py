"""Tessellation stays inside the triangle budget before native work starts.

A large face and a tiny requested tolerance are coarsened. A solid with too
many faces is refused before a face is meshed.
"""

from __future__ import annotations

import pytest

from aicad.errors import GeometryError
from aicad import kernel

_CAP = 1_000_000


def test_a_large_face_is_coarsened_before_export(tmp_path, monkeypatch):
    shape = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [2000, 2000, 10]}])
    seen: dict = {}

    def fake_export(_impl, path, tolerance, angular_tolerance):
        seen["linear"] = tolerance
        seen["angular"] = angular_tolerance
        path.write_bytes(b"solid")
        return True

    monkeypatch.setattr(kernel, "export_stl", fake_export)
    kernel.export_stl_file(shape, tmp_path / "part.stl", linear_tolerance=1e-6, angular_tolerance=1e-6)
    area = abs(float(shape.impl.area))
    assert seen["linear"] > 1e-6
    assert 2.0 * area / (seen["linear"] ** 2) <= _CAP
    assert seen["angular"] >= 0.05


def test_face_deflection_stays_inside_the_triangle_cap():
    area = 4_000_000.0
    linear = kernel.face_linear(area, 0.08, _CAP)
    assert 2.0 * area / (linear * linear) <= _CAP
    small = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10]}])
    mesh = kernel.viewport_mesh(small)
    assert len(mesh["triangle_face"]) <= _CAP
    assert mesh["triangle_face"]


def test_too_many_faces_are_refused_before_tessellation():
    class Impl:
        def faces(self):
            return [object()] * (kernel._MAX_FACES + 1)

        def bounding_box(self):
            raise AssertionError("tessellation ran")

    shape = kernel.Shape(_impl=Impl())
    with pytest.raises(GeometryError, match="faces"):
        kernel.tessellate(shape)
    with pytest.raises(GeometryError, match="faces"):
        kernel.viewport_mesh(shape)
