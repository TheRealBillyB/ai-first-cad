"""Pull an assembly apart for a picture.

The grounded part stays where it is. Each child moves away from its parent
along the joint that holds it, by one gap, and that shift adds up along the
tree. A slide moves along its axis. A hinge slides off its pin. A catalog
fastener lifts out along its axis. Nothing here is a disassembly order, and
the gap is not a dimension.
"""

from __future__ import annotations

import copy
import math

from aicad import kernel, library
from aicad.errors import GeometryError
from aicad.kinematics import transform_point

_FASTENERS = {"socket_head_cap_screw", "hex_nut", "plain_washer"}

OMITS = [
    "a step-by-step assembly order",
    "tools, torque, or threadlocker",
    "cable and hose routes, which are not solids",
    "the real clearance a person needs to get a wrench in",
]
ASSUMPTIONS = [
    "The grounded part, the one with no parent joint, stays where the pose put it.",
    "Each other part moves away from its parent by the same gap, and the shift adds up along the joint tree.",
    "A prismatic or cylindrical joint moves along its axis.",
    "A revolute joint moves from the pin toward the part, square to the hinge axis.",
    "A catalog screw, nut, or washer lifts out along the joint axis.",
    "Any other fixed part moves from the parent's center toward its own center.",
    "The direction is flipped when that would push the part back into its parent.",
]


def plan(
    spec: dict,
    solved: dict,
    shapes: dict[str, kernel.Shape],
    libraries: dict[str, dict | None],
    gap_mm: float | None = None,
) -> dict:
    """Decide where each instance goes. The assembly spec is not changed."""
    joints = {joint["child"]: joint for joint in solved["joints"]}
    children: dict[str, list[str]] = {}
    for joint in solved["joints"]:
        children.setdefault(joint["parent"], []).append(joint["child"])
    names = [instance["name"] for instance in solved["instances"]]
    roots = [name for name in names if name not in joints]
    if not roots:
        raise GeometryError(f"Assembly {spec.get('name')!r} has no grounded part to explode from.")

    depth: dict[str, int] = {}
    order: list[str] = []
    queue = [(root, 0) for root in roots]
    while queue:
        name, level = queue.pop(0)
        if name in depth:
            continue
        depth[name] = level
        order.append(name)
        for child in children.get(name, []):
            queue.append((child, level + 1))
    for name in names:
        depth.setdefault(name, 0)
        if name not in order:
            order.append(name)

    centers = _centers(solved, shapes)
    span = _span(solved, shapes)
    chosen = gap_mm is None
    gap, capped = _gap(gap_mm, span)
    by_name = {instance["name"]: instance for instance in solved["instances"]}
    offsets: dict[str, list[float]] = {name: [0.0, 0.0, 0.0] for name in names}
    moves = []
    for name in order:
        instance = by_name[name]
        if name not in joints:
            moves.append(_move(instance, 0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "This part is grounded, so it stays put."))
            continue
        joint = joints[name]
        parent = joint["parent"]
        direction, reason = _direction(joint, centers, libraries.get(instance["part"]))
        parent_offset = offsets[parent]
        offsets[name] = [parent_offset[axis] + direction[axis] * gap for axis in range(3)]
        moves.append(_move(instance, depth[name], offsets[name], direction, reason))

    return {
        "assembly": spec.get("name"),
        "gap_mm": gap,
        "gap_chosen": chosen,
        "gap_capped": capped,
        "span_mm": round(span, 3),
        "moves": moves,
        "offsets": offsets,
        "assumptions": list(ASSUMPTIONS),
        "omits": list(OMITS),
        "note": (
            f"Parts are pulled apart by {gap:g} mm at each joint"
            + (" (chosen from the assembly size)." if chosen else " (the gap you set).")
            + (" The automatic gap is capped at 1000000 mm." if capped else "")
            + " This picture is not a disassembly procedure, and the gaps are not dimensions."
        ),
    }


def apply_moves(solved: dict, offsets: dict[str, list[float]]) -> dict:
    """Copy a solved pose and add each instance's explode offset."""
    moved = copy.deepcopy(solved)
    for instance in moved["instances"]:
        delta = offsets.get(instance["name"]) or [0.0, 0.0, 0.0]
        matrix = instance["matrix"]
        for axis in range(3):
            matrix[axis][3] = float(matrix[axis][3]) + float(delta[axis])
        instance["origin_mm"] = [round(matrix[axis][3], 4) for axis in range(3)]
    return moved


def _move(instance: dict, depth: int, offset: list[float], direction: list[float], reason: str) -> dict:
    return {
        "instance": instance["name"],
        "part": instance["part"],
        "depth": depth,
        "offset_mm": [round(component, 3) for component in offset],
        "direction": [round(component, 4) for component in direction],
        "reason": reason,
    }


def _gap(gap_mm: float | None, span: float) -> tuple[float, bool]:
    """Return the gap and whether the automatic gap was capped."""
    if gap_mm is None:
        gap = max(20.0, round(0.18 * span, 1))
        if gap > 1_000_000:
            return 1_000_000.0, True
        return gap, False
    try:
        gap = float(gap_mm)
    except (TypeError, ValueError) as exc:
        raise GeometryError("gap_mm must be a positive number of millimeters.") from exc
    if not math.isfinite(gap) or gap <= 0:
        raise GeometryError("gap_mm must be a positive number of millimeters.")
    if gap > 1_000_000:
        raise GeometryError("gap_mm is larger than 1000000 mm. Pass a smaller gap.")
    return gap, False


def _centers(solved: dict, shapes: dict[str, kernel.Shape]) -> dict[str, list[float]]:
    cache: dict[str, list[float]] = {}
    centers = {}
    for instance in solved["instances"]:
        part = instance["part"]
        if part not in cache:
            measured = kernel.measure(shapes[part])
            cache[part] = [
                measured.center_of_mass_mm.x,
                measured.center_of_mass_mm.y,
                measured.center_of_mass_mm.z,
            ]
        centers[instance["name"]] = transform_point(instance["matrix"], cache[part])
    return centers


def _span(solved: dict, shapes: dict[str, kernel.Shape]) -> float:
    lows = [math.inf, math.inf, math.inf]
    highs = [-math.inf, -math.inf, -math.inf]
    for instance in solved["instances"]:
        box = shapes[instance["part"]].impl.bounding_box()
        corners = [
            (x, y, z)
            for x in (float(box.min.X), float(box.max.X))
            for y in (float(box.min.Y), float(box.max.Y))
            for z in (float(box.min.Z), float(box.max.Z))
        ]
        for corner in corners:
            world = transform_point(instance["matrix"], list(corner))
            for axis in range(3):
                lows[axis] = min(lows[axis], world[axis])
                highs[axis] = max(highs[axis], world[axis])
    if not math.isfinite(lows[0]):
        return 1.0
    return max(highs[axis] - lows[axis] for axis in range(3))


def _direction(joint: dict, centers: dict[str, list[float]], catalog: dict | None) -> tuple[list[float], str]:
    axis = list(joint["axis"])
    parent = centers[joint["parent"]]
    child = centers[joint["child"]]
    apart = [child[axis_index] - parent[axis_index] for axis_index in range(3)]
    kind = joint["type"]
    library_kind = None
    if catalog and catalog.get("kind"):
        library_kind = library._canonical_kind(str(catalog["kind"]))  # noqa: SLF001
    if kind in ("prismatic", "cylindrical"):
        direction = axis
        reason = "The part slides on this joint, so the picture pulls it along the axis."
    elif kind == "revolute":
        origin = joint["origin_mm"]
        away = [child[axis_index] - origin[axis_index] for axis_index in range(3)]
        along = sum(away[axis_index] * axis[axis_index] for axis_index in range(3))
        radial = [away[axis_index] - along * axis[axis_index] for axis_index in range(3)]
        direction = _unit(radial) or _unit(apart) or axis
        reason = "The part hinges on a pin, so the picture pulls it off the pin."
    elif library_kind in _FASTENERS:
        direction = axis
        reason = "This is a catalog fastener, so the picture lifts it out along its axis."
    else:
        direction = _unit(apart) or [0.0, 0.0, 1.0]
        reason = "The part is fixed to its parent, so the picture moves it straight away from that parent."
    direction = _unit(direction) or [0.0, 0.0, 1.0]
    if sum(direction[axis_index] * apart[axis_index] for axis_index in range(3)) < 0:
        direction = [-component for component in direction]
    return direction, reason


def _unit(vector: list[float]) -> list[float] | None:
    length = math.sqrt(sum(component * component for component in vector))
    if length < 1e-9:
        return None
    return [component / length for component in vector]
