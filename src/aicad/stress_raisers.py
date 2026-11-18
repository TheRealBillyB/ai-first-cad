"""Sharp inside corners and small inside radii, the places a part cracks first.

An inside corner is an edge where the material wraps more than half way round,
such as the root of a notch, a slot, or an L bracket's bend with no fillet.
Under load the stress there climbs as the corner gets sharper, and a mesh
coarser than the radius cannot see it. An inside radius is a partial cylinder
whose axis is on the empty side: a fillet in such a corner. A whole cylinder is
a hole, not a corner, and is left out.
"""

from __future__ import annotations

import math

import numpy as np

from aicad import kernel

# An inside radius under this is reported with the sharp corners.
SMALL_RADIUS_MM = 1.0
# The most points kept along one corner.
_MAX_SAMPLES = 200


def inside_corners(shape: kernel.Shape, small_radius_mm: float = SMALL_RADIUS_MM) -> list[dict]:
    """Every sharp inside corner and every inside radius under ``small_radius_mm``.

    Each item has ``kind`` (``sharp`` or ``small_radius``), ``radius_mm`` (0 for
    sharp), ``faces`` (indices from ``describe_faces``), ``at_mm`` (a point on
    the middle of it), ``length_mm``, and ``points`` (an array of points along it).
    """
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepOffset import BRepOffset_Analyse
    from OCP.ChFiDS import ChFiDS_TypeOfConcavity
    from OCP.GCPnts import GCPnts_AbscissaPoint

    faces = list(shape.impl.faces())
    found: list[dict] = []
    # A face meeting another within 1 degree of tangent is a smooth join, not a corner.
    analysis = BRepOffset_Analyse(shape.impl.wrapped, math.radians(1.0))
    concave = ChFiDS_TypeOfConcavity.ChFiDS_Concave
    for edge in shape.impl.edges():
        try:
            kinds = {interval.Type() for interval in analysis.Type(edge.wrapped)}
        except Exception:
            continue
        if concave not in kinds:
            continue
        curve = BRepAdaptor_Curve(edge.wrapped)
        try:
            length = float(GCPnts_AbscissaPoint.Length_s(curve))
        except Exception:
            continue
        if not math.isfinite(length) or length <= 1e-6:
            continue
        count = max(3, min(_MAX_SAMPLES, int(math.ceil(length)) + 1))
        first, last = curve.FirstParameter(), curve.LastParameter()
        points = np.array(
            [_xyz(curve.Value(first + (last - first) * k / (count - 1))) for k in range(count)], dtype=np.float64
        )
        middle = _xyz(curve.Value((first + last) / 2.0))
        found.append(
            {
                "kind": "sharp",
                "radius_mm": 0.0,
                "faces": _faces_of_edge(faces, edge),
                "at_mm": middle,
                "length_mm": length,
                "points": points,
            }
        )
    for index, face in enumerate(faces):
        item = _small_inside_radius(face, index, small_radius_mm)
        if item is not None:
            found.append(item)
    return found


def _small_inside_radius(face, index: int, small_radius_mm: float) -> dict | None:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_REVERSED

    surface = BRepAdaptor_Surface(face.wrapped)
    if surface.GetType() != GeomAbs_Cylinder:
        return None
    cylinder = surface.Cylinder()
    radius = float(cylinder.Radius())
    if radius >= small_radius_mm:
        return None
    u0, u1 = surface.FirstUParameter(), surface.LastUParameter()
    v0, v1 = surface.FirstVParameter(), surface.LastVParameter()
    # A whole turn is a hole or a pin, not a rounded corner.
    if u1 - u0 >= 2.0 * math.pi - 1e-3:
        return None
    um, vm = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    props = BRepLProp_SLProps(surface, um, vm, 1, 1e-6)
    if not props.IsNormalDefined():
        return None
    point = props.Value()
    normal = np.array(_xyz(props.Normal()), dtype=np.float64)
    if face.wrapped.Orientation() == TopAbs_REVERSED:
        normal = -normal
    axis = cylinder.Axis()
    origin = np.array(_xyz(axis.Location()), dtype=np.float64)
    direction = np.array(_xyz(axis.Direction()), dtype=np.float64)
    here = np.array(_xyz(point), dtype=np.float64)
    on_axis = origin + direction * float(np.dot(here - origin, direction))
    # An inside radius has its axis on the empty side, where the outward normal points.
    if float(np.dot(normal, on_axis - here)) <= 0.0:
        return None
    length = abs(v1 - v0)
    count = max(3, min(_MAX_SAMPLES, int(math.ceil(length)) + 1))
    points = np.array(
        [_xyz(surface.Value(um, v0 + (v1 - v0) * k / (count - 1))) for k in range(count)], dtype=np.float64
    )
    return {
        "kind": "small_radius",
        "radius_mm": radius,
        "faces": [index],
        "at_mm": _xyz(point),
        "length_mm": float(length),
        "points": points,
    }


def _faces_of_edge(faces: list, edge) -> list[int]:
    out = []
    for index, face in enumerate(faces):
        for candidate in face.edges():
            if candidate.wrapped.IsSame(edge.wrapped):
                out.append(index)
                break
    return out


def _xyz(value) -> tuple[float, float, float]:
    return (float(value.X()), float(value.Y()), float(value.Z()))
