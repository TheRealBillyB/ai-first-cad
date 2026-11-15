"""A point typed on a face picks that face, whatever its shape.

Among faces in one plane, ``at`` took the face whose center was closest. An
L-shaped or ring face's center can be far from the face, so a point on the
face could pick a smaller face nearby. A viewport click is not affected: its
point is the clicked face's own center.
"""

from __future__ import annotations

from aicad import fea, kernel


def _l_block_and_post():
    """A base plate with an L-shaped block and a square post on it, both topped at z = 10."""
    return kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [60, 60, 5], "center": [30, 30, 2.5]},
            {"op": "add", "shape": "box", "size": [40, 10, 5], "center": [20, 5, 7.5]},
            {"op": "add", "shape": "box", "size": [10, 40, 5], "center": [5, 20, 7.5]},
            {"op": "add", "shape": "box", "size": [4, 4, 5], "center": [22, 22, 7.5]},
        ]
    )


def _top_faces(shape):
    return [
        face
        for face in kernel.describe_faces(shape)
        if face["kind"] == "plane" and face["normal"][2] > 0.9 and abs(face["center_mm"][2] - 10) < 1e-6
    ]


def test_a_point_on_the_arm_of_an_l_picks_the_l():
    shape = _l_block_and_post()
    tops = _top_faces(shape)
    post = next(face for face in tops if abs(face["area_mm2"] - 16) < 1e-6)
    l_face = max(tops, key=lambda face: face["area_mm2"])
    catalog = kernel.describe_faces(shape)
    point = [38, 5, 10]  # on the end of the L's long arm, nearer the post's center than the L's
    selector = {"kind": "plane", "normal": [0, 0, 1], "at": point}
    assert fea.select_faces(catalog, selector, shape)[0]["index"] == l_face["index"]
    assert fea.select_faces(catalog, {"kind": "nearest", "at": point}, shape)[0]["index"] == l_face["index"]
    # Without the solid, the center is all there is: this is what picked the post.
    assert fea.select_faces(catalog, selector)[0]["index"] == post["index"]


def test_a_point_on_a_ring_picks_the_ring_not_the_disc_inside_it():
    shape = kernel.make_from_ops(
        [
            {"op": "add", "shape": "cylinder", "radius": 20, "height": 10, "center": [0, 0, 5]},
            {"op": "cut", "shape": "cylinder", "radius": 10, "height": 3, "center": [0, 0, 8.5]},
            {"op": "add", "shape": "cylinder", "radius": 8, "height": 3, "center": [0, 0, 8.5]},
        ]
    )
    tops = _top_faces(shape)
    assert len(tops) == 2
    ring = max(tops, key=lambda face: face["area_mm2"])
    disc = min(tops, key=lambda face: face["area_mm2"])
    catalog = kernel.describe_faces(shape)
    for point, wanted in (([15, 0, 10], ring), ([0, -17, 10], ring), ([2, 1, 10], disc)):
        selector = {"kind": "plane", "normal": [0, 0, 1], "at": point}
        assert fea.select_faces(catalog, selector, shape)[0]["index"] == wanted["index"], point


def test_a_viewport_ref_still_names_the_face_it_came_from():
    shape = _l_block_and_post()
    catalog = kernel.describe_faces(shape)
    for face in _top_faces(shape):
        assert fea.select_faces(catalog, {"ref": face["ref"]}, shape)[0]["index"] == face["index"]


def test_loads_and_supports_are_picked_on_the_solid(tmp_path):
    shape = _l_block_and_post()
    catalog = kernel.describe_faces(shape)
    l_face = max(_top_faces(shape), key=lambda face: face["area_mm2"])
    select = {"kind": "plane", "normal": [0, 0, 1], "at": [38, 5, 10]}
    load = fea._bind_load({"type": "force", "vector": [0, 0, -10], "select": select}, catalog, 0, shape)
    fix = fea._bind_support({"select": select}, catalog, 0, shape)
    assert [face["index"] for face in load["faces"]] == [l_face["index"]]
    assert [face["index"] for face in fix["faces"]] == [l_face["index"]]
