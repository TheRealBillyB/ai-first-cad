"""Mesh and backlash check for a gear pair.

Spur and helical pairs use the ISO 21771 operating centre already computed for
placement. Straight and spiral bevel pairs use the same Tredgold virtual spur
as the contact ratio. An offset pair is the hypoid face-mill solid: the ring
tooth and the pinion blank that tooth leaves, placed on the offset shafts.
"""

from __future__ import annotations

import math
from typing import Callable

from aicad.errors import GeometryError
from aicad.gears.geometry import (
    INTERSECTING,
    OFFSET,
    PARALLEL,
    cylindrical_mount,
    gear_data,
    spiral_offset,
    tooth_profile,
    virtual_spur_at,
)
from aicad.honesty import VERIFIED_CHECK, finish

# A chord across the involute sags by about a thousandth of a millimetre.
# Deeper than this, the tooth outlines have crossed.
_PENETRATION_MM = 0.02
_CONTACT_MINIMUM = 1.0

_CHECKERS: dict[str, Callable] = {}

_LIMITS = [
    "Tooth thickness is the nominal size, not a tolerance grade or a measured gap.",
    "The sweep tests the transverse tooth outline the solid is drawn from, through one turn of the smaller gear.",
    "A bevel pair is swept as the Tredgold virtual spur at the toe, the mid-face, and the heel.",
    "An offset pair is checked on the generated face-mill solids at a few mesh poses, not as a spiral bevel and not through a full turn.",
]

_WHAT = (
    "Nominal mesh of the involute model: centre distance against the zero-backlash centre, "
    "circumferential and normal backlash against the given range, contact ratio, undercut, "
    "and tooth interference through one turn of the smaller gear."
)


def register_mesh_check(axis_layout: str, checker: Callable) -> None:
    """Register the checker for one axis layout.

    Layouts are ``parallel`` (spur, helical), ``intersecting`` (straight and
    spiral bevel), and ``offset`` (hypoid). The offset checker is registered
    with the others. The checker returns the report body.
    """
    if axis_layout not in {PARALLEL, INTERSECTING, OFFSET}:
        raise GeometryError(
            f"Mesh check layout {axis_layout!r} is unknown. Use 'parallel', 'intersecting', or 'offset'."
        )
    _CHECKERS[axis_layout] = checker


def unregister_mesh_check(axis_layout: str) -> None:
    """Drop a checker. The built-in spur, helical, and bevel checkers can be put back."""
    _CHECKERS.pop(axis_layout, None)


def registered_mesh_checks() -> list[str]:
    """Axis layouts that currently have a mesh checker."""
    return sorted(_CHECKERS)


def check_pair(
    pinion: dict,
    gear: dict,
    mounting: dict | None = None,
    backlash_mm=None,
    steps: int | None = None,
) -> dict:
    """Check one pair.

    ``mounting`` may carry ``centre_distance_mm``, ``shaft_angle_deg``,
    ``offset_mm``, and the tooth phases in degrees. Omit the centre distance
    of a spur or helical pair to use the zero-backlash centre. For a bevel,
    the centre distance is a signed axis offset from intersecting shafts, and
    zero is the generated mount.

    ``backlash_mm`` is ``{min, max}`` or ``[min, max]`` and applies to both
    backlash numbers, or a dict with ``circumferential`` and ``normal`` ranges.
    """
    if not isinstance(pinion, dict) or not isinstance(gear, dict):
        raise GeometryError("A gear pair needs two gear specs.")
    left = gear_data(pinion)
    right = gear_data(gear)
    if left["axis_layout"] != right["axis_layout"]:
        raise GeometryError(
            f"A {left['type']} ({left['axis_layout']} axes) cannot mesh with "
            f"a {right['type']} ({right['axis_layout']} axes)."
        )
    checker = _CHECKERS.get(left["axis_layout"])
    if checker is None:
        raise GeometryError(
            f"No mesh check is registered for axis layout {left['axis_layout']!r}. "
            "An offset (hypoid) pair plugs in through register_mesh_check."
        )
    body = checker(pinion, gear, left, right, mounting or {}, _ranges(backlash_mm), steps)
    name = body.pop("result_name", None) or "gear pair"
    what = body.pop("what", None) or _WHAT
    limits = body.pop("limits", None) or _LIMITS
    return finish(body, kind=VERIFIED_CHECK, name=name, what=what, limits=limits)


def mounting_from_frames(pinion_matrix: list, gear_matrix: list) -> dict:
    """Axis distance, shaft angle, and tooth phase from two placement matrices.

    Phase is the angle from the direction toward the mate to the tooth-0
    centre (+X), about the gear axis. It is omitted when the origins coincide,
    which is the bevel case: the check then uses the generated tooth phase.
    """
    origin_p = [float(pinion_matrix[row][3]) for row in range(3)]
    origin_g = [float(gear_matrix[row][3]) for row in range(3)]
    axis_p = _unit([float(pinion_matrix[row][2]) for row in range(3)])
    axis_g = _unit([float(gear_matrix[row][2]) for row in range(3)])
    delta = [origin_g[i] - origin_p[i] for i in range(3)]
    crossed = _cross(axis_p, axis_g)
    sine = _norm(crossed)
    cosine = _dot(axis_p, axis_g)
    shaft = math.degrees(math.atan2(sine, cosine))
    if sine < 1e-8:
        centre = _norm(_cross(delta, axis_p))
        parallel = True
    else:
        centre = abs(_dot(delta, _unit(crossed)))
        parallel = False
    mounting = {
        "centre_distance_mm": centre,
        "shaft_angle_deg": 0.0 if parallel else shaft,
        "parallel": parallel,
    }
    if parallel and centre > 1e-6:
        mounting["pinion_phase_deg"] = _phase(pinion_matrix, axis_p, delta)
        mounting["gear_phase_deg"] = _phase(gear_matrix, axis_g, [-component for component in delta])
    return mounting


def _check_parallel(pinion_spec, gear_spec, left, right, mounting, ranges, steps) -> dict:
    centre = mounting.get("centre_distance_mm")
    mount = cylindrical_mount(left, right, centre)
    shaft = mounting.get("shaft_angle_deg")
    extra = []
    if shaft is not None and abs(math.sin(math.radians(float(shaft)))) > 1e-3:
        if min(abs(float(shaft)) % 360.0, abs(abs(float(shaft)) % 360.0 - 180.0)) > 0.05:
            extra.append("The shafts are not parallel.")
    sweep = _sweep_cylindrical(pinion_spec, gear_spec, left, right, mount["centre_distance_mm"], mounting, steps)
    inside = _tips_past_base(left, right, mount["centre_distance_mm"], mount["working_transverse_pressure_angle_deg"], mount["backlash_defined"])
    return _report(
        left,
        right,
        mount,
        ideal_centre=mount["ideal_centre_distance_mm"],
        actual_centre=mount["centre_distance_mm"],
        shaft_actual=0.0 if shaft is None else float(shaft),
        shaft_ideal=0.0,
        ranges=ranges,
        sweep=sweep,
        extra_findings=extra,
        tips_interfere=inside,
        assumptions=_CYLINDRICAL_ASSUMPTIONS,
        standards=["ISO 21771:2007", "ISO 53:1998"],
    )


def _check_intersecting(pinion_spec, gear_spec, left, right, mounting, ranges, steps) -> dict:
    _require_bevel_pair(left, right)
    offset = mounting.get("offset_mm", mounting.get("centre_distance_mm"))
    if offset is None:
        offset = 0.0
    offset = float(offset)
    if not math.isfinite(offset):
        raise GeometryError("centre_distance_mm for a bevel pair must be a finite axis offset.")
    stations = _bevel_stations(left, right)
    mid = stations[len(stations) // 2]
    section_p = virtual_spur_at(left, mid)
    section_g = virtual_spur_at(right, mid)
    virtual_left = _virtual_mount_record(section_p, left)
    virtual_right = _virtual_mount_record(section_g, right)
    ideal = cylindrical_mount(virtual_left, virtual_right, None)
    if abs(offset) < 1e-12:
        mount = ideal
    else:
        mount = cylindrical_mount(virtual_left, virtual_right, ideal["centre_distance_mm"] + offset)
    shaft_ideal = float(left["shaft_angle_deg"])
    shaft_actual = float(mounting.get("shaft_angle_deg", shaft_ideal))
    extra = []
    if abs(shaft_actual - shaft_ideal) > 0.05:
        extra.append(
            f"Shaft angle is {shaft_actual:.3f} degrees. The pair was generated for {shaft_ideal:.3f} degrees."
        )
    if left["spiral_angle_deg"] * right["spiral_angle_deg"] > 1e-9:
        extra.append("A spiral bevel pair needs opposite hands.")
    sweep = _sweep_bevel(left, right, stations, offset, steps)
    inside = _tips_past_base(
        virtual_left,
        virtual_right,
        mount["centre_distance_mm"],
        mount["working_transverse_pressure_angle_deg"],
        mount["backlash_defined"],
    )
    # Overlap is the spiral travel. The virtual mount has no helix, so keep the bevel figure.
    from aicad.gears.geometry import pair_geometry

    nominal = pair_geometry(pinion_spec, gear_spec)
    mount = dict(mount)
    mount["overlap_ratio"] = nominal["overlap_ratio"]
    if mount["transverse_contact_ratio"] is None:
        mount["total_contact_ratio"] = None
    else:
        mount["total_contact_ratio"] = mount["transverse_contact_ratio"] + nominal["overlap_ratio"]
    mount["warnings"] = list(nominal.get("warnings") or [])
    report = _report(
        left,
        right,
        mount,
        ideal_centre=0.0,
        actual_centre=offset,
        shaft_actual=shaft_actual,
        shaft_ideal=shaft_ideal,
        ranges=ranges,
        sweep=sweep,
        extra_findings=extra,
        tips_interfere=inside,
        assumptions=_BEVEL_ASSUMPTIONS,
        standards=["ISO 23509:2016", "ANSI/AGMA 2005-D03", "ISO 21771:2007"],
    )
    report["virtual_centre_distance_mm"] = mount["centre_distance_mm"]
    report["ideal_virtual_centre_distance_mm"] = ideal["centre_distance_mm"]
    return report


_CYLINDRICAL_ASSUMPTIONS = [
    "Circumferential backlash is 2 a (inv α' − inv α) on the operating pitch circle, from ISO 21771.",
    "Normal backlash is that arc times cos α' times cos β at the operating pitch cylinder.",
    "The sweep turns the smaller gear through one full turn and turns the mate by the tooth ratio.",
    "A helical pair is also checked at both ends of the shorter face, with the helix twist at that height.",
    "Undercut is the ISO 53 limit already stored on each gear.",
    "Tooth thickness is the nominal zero-backlash size.",
]

_BEVEL_ASSUMPTIONS = [
    "A bevel centre distance is a signed axis offset. Zero means the pitch apexes coincide.",
    "Backlash is the virtual spur pair at mid-face, with that offset added to the virtual centre.",
    "The sweep turns the smaller gear's virtual spur through one full turn at the toe, mid-face, and heel.",
    "That sweep starts at the generated mesh phase, a tooth space facing the pinion tooth. It does not read a clocking about the pitch generator.",
    "A spiral offset along the cone is the same constant-angle spiral the solid uses, unwrapped onto the back cone.",
    "This is not a hypoid tooth. A designed offset plugs in as its own checker.",
    "Tooth thickness is the nominal Gleason size, and the shifts sum to zero.",
]


def _report(
    left,
    right,
    mount,
    *,
    ideal_centre,
    actual_centre,
    shaft_actual,
    shaft_ideal,
    ranges,
    sweep,
    extra_findings,
    tips_interfere,
    assumptions,
    standards,
) -> dict:
    findings = list(extra_findings)
    warnings = list(mount.get("warnings") or [])
    undercut_pinion = bool(left.get("undercut"))
    undercut_gear = bool(right.get("undercut"))
    if undercut_pinion:
        findings.append("The pinion is undercut. The root fillet cuts into the involute.")
    if undercut_gear:
        findings.append("The gear is undercut. The root fillet cuts into the involute.")
    for warning in warnings:
        if "opposite hands" in warning or "same hand" in warning:
            if warning not in findings:
                findings.append(warning)
    interfere = bool(mount.get("base_circles_overlap")) or tips_interfere or sweep["worst_penetration_mm"] > _PENETRATION_MM
    backlash_t = mount["circumferential_backlash_mm"]
    backlash_n = mount["normal_backlash_mm"]
    if backlash_t is not None and backlash_t < -_PENETRATION_MM:
        interfere = True
    if mount.get("base_circles_overlap"):
        findings.append("The centre distance is inside the base circles, so the flanks interfere.")
    elif tips_interfere:
        findings.append("A tip crosses the mate's base circle, so the involute cannot mesh there.")
    if sweep["worst_penetration_mm"] > _PENETRATION_MM:
        findings.append(
            f"Tooth profiles interfere. The deepest pass is {sweep['worst_penetration_mm']:.3f} mm "
            f"at {sweep['worst_at_deg']:.1f} degrees."
        )
    elif interfere and backlash_t is not None and backlash_t < -_PENETRATION_MM:
        findings.append(
            f"Circumferential backlash is {backlash_t:.3f} mm, so the flanks overlap."
        )
    total = mount["total_contact_ratio"]
    if total is None:
        findings.append("There is no working pressure angle, so the contact ratio is not defined.")
    elif total < _CONTACT_MINIMUM:
        findings.append(f"Total contact ratio is {total:.3f}, below 1.")
    backlash = _judge_backlash(backlash_t, backlash_n, ranges, findings)
    # Drop duplicate finding strings while keeping order.
    ordered = []
    for item in findings:
        if item not in ordered:
            ordered.append(item)
    status = "pass" if not ordered else "fail"
    smaller = "gear" if float(right["teeth"]) < float(left["teeth"]) else "pinion"
    return {
        "status": status,
        "axis_layout": left["axis_layout"],
        "result_name": f"{left['type']} {left['teeth']}/{right['teeth']}",
        "pinion": _member(left, undercut_pinion),
        "gear": _member(right, undercut_gear),
        "centre_distance_mm": actual_centre,
        "ideal_centre_distance_mm": ideal_centre,
        "centre_distance_error_mm": actual_centre - ideal_centre,
        "shaft_angle_deg": shaft_actual,
        "ideal_shaft_angle_deg": shaft_ideal,
        "shaft_angle_error_deg": shaft_actual - shaft_ideal,
        "backlash": backlash,
        "transverse_contact_ratio": mount["transverse_contact_ratio"],
        "overlap_ratio": mount["overlap_ratio"],
        "total_contact_ratio": total,
        "undercut": undercut_pinion or undercut_gear,
        "interference": interfere,
        "smaller_gear": smaller,
        "findings": ordered,
        "warnings": warnings,
        "sweep": sweep,
        "assumptions": list(assumptions),
        "standards": standards,
    }


def _member(data: dict, undercut: bool) -> dict:
    return {
        "type": data["type"],
        "teeth": data["teeth"],
        "hand": data.get("hand"),
        "undercut": undercut,
    }


def _judge_backlash(circumferential, normal, ranges, findings: list[str]) -> dict:
    block = {
        "circumferential_mm": circumferential,
        "normal_mm": normal,
        "circumferential_range_mm": None,
        "normal_range_mm": None,
        "circumferential_in_range": None,
        "normal_in_range": None,
        "in_range": None,
    }
    if ranges is None:
        return block
    circ_span, norm_span = ranges
    block["circumferential_range_mm"] = [circ_span[0], circ_span[1]]
    block["normal_range_mm"] = [norm_span[0], norm_span[1]]
    block["circumferential_in_range"] = _inside(circumferential, circ_span)
    block["normal_in_range"] = _inside(normal, norm_span)
    block["in_range"] = block["circumferential_in_range"] and block["normal_in_range"]
    if circumferential is None or normal is None:
        findings.append("Backlash is not defined at this centre distance, so it is outside the range.")
        return block
    if not block["circumferential_in_range"]:
        findings.append(
            f"Circumferential backlash {circumferential:.4f} mm is outside "
            f"{circ_span[0]:.4f} to {circ_span[1]:.4f} mm."
        )
    if not block["normal_in_range"]:
        findings.append(
            f"Normal backlash {normal:.4f} mm is outside {norm_span[0]:.4f} to {norm_span[1]:.4f} mm."
        )
    return block


def _inside(value, span) -> bool:
    if value is None or not math.isfinite(value):
        return False
    return span[0] - 1e-6 <= value <= span[1] + 1e-6


def _ranges(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        span = _span(value[0], value[1], "backlash_mm")
        return span, span
    if isinstance(value, dict):
        circ = value.get("circumferential", value.get("circumferential_mm"))
        norm = value.get("normal", value.get("normal_mm"))
        if circ is None and norm is None and ("min" in value or "min_mm" in value or "max" in value or "max_mm" in value):
            span = _span(_bound(value, "min"), _bound(value, "max"), "backlash_mm")
            return span, span
        if circ is None or norm is None:
            raise GeometryError("backlash_mm needs both a circumferential range and a normal range, or one shared range.")
        return _range_value(circ, "circumferential backlash"), _range_value(norm, "normal backlash")
    raise GeometryError("backlash_mm is {min, max}, [min, max], or circumferential and normal ranges.")


def _range_value(value, label: str):
    if isinstance(value, dict):
        return _span(_bound(value, "min"), _bound(value, "max"), label)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return _span(value[0], value[1], label)
    raise GeometryError(f"{label} is a [min, max] range.")


def _bound(value: dict, end: str) -> float:
    if end in value:
        return value[end]
    key = f"{end}_mm"
    if key in value:
        return value[key]
    raise GeometryError(f"backlash_mm needs {end}.")


def _span(low, high, label: str) -> tuple[float, float]:
    try:
        start = float(low)
        stop = float(high)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be two numbers.") from exc
    if not math.isfinite(start) or not math.isfinite(stop):
        raise GeometryError(f"{label} must be finite.")
    if start > stop:
        raise GeometryError(f"{label} has the minimum above the maximum.")
    return start, stop


def _sweep_plan(tooth_count: float, steps: int | None) -> tuple[float, int]:
    if steps is None:
        pitch = 360.0 / float(tooth_count)
        step = min(2.0, pitch / 6.0)
        count = int(math.ceil(360.0 / step - 1e-9))
        if count > 480:
            count = 480
            step = 360.0 / count
        return step, count
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 8 or steps > 1440:
        raise GeometryError("steps must be an integer from 8 to 1440, covering one full turn.")
    return 360.0 / steps, steps


def _sweep_cylindrical(pinion_spec, gear_spec, left, right, centre, mounting, steps) -> dict:
    pinion_tooth = tooth_profile(pinion_spec)
    gear_tooth = tooth_profile(gear_spec)
    z_p = float(left["teeth"])
    z_g = float(right["teeth"])
    smaller = z_g < z_p - 1e-9
    step, count = _sweep_plan(z_g if smaller else z_p, steps)
    pinion_phase = math.radians(float(mounting.get("pinion_phase_deg", 0.0)))
    gear_phase = math.radians(float(mounting.get("gear_phase_deg", 180.0 / z_g)))
    sections = _helix_sections(left, right)
    return _turn(
        pinion_tooth,
        gear_tooth,
        z_p,
        z_g,
        centre,
        pinion_phase,
        gear_phase,
        sections,
        step,
        count,
        smaller_is_gear=smaller,
        model="transverse outline",
    )


def _sweep_bevel(left, right, stations, offset, steps) -> dict:
    z_p = float(left["teeth"])
    z_g = float(right["teeth"])
    smaller = z_g < z_p - 1e-9
    # The virtual tooth count sets the outline spacing. One turn is still the real smaller gear.
    step, count = _sweep_plan(z_g if smaller else z_p, steps)
    worst = 0.0
    worst_at = 0.0
    section_names = []
    for distance in stations:
        section_p = virtual_spur_at(left, distance)
        section_g = virtual_spur_at(right, distance)
        centre = section_p["rp"] + section_g["rp"] + offset
        twist_p = _spiral_turn(left, distance)
        twist_g = _spiral_turn(right, distance)
        pinion_phase = twist_p
        gear_phase = math.pi / section_g["z"] + twist_g
        turned = _turn(
            tooth_profile(section_p["spec"]),
            tooth_profile(section_g["spec"]),
            section_p["z"],
            section_g["z"],
            centre,
            pinion_phase,
            gear_phase,
            [(0.0, 0.0)],
            step,
            count,
            smaller_is_gear=smaller,
            model="virtual spur",
        )
        if turned["worst_penetration_mm"] > worst:
            worst = turned["worst_penetration_mm"]
            worst_at = turned["worst_at_deg"]
        section_names.append(round(distance, 6))
    return {
        "member": "gear" if smaller else "pinion",
        "rotation_deg": 360.0,
        "step_deg": step,
        "steps": count,
        "sections": section_names,
        "model": "virtual spur",
        "worst_penetration_mm": worst,
        "worst_at_deg": worst_at,
    }


def _turn(
    pinion_tooth,
    gear_tooth,
    z_p,
    z_g,
    centre,
    pinion_phase,
    gear_phase,
    sections,
    step,
    count,
    *,
    smaller_is_gear,
    model,
) -> dict:
    worst = 0.0
    worst_at = 0.0
    step_rad = math.radians(step)
    ratio = z_p / z_g
    for index in range(count):
        theta = index * step_rad
        if smaller_is_gear:
            phase_g = gear_phase + theta
            phase_p = pinion_phase - theta / ratio
            at = index * step
        else:
            phase_p = pinion_phase + theta
            phase_g = gear_phase - theta * ratio
            at = index * step
        for twist_p, twist_g in sections:
            depth = _mesh_depth(
                pinion_tooth,
                z_p,
                phase_p + twist_p,
                (0.0, 0.0),
                0.0,
                gear_tooth,
                z_g,
                phase_g + twist_g,
                (centre, 0.0),
                math.pi,
            )
            if depth > worst:
                worst = depth
                worst_at = at
    return {
        "member": "gear" if smaller_is_gear else "pinion",
        "rotation_deg": step * count,
        "step_deg": step,
        "steps": count,
        "sections": len(sections),
        "model": model,
        "worst_penetration_mm": worst,
        "worst_at_deg": worst_at,
    }


def _helix_sections(left, right) -> list[tuple[float, float]]:
    beta = math.radians(left["helix_angle_deg"])
    if abs(beta) < 1e-12:
        return [(0.0, 0.0)]
    face = min(left["face_width_mm"], right["face_width_mm"])
    rp_p = left["reference_diameter_mm"] / 2.0
    rp_g = right["reference_diameter_mm"] / 2.0
    beta_g = math.radians(right["helix_angle_deg"])
    sections = []
    for height in (-face / 2.0, 0.0, face / 2.0):
        twist_p = height * math.tan(beta) / rp_p
        twist_g = height * math.tan(beta_g) / rp_g
        sections.append((twist_p, twist_g))
    return sections


def _spiral_turn(data: dict, cone_distance: float) -> float:
    """Tooth rotation in the back-cone development, from the mean section.

    ``spiral_offset`` is the rotation about the gear axis. The development
    unwraps that angle by the cosine of the pitch angle, which is the same
    relation the solid uses when it wraps the virtual spur onto the cone.
    """
    beta = math.radians(float(data.get("spiral_angle_deg") or 0.0))
    if abs(beta) < 1e-12:
        return 0.0
    delta = math.radians(data["pitch_angle_deg"])
    cone = {
        "mean_cone_distance_mm": data["mean_cone_distance_mm"],
        "pitch_angle_rad": delta,
    }
    return spiral_offset(cone, cone_distance, beta) * math.cos(delta)


def _bevel_stations(left, right) -> list[float]:
    outer = min(float(left["outer_cone_distance_mm"]), float(right["outer_cone_distance_mm"]))
    face = min(float(left["face_width_mm"]), float(right["face_width_mm"]))
    toe = max(outer - face, outer * 0.05)
    return [toe, outer - face / 2.0, outer]


def _mesh_depth(tooth_a, count_a, phase_a, origin_a, mesh_a, tooth_b, count_b, phase_b, origin_b, mesh_b) -> float:
    polys_a = _near_teeth(tooth_a, count_a, phase_a, origin_a, mesh_a)
    polys_b = _near_teeth(tooth_b, count_b, phase_b, origin_b, mesh_b)
    worst = 0.0
    for poly in polys_a:
        box = _box(poly)
        for other in polys_b:
            if not _boxes_meet(box, _box(other)):
                continue
            worst = max(worst, _penetration(poly, other), _penetration(other, poly))
    return worst


def _near_teeth(tooth, count, phase, origin, mesh_angle) -> list[list[tuple[float, float]]]:
    pitch = 2.0 * math.pi / float(count)
    # phase is the angle of tooth 0 past the mesh direction. Teeth near the mesh have k ≈ -phase/pitch.
    centre = -phase / pitch
    start = math.floor(centre) - 2
    placed = []
    for offset in range(5):
        index = start + offset
        rotation = mesh_angle + phase + index * pitch
        placed.append(_rotate(tooth, rotation, origin))
    return placed


def _rotate(points, angle, origin) -> list[tuple[float, float]]:
    cosine = math.cos(angle)
    sine = math.sin(angle)
    ox, oy = origin
    return [(ox + x * cosine - y * sine, oy + x * sine + y * cosine) for x, y in points]


def _box(poly):
    xs = [point[0] for point in poly]
    ys = [point[1] for point in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _boxes_meet(a, b) -> bool:
    return not (a[2] < b[0] - 0.05 or b[2] < a[0] - 0.05 or a[3] < b[1] - 0.05 or b[3] < a[1] - 0.05)


def _penetration(poly, other) -> float:
    """Deepest vertex of ``poly`` that lands inside ``other``, in millimetres."""
    depth = 0.0
    count = len(other)
    for x, y in poly:
        if not _contains(x, y, other):
            continue
        nearest = min(
            _segment_distance(x, y, other[index], other[(index + 1) % count]) for index in range(count)
        )
        if nearest > depth:
            depth = nearest
    return depth


def _contains(x: float, y: float, poly) -> bool:
    inside = False
    count = len(poly)
    for index in range(count):
        x0, y0 = poly[index]
        x1, y1 = poly[(index + 1) % count]
        if (y0 > y) != (y1 > y):
            # Crossing the ray toward +X. A point on the boundary is not inside.
            if y1 != y0:
                cross = x0 + (y - y0) / (y1 - y0) * (x1 - x0)
                if x < cross:
                    inside = not inside
    return inside


def _segment_distance(px, py, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    length = dx * dx + dy * dy
    if length < 1e-18:
        return math.hypot(px - ax, py - ay)
    tee = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return math.hypot(px - (ax + tee * dx), py - (ay + tee * dy))


def _tips_past_base(left, right, centre, alpha_deg, defined: bool) -> bool:
    if not defined:
        return True
    alpha = math.radians(alpha_deg)
    ra1 = left["tip_diameter_mm"] / 2.0
    rb1 = left["base_diameter_mm"] / 2.0
    ra2 = right["tip_diameter_mm"] / 2.0
    rb2 = right["base_diameter_mm"] / 2.0
    return _past_base(ra1, rb1, rb2, centre, alpha) or _past_base(ra2, rb2, rb1, centre, alpha)


def _past_base(ra_tip, rb_tip, rb_other, centre, alpha) -> bool:
    reach = math.sqrt(max(ra_tip * ra_tip - rb_tip * rb_tip, 0.0))
    along = centre * math.sin(alpha) - reach
    return along < -1e-4


def _virtual_mount_record(section: dict, data: dict) -> dict:
    return {
        "normal_module_mm": section["m"],
        "normal_pressure_angle_deg": section["alpha_t_deg"],
        "helix_angle_deg": 0.0,
        "profile_shift": section["x"],
        "teeth": section["z"],
        "transverse_pressure_angle_deg": section["alpha_t_deg"],
        "reference_diameter_mm": section["d"],
        "base_diameter_mm": section["db"],
        "tip_diameter_mm": section["da"],
        "transverse_module_mm": section["mt"],
        "face_width_mm": data["face_width_mm"],
        "form_radius_mm": section["form_radius"],
        "hand": data.get("hand"),
    }


def _require_bevel_pair(left, right) -> None:
    if abs(left["outer_transverse_module_mm"] - right["outer_transverse_module_mm"]) > 1e-6:
        raise GeometryError("A bevel pair needs the same outer transverse module.")
    if abs(left["shaft_angle_deg"] - right["shaft_angle_deg"]) > 1e-4:
        raise GeometryError("Both bevel gears must use the same shaft angle.")
    if abs(left["normal_pressure_angle_deg"] - right["normal_pressure_angle_deg"]) > 1e-6:
        raise GeometryError("A bevel pair needs the same normal pressure angle.")
    if left["type"] == "spiral_bevel" or right["type"] == "spiral_bevel":
        if abs(abs(left["spiral_angle_deg"]) - abs(right["spiral_angle_deg"])) > 1e-6:
            raise GeometryError("A spiral bevel pair needs equal mean spiral angles.")


def _unit(vector: list[float]) -> list[float]:
    length = _norm(vector)
    if length < 1e-12:
        raise GeometryError("A gear axis has no direction.")
    return [component / length for component in vector]


def _norm(vector) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _dot(a, b) -> float:
    return sum(a[i] * b[i] for i in range(3))


def _cross(a, b) -> list[float]:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _phase(matrix, axis, toward) -> float:
    """Degrees, about ``axis``, from ``toward`` to the part's +X."""
    x_axis = [float(matrix[row][0]) for row in range(3)]

    def project(vector):
        scale = _dot(vector, axis)
        return [vector[i] - scale * axis[i] for i in range(3)]

    target = project(toward)
    local = project(x_axis)
    if _norm(target) < 1e-9 or _norm(local) < 1e-9:
        raise GeometryError("The tooth phase is undefined when the axes are not separated.")
    crossed = _cross(target, local)
    return math.degrees(math.atan2(_dot(axis, crossed), _dot(target, local)))


def _check_offset(pinion_spec, gear_spec, left, right, mounting, ranges, steps) -> dict:
    from aicad.gears.hypoid_mesh import check_offset_pair

    return check_offset_pair(pinion_spec, gear_spec, left, right, mounting, ranges, steps)


def _install() -> None:
    register_mesh_check(PARALLEL, _check_parallel)
    register_mesh_check(INTERSECTING, _check_intersecting)
    register_mesh_check(OFFSET, _check_offset)


_install()
