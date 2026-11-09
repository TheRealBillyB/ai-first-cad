"""The faster meshing gives exactly the meshes the previous code gave.

Meshing the 38 parts of a finger rig took 8.5 s, and most of it was Python:
one build123d Vector per node, then a loop per triangle with 4.7 million
round() calls. The triangulation is OpenCascade's in both versions. The
references below are the previous functions, kept here unchanged.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from aicad import kernel

# The previous functions, as they were.


def _reference_viewport_mesh(shape: kernel.Shape) -> dict:
    impl = shape.impl
    face_list = list(impl.faces())
    kernel._refuse_face_count(len(face_list))
    faces = kernel.describe_faces(shape)
    edges = kernel.describe_edges(shape)
    diagonal = float(impl.bounding_box().diagonal) or 1.0
    base = min(0.28, max(0.08, diagonal * 0.004))
    positions: list[float] = []
    normals: list[float] = []
    triangle_face: list[int] = []
    remaining = kernel._MAX_TRIANGLES
    for index, face in enumerate(face_list):
        tolerance = kernel.face_linear(kernel._surface_area(face), base, remaining)
        vertices, triangles = face.tessellate(tolerance)
        if not triangles:
            continue
        points = [(float(vertex.X), float(vertex.Y), float(vertex.Z)) for vertex in vertices]
        for tri in triangles:
            corners = [points[int(tri[0])], points[int(tri[1])], points[int(tri[2])]]
            ax, ay, az = corners[1][0] - corners[0][0], corners[1][1] - corners[0][1], corners[1][2] - corners[0][2]
            bx, by, bz = corners[2][0] - corners[0][0], corners[2][1] - corners[0][1], corners[2][2] - corners[0][2]
            nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
            length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            nx, ny, nz = nx / length, ny / length, nz / length
            for corner in corners:
                positions.extend(round(component, 4) for component in corner)
                normals.extend((round(nx, 4), round(ny, 4), round(nz, 4)))
            triangle_face.append(index)
            remaining = kernel._MAX_TRIANGLES - len(triangle_face)
    edge_payload = []
    for edge, record in zip(impl.edges(), edges):
        samples = edge.positions(deflection=base)
        edge_payload.append(
            {
                "index": record["index"],
                "kind": record["kind"],
                "ref": record["ref"],
                "length_mm": record["length_mm"],
                "midpoint_mm": record["midpoint_mm"],
                "points": [[round(float(point.X), 4), round(float(point.Y), 4), round(float(point.Z), 4)] for point in samples],
            }
        )
    bbox = impl.bounding_box()
    return {
        "units": "mm",
        "positions": positions,
        "normals": normals,
        "triangle_face": triangle_face,
        "faces": faces,
        "edges": edge_payload,
        "bounding_box": {
            "min": [kernel.round_mm(bbox.min.X), kernel.round_mm(bbox.min.Y), kernel.round_mm(bbox.min.Z)],
            "max": [kernel.round_mm(bbox.max.X), kernel.round_mm(bbox.max.Y), kernel.round_mm(bbox.max.Z)],
        },
    }


def _reference_tessellate(shape: kernel.Shape) -> kernel.TriangleMesh:
    impl = shape.impl
    box = impl.bounding_box()
    diagonal = float(box.diagonal)
    size = box.size
    linear = kernel.tessellation_linear(float(size.X), float(size.Y), float(size.Z), diagonal)
    linear = kernel.linear_for_surface(kernel._surface_area(impl), linear)
    vertices, triangles, normals, _uvs = impl.tessellate_with_uvs(linear, angular_tolerance=0.18, atlas_packing=False)
    points = np.array([(v.X, v.Y, v.Z) for v in vertices], dtype=np.float64)
    norms = np.array([(v.X, v.Y, v.Z) for v in normals], dtype=np.float64)
    faces = np.array(triangles, dtype=np.int32)
    return kernel.TriangleMesh(vertices=points, normals=norms, triangles=faces, edges=[])


_SOLIDS = {
    "plate with holes": [
        {"op": "add", "shape": "box", "size": [40, 24, 6]},
        {"op": "cut", "shape": "cylinder", "radius": 3, "height": 20, "center": [-10, 0, 0]},
        {"op": "cut", "shape": "cylinder", "radius": 2.2, "height": 20, "center": [12, 5, 0]},
    ],
    "ball on a post": [
        {"op": "add", "shape": "cylinder", "radius": 4, "height": 30},
        {"op": "add", "shape": "sphere", "radius": 9, "center": [0, 0, 15]},
    ],
    "cone in a box": [
        {"op": "add", "shape": "box", "size": [20, 20, 20]},
        {"op": "cut", "shape": "cone", "radius": 8, "height": 18, "center": [0, 0, 3]},
    ],
}


def _solid(ops) -> kernel.Shape:
    shape, errors = kernel.prefix_from_ops(ops)
    assert shape is not None, errors
    return shape


def _copies(name: str):
    """Two copies of one solid with no triangulation yet: OpenCascade keeps a mesh on the solid."""
    ops = _SOLIDS[name]
    first, second = _solid(ops), _solid(ops)
    return first, second


@pytest.mark.parametrize("name", sorted(_SOLIDS))
@pytest.mark.parametrize("placed", [False, True], ids=["at the origin", "moved and turned"])
def test_the_viewport_mesh_is_the_same(name, placed):
    first, second = _copies(name)
    if placed:
        first = kernel.place(first, (12.5, -3.25, 40), (15, 30, 110))
        second = kernel.place(second, (12.5, -3.25, 40), (15, 30, 110))
    new = kernel.viewport_mesh(first)
    old = _reference_viewport_mesh(second)
    # The same bytes on the page.
    assert json.dumps(new) == json.dumps(old)


@pytest.mark.parametrize("name", sorted(_SOLIDS))
@pytest.mark.parametrize("placed", [False, True], ids=["at the origin", "moved and turned"])
def test_the_assembly_tessellation_is_the_same(name, placed):
    first, second = _copies(name)
    if placed:
        first = kernel.place(first, (-7, 2, 9), (90, 0, 33))
        second = kernel.place(second, (-7, 2, 9), (90, 0, 33))
    new = kernel.tessellate(first)
    old = _reference_tessellate(second)
    assert np.array_equal(new.triangles, old.triangles)
    assert np.array_equal(new.vertices, old.vertices)
    assert np.array_equal(new.normals, old.normals)


def test_rounding_near_a_half_matches_python():
    values = np.array([0.00005, -0.00005, 1.23455, 2.675, -3.14155, 1e-9, -0.0, 12345.67885, 0.30000000000000004])
    assert kernel._rounded_list(values) == [round(float(value), 4) for value in values]
