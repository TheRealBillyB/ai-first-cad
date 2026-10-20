"""Kinematic cable and hose routes.

Waypoints and clips are points fixed on assembly instances. The route is the
polyline through those points after the joints move. Bend radius is the
circumradius of each three consecutive samples. Length, strain, and pinch
gaps are geometric. A flexible-body model would replace this polyline with a
rod that sags, contacts, and slips through clips; the numbers reported here
are the checks that model would still have to pass.
"""

from __future__ import annotations

import math
from typing import Any

from aicad.errors import GeometryError
from aicad.kinematics import solve, transform_point

_STIFFNESS = {"low": 80.0, "medium": 600.0, "high": 4000.0}


def normalize_cable(cable: dict, index: int = 0) -> dict:
    if not isinstance(cable, dict):
        raise GeometryError(f"Cable {index} must be an object.")
    name = str(cable.get("name") or f"cable_{index + 1}")
    kind = str(cable.get("kind") or "cable")
    if kind not in ("cable", "hose"):
        raise GeometryError(f"{name}: kind must be cable or hose.")
    waypoints = cable.get("waypoints")
    if not isinstance(waypoints, list) or len(waypoints) < 2:
        raise GeometryError(f"{name} needs at least two waypoints.")
    cleaned = []
    for point_index, point in enumerate(waypoints):
        if not isinstance(point, dict) or not point.get("instance") or point.get("at") is None:
            raise GeometryError(f"{name} waypoint {point_index} needs instance and at.")
        at = point["at"]
        if not isinstance(at, (list, tuple)) or len(at) != 3:
            raise GeometryError(f"{name} waypoint {point_index} at must be three numbers.")
        cleaned.append(
            {
                "instance": str(point["instance"]),
                "at": [float(component) for component in at],
                "clip": bool(point.get("clip", False)),
            }
        )
    stiffness = cable.get("stiffness_n", cable.get("stiffness", "medium"))
    if isinstance(stiffness, str):
        if stiffness not in _STIFFNESS:
            raise GeometryError(f"{name} stiffness must be low, medium, high, or a number of newtons per unit strain.")
        stiffness_n = _STIFFNESS[stiffness]
        stiffness_label = stiffness
    else:
        stiffness_n = float(stiffness)
        stiffness_label = "custom"
        if stiffness_n < 0:
            raise GeometryError(f"{name} stiffness cannot be negative.")
    return {
        "name": name,
        "kind": kind,
        "diameter_mm": float(cable.get("diameter_mm") or cable.get("diameter") or 6.0),
        "min_bend_radius_mm": float(cable.get("min_bend_radius_mm") or cable.get("min_bend_radius") or 20.0),
        "max_strain": float(cable.get("max_strain") if cable.get("max_strain") is not None else (0.01 if kind == "hose" else 0.02)),
        "stiffness_n": stiffness_n,
        "stiffness": stiffness_label,
        "pinch_mm": float(cable.get("pinch_mm") if cable.get("pinch_mm") is not None else 0.5),
        "waypoints": cleaned,
        "rest_length_mm": None if cable.get("rest_length_mm") is None else float(cable["rest_length_mm"]),
    }


def world_waypoints(cable: dict, solved: dict) -> list[dict]:
    frames = {instance["name"]: instance["matrix"] for instance in solved["instances"]}
    points = []
    for index, waypoint in enumerate(cable["waypoints"]):
        matrix = frames.get(waypoint["instance"])
        if matrix is None:
            raise GeometryError(
                f"{cable['name']} waypoint {index} is on {waypoint['instance']!r}, which is not in the assembly."
            )
        at = transform_point(matrix, waypoint["at"])
        points.append(
            {
                "instance": waypoint["instance"],
                "clip": waypoint["clip"],
                "at_mm": [round(component, 4) for component in at],
            }
        )
    return points


def measure_route(points: list[dict], cable: dict, rest_length_mm: float | None) -> dict:
    coords = [point["at_mm"] for point in points]
    length = _length(coords)
    bends = []
    worst = None
    for index in range(1, len(coords) - 1):
        radius, turn = _bend(coords[index - 1], coords[index], coords[index + 1])
        violation = radius + 1e-6 < cable["min_bend_radius_mm"]
        row = {
            "at": index,
            "instance": points[index]["instance"],
            "clip": points[index]["clip"],
            "point_mm": coords[index],
            "radius_mm": None if radius == math.inf else round(radius, 3),
            "turn_deg": round(math.degrees(turn), 2),
            "ok": not violation,
        }
        bends.append(row)
        if worst is None or (radius < worst):
            worst = radius
    rest = length if rest_length_mm is None else rest_length_mm
    change = length - rest
    strain = 0.0 if rest <= 1e-9 else change / rest
    tension = cable["stiffness_n"] * strain if strain > 0 else 0.0
    self_hits = _self_approach(coords, cable["diameter_mm"])
    return {
        "name": cable["name"],
        "kind": cable["kind"],
        "length_mm": round(length, 3),
        "rest_length_mm": round(rest, 3),
        "length_change_mm": round(change, 3),
        "strain": round(strain, 5),
        "strain_ok": strain <= cable["max_strain"] + 1e-9,
        "slack": change < -1e-4,
        "tension_n": round(tension, 3),
        "min_bend_radius_mm": None if worst in (None, math.inf) else round(worst, 3),
        "bend_ok": all(row["ok"] for row in bends),
        "bends": bends,
        "self_pinch": self_hits,
        "diameter_mm": cable["diameter_mm"],
        "limit_bend_mm": cable["min_bend_radius_mm"],
        "max_strain": cable["max_strain"],
        "stiffness_n": cable["stiffness_n"],
    }


def sample_route(points: list[dict], step_mm: float) -> list[list[float]]:
    coords = [point["at_mm"] for point in points]
    if step_mm <= 0:
        raise GeometryError("Cable sample step must be positive.")
    samples = [list(coords[0])]
    for start, end in zip(coords, coords[1:]):
        span = _dist(start, end)
        count = max(1, int(math.ceil(span / step_mm)))
        for step in range(1, count + 1):
            blend = step / count
            samples.append(
                [start[axis] + (end[axis] - start[axis]) * blend for axis in range(3)]
            )
    return samples


def assumptions() -> list[str]:
    return [
        "The route is a polyline through waypoints and clips that are fixed on parts. It does not sag under gravity.",
        "Bend radius is the circumradius of three consecutive waypoints. A smooth fillet would be at least this large only when the points already lie on that arc.",
        "Strain is (length - rest length) / rest length. Rest length is the authored value, or the length of the first frame when it is omitted.",
        "Tension is stiffness times positive strain. Stiffness is newtons per unit strain, a stand-in for EA. A shorter route is slack, not compression.",
        "A pinch is a centerline sample closer to a solid than the cable radius plus pinch_mm, or two non-adjacent spans closer than the diameter. Samples within one sample step of a waypoint are the clip seat on that part and are not counted against it.",
        "Clips do not slip. A later flexible-body model (a Cosserat rod or a chain of rigid segments with bending springs) would add sag, contact, and clip slip, and would be checked against these same length, bend, and gap limits.",
    ]


def _length(coords: list[list[float]]) -> float:
    return sum(_dist(start, end) for start, end in zip(coords, coords[1:]))


def _bend(a: list[float], b: list[float], c: list[float]) -> tuple[float, float]:
    ab = _dist(a, b)
    bc = _dist(b, c)
    ac = _dist(a, c)
    if ab < 1e-9 or bc < 1e-9:
        return 0.0, 0.0
    area = _triangle_area(a, b, c)
    turn = math.acos(max(-1.0, min(1.0, _cosine(a, b, c))))
    # turn is the interior angle at b. A straight route is pi.
    deflection = math.pi - turn
    if area < 1e-8 or deflection < 1e-4:
        return math.inf, deflection
    radius = ab * bc * ac / (4.0 * area)
    return radius, deflection


def _cosine(a, b, c) -> float:
    v1 = [a[i] - b[i] for i in range(3)]
    v2 = [c[i] - b[i] for i in range(3)]
    n1 = math.sqrt(sum(component * component for component in v1))
    n2 = math.sqrt(sum(component * component for component in v2))
    return sum(v1[i] * v2[i] for i in range(3)) / (n1 * n2)


def _triangle_area(a, b, c) -> float:
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    cross = [
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    ]
    return 0.5 * math.sqrt(sum(component * component for component in cross))


def _self_approach(coords: list[list[float]], diameter: float) -> list[dict]:
    hits = []
    spans = list(range(len(coords) - 1))
    for i in spans:
        for j in spans:
            if j <= i + 1:
                continue
            gap = _segment_distance(coords[i], coords[i + 1], coords[j], coords[j + 1])
            if gap + 1e-6 < diameter:
                hits.append({"spans": [i, j], "gap_mm": round(gap, 3), "limit_mm": diameter})
    return hits


def _segment_distance(a, b, c, d) -> float:
    # Sample both spans. Exact segment distance is more code than this route needs.
    best = _dist(a, c)
    for u in range(8):
        p = [a[axis] + (b[axis] - a[axis]) * (u / 7) for axis in range(3)]
        for v in range(8):
            q = [c[axis] + (d[axis] - c[axis]) * (v / 7) for axis in range(3)]
            best = min(best, _dist(p, q))
    return best


def _dist(a, b) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def route_status(metrics: dict, pinches: list[dict]) -> str:
    if not metrics["bend_ok"] or not metrics["strain_ok"] or metrics["self_pinch"] or pinches:
        return "fail"
    return "pass"
