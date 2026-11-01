"""Snap a catalog mate frame onto a face or an axis.

A bearing bore or a motor shaft lines up with a cylindrical face. A motor
flange or another face mate sits flush on a plane. ``rotation_deg`` then turns
the part about that axis, in steps of 15 degrees. ``flip`` turns the mate
around. The stored reference is the target's unplaced face, so a later move of
the target keeps the mount.

The mate axis follows the target axis. On a plane it points into the face, so
a motor body stays on the outside and the shaft points into the face. ``flip``
reverses that direction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from aicad import kernel, library
from aicad.errors import DocumentError, GeometryError, MountError
from aicad.kinematics import matrix_from_placement

SNAP_DEG = 15.0
_MAX_SPIN_DEG = 3600.0
_FACE_DISTANCE_MM = 0.05

# The mate that belongs on a cylinder, and the mate that belongs on a plane.
_AXIS_MATE = {
    "ball_bearing": "bore",
    "tapered_roller_bearing": "bore",
    "radial_shaft_seal": "shaft",
    "socket_head_cap_screw": "axis",
    "hex_nut": "axis",
    "plain_washer": "axis",
    "stepper_motor": "shaft",
    "gearbox": "output",
}
_FACE_MATE = {
    "ball_bearing": "seat",
    "tapered_roller_bearing": "cone_back",
    "socket_head_cap_screw": "head",
    "stepper_motor": "mount",
    "gearbox": "input",
}
_MOUNT_KEYS = {"part", "ref", "mate", "rotation_deg", "flip"}


@dataclass(frozen=True)
class MountPose:
    """World placement that puts the mate frame on the target."""

    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    mount: dict

    def placement(self) -> dict:
        return {
            "position": [float(item) for item in self.position],
            "rotation_deg": [float(item) for item in self.rotation],
            "mount": {
                "part": self.mount["part"],
                "ref": self.mount["ref"],
                "mate": self.mount["mate"],
                "rotation_deg": float(self.mount["rotation_deg"]),
                "flip": bool(self.mount["flip"]),
            },
        }


def snap_rotation(value: float) -> float:
    """Nearest 15 degree step, wrapped to (-180, 180]. Halfway rounds toward +∞."""
    steps = math.floor(float(value) / SNAP_DEG + 0.5)
    snapped = steps * SNAP_DEG
    wrapped = (snapped + 180.0) % 360.0 - 180.0
    if wrapped <= -180.0 + 1e-9:
        return 180.0
    if abs(wrapped) < 1e-9:
        return 0.0
    return wrapped


def prepare_mount(parts: dict, library_spec: dict, mount: dict, moving: str) -> MountPose:
    """Resolve a mount the caller just asked for. A miss leaves the part unmoved."""
    cleaned = clean_mount(mount)
    return _solve(parts, library_spec, cleaned, moving, stored=False, overrides=None)


def reapply_mount(
    parts: dict,
    library_spec: dict,
    mount: dict,
    moving: str,
    overrides: dict | None = None,
) -> MountPose:
    """Place a mount that was already stored. A missing face is an error."""
    cleaned = clean_mount(mount)
    return _solve(parts, library_spec, cleaned, moving, stored=True, overrides=overrides)


def clean_mount(mount: dict) -> dict:
    """Check the mount object and snap its spin. This does not look at a solid."""
    if not isinstance(mount, dict):
        raise MountError("A mount is an object with the target part and a face or an axis.")
    unknown = sorted(set(mount) - _MOUNT_KEYS)
    if unknown:
        raise MountError(f"Unknown mount field {unknown[0]!r}.")
    target = mount.get("part")
    ref = mount.get("ref")
    if not isinstance(target, str) or not target.strip():
        raise MountError("A mount needs the part to snap onto.")
    if not isinstance(ref, str) or not ref.strip():
        raise MountError("A mount needs the face or axis to snap onto.")
    mate = mount.get("mate")
    if mate is not None and (not isinstance(mate, str) or not mate.strip()):
        raise MountError("A mount mate is the name of a frame on the catalog part.")
    spin = mount.get("rotation_deg", 0.0)
    try:
        number = kernel.finite_number(spin, "rotation_deg", bound=_MAX_SPIN_DEG, unit="deg")
    except GeometryError as exc:
        raise MountError(str(exc)) from exc
    flip = mount.get("flip", False)
    if not isinstance(flip, bool):
        raise MountError("flip must be true or false.")
    cleaned = {
        "part": target.strip(),
        "ref": ref.strip(),
        "rotation_deg": snap_rotation(number),
        "flip": flip,
    }
    if isinstance(mate, str):
        cleaned["mate"] = mate.strip()
    return cleaned


def _solve(parts, library_spec, mount: dict, moving: str, *, stored: bool, overrides) -> MountPose:
    if not isinstance(library_spec, dict) or not library_spec.get("kind"):
        raise MountError(f"{moving!r} is not a catalog part, so it has no mount frame.")
    target_name = mount["part"]
    if target_name == moving:
        raise MountError(f"{moving!r} cannot mount on itself.")
    target = parts.get(target_name)
    if target is None:
        known = ", ".join(parts) or "(none)"
        raise MountError(
            f"No part {target_name!r} to mount on. Parts: {known}. {moving!r} was not moved."
        )
    face = _bind_face(target, mount["ref"], moving, stored=stored, overrides=overrides)
    kind = _frame_kind(face, moving, target_name)
    mate_name = mount.get("mate") or _default_mate(str(library_spec.get("kind")), kind)
    frame = _mate_frame(
        str(library_spec.get("kind")),
        str(library_spec.get("size") or ""),
        library_spec.get("length"),
        mate_name,
    )
    local_point, local_axis = _face_axis(face)
    world_point, world_axis = _to_world(_placement(target, overrides), local_point, local_axis)
    position, rotation = frame_pose(
        frame["anchor"],
        frame["axis"],
        world_point,
        world_axis,
        mount["rotation_deg"],
        mount["flip"],
        kind,
    )
    stored_mount = {
        "part": target_name,
        "ref": str(face["ref"]),
        "mate": str(frame["name"]),
        "rotation_deg": float(mount["rotation_deg"]),
        "flip": bool(mount["flip"]),
    }
    return MountPose(position, rotation, stored_mount)


def frame_pose(anchor, mate_axis, target_point, target_axis, rotation_deg: float, flip: bool, kind: str):
    """Euler placement that puts ``anchor`` on the target and aligns the mate axis.

    ``kind`` is ``axis`` or ``face``. On a face the mate axis points into the
    face unless ``flip`` is set. Rotation is degrees about the aligned axis,
    right-handed, and is already snapped.
    """
    source = _unit(mate_axis, "mate axis")
    axis = _unit(target_axis, "target axis")
    if kind == "face":
        desired = _scale(axis, -1.0 if not flip else 1.0)
    else:
        desired = axis if not flip else _scale(axis, -1.0)
    aligned = _align(source, desired)
    spun = _matmat(_rotation(desired, math.radians(float(rotation_deg))), aligned)
    anchor_v = [float(anchor[0]), float(anchor[1]), float(anchor[2])]
    rotated_anchor = _matvec(spun, anchor_v)
    translation = [float(target_point[index]) - rotated_anchor[index] for index in range(3)]
    from aicad.chain import _euler_from

    euler = _euler_from(spun)
    return _tidy(translation), _tidy(euler)


def _bind_face(part, ref: str, moving: str, *, stored: bool, overrides) -> dict:
    base = _base(part, overrides)
    try:
        base_faces = kernel.describe_faces(base)
    except GeometryError as exc:
        raise MountError(str(exc)) from exc
    if stored:
        found = _select(base_faces, ref, moving)
        if found is None:
            raise MountError(
                f"The face {ref!r} on {part.name!r} is gone. {moving!r} was not moved."
            )
        return found
    placed = _placed_shape(part)
    if placed is not None:
        try:
            world_faces = kernel.describe_faces(placed)
        except GeometryError as exc:
            raise MountError(str(exc)) from exc
        found = _select(world_faces, ref, moving)
        if found is not None:
            local = _base_face(part, found, base_faces)
            if local is None:
                raise MountError(
                    f"No face {ref!r} on {part.name!r}. {moving!r} was not moved."
                )
            return local
    found = _select(base_faces, ref, moving)
    if found is None:
        raise MountError(f"No face {ref!r} on {part.name!r}. {moving!r} was not moved.")
    return found


def _select(faces: list[dict], ref: str, moving: str) -> dict | None:
    from aicad.fea import select_faces

    try:
        found = select_faces(faces, {"ref": ref})
    except GeometryError as exc:
        raise MountError(f"{exc} {moving!r} was not moved.") from exc
    if not found:
        return None
    return found[0]


def _base_face(part, world_face: dict, base_faces: list[dict]) -> dict | None:
    placement = part.placement if isinstance(part.placement, dict) else {}
    local_center = _to_local(placement, world_face["center_mm"])
    index = world_face.get("index")
    if isinstance(index, int) and 0 <= index < len(base_faces):
        candidate = base_faces[index]
        if candidate.get("kind") == world_face.get("kind") and _near(candidate["center_mm"], local_center):
            return candidate
    same = [face for face in base_faces if face.get("kind") == world_face.get("kind")]
    if not same:
        return None
    best = min(same, key=lambda face: _distance(face["center_mm"], local_center))
    if _distance(best["center_mm"], local_center) > _FACE_DISTANCE_MM:
        return None
    return best


def _frame_kind(face: dict, moving: str, target: str) -> str:
    if face.get("kind") == "cylinder" and face.get("axis_direction") and face.get("axis_origin_mm"):
        return "axis"
    if face.get("kind") == "plane" and face.get("normal"):
        return "face"
    ref = face.get("ref", "")
    raise MountError(
        f"{ref!r} on {target!r} is not a face or an axis to mount on. {moving!r} was not moved."
    )


def _default_mate(kind: str, target_kind: str) -> str:
    try:
        canonical = library._canonical_kind(kind)  # noqa: SLF001
    except GeometryError as exc:
        raise MountError(str(exc)) from exc
    table = _FACE_MATE if target_kind == "face" else _AXIS_MATE
    name = table.get(canonical)
    if name is None:
        label = canonical.replace("_", " ")
        if target_kind == "face":
            raise MountError(f"A {label} mounts on an axis, not a flat face.")
        raise MountError(f"A {label} has no axis mate to mount.")
    return name


def _mate_frame(kind: str, size: str, length, name: str) -> dict:
    try:
        frames = library.mates(kind, size, length)
    except GeometryError as exc:
        raise MountError(str(exc)) from exc
    wanted = name.strip().lower()
    for frame in frames:
        if str(frame["name"]).lower() == wanted:
            return frame
    known = ", ".join(str(frame["name"]) for frame in frames) or "(none)"
    raise MountError(f"No mate {name!r} on this catalog part. Mates: {known}.")


def _face_axis(face: dict) -> tuple[list[float], list[float]]:
    if face["kind"] == "cylinder":
        origin = face["axis_origin_mm"]
        direction = _unit(face["axis_direction"], "cylinder axis")
        point = _project(face["center_mm"], origin, direction)
        return point, direction
    return [float(item) for item in face["center_mm"]], _unit(face["normal"], "face normal")


def _to_world(placement: dict, point, direction) -> tuple[list[float], list[float]]:
    matrix = matrix_from_placement(placement or {})
    world_point = [
        matrix[0][0] * point[0] + matrix[0][1] * point[1] + matrix[0][2] * point[2] + matrix[0][3],
        matrix[1][0] * point[0] + matrix[1][1] * point[1] + matrix[1][2] * point[2] + matrix[1][3],
        matrix[2][0] * point[0] + matrix[2][1] * point[1] + matrix[2][2] * point[2] + matrix[2][3],
    ]
    world_axis = [
        matrix[0][0] * direction[0] + matrix[0][1] * direction[1] + matrix[0][2] * direction[2],
        matrix[1][0] * direction[0] + matrix[1][1] * direction[1] + matrix[1][2] * direction[2],
        matrix[2][0] * direction[0] + matrix[2][1] * direction[1] + matrix[2][2] * direction[2],
    ]
    return world_point, _unit(world_axis, "mounted axis")


def _to_local(placement: dict, point) -> list[float]:
    matrix = matrix_from_placement(placement or {})
    delta = [float(point[index]) - matrix[index][3] for index in range(3)]
    return [
        matrix[0][0] * delta[0] + matrix[1][0] * delta[1] + matrix[2][0] * delta[2],
        matrix[0][1] * delta[0] + matrix[1][1] * delta[1] + matrix[2][1] * delta[2],
        matrix[0][2] * delta[0] + matrix[1][2] * delta[1] + matrix[2][2] * delta[2],
    ]


def _placement(part, overrides) -> dict:
    if overrides and part.name in overrides and overrides[part.name].get("placement") is not None:
        return overrides[part.name]["placement"]
    placement = part.placement if isinstance(getattr(part, "placement", None), dict) else {}
    return placement


def _base(part, overrides):
    if overrides and part.name in overrides and overrides[part.name].get("base") is not None:
        return overrides[part.name]["base"]
    return part.base


def _placed_shape(part):
    try:
        return part.shape
    except DocumentError:
        return None


def _project(point, origin, direction) -> list[float]:
    delta = [float(point[index]) - float(origin[index]) for index in range(3)]
    scale = sum(delta[index] * direction[index] for index in range(3))
    return [float(origin[index]) + scale * direction[index] for index in range(3)]


def _align(source, target) -> list[list[float]]:
    cosine = max(-1.0, min(1.0, _dot(source, target)))
    if cosine > 1.0 - 1e-10:
        return _eye()
    if cosine < -1.0 + 1e-10:
        return _rotation(_orthogonal(source), math.pi)
    axis = _unit(_cross(source, target), "rotation axis")
    return _rotation(axis, math.acos(cosine))


def _rotation(axis, angle: float) -> list[list[float]]:
    x, y, z = axis
    cosine = math.cos(angle)
    sine = math.sin(angle)
    complement = 1.0 - cosine
    return [
        [complement * x * x + cosine, complement * x * y - sine * z, complement * x * z + sine * y],
        [complement * x * y + sine * z, complement * y * y + cosine, complement * y * z - sine * x],
        [complement * x * z - sine * y, complement * y * z + sine * x, complement * z * z + cosine],
    ]


def _orthogonal(axis) -> list[float]:
    hint = [1.0, 0.0, 0.0] if abs(axis[0]) < 0.9 else [0.0, 1.0, 0.0]
    return _unit(_cross(axis, hint), "flip axis")


def _eye():
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _matvec(matrix, vector):
    return [
        sum(matrix[row][col] * vector[col] for col in range(3))
        for row in range(3)
    ]


def _matmat(left, right):
    return [
        [sum(left[row][k] * right[k][col] for k in range(3)) for col in range(3)]
        for row in range(3)
    ]


def _cross(left, right):
    return [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]


def _dot(left, right) -> float:
    return sum(float(left[index]) * float(right[index]) for index in range(3))


def _scale(vector, factor: float) -> list[float]:
    return [float(vector[index]) * factor for index in range(3)]


def _unit(vector, label: str) -> list[float]:
    length = math.sqrt(sum(float(component) * float(component) for component in vector))
    if length < 1e-12:
        raise MountError(f"The {label} has no direction.")
    return [float(component) / length for component in vector]


def _distance(left, right) -> float:
    return math.sqrt(sum((float(left[index]) - float(right[index])) ** 2 for index in range(3)))


def _near(left, right) -> bool:
    return _distance(left, right) <= _FACE_DISTANCE_MM


def _tidy(values) -> tuple[float, float, float]:
    cleaned = []
    for value in values:
        number = float(value)
        if abs(number) < 1e-9:
            number = 0.0
        cleaned.append(number)
    return cleaned[0], cleaned[1], cleaned[2]
