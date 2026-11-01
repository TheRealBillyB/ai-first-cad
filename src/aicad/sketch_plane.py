"""Where a sketch sits in the model.

A plane is the XY, YZ, or XZ datum, that datum shifted or tilted, or a
planar face of a solid. Solving stays in the plane's own coordinates. The
solid is placed on the plane when it is built. A face reference stored on
the part moves and turns with that part. A face that is no longer in the
model raises :class:`aicad.errors.SketchPlaneError`.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from aicad.errors import GeometryError, SketchPlaneError

_BASES = ("xy", "yz", "xz")
_AXES = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}


def normalize_plane(plane: Any) -> str | dict:
    """Return the stored plane. A bad form is a geometry error, not a missing face."""
    if plane is None:
        return "xy"
    if isinstance(plane, str):
        name = plane.strip().lower()
        if name not in _BASES:
            raise GeometryError(
                f"A sketch plane is xy, yz, xz, a datum, or a planar face, not {plane!r}."
            )
        return name
    if not isinstance(plane, dict):
        raise GeometryError("A sketch plane is xy, yz, xz, a datum, or a planar face.")
    kind = plane.get("kind")
    if kind == "datum":
        base = str(plane.get("base", "xy")).strip().lower()
        if base not in _BASES:
            raise GeometryError("A datum plane starts from xy, yz, or xz.")
        stored: dict[str, Any] = {"kind": "datum", "base": base}
        if "offset" in plane and plane.get("offset") is not None:
            stored["offset"] = _finite(plane.get("offset"), "datum offset")
        if "angle" in plane and plane.get("angle") is not None:
            stored["angle"] = _finite(plane.get("angle"), "datum angle")
            axis = str(plane.get("axis", "x")).strip().lower()
            if axis not in _AXES:
                raise GeometryError("A datum angle is about x, y, or z.")
            stored["axis"] = axis
        if "offset" not in stored and "angle" not in stored:
            return base
        return stored
    if kind == "face":
        part = plane.get("part")
        ref = plane.get("ref")
        if not isinstance(part, str) or not part.strip():
            raise GeometryError("A sketch on a face needs the part name.")
        if not isinstance(ref, str) or ref.count("/") < 2:
            raise GeometryError(
                "A sketch on a face needs the face reference, such as plane/z+/0,0,10."
            )
        stored = {"kind": "face", "part": part.strip(), "ref": ref}
        if plane.get("space") == "part":
            stored["space"] = "part"
        return stored
    raise GeometryError("A sketch plane is xy, yz, xz, a datum, or a planar face.")


def plane_hosts(plane: Any) -> list[str]:
    """Part names a face plane is attached to."""
    try:
        stored = normalize_plane(plane)
    except GeometryError:
        return []
    if isinstance(stored, dict) and stored.get("kind") == "face":
        return [stored["part"]]
    return []


def sketch_frame(
    plane: Any,
    sketch_name: str,
    shape_of: Callable[[str], Any] | None = None,
    sketch_of: Callable[[str], str | None] | None = None,
    *,
    base_of: Callable[[str], Any] | None = None,
    placement_of: Callable[[str], dict | None] | None = None,
) -> dict[str, list[float]]:
    """The plane's origin, x axis, and normal in world millimeters.

    ``shape_of`` returns a part's placed solid, or None when that part is
    missing. ``sketch_of`` returns the sketch that built a part, so a sketch
    cannot sit on the solid it builds. A face stored in part coordinates uses
    ``base_of`` for the unplaced solid and ``placement_of`` for the move.
    """
    stored = normalize_plane(plane)
    if isinstance(stored, str):
        origin, x_axis, _y_axis, normal = _principal(stored)
        return _payload(origin, x_axis, normal)
    if stored["kind"] == "datum":
        origin, x_axis, _y_axis, normal = _principal(stored["base"])
        if "angle" in stored:
            axis = _AXES[stored["axis"]]
            x_axis = _rotate(x_axis, axis, stored["angle"])
            normal = _rotate(normal, axis, stored["angle"])
            x_axis = _unit(x_axis)
            normal = _unit(normal)
        if "offset" in stored:
            distance = stored["offset"]
            origin = tuple(origin[index] + normal[index] * distance for index in range(3))
        return _payload(origin, x_axis, normal)
    return _face_frame(stored, sketch_name, shape_of, sketch_of, base_of, placement_of)


def _face_frame(
    stored: dict,
    sketch_name: str,
    shape_of,
    sketch_of,
    base_of,
    placement_of,
) -> dict[str, list[float]]:
    part_name = stored["part"]
    ref = stored["ref"]
    if sketch_of is not None and sketch_of(part_name) == sketch_name:
        raise SketchPlaneError(
            f"Sketch {sketch_name!r} cannot use a face of {part_name!r}, the solid that sketch builds."
        )
    on_part = stored.get("space") == "part" and base_of is not None
    if on_part:
        shape = base_of(part_name)
    else:
        shape = None if shape_of is None else shape_of(part_name)
    if shape is None:
        raise SketchPlaneError(
            f"Sketch {sketch_name!r} is attached to {part_name!r}, which is not in this model."
        )
    from aicad.fea import select_faces
    from aicad.kernel import describe_faces
    from aicad.refs import ref_to_selector

    try:
        selector = ref_to_selector(ref)
    except GeometryError as exc:
        raise SketchPlaneError(
            f"Sketch {sketch_name!r} is attached to a planar face of {part_name!r} that is no longer there."
        ) from exc
    if selector.get("kind") != "plane":
        raise SketchPlaneError(
            f"Sketch {sketch_name!r} needs a planar face. {part_name!r} {ref!r} is not a plane."
        )
    try:
        matches = select_faces(describe_faces(shape), selector)
    except GeometryError as exc:
        raise SketchPlaneError(
            f"Sketch {sketch_name!r} is attached to a planar face of {part_name!r} that is no longer there."
        ) from exc
    face = matches[0] if matches else None
    if face is None or face.get("kind") != "plane" or not face.get("normal"):
        raise SketchPlaneError(
            f"Sketch {sketch_name!r} is attached to a planar face of {part_name!r} that is no longer there."
        )
    normal = _unit(tuple(float(component) for component in face["normal"]))
    center = tuple(float(component) for component in face["center_mm"])
    anchor = tuple(float(component) for component in selector["at"])
    # A face stored on the part is attached to that face's center, so an edit
    # that moves the face carries the sketch. The stored point only picks the face.
    origin = center if on_part else _project(anchor, center, normal)
    x_axis, _y_axis = _axes(normal)
    frame = _payload(origin, x_axis, normal)
    if on_part and placement_of is not None:
        frame = _apply_placement(frame, placement_of(part_name))
    return frame


def _apply_placement(frame: dict[str, list[float]], placement: dict | None) -> dict[str, list[float]]:
    """Carry a part-local sketch frame into the part's placed position."""
    if not isinstance(placement, dict):
        return frame
    position = placement.get("position") or [0.0, 0.0, 0.0]
    rotation = placement.get("rotation_deg") or [0.0, 0.0, 0.0]
    try:
        still = all(abs(float(value)) < 1e-12 for value in (*position, *rotation))
    except (TypeError, ValueError):
        still = False
    if still:
        return frame
    from aicad.kinematics import matrix_from_placement

    matrix = matrix_from_placement(placement)
    return _payload(
        _mul_point(matrix, frame["origin"]),
        _mul_dir(matrix, frame["x"]),
        _mul_dir(matrix, frame["normal"]),
    )


def _mul_point(matrix, point) -> tuple[float, float, float]:
    return (
        matrix[0][0] * point[0] + matrix[0][1] * point[1] + matrix[0][2] * point[2] + matrix[0][3],
        matrix[1][0] * point[0] + matrix[1][1] * point[1] + matrix[1][2] * point[2] + matrix[1][3],
        matrix[2][0] * point[0] + matrix[2][1] * point[1] + matrix[2][2] * point[2] + matrix[2][3],
    )


def _mul_dir(matrix, direction) -> tuple[float, float, float]:
    return (
        matrix[0][0] * direction[0] + matrix[0][1] * direction[1] + matrix[0][2] * direction[2],
        matrix[1][0] * direction[0] + matrix[1][1] * direction[1] + matrix[1][2] * direction[2],
        matrix[2][0] * direction[0] + matrix[2][1] * direction[1] + matrix[2][2] * direction[2],
    )


def _principal(name: str):
    if name == "yz":
        return (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
    if name == "xz":
        return (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)
    return (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)


def _axes(normal: tuple[float, float, float]):
    reference = (1.0, 0.0, 0.0)
    projected = _reject(reference, normal)
    if _length(projected) < 1e-8:
        projected = _reject((0.0, 1.0, 0.0), normal)
    x_axis = _unit(projected)
    y_axis = _unit(_cross(normal, x_axis))
    return x_axis, y_axis


def _project(point, plane_point, normal):
    distance = sum((point[index] - plane_point[index]) * normal[index] for index in range(3))
    return tuple(point[index] - distance * normal[index] for index in range(3))


def _reject(vector, normal):
    dot = sum(vector[index] * normal[index] for index in range(3))
    return tuple(vector[index] - dot * normal[index] for index in range(3))


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _rotate(vector, axis, degrees: float):
    theta = math.radians(degrees)
    cosine, sine = math.cos(theta), math.sin(theta)
    cross = _cross(axis, vector)
    dot = sum(axis[index] * vector[index] for index in range(3))
    return tuple(
        vector[index] * cosine + cross[index] * sine + axis[index] * dot * (1.0 - cosine)
        for index in range(3)
    )


def _length(vector) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _unit(vector):
    length = _length(vector)
    if length < 1e-12:
        raise GeometryError("A sketch plane needs a direction.")
    return tuple(component / length for component in vector)


def _payload(origin, x_axis, normal) -> dict[str, list[float]]:
    x_axis = _unit(x_axis)
    normal = _unit(_reject(normal, x_axis))
    return {
        "origin": [origin[0], origin[1], origin[2]],
        "x": [x_axis[0], x_axis[1], x_axis[2]],
        "normal": [normal[0], normal[1], normal[2]],
    }


def _finite(value, label: str) -> float:
    from aicad.kernel import finite_number

    return float(finite_number(value, label))
