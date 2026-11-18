"""Insertion paths: can each part go in, along a straight line, past the parts already there?

A final pose can be clear while the part has no way in: a jaw whose rails are
blocked at the open end, a pin whose hole is behind a cheek. This check moves
each part from outside the assembly to its seated pose in small steps, with
the parts already in place held still, and reports the first collision.

An assembly states the plan in ``insertion``: the parts in the order they go
in, and the way each one moves as it goes in. A part that is not listed is in
place from the start. A part hung from a listed part by a joint, and not
listed itself, goes in with it (a pad on a jaw). A listed part with no stated
direction slides along its prismatic joint's axis, trying both senses. A part
with ``"check": false`` goes in at its turn without a straight path being
checked: a screw threaded in, a part snapped or turned into place.
"""

from __future__ import annotations

import math

from aicad import kernel
from aicad.assembly import posed_shapes
from aicad.errors import GeometryError
from aicad.honesty import VERIFIED_CHECK, finish

# The step between checked positions, unless the plan or the call names one.
STEP_MM = 1.0
# How far outside everything already in place a part starts.
_MARGIN_MM = 1.0
# A first contact is narrowed down to this.
_RESOLUTION_MM = 0.05
# Overlap below this is the boolean's own noise.
_VOLUME_MM3 = 1e-3
# A path longer than this is a mistake, not an assembly.
_MAX_TRAVEL_MM = 10_000.0
_MAX_POSITIONS = 5_000


def normalize_insertion(raw, instance_names) -> list[dict]:
    """The stored plan, in order: ``{"instance", "direction"?, "distance_mm"?, "step_mm"?, "check"?}``."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise GeometryError("insertion is a list of parts in the order they go in.")
    names = set(instance_names)
    seen: set[str] = set()
    plan = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            item = {"instance": item}
        if not isinstance(item, dict):
            raise GeometryError(f"insertion entry {index} is an object with an instance.")
        name = item.get("instance")
        if not isinstance(name, str) or name not in names:
            raise GeometryError(f"insertion entry {index} names {name!r}, which is not an instance of this assembly.")
        if name in seen:
            raise GeometryError(f"{name!r} is listed twice in insertion.")
        seen.add(name)
        entry: dict = {"instance": name}
        if item.get("direction") is not None:
            entry["direction"] = list(_unit(_vector(item["direction"], f"{name} direction"), name))
        for key in ("distance_mm", "step_mm"):
            if item.get(key) is not None:
                value = _positive(item[key], f"{name} {key}")
                if key == "distance_mm" and value > _MAX_TRAVEL_MM:
                    raise GeometryError(f"{name} distance_mm is over {_MAX_TRAVEL_MM:g} mm.")
                entry[key] = value
        if item.get("check") is not None:
            if not isinstance(item["check"], bool):
                raise GeometryError(f"{name} check is true or false.")
            if not item["check"]:
                entry["check"] = False
        plan.append(entry)
    return plan


def default_plan(spec: dict) -> list[dict]:
    """Without a stated plan: every part on a prismatic joint, in joint order, along that joint."""
    return [{"instance": joint["child"]} for joint in spec.get("joints") or [] if joint.get("type") == "prismatic"]


def check_insertion(
    spec: dict,
    shapes: dict[str, kernel.Shape],
    plan: list | None = None,
    pose: dict | None = None,
    step_mm: float | None = None,
) -> dict:
    """Move each part in along its path, in order, and report the first collision of each."""
    names = [instance["name"] for instance in spec["instances"]]
    stated = plan if plan is not None else spec.get("insertion")
    source = "call" if plan is not None else ("assembly" if stated else "prismatic joints")
    steps = normalize_insertion(stated, names) if stated else default_plan(spec)
    if not steps:
        raise GeometryError(
            "There is nothing to insert. State the order in the assembly's insertion list, or pass one: "
            '[{"instance": "jaw", "direction": [-1, 0, 0]}, ...]. With neither, the parts on prismatic joints are checked.'
        )
    default_step = STEP_MM if step_mm is None else _positive(step_mm, "step_mm")
    solved, placed = posed_shapes(spec, shapes, pose)
    joints = solved["joints"]
    children: dict[str, list[str]] = {}
    slide_axis: dict[str, list[float]] = {}
    for joint in joints:
        children.setdefault(joint["parent"], []).append(joint["child"])
        if joint["type"] == "prismatic":
            slide_axis[joint["child"]] = _world_axis(solved, joint)
    listed = [step["instance"] for step in steps]
    groups = {name: _group(name, children, set(listed)) for name in listed}
    moving = {member for group in groups.values() for member in group}
    present = [name for name in names if name not in moving]
    at_start = list(present)
    results = []
    for step in steps:
        name = step["instance"]
        group = groups[name]
        if step.get("check") is False:
            results.append(
                {
                    "instance": name,
                    "moves_with": [member for member in group if member != name],
                    "status": "not_checked",
                    "note": "Goes in at this point without a straight path being checked (check is false).",
                }
            )
            present.extend(group)
            continue
        if "direction" in step:
            directions = [tuple(step["direction"])]
            how = "stated"
        elif name in slide_axis:
            axis = slide_axis[name]
            directions = [tuple(-value for value in axis), tuple(axis)]
            how = "prismatic joint axis, both senses"
        else:
            raise GeometryError(
                f"{name!r} has no stated direction and no prismatic joint. "
                'Give it one in insertion, such as {"instance": "%s", "direction": [0, 0, -1]}.' % name
            )
        step_size = step.get("step_mm", default_step)
        tries = [
            _sweep(group, present, placed, direction, step.get("distance_mm"), step_size) for direction in directions
        ]
        chosen = next((item for item in tries if item["status"] == "clear"), tries[0])
        result = {
            "instance": name,
            "moves_with": [member for member in group if member != name],
            "direction_from": how,
            **chosen,
        }
        if len(tries) > 1:
            result["other_sense"] = {key: tries[1 if chosen is tries[0] else 0][key] for key in ("direction", "status")}
        results.append(result)
        present.extend(group)
    blocked = [item for item in results if item["status"] == "blocked"]
    status = "blocked" if blocked else "clear"
    if blocked:
        first = blocked[0]["first_collision"]
        who = "it" if first["moving"] == blocked[0]["instance"] else f"{first['moving']}, which goes in with it,"
        note = (
            f"{blocked[0]['instance']} cannot go in: {who} hits {first['other']} "
            f"{first['distance_from_seated_mm']:.2f} mm before it is seated, at {_where(first['at_mm'])}."
        )
        if len(blocked) > 1:
            note += f" {len(blocked) - 1} more part{'s' if len(blocked) > 2 else ''} cannot go in either."
    else:
        note = f"Every part goes in along its path: {', '.join(listed)}."
    payload = {
        "assembly": spec["name"],
        "status": status,
        "plan_from": source,
        "order": listed,
        "in_place_at_start": at_start,
        "pose": dict(pose or {}),
        "results": results,
        "note": note,
    }
    return finish(
        payload,
        kind=VERIFIED_CHECK,
        name="Insertion",
        what=(
            "Each part moves in a straight line from outside the parts already in place to its seated pose, in "
            "steps, and the modeled solids are intersected at each step. A first contact is narrowed to "
            f"{_RESOLUTION_MM:g} mm."
        ),
        limits=[
            "Only straight paths are checked: no turning, tilting, or bending a part to get it in.",
            "Only the sampled positions are checked. A wall thinner than the step can be passed over; "
            "lower step_mm for thin features.",
            "Parts that already overlap when seated (a thread, a press fit, a snap) are listed in seated_overlaps "
            "and not counted on the way in.",
            "A part not in the plan is in place from the start; a part hung from a planned part goes in with it.",
        ],
    )


def _group(name: str, children: dict[str, list[str]], listed: set[str]) -> list[str]:
    """``name`` and the parts hung from it that are not planned on their own."""
    group = [name]
    queue = list(children.get(name, []))
    while queue:
        child = queue.pop(0)
        if child in listed:
            continue
        group.append(child)
        queue.extend(children.get(child, []))
    return group


def _world_axis(solved: dict, joint: dict) -> list[float]:
    """A prismatic joint's axis in the assembly frame: its parent's rotation applied to it."""
    frames = {instance["name"]: instance["matrix"] for instance in solved["instances"]}
    matrix = frames.get(joint["parent"])
    axis = joint["axis"]
    if matrix is None:
        return list(axis)
    turned = [sum(float(matrix[row][col]) * float(axis[col]) for col in range(3)) for row in range(3)]
    return list(_unit(turned, joint["name"]))


def _sweep(group, present, placed, direction, distance, step) -> dict:
    """Move ``group`` along ``direction`` to its seated pose, past ``present``."""
    seated = []
    skip = set()
    for mover in group:
        for other in present:
            if kernel.aabb_separated(placed[mover], placed[other]):
                continue
            volume = kernel.overlap_volume(placed[mover], placed[other])
            if volume > _VOLUME_MM3:
                seated.append({"moving": mover, "other": other, "interference_volume_mm3": round(volume, 4)})
                skip.add((mover, other))
    if distance is None:
        distance = _travel(group, present, placed, direction)
    positions = _positions(distance, step)
    base = {
        "direction": [round(value, 6) for value in direction],
        "distance_mm": round(distance, 4),
        "step_mm": step,
        "positions_checked": 0,
        "seated_overlaps": seated,
    }
    boxes = {name: kernel.aabb_bounds(placed[name]) for name in (*group, *present)}
    checked = 0
    clear_at = None
    for back in positions:
        checked += 1
        hits = _hits(group, present, placed, boxes, direction, back, skip)
        if hits:
            if clear_at is not None:
                back, hits, extra = _narrow(group, present, placed, boxes, direction, clear_at, back, hits, skip)
                checked += extra
            first = max(hits, key=lambda hit: hit["interference_volume_mm3"])
            return {
                **base,
                "positions_checked": checked,
                "status": "blocked",
                "first_collision": {
                    **first,
                    "distance_from_seated_mm": round(back, 3),
                    "travelled_mm": round(distance - back, 3),
                    "starts_blocked": clear_at is None,
                },
                "collisions_at_first_contact": hits,
            }
        clear_at = back
    return {**base, "positions_checked": checked, "status": "clear"}


def _travel(group, present, placed, direction) -> float:
    """How far back along ``direction`` the group starts, clear of everything in place."""
    if not present:
        return 0.0
    ahead = max(_extent(placed[name], direction)[1] for name in group)
    behind = min(_extent(placed[name], direction)[0] for name in present)
    travel = max(0.0, ahead - behind) + _MARGIN_MM
    if travel > _MAX_TRAVEL_MM:
        raise GeometryError(f"The path would be {travel:.0f} mm long. Give distance_mm for this part.")
    return travel


def _extent(shape, direction) -> tuple[float, float]:
    low, high = kernel.aabb_bounds(shape)
    corners = [
        (x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])
    ]
    values = [sum(corner[axis] * direction[axis] for axis in range(3)) for corner in corners]
    return min(values), max(values)


def _positions(distance: float, step: float) -> list[float]:
    """Distances back from the seated pose, from the start down to zero."""
    count = int(math.ceil(distance / step - 1e-9))
    if count > _MAX_POSITIONS:
        raise GeometryError(
            f"{count} positions at a {step:g} mm step is too many. Give a larger step_mm or a shorter distance_mm."
        )
    values = [distance - index * step for index in range(count)]
    values.append(0.0)
    return values


def _hits(group, present, placed, boxes, direction, back, skip) -> list[dict]:
    shift = [-back * value for value in direction]
    hits = []
    for mover in group:
        low, high = boxes[mover]
        moved_box = (
            tuple(low[axis] + shift[axis] for axis in range(3)),
            tuple(high[axis] + shift[axis] for axis in range(3)),
        )
        moved = None
        for other in present:
            if (mover, other) in skip or _apart(moved_box, boxes[other]):
                continue
            if moved is None:
                moved = kernel.transform_shape(
                    placed[mover],
                    [[1, 0, 0, shift[0]], [0, 1, 0, shift[1]], [0, 0, 1, shift[2]], [0, 0, 0, 1]],
                )
            volume, center = kernel.overlap_center(moved, placed[other])
            if volume > _VOLUME_MM3:
                hits.append(
                    {
                        "moving": mover,
                        "other": other,
                        "interference_volume_mm3": round(volume, 4),
                        "at_mm": None if center is None else [round(center.x, 3), round(center.y, 3), round(center.z, 3)],
                    }
                )
    return hits


def _narrow(group, present, placed, boxes, direction, clear_back, hit_back, hits, skip):
    """Halve the gap between the last clear position and the first blocked one."""
    extra = 0
    while clear_back - hit_back > _RESOLUTION_MM:
        middle = (clear_back + hit_back) / 2.0
        found = _hits(group, present, placed, boxes, direction, middle, skip)
        extra += 1
        if found:
            hit_back, hits = middle, found
        else:
            clear_back = middle
    return hit_back, hits, extra


def _apart(first, second) -> bool:
    return any(first[1][axis] < second[0][axis] or second[1][axis] < first[0][axis] for axis in range(3))


def _where(point) -> str:
    if point is None:
        return "an unknown point"
    return "(" + ", ".join(f"{float(value):.1f}" for value in point) + ")"


def _vector(value, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3 or any(isinstance(item, bool) for item in value):
        raise GeometryError(f"{label} must be three numbers.")
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be three numbers.") from exc
    if not all(math.isfinite(item) for item in result):
        raise GeometryError(f"{label} must be three finite numbers.")
    return result  # type: ignore[return-value]


def _unit(vector, label: str) -> tuple[float, float, float]:
    length = math.sqrt(sum(float(value) ** 2 for value in vector))
    if length < 1e-12:
        raise GeometryError(f"{label}: a direction must not be zero.")
    return tuple(float(value) / length for value in vector)  # type: ignore[return-value]


def _positive(value, label: str) -> float:
    if isinstance(value, bool):
        raise GeometryError(f"{label} must be a positive number of millimetres.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a positive number of millimetres.") from exc
    if not math.isfinite(number) or number <= 0:
        raise GeometryError(f"{label} must be a positive number of millimetres.")
    return number
