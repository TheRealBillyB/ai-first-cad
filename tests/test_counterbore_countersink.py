"""Counterbore and countersink holes are shape operations.

A cap screw's counterbore or a flat-head screw's countersink had to be built
from two cylinders, or a script, each time. They are now one cut operation.
"""

from __future__ import annotations

import math

import pytest

from aicad import kernel
from aicad.errors import GeometryError
from aicad.workspace import Workspace

_PLATE = [{"op": "add", "shape": "box", "size": [40, 40, 10], "center": [0, 0, 5]}]
_VOLUME = 40 * 40 * 10


def _removed(hole: dict) -> float:
    return _VOLUME - float(kernel.make_from_ops([*_PLATE, {"op": "cut", **hole}]).impl.volume)


def test_a_counterbore_removes_the_hole_and_the_bore():
    removed = _removed(
        {"shape": "counterbore", "at": [0, 0, 10], "diameter": 6.6, "depth": 10, "counterbore_diameter": 11, "counterbore_depth": 6.5}
    )
    assert removed == pytest.approx(math.pi * (3.3**2 * 10 + (5.5**2 - 3.3**2) * 6.5), rel=1e-6)


@pytest.mark.parametrize("angle", [90.0, 82.0])
def test_a_countersink_removes_the_cone_and_the_hole(angle):
    removed = _removed(
        {"shape": "countersink", "at": [0, 0, 10], "diameter": 6.6, "depth": 10, "countersink_diameter": 12.4, "countersink_angle": angle}
    )
    sink = (6.2 - 3.3) / math.tan(math.radians(angle) / 2)
    cone = math.pi * sink / 3 * (6.2**2 + 6.2 * 3.3 + 3.3**2)
    assert removed == pytest.approx(cone + math.pi * 3.3**2 * (10 - sink), rel=1e-6)


def test_a_blind_hole_drilled_from_a_side_face():
    removed = _removed(
        {"shape": "counterbore", "at": [20, 0, 5], "axis": [-1, 0, 0], "diameter": 4, "depth": 15, "counterbore_diameter": 7, "counterbore_depth": 4}
    )
    assert removed == pytest.approx(math.pi * (2**2 * 15 + (3.5**2 - 2**2) * 4), rel=1e-6)


def test_a_countersink_has_a_cone_of_the_stated_angle():
    shape = kernel.make_from_ops(
        [*_PLATE, {"op": "cut", "shape": "countersink", "at": [0, 0, 10], "diameter": 6.6, "depth": 10, "countersink_diameter": 12.4}]
    )
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType

    cones = []
    for face in shape.impl.faces():
        adaptor = BRepAdaptor_Surface(face.wrapped)
        if adaptor.GetType() == GeomAbs_SurfaceType.GeomAbs_Cone:
            cones.append(math.degrees(adaptor.Cone().SemiAngle()))
    assert len(cones) == 1 and abs(cones[0]) == pytest.approx(45.0, abs=1e-6)


def test_a_counterbore_is_a_step_on_a_shape_part(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "plate", "ops": _PLATE}, source="agent")
    workspace.execute(
        "add_step",
        {
            "name": "plate",
            "step": {"op": "cut", "shape": "counterbore", "at": [10, 10, 10], "diameter": 5.5, "depth": 10, "counterbore_diameter": 9.5, "counterbore_depth": 5.4},
        },
        source="agent",
    )
    volume = workspace.document.measure("plate").volume_mm3
    assert volume == pytest.approx(_VOLUME - math.pi * (2.75**2 * 10 + (4.75**2 - 2.75**2) * 5.4), rel=1e-6)


@pytest.mark.parametrize(
    ("hole", "message"),
    [
        ({"op": "add", "shape": "counterbore", "diameter": 5, "depth": 5, "counterbore_diameter": 8, "counterbore_depth": 2}, "Use it with op cut"),
        ({"op": "cut", "shape": "counterbore", "diameter": 5, "depth": 5, "counterbore_diameter": 4, "counterbore_depth": 2}, "larger than diameter"),
        ({"op": "cut", "shape": "counterbore", "diameter": 5, "depth": 5, "counterbore_diameter": 8, "counterbore_depth": 6}, "less than the hole depth"),
        ({"op": "cut", "shape": "countersink", "diameter": 5, "depth": 1, "countersink_diameter": 12}, "deeper than the hole"),
        ({"op": "cut", "shape": "countersink", "diameter": 5, "depth": 5, "countersink_diameter": 8, "countersink_angle": 180}, "included angle"),
        ({"op": "cut", "shape": "countersink", "diameter": 5, "depth": 5, "countersink_diameter": 8, "axis": [0, 0, 0]}, "non-zero"),
    ],
)
def test_a_hole_that_cannot_be_made_is_refused(hole, message):
    with pytest.raises(GeometryError, match=message):
        kernel.make_from_ops([*_PLATE, {"at": [0, 0, 10], **hole}])
