"""Matching mesh surfaces to CAD faces walks the part's faces once."""

from __future__ import annotations

import numpy as np
import pytest

from aicad import fea, kernel
from aicad.errors import GeometryError


def _plate():
    ops = [{"op": "add", "shape": "box", "size": [40, 20, 6], "center": [0, 0, 3]}]
    for x in (-10, 10):
        ops.append({"op": "cut", "shape": "cylinder", "radius": 2.5, "height": 20, "center": [x, 0, 3]})
    return kernel.make_from_ops(ops)


class _CountingImpl:
    def __init__(self, impl):
        self._impl = impl
        self.calls = 0

    def faces(self):
        self.calls += 1
        return self._impl.faces()


class _Counting:
    def __init__(self, shape):
        self.impl = _CountingImpl(shape.impl)


def test_the_table_matches_one_distance_at_a_time():
    shape = _plate()
    faces = list(range(len(kernel.describe_faces(shape))))
    points = [(0.0, 0.0, 6.0), (20.0, 0.0, 3.0), (10.0, 2.5, 3.0), (-3.0, 4.0, 0.0)]
    table = kernel.distances_points_to_faces(shape, faces, points)
    assert len(table) == len(faces)
    for face_index, row in zip(faces, table):
        assert row == pytest.approx([kernel.distance_point_to_face(shape, face_index, point) for point in points])


def test_a_face_index_outside_the_part_is_refused():
    shape = _plate()
    with pytest.raises(GeometryError, match="outside this part"):
        kernel.distances_points_to_faces(shape, [999], [(0.0, 0.0, 0.0)])


def test_surface_matching_builds_the_face_list_once():
    shape = _plate()
    catalog = kernel.describe_faces(shape)
    points = np.array([[0.0, 0.0, 6.0], [5.0, 5.0, 6.0], [0.0, 0.0, 0.0], [5.0, 5.0, 0.0], [20.0, 0.0, 3.0]])
    surfaces = [
        {"point_indices": [0, 1]},
        {"point_indices": [2, 3]},
        {"point_indices": [4]},
        {"point_indices": []},
    ]
    counting = _Counting(shape)
    fea._assign_surface_faces(counting, catalog, surfaces, points, 2.0)
    assert counting.impl.calls == 1
    tops = [face["index"] for face in catalog if face["kind"] == "plane" and face["normal"] == pytest.approx([0, 0, 1])]
    bottoms = [face["index"] for face in catalog if face["kind"] == "plane" and face["normal"] == pytest.approx([0, 0, -1])]
    assert surfaces[0]["face_index"] in tops
    assert surfaces[1]["face_index"] in bottoms
    assert surfaces[2]["face_index"] is not None
    assert surfaces[3]["face_index"] is None
