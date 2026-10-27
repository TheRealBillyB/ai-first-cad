"""A print mesh does not pack texture coordinates.

Two spur gears in a housing have enough faces, in enough sizes, that packing
those coordinates overflows the call stack. Print checks and pictures never
read the coordinates.
"""

from __future__ import annotations

from aicad import kernel
from aicad.gears import build_gear


def test_two_gears_in_a_housing_still_mesh():
    gear = kernel.adopt(
        build_gear(
            {
                "type": "spur",
                "module_mm": 1.0,
                "teeth": 20,
                "face_width_mm": 4.0,
                "bore_mm": 4.0,
            }
        )
    )
    centre = 20.8
    ring_inner = centre + 11.0 + 0.4
    housing = kernel.make_from_ops(
        [
            {"op": "add", "shape": "cylinder", "radius": ring_inner + 3.0, "height": 6.5, "center": [0, 0, 3.25]},
            {"op": "cut", "shape": "cylinder", "radius": ring_inner, "height": 6.0, "center": [0, 0, 3.5]},
            {"op": "add", "shape": "cylinder", "radius": 1.7, "height": 6.2, "center": [0, 0, 3.1]},
            {"op": "add", "shape": "cylinder", "radius": 3.2, "height": 1.0, "center": [0, 0, 6.0]},
            {"op": "add", "shape": "cylinder", "radius": 1.7, "height": 6.2, "center": [centre, 0, 3.1]},
            {"op": "add", "shape": "cylinder", "radius": 3.2, "height": 1.0, "center": [centre, 0, 6.0]},
        ]
    )
    shape = kernel.compound(
        [
            housing,
            kernel.place(gear, [0.0, 0.0, 3.0], [0.0, 0.0, 0.0]),
            kernel.place(gear, [centre, 0.0, 3.0], [0.0, 0.0, 0.0]),
        ]
    )
    mesh = kernel.tessellate(shape)
    assert len(mesh.triangles) > 1000
    assert len(mesh.vertices) > 1000
