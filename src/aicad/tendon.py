"""Tendon routes over pulleys, guides, and wrap posts.

A tendon is an anchor, a sequence of pulleys, guides, or wrap posts fixed on
parts, and an end anchor. The path is straight between tangent points and
follows the circular arc on each sheave. Length is the sum of those spans and
arcs. It does not sag.

``radius_mm`` is the radius of the cable centerline. ``wrap`` is ``cw`` or
``ccw`` about the sheave axis, right-hand. An omitted wrap uses the shorter
arc. ``actuator_force_n`` is the tension in the span at ``actuator_end``.
Moving away from that end, a pulley with friction ``μ`` multiplies tension by
``exp(-μ φ)``, where ``φ`` is the wrap angle in radians. The capstan ratio of
that pulley is ``exp(μ φ)``.

A path more than 0.01 mm shorter than the rest length is slack and carries no
tension. A longer path is stretch. Rest length is the authored value, or the
length of the first sample. Elastic elongation is the actuator force times
length, divided by Young's modulus times the circular area. It is not added
to the path length.
"""

from __future__ import annotations

import math

from aicad.errors import GeometryError
from aicad.fea import lookup_material
from aicad.kinematics import transform_point
from aicad import kernel

_ROLES = ("anchor", "pulley", "guide", "wrap")
_KINDS = ("cable", "hose", "tendon")
_CABLE_KEYS = {
    "name",
    "kind",
    "diameter_mm",
    "diameter",
    "material",
    "min_bend_radius_mm",
    "min_bend_radius",
    "rest_length_mm",
    "actuator_force_n",
    "actuator_end",
    "max_strain",
    "modulus_mpa",
    "pinch_mm",
    "points",
    "waypoints",
}
_POINT_KEYS = {"role", "instance", "at", "name", "axis", "radius_mm", "wrap", "friction", "turns"}
_SLACK_MM = 0.01
_MAX_FREE_WRAPS = 8
_MAX_POINTS = 32
_MAX_TURNS = 20


def presentation() -> tuple[str, list[str]]:
    return (
        "A tendon is the centerline through anchors, pulley arcs, and guides on the posed parts. "
        "Tension follows the capstan equation from the given actuator force. The line does not sag.",
        [
            "Length is the straight spans plus radius times wrap angle. It is not a sagging cable.",
            "radius_mm is the cable centerline. The bend check compares that radius with min_bend_radius_mm.",
            "An omitted wrap uses the shorter arc. cw and ccw follow the right-hand rule about the axis.",
            "Capstan tension is multiplied by exp(-friction × wrap) across each pulley, moving away from the actuator. A slack route carries no tension.",
            "Elastic elongation uses the library modulus and a circular cross-section. It is not added to the path.",
            "A collision is a centerline sample inside a solid, or closer than the cable radius plus pinch_mm. Samples on a pulley's own arc are the groove and are not counted against that part.",
            "Pulleys whose axes are not parallel use the straight line between the in-plane tangents.",
            "Only the sampled poses are checked. A hit between samples is not seen.",
        ],
    )


def assumptions() -> list[str]:
    return [
        "The route is straight between tangent points and circular on each sheave. It does not sag, and it does not slip along a groove.",
        "radius_mm is the radius of the cable centerline, not the sheave metal. Bend radius for a pulley or a wrap is that number.",
        "A guide with no radius is a point. Its bend radius is the circumradius of the incoming point, the guide, and the outgoing point. A guide that sits on the incoming or outgoing point is not a corner, so that radius stays unlimited.",
        "Wrap is cw or ccw about the posed axis. When wrap is omitted, the solver keeps the combination with the smaller total arc.",
        "Rest length is the authored value, or the length of the first sample. Slack is a path more than 0.01 mm shorter than that. Stretch is a path more than 0.01 mm longer.",
        "actuator_force_n is the tension in the span at actuator_end. Across a pulley, tension on the far side is the near tension times exp(-friction × wrap in radians). Friction of zero passes the tension through.",
        "A slack tendon carries no tension. Elastic elongation is force times length, divided by Young's modulus times the circular area, and it is not added to the path.",
        "A collision is a centerline sample inside a solid, or closer to the surface than the cable radius plus pinch_mm. When several samples meet one part, the report keeps the closest of that kind, and a centerline inside the solid is kept ahead of a rub. The arc seated on a pulley's own part is the groove and is not a hit. The sample step is the larger of 2 mm and the diameter.",
        "When two pulley axes are not parallel, the span is the straight line between the tangents taken in each pulley plane. Fleet angle is reported and is not a failure by itself.",
    ]


def normalize_tendon(cable: dict, index: int = 0) -> dict:
    if not isinstance(cable, dict):
        raise GeometryError(f"Cable {index} must be an object.")
    unknown = set(cable) - _CABLE_KEYS
    if unknown:
        raise GeometryError(
            f"Cable {index} has unknown fields: {', '.join(sorted(unknown))}."
        )
    from aicad.labels import require_one_line

    name = require_one_line(str(cable.get("name") or f"cable_{index + 1}"), "A tendon name", GeometryError)
    if cable.get("waypoints"):
        raise GeometryError(f"{name} uses points for a tendon. Waypoints are the clip route.")
    kind = str(cable.get("kind") or "tendon")
    if kind not in _KINDS:
        raise GeometryError(f"{name}: kind must be cable, hose, or tendon.")
    points = cable.get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise GeometryError(f"{name} needs at least two points.")
    if len(points) > _MAX_POINTS:
        raise GeometryError(f"{name} has more than {_MAX_POINTS} points.")
    cleaned = [_point(item, name, point_index) for point_index, item in enumerate(points)]
    if cleaned[0]["role"] != "anchor" or cleaned[-1]["role"] != "anchor":
        raise GeometryError(f"{name} must start and end on an anchor.")
    for item in cleaned[1:-1]:
        if item["role"] == "anchor":
            raise GeometryError(f"{name} can only use an anchor at the two ends.")
    diameter = _positive(cable.get("diameter_mm", cable.get("diameter", 2.0)), f"{name} diameter_mm")
    material = _line_material(cable, name)
    if cable.get("min_bend_radius_mm") is None and cable.get("min_bend_radius") is None:
        bend_limit = 8.0 * diameter
    else:
        bend_limit = _nonnegative(
            cable.get("min_bend_radius_mm", cable.get("min_bend_radius")),
            f"{name} min_bend_radius_mm",
        )
    actuator = cable.get("actuator_force_n")
    if actuator is not None:
        actuator = _nonnegative(actuator, f"{name} actuator_force_n")
    end = str(cable.get("actuator_end") or "end")
    if end not in ("start", "end"):
        raise GeometryError(f"{name} actuator_end must be start or end.")
    if cable.get("max_strain") is None:
        max_strain = 0.02
    else:
        max_strain = _nonnegative(cable.get("max_strain"), f"{name} max_strain")
    rest = cable.get("rest_length_mm")
    if rest is not None:
        rest = _positive(rest, f"{name} rest_length_mm")
    return {
        "route": "pulley",
        "name": name,
        "kind": kind,
        "diameter_mm": diameter,
        "material": str(material["id"]),
        "material_name": str(material["name"]),
        "modulus_mpa": float(material["youngs_modulus_mpa"]),
        "min_bend_radius_mm": bend_limit,
        "max_strain": max_strain,
        "pinch_mm": _nonnegative(cable.get("pinch_mm") if cable.get("pinch_mm") is not None else 0.5, f"{name} pinch_mm"),
        "actuator_force_n": actuator,
        "actuator_end": end,
        "rest_length_mm": rest,
        "points": cleaned,
    }


def _line_material(cable: dict, name: str) -> dict:
    """A library material, or the modulus stated for this line.

    A braided or laid line is not a bulk material, so its datasheet modulus can
    be given as modulus_mpa. material is then only its label.
    """
    from aicad.labels import require_one_line

    material_name = cable.get("material")
    named = isinstance(material_name, str) and bool(material_name.strip())
    if cable.get("modulus_mpa") is None:
        if not named:
            raise GeometryError(f"{name} needs a material, or modulus_mpa for the line.")
        return lookup_material(material_name.strip())
    modulus = _positive(cable.get("modulus_mpa"), f"{name} modulus_mpa")
    label = require_one_line(material_name.strip(), "A tendon material", GeometryError) if named else "stated line"
    return {"id": label, "name": label, "youngs_modulus_mpa": modulus}


def evaluate(cable: dict, solved: dict, placed: dict, rest_length: float | None) -> tuple[dict, float]:
    """Path, tension, and collisions for one posed assembly."""
    nodes = _place(cable, solved)
    route = _solve(cable["name"], nodes)
    if rest_length is None:
        rest_length = cable["rest_length_mm"] if cable["rest_length_mm"] is not None else route["length"]
    metrics = _metrics(cable, route, rest_length)
    hits = _collisions(route, cable, placed)
    metrics["pinches"] = hits
    metrics["collisions"] = hits
    metrics["status"] = "fail" if (not metrics["bend_ok"] or not metrics["strain_ok"] or hits) else "pass"
    return metrics, rest_length


def summarize(cable: dict, rows: list[dict]) -> dict:
    worst = min((row["min_bend_radius_mm"] if row["min_bend_radius_mm"] is not None else 1e9) for row in rows)
    wraps = []
    for index, wrap in enumerate(rows[0]["wraps"]):
        series = [row["wraps"][index]["wrap_deg"] for row in rows]
        wraps.append(
            {
                "name": wrap["name"],
                "role": wrap["role"],
                "radius_mm": wrap["radius_mm"],
                "max_wrap_deg": round(max(series), 3),
                "wrap_deg": series,
                "bend_ok": all(row["wraps"][index]["bend_ok"] for row in rows),
            }
        )
    pinches = [hit for row in rows for hit in row["pinches"]]
    return {
        "name": cable["name"],
        "kind": cable["kind"],
        "route": "pulley",
        "material": cable["material_name"],
        "status": "fail" if any(row["status"] == "fail" for row in rows) else "pass",
        "rest_length_mm": rows[0]["rest_length_mm"],
        "length_mm": [row["length_mm"] for row in rows],
        "length_change_mm": [row["length_change_mm"] for row in rows],
        "length_state": [row["length_state"] for row in rows],
        "max_strain": round(max(row["strain"] for row in rows), 5),
        "strain_limit": cable["max_strain"],
        "min_bend_radius_mm": None if worst >= 1e8 else round(worst, 3),
        "bend_limit_mm": cable["min_bend_radius_mm"],
        "pinch_count": len(pinches),
        "collision_count": len(pinches),
        "wraps": wraps,
        "actuator_force_n": cable["actuator_force_n"],
        "actuator_end": cable["actuator_end"],
        "tension_ratio": [row["tension_ratio"] for row in rows],
        "tension_n": [row["tension_n"] for row in rows],
        "frames": rows,
    }


def _point(item, cable: str, index: int) -> dict:
    if not isinstance(item, dict):
        raise GeometryError(f"{cable} point {index} must be an object.")
    unknown = set(item) - _POINT_KEYS
    if unknown:
        raise GeometryError(
            f"{cable} point {index} has unknown fields: {', '.join(sorted(unknown))}."
        )
    role = str(item.get("role") or "")
    if role not in _ROLES:
        raise GeometryError(f"{cable} point {index} role must be anchor, pulley, guide, or wrap.")
    instance = item.get("instance")
    if not isinstance(instance, str) or not instance.strip():
        raise GeometryError(f"{cable} point {index} needs an instance.")
    at = item.get("at")
    if not isinstance(at, (list, tuple)) or len(at) != 3:
        raise GeometryError(f"{cable} point {index} at must be three numbers.")
    label = f"{cable} point {index}"
    from aicad.labels import require_one_line

    name = require_one_line(str(item.get("name") or f"{role}_{index + 1}"), "A tendon name", GeometryError)
    radius = item.get("radius_mm")
    sheave = role in ("pulley", "wrap") or (role == "guide" and radius is not None)
    if role == "anchor" and any(key in item for key in ("axis", "radius_mm", "wrap", "friction", "turns")):
        raise GeometryError(f"{label} is an anchor, so it has no radius, axis, wrap, or friction.")
    if role == "guide" and not sheave and any(key in item for key in ("axis", "wrap", "friction", "turns")):
        raise GeometryError(f"{label} needs radius_mm before it can wrap.")
    record = {
        "role": role,
        "name": name,
        "instance": instance.strip(),
        "at": [_finite(component, f"{label} at") for component in at],
        "kind": "point",
    }
    if not sheave:
        return record
    if radius is None:
        raise GeometryError(f"{label} needs radius_mm.")
    record.update(
        {
            "kind": "sheave",
            "radius_mm": _positive(radius, f"{label} radius_mm"),
            "axis": _axis(item.get("axis"), f"{label} axis"),
            "wrap": _wrap(item.get("wrap"), label),
            "friction": _nonnegative(item.get("friction") if item.get("friction") is not None else 0.0, f"{label} friction"),
            "turns": _turns(item.get("turns"), label),
        }
    )
    return record


def _place(cable: dict, solved: dict) -> list[dict]:
    frames = {instance["name"]: instance["matrix"] for instance in solved["instances"]}
    nodes = []
    for index, point in enumerate(cable["points"]):
        matrix = frames.get(point["instance"])
        if matrix is None:
            raise GeometryError(
                f"{cable['name']} point {index} is on {point['instance']!r}, which is not in the assembly."
            )
        node = {
            "role": point["role"],
            "name": point["name"],
            "instance": point["instance"],
            "kind": point["kind"],
            "at": transform_point(matrix, point["at"]),
        }
        if point["kind"] == "sheave":
            axis = _unit(_rotate(matrix, point["axis"]), f"{cable['name']} {point['name']} axis")
            node.update(
                {
                    "center": node["at"],
                    "axis": axis,
                    "radius": point["radius_mm"],
                    "sense": point["wrap"],
                    "friction": point["friction"],
                    "turns": point["turns"],
                }
            )
        nodes.append(node)
    return nodes


def _solve(name: str, nodes: list[dict]) -> dict:
    free = [index for index, node in enumerate(nodes) if node["kind"] == "sheave" and node["sense"] is None]
    if not free:
        return _build(name, nodes)
    if len(free) > _MAX_FREE_WRAPS:
        raise GeometryError(f"{name} needs wrap set to cw or ccw when more than {_MAX_FREE_WRAPS} pulleys omit it.")
    best = None
    best_key = None
    last_error = None
    for mask in range(1 << len(free)):
        trial = []
        for index, node in enumerate(nodes):
            item = dict(node)
            if node["kind"] == "sheave" and node["sense"] is None:
                bit = free.index(index)
                item["sense"] = 1 if (mask >> bit) & 1 else -1
            trial.append(item)
        try:
            route = _build(name, trial)
        except GeometryError as exc:
            last_error = exc
            continue
        arc = sum(wrap["wrap_rad"] for wrap in route["wraps"])
        ccw = sum(1 for index in free if trial[index]["sense"] > 0)
        key = (arc, -ccw)
        if best_key is None or key < best_key:
            best = route
            best_key = key
    if best is None:
        if last_error is not None:
            raise last_error
        raise GeometryError(f"{name} has no wrap that meets these pulleys.")
    return best


def _build(name: str, nodes: list[dict]) -> dict:
    spans = []
    arrivals: dict[int, list[float]] = {}
    departures: dict[int, list[float]] = {}
    metas: dict[int, dict] = {}
    for index in range(len(nodes) - 1):
        start, end, meta = _connect(name, nodes[index], nodes[index + 1])
        spans.append(
            {
                "start": start,
                "end": end,
                "length": _dist(start, end),
                "owner_start": nodes[index]["instance"],
                "owner_end": nodes[index + 1]["instance"],
                "approximate": bool(meta.get("approximate")),
            }
        )
        if nodes[index]["kind"] == "sheave":
            departures[index] = start
            metas.setdefault(index, {}).update(departure=meta)
        if nodes[index + 1]["kind"] == "sheave":
            arrivals[index + 1] = end
            metas.setdefault(index + 1, {}).update(arrival=meta)
    wraps = []
    for index, node in enumerate(nodes):
        if node["kind"] != "sheave":
            continue
        arrived = arrivals[index]
        left = departures[index]
        r_in = _planar(node["center"], node["axis"], arrived)
        r_out = _planar(node["center"], node["axis"], left)
        base = _base_wrap(r_in, r_out, node["axis"], node["sense"])
        landed = _rot(_unit(r_in, "radius"), node["axis"], node["sense"] * base)
        if _dist(landed, _unit(r_out, "radius")) > 1e-3:
            raise GeometryError(f"{name} {node['name']} wrap does not meet the leaving tangent.")
        full = base + node["turns"] * 2.0 * math.pi
        arrival_meta = metas.get(index, {}).get("arrival") or {}
        departure_meta = metas.get(index, {}).get("departure") or {}
        fleet = max(abs(arrival_meta.get("fleet_deg") or 0.0), abs(departure_meta.get("fleet_deg") or 0.0))
        wraps.append(
            {
                "name": node["name"],
                "role": node["role"],
                "instance": node["instance"],
                "center": node["center"],
                "axis": node["axis"],
                "radius": node["radius"],
                "sense": node["sense"],
                "friction": node["friction"],
                "turns": node["turns"],
                "wrap_rad": full,
                "r_in": r_in,
                "fleet_deg": fleet,
                "plane_gap_mm": departure_meta.get("plane_gap_mm"),
                "approximate": bool(arrival_meta.get("approximate") or departure_meta.get("approximate")),
            }
        )
    span_length = sum(span["length"] for span in spans)
    arc_length = sum(wrap["radius"] * wrap["wrap_rad"] for wrap in wraps)
    contacts = _contacts(nodes, arrivals, departures)
    bends = _bends(nodes, spans, wraps)
    return {
        "length": span_length + arc_length,
        "span_length": span_length,
        "arc_length": arc_length,
        "spans": spans,
        "wraps": wraps,
        "contacts": contacts,
        "bends": bends,
        "nodes": nodes,
    }


def _connect(name: str, left: dict, right: dict) -> tuple[list[float], list[float], dict]:
    if left["kind"] == "point" and right["kind"] == "point":
        return left["at"], right["at"], {}
    if left["kind"] == "point" and right["kind"] == "sheave":
        tangent, meta = _tangent_from_point(
            name, right, left["at"], right["sense"], arriving=True, tied=_tied(left, right)
        )
        meta["fleet_deg"] = _fleet(left["at"], tangent, right["axis"])
        return left["at"], tangent, meta
    if left["kind"] == "sheave" and right["kind"] == "point":
        tangent, meta = _tangent_from_point(
            name, left, right["at"], left["sense"], arriving=False, tied=_tied(right, left)
        )
        meta["fleet_deg"] = _fleet(tangent, right["at"], left["axis"])
        return tangent, right["at"], meta
    start, end, meta = _sheave_span(name, left, right)
    meta["fleet_deg"] = max(
        abs(_fleet(start, end, left["axis"])),
        abs(_fleet(start, end, right["axis"])),
    )
    return start, end, meta


def _tied(point: dict, sheave: dict) -> bool:
    """An end anchor fixed on the same part as the sheave next to it.

    That is a line tied off on its own drum or hub: it can sit on the
    sheave circle, or inside it in a hole, instead of reaching it from outside.
    """
    return point["role"] == "anchor" and point["instance"] == sheave["instance"]


def _tangent_from_point(
    name: str, sheave: dict, point: list[float], sense: int, arriving: bool, tied: bool = False
) -> tuple[list[float], dict]:
    center = sheave["center"]
    axis = sheave["axis"]
    radius = sheave["radius"]
    offset_vector = _sub(point, center)
    offset = _dot(offset_vector, axis)
    planar = _sub(offset_vector, _scale(axis, offset))
    dist = _norm(planar)
    if dist <= radius + 1e-7:
        if tied and dist > 1e-6:
            # The wrap ends at the anchor's angle, then runs straight to the anchor.
            return _add(center, _scale(planar, radius / dist)), {"plane_offset_mm": offset}
        if tied:
            raise GeometryError(
                f"{name} is tied off at the centre of {sheave['name']}, so it has no angle to wrap to."
            )
        raise GeometryError(
            f"{name} meets {sheave['name']} inside its radius. The cable has to reach the sheave from outside, "
            "unless it is the end anchor on the same part as that sheave."
        )
    direction = _scale(planar, 1.0 / dist)
    beta = math.acos(max(-1.0, min(1.0, radius / dist)))
    best = None
    best_dot = -2.0
    for sign in (1.0, -1.0):
        radial = _rot(direction, axis, sign * beta)
        tangent = _add(center, _scale(radial, radius))
        travel = _sub(tangent, point) if arriving else _sub(point, tangent)
        travel_norm = _norm(travel)
        if travel_norm < 1e-9:
            continue
        expected = _scale(_cross(axis, radial), sense)
        alignment = _dot(_scale(travel, 1.0 / travel_norm), expected)
        if alignment > best_dot:
            best_dot = alignment
            best = tangent
    if best is None or best_dot < 0.2:
        side = "cw" if sense < 0 else "ccw"
        raise GeometryError(f"{name} has no {side} tangent on {sheave['name']}.")
    return best, {"plane_offset_mm": offset}


def _sheave_span(name: str, left: dict, right: dict) -> tuple[list[float], list[float], dict]:
    align = _dot(left["axis"], right["axis"])
    if abs(abs(align) - 1.0) < 1e-6:
        return _parallel_span(name, left, right, align)
    return _skew_span(name, left, right)


def _parallel_span(name: str, left: dict, right: dict, align: float) -> tuple[list[float], list[float], dict]:
    axis = left["axis"]
    sense_right = right["sense"] if align >= 0 else -right["sense"]
    external = left["sense"] == sense_right
    between = _sub(right["center"], left["center"])
    gap = _dot(between, axis)
    planar = _sub(between, _scale(axis, gap))
    distance = _norm(planar)
    if distance < 1e-7:
        raise GeometryError(f"{name} pulleys {left['name']} and {right['name']} are concentric.")
    direction = _scale(planar, 1.0 / distance)
    perpendicular = _cross(axis, direction)
    if external:
        delta = (left["radius"] - right["radius"]) / distance
    else:
        delta = (left["radius"] + right["radius"]) / distance
    if abs(delta) > 1.0 + 1e-9:
        how = "a crossed wrap" if not external else "an open wrap"
        raise GeometryError(
            f"{name} pulleys {left['name']} and {right['name']} are too close for {how}."
        )
    delta = max(-1.0, min(1.0, delta))
    height = math.sqrt(max(0.0, 1.0 - delta * delta))
    for sign in (1.0, -1.0):
        radial = _add(_scale(direction, delta), _scale(perpendicular, sign * height))
        radial = _unit(radial, "tangent")
        other = radial if external else _scale(radial, -1.0)
        start = _add(left["center"], _scale(radial, left["radius"]))
        end = _add(right["center"], _scale(other, right["radius"]))
        travel = _sub(end, start)
        travel_plane = _sub(travel, _scale(axis, _dot(travel, axis)))
        if _norm(travel_plane) < 1e-8:
            continue
        travel_hat = _unit(travel_plane, "span")
        leave = _scale(_cross(axis, radial), left["sense"])
        arrive = _scale(_cross(axis, other), sense_right)
        if _dot(travel_hat, leave) > 0.5 and _dot(travel_hat, arrive) > 0.5:
            return start, end, {"plane_gap_mm": gap, "approximate": False}
    raise GeometryError(
        f"{name} has no common tangent from {left['name']} to {right['name']} for those wrap directions."
    )


def _skew_span(name: str, left: dict, right: dict) -> tuple[list[float], list[float], dict]:
    start = left["center"]
    end = right["center"]
    meta = {"approximate": True, "plane_gap_mm": None}
    for _ in range(16):
        start, _ = _tangent_from_point(name, left, end, left["sense"], arriving=False)
        end, _ = _tangent_from_point(name, right, start, right["sense"], arriving=True)
    return start, end, meta


def _metrics(cable: dict, route: dict, rest: float) -> dict:
    length = route["length"]
    change = length - rest
    if change < -_SLACK_MM:
        state = "slack"
    elif change > _SLACK_MM:
        state = "stretch"
    else:
        state = "neutral"
    strain = 0.0 if rest <= 1e-9 else change / rest
    carried = state != "slack"
    tensions, ratio = _tensions(cable, route, carried)
    actuator = cable["actuator_force_n"]
    tension = None
    if actuator is None:
        elastic = None
    elif not carried:
        tension = 0.0
        elastic = 0.0
    else:
        tension = actuator
        area = math.pi * (cable["diameter_mm"] / 2.0) ** 2
        modulus = cable["modulus_mpa"]
        elastic = 0.0 if area <= 0 or modulus <= 0 else actuator * length / (modulus * area)
    bends = []
    limit = cable["min_bend_radius_mm"]
    worst = None
    for bend in route["bends"]:
        radius = bend["radius"]
        violation = radius != math.inf and radius + 1e-6 < limit
        row = {
            "name": bend["name"],
            "role": bend["role"],
            "instance": bend["instance"],
            "radius_mm": None if radius == math.inf else round(radius, 3),
            "turn_deg": round(math.degrees(bend["turn"]), 3),
            "ok": not violation,
        }
        bends.append(row)
        if radius != math.inf and (worst is None or radius < worst):
            worst = radius
    wraps = []
    sheave_bends = [bend for bend in route["bends"] if bend["sheave"]]
    for wrap, bend in zip(route["wraps"], sheave_bends):
        phi = wrap["wrap_rad"]
        radius = bend["radius"]
        wraps.append(
            {
                "name": wrap["name"],
                "role": wrap["role"],
                "instance": wrap["instance"],
                "radius_mm": round(wrap["radius"], 4),
                "wrap_rad": round(phi, 6),
                "wrap_deg": round(math.degrees(phi), 3),
                "friction": wrap["friction"],
                "capstan_ratio": round(math.exp(wrap["friction"] * phi), 6),
                "bend_radius_mm": None if radius == math.inf else round(radius, 3),
                "bend_ok": radius == math.inf or radius + 1e-6 >= limit,
                "fleet_angle_deg": round(wrap["fleet_deg"], 3),
                "plane_gap_mm": None if wrap["plane_gap_mm"] is None else round(wrap["plane_gap_mm"], 4),
                "approximate": wrap["approximate"],
            }
        )
    warnings = []
    if any(span["approximate"] for span in route["spans"]):
        warnings.append(
            "Two pulleys do not share an axis. That span is the straight line between the in-plane tangents."
        )
    return {
        "name": cable["name"],
        "kind": cable["kind"],
        "route": "pulley",
        "material": cable["material_name"],
        "length_mm": round(length, 3),
        "span_length_mm": round(route["span_length"], 3),
        "arc_length_mm": round(route["arc_length"], 3),
        "rest_length_mm": round(rest, 3),
        "length_change_mm": round(change, 3),
        "length_state": state,
        "strain": round(strain, 5),
        "strain_ok": strain <= cable["max_strain"] + 1e-9,
        "slack": state == "slack",
        "stretch": state == "stretch",
        "tension_n": None if tension is None else round(tension, 4),
        "tensions_n": None if tensions is None else [round(value, 4) for value in tensions],
        "tension_ratio": None if ratio is None else round(ratio, 6),
        "tension_carried": carried and actuator is not None,
        "elastic_elongation_mm": None if elastic is None else round(elastic, 6),
        "min_bend_radius_mm": None if worst is None else round(worst, 3),
        "bend_ok": all(row["ok"] for row in bends),
        "bends": bends,
        "wraps": wraps,
        "contacts": route["contacts"],
        "warnings": warnings,
        "diameter_mm": cable["diameter_mm"],
        "limit_bend_mm": cable["min_bend_radius_mm"],
        "max_strain": cable["max_strain"],
        "modulus_mpa": cable["modulus_mpa"],
    }


def _tensions(cable: dict, route: dict, carried: bool) -> tuple[list[float] | None, float | None]:
    actuator = cable["actuator_force_n"]
    spans = route["spans"]
    if not spans:
        return None, None
    ratio = 1.0
    between = _between(route)
    for item in between:
        if item is not None:
            ratio *= math.exp(item["friction"] * item["wrap_rad"])
    if actuator is None:
        return None, ratio
    count = len(spans)
    tensions = [0.0] * count
    if not carried or actuator == 0:
        return tensions, ratio
    if cable["actuator_end"] == "end":
        tensions[-1] = actuator
        for index in range(count - 2, -1, -1):
            item = between[index]
            factor = 1.0 if item is None else math.exp(-item["friction"] * item["wrap_rad"])
            tensions[index] = tensions[index + 1] * factor
    else:
        tensions[0] = actuator
        for index in range(count - 1):
            item = between[index]
            factor = 1.0 if item is None else math.exp(-item["friction"] * item["wrap_rad"])
            tensions[index + 1] = tensions[index] * factor
    return tensions, ratio


def _between(route: dict) -> list[dict | None]:
    """The sheave between each pair of spans, or None when that node is a guide."""
    nodes = route["nodes"]
    sheave_wraps = {}
    wrap_index = 0
    for index, node in enumerate(nodes):
        if node["kind"] == "sheave":
            sheave_wraps[index] = route["wraps"][wrap_index]
            wrap_index += 1
    return [sheave_wraps.get(index) for index in range(1, len(nodes) - 1)]


def _contacts(nodes, arrivals, departures) -> list[dict]:
    rows = []
    for index, node in enumerate(nodes):
        if node["kind"] == "point":
            rows.append(
                {
                    "name": node["name"],
                    "role": node["role"],
                    "instance": node["instance"],
                    "at_mm": _round_point(node["at"]),
                }
            )
            continue
        rows.append(
            {
                "name": node["name"],
                "role": node["role"],
                "instance": node["instance"],
                "at_mm": _round_point(arrivals[index]),
                "where": "arrive",
            }
        )
        rows.append(
            {
                "name": node["name"],
                "role": node["role"],
                "instance": node["instance"],
                "at_mm": _round_point(departures[index]),
                "where": "leave",
            }
        )
    return rows


def _bends(nodes, spans, wraps) -> list[dict]:
    rows = []
    wrap_at = {index: wrap for index, wrap in _indexed_wraps(nodes, wraps)}
    for index, node in enumerate(nodes):
        if node["kind"] == "sheave":
            wrap = wrap_at[index]
            radius = wrap["radius"] if wrap["wrap_rad"] > 1e-4 else math.inf
            rows.append(
                {
                    "name": node["name"],
                    "role": node["role"],
                    "instance": node["instance"],
                    "radius": radius,
                    "turn": wrap["wrap_rad"],
                    "sheave": True,
                }
            )
            continue
        if index == 0 or index == len(nodes) - 1:
            continue
        previous = spans[index - 1]["start"]
        here = node["at"]
        nxt = spans[index]["end"]
        radius, turn = _circumradius(previous, here, nxt)
        rows.append(
            {
                "name": node["name"],
                "role": node["role"],
                "instance": node["instance"],
                "radius": radius,
                "turn": turn,
                "sheave": False,
            }
        )
    return rows


def _indexed_wraps(nodes, wraps):
    index = 0
    for node_index, node in enumerate(nodes):
        if node["kind"] == "sheave":
            yield node_index, wraps[index]
            index += 1


def _collisions(route: dict, cable: dict, placed: dict) -> list[dict]:
    from aicad.cable import prefer_sample

    step = max(2.0, cable["diameter_mm"])
    seat = max(step, cable["diameter_mm"])
    limit = cable["diameter_mm"] / 2.0 + cable["pinch_mm"]
    radius = cable["diameter_mm"] / 2.0
    hits: dict[str, dict] = {}
    for point, owners in _samples(route, step, seat):
        for instance, shape in placed.items():
            if instance in owners:
                continue
            gap = kernel.distance_to_point(shape, point)
            if not gap["inside"] and gap["distance_mm"] >= limit:
                continue
            clearance = -gap["distance_mm"] if gap["inside"] else gap["distance_mm"] - radius
            if not prefer_sample(hits.get(instance), gap["inside"], round(clearance, 3)):
                continue
            hits[instance] = {
                "part": instance,
                "at_mm": [round(component, 3) for component in point],
                "distance_mm": gap["distance_mm"],
                "inside": gap["inside"],
                "kind": "inside" if gap["inside"] else "rub",
                "clearance_mm": round(clearance, 3),
            }
    return list(hits.values())


def _samples(route: dict, step: float, seat: float):
    for span in route["spans"]:
        length = span["length"]
        count = max(1, int(math.ceil(length / step))) if length > 1e-9 else 1
        for index in range(count + 1):
            blend = index / count
            point = _add(span["start"], _scale(_sub(span["end"], span["start"]), blend))
            owners = set()
            if _dist(point, span["start"]) <= seat:
                owners.add(span["owner_start"])
            if _dist(point, span["end"]) <= seat:
                owners.add(span["owner_end"])
            yield point, owners
    for wrap in route["wraps"]:
        if wrap["wrap_rad"] <= 1e-8 or wrap["radius"] <= 1e-9:
            continue
        arc = wrap["radius"] * wrap["wrap_rad"]
        count = max(1, int(math.ceil(arc / step)))
        radial = _unit(wrap["r_in"], "radius")
        for index in range(count + 1):
            angle = wrap["wrap_rad"] * index / count
            direction = _rot(radial, wrap["axis"], wrap["sense"] * angle)
            yield _add(wrap["center"], _scale(direction, wrap["radius"])), {wrap["instance"]}


def _circumradius(a, b, c) -> tuple[float, float]:
    ab = _dist(a, b)
    bc = _dist(b, c)
    ac = _dist(a, c)
    if ab < 1e-9 or bc < 1e-9:
        # A repeated point is not a corner. Radius 0 would fail every bend limit.
        return math.inf, 0.0
    area = _triangle_area(a, b, c)
    turn = math.acos(max(-1.0, min(1.0, _cosine(a, b, c))))
    deflection = math.pi - turn
    if area < 1e-8 or deflection < 1e-4:
        return math.inf, deflection
    return ab * bc * ac / (4.0 * area), deflection


def _cosine(a, b, c) -> float:
    v1 = _sub(a, b)
    v2 = _sub(c, b)
    return _dot(v1, v2) / (_norm(v1) * _norm(v2))


def _triangle_area(a, b, c) -> float:
    ab = _sub(b, a)
    ac = _sub(c, a)
    cross = _cross(ab, ac)
    return 0.5 * _norm(cross)


def _base_wrap(r_in, r_out, axis, sense: int) -> float:
    signed = math.atan2(_dot(axis, _cross(r_in, r_out)), _dot(r_in, r_out))
    if abs(signed) <= 1e-8:
        return 0.0
    if sense > 0:
        return signed if signed > 0 else signed + 2.0 * math.pi
    return -signed if signed < 0 else 2.0 * math.pi - signed


def _fleet(origin, target, axis) -> float:
    direction = _sub(target, origin)
    length = _norm(direction)
    if length < 1e-9:
        return 0.0
    return math.degrees(math.asin(max(-1.0, min(1.0, _dot(direction, axis) / length))))


def _planar(center, axis, point) -> list[float]:
    return _sub(_sub(point, center), _scale(axis, _dot(_sub(point, center), axis)))


def _round_point(point) -> list[float]:
    return [round(component, 4) for component in point]


def _rotate(matrix, vector) -> list[float]:
    return [
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
        matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
    ]


def _rot(vector, axis, angle) -> list[float]:
    cosine = math.cos(angle)
    sine = math.sin(angle)
    cross = _cross(axis, vector)
    dot = _dot(axis, vector)
    return [
        vector[index] * cosine + cross[index] * sine + axis[index] * dot * (1.0 - cosine)
        for index in range(3)
    ]


def _axis(value, label: str) -> list[float]:
    if value is None:
        return [0.0, 0.0, 1.0]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryError(f"{label} must be three numbers.")
    return _unit([_finite(component, label) for component in value], label)


def _wrap(value, label: str) -> int | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "ccw":
        return 1
    if text == "cw":
        return -1
    raise GeometryError(f"{label} wrap must be cw or ccw.")


def _turns(value, label: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (
        isinstance(value, float) and not value.is_integer()
    ):
        raise GeometryError(f"{label} turns must be a whole number.")
    turns = int(value)
    if turns < 0 or turns > _MAX_TURNS:
        raise GeometryError(f"{label} turns must be from 0 to {_MAX_TURNS}.")
    return turns


def _positive(value, label: str) -> float:
    number = _finite(value, label)
    if number <= 0:
        raise GeometryError(f"{label} must be positive.")
    return number


def _nonnegative(value, label: str) -> float:
    number = _finite(value, label)
    if number < 0:
        raise GeometryError(f"{label} cannot be negative.")
    return number


def _finite(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"{label} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise GeometryError(f"{label} must be finite.")
    return number


def _unit(vector, label: str) -> list[float]:
    length = _norm(vector)
    if length < 1e-12:
        raise GeometryError(f"{label} cannot be zero.")
    return [component / length for component in vector]


def _add(a, b):
    return [a[index] + b[index] for index in range(3)]


def _sub(a, b):
    return [a[index] - b[index] for index in range(3)]


def _scale(vector, factor):
    return [component * factor for component in vector]


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _norm(vector) -> float:
    return math.sqrt(_dot(vector, vector))


def _dist(a, b) -> float:
    return _norm(_sub(a, b))
