"""Hypoid ring and pinion from face-mill cutter kinematics.

The pitch point is not taken from a spiral bevel and then slid sideways.
It is the point at the requested ring radius where three things are true at
once: the pitch cones share a tangent plane, the tooth numbers share one
normal module, and the relative velocity lies along the ring tooth. Among
those cones the one with the least sliding is kept.

The ring tooth is the cone swept by a circular face-mill blade. The pinion
is the blank that remains after that same tooth is rolled about the offset
shafts at the tooth ratio and subtracted. A small shift of each flank along
its normal leaves a gap, so a flat facet of the blade cannot cross the mate.
"""

from __future__ import annotations

import functools
import math

from aicad.errors import GeometryError

# Ring flanks move into the metal by this much. The pinion is cut by a tooth
# that is this much larger than the blade, so the pair keeps a normal gap of
# the sum and a facet cannot cross the true surface.
_BIAS_MM = 0.012
_TOOL_STOCK_MM = 0.04
_SAGITTA_MM = 0.006
# One ring tooth is rolled across this many pitches, in steps fine enough that
# the scallop between poses stays inside the stock.
_ROLL_HALF_PITCHES = 1.8
_ROLL_STEP_DEG = 3.5


# --- small vector helpers -------------------------------------------------


def _dot(a, b) -> float:
    return sum(left * right for left, right in zip(a, b))


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return tuple(left - right for left, right in zip(a, b))


def _scale(a, s: float):
    return tuple(component * s for component in a)


def _norm(a) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a, label: str = "vector"):
    length = _norm(a)
    if length < 1e-15:
        raise GeometryError(f"A hypoid {label} has no direction.")
    return _scale(a, 1.0 / length)


def _rot_z(point, angle: float):
    cosine, sine = math.cos(angle), math.sin(angle)
    return (
        cosine * point[0] - sine * point[1],
        sine * point[0] + cosine * point[1],
        point[2],
    )


def _rot_axis(point, axis, angle: float):
    axis = _unit(axis)
    cosine, sine = math.cos(angle), math.sin(angle)
    parallel = _scale(axis, _dot(axis, point))
    return _add(_add(_scale(point, cosine), _scale(_cross(axis, point), sine)), _scale(parallel, 1.0 - cosine))


def _polar(point) -> float:
    return math.atan2(point[1], point[0])


def _unwrap(angle: float, reference: float) -> float:
    while angle - reference > math.pi:
        angle -= 2.0 * math.pi
    while angle - reference < -math.pi:
        angle += 2.0 * math.pi
    return angle


# --- spec ------------------------------------------------------------------


def normalize_hypoid(spec: dict) -> dict:
    """Fill defaults for one member of a hypoid pair."""
    from aicad.gears import geometry as geom

    teeth = geom._required_teeth(spec)
    role = spec.get("role", spec.get("member"))
    if role is not None:
        role = str(role).strip().lower()
        if role in {"gear", "wheel", "ring_gear"}:
            role = "ring"
        if role not in {"pinion", "ring"}:
            raise GeometryError("role must be 'pinion' or 'ring'.")
    mate = spec.get("mate_teeth")
    ratio = spec.get("ratio")
    if mate is None and ratio is None:
        raise GeometryError("A hypoid gear needs mate_teeth or ratio, and a role when they do not decide which member this is.")
    if mate is not None:
        if isinstance(mate, bool) or not float(mate).is_integer():
            raise GeometryError("mate_teeth must be an integer.")
        mate = int(mate)
        if mate < 6 or mate > 500:
            raise GeometryError("mate_teeth must be from 6 through 500.")
    if ratio is not None:
        ratio = geom._number(ratio, "ratio")
        if ratio <= 0:
            raise GeometryError("ratio must be positive. It is ring teeth divided by pinion teeth.")
    if mate is not None and ratio is not None:
        if role == "ring":
            expected = teeth / mate
        elif role == "pinion":
            expected = mate / teeth
        else:
            expected = (mate / teeth) if teeth < mate else (teeth / mate if mate < teeth else None)
        if expected is None or abs(ratio - expected) > 1e-6 * max(1.0, abs(expected)):
            raise GeometryError(
                "ratio does not match the tooth counts. ratio is ring teeth divided by pinion teeth."
            )
    if role is None:
        if mate is None:
            raise GeometryError("Pass role ('pinion' or 'ring') when the hypoid is sized by ratio.")
        if teeth == mate:
            raise GeometryError("Pinion and ring have the same tooth count. Pass role.")
        role = "pinion" if teeth < mate else "ring"
    if role == "pinion":
        pinion_teeth, ring_teeth = teeth, mate
    else:
        pinion_teeth, ring_teeth = mate, teeth
    if pinion_teeth is None:
        if abs(teeth / ratio - round(teeth / ratio)) > 1e-6:
            raise GeometryError("ratio does not land on an integer pinion tooth count.")
        pinion_teeth = int(round(teeth / ratio))
        ring_teeth = teeth
    elif ring_teeth is None:
        if abs(teeth * ratio - round(teeth * ratio)) > 1e-6:
            raise GeometryError("ratio does not land on an integer ring tooth count.")
        ring_teeth = int(round(teeth * ratio))
        pinion_teeth = teeth
    if pinion_teeth < 6 or ring_teeth < 6:
        raise GeometryError("Hypoid tooth counts must be at least 6.")
    pair_ratio = ring_teeth / pinion_teeth
    if ratio is not None and abs(pair_ratio - ratio) > 1e-6 * max(1.0, ratio):
        raise GeometryError("ratio does not match ring teeth divided by pinion teeth.")

    offset = geom._number(spec.get("offset_mm"), "offset_mm")
    if abs(offset) < 1e-9:
        raise GeometryError(
            "A hypoid needs a nonzero offset_mm. Intersecting shafts are a spiral bevel."
        )
    if abs(offset) > 1.0e6:
        raise GeometryError("offset_mm must stay within a million millimetres.")
    shaft = geom._number(spec.get("shaft_angle_deg"), "shaft_angle_deg", default=90.0)
    if not 1.0 < shaft < 179.0:
        raise GeometryError("shaft_angle_deg must be between 1 and 179 degrees.")
    diameter = geom._required_positive(spec, "ring_diameter_mm")
    face = geom._required_positive(spec, "face_width_mm")
    if face >= diameter * 0.45:
        raise GeometryError("face_width_mm must be shorter than the ring radius.")
    spiral = geom._number(spec.get("spiral_angle_deg"), "spiral_angle_deg", default=None)
    if abs(spiral) < 1e-9:
        raise GeometryError("A hypoid needs a nonzero spiral_angle_deg (the ring mean spiral angle).")
    if abs(spiral) >= 65.0:
        raise GeometryError("spiral_angle_deg must be between -65 and 65 degrees, and not 0.")
    hand = spec.get("hand")
    if hand is not None:
        hand = str(hand).strip().lower()
        if hand not in {"right", "left"}:
            raise GeometryError("hand must be 'right' or 'left'.")
    spiral = geom._apply_hand(spiral, hand if role == "ring" else None, "spiral_angle_deg")
    ring_hand = "left" if spiral < 0 else "right"
    member_hand = ring_hand if role == "ring" else ("right" if ring_hand == "left" else "left")
    if role == "pinion" and hand is not None and hand != member_hand:
        raise GeometryError(
            "The pinion hand is opposite the ring. spiral_angle_deg is the ring spiral, "
            "and a positive angle is a right-hand ring."
        )
    if role == "ring" and hand is not None and hand != ring_hand:
        raise GeometryError("hand and spiral_angle_deg disagree.")

    pressure = geom._number(spec.get("pressure_angle_deg"), "pressure_angle_deg", default=20.0)
    if not 8.0 <= pressure <= 35.0:
        raise GeometryError("pressure_angle_deg must be between 8 and 35 degrees.")
    drive = spec.get("drive_pressure_angle_deg")
    coast = spec.get("coast_pressure_angle_deg")
    drive = pressure if drive is None else geom._number(drive, "drive_pressure_angle_deg")
    coast = pressure if coast is None else geom._number(coast, "coast_pressure_angle_deg")
    for label, angle in (("drive_pressure_angle_deg", drive), ("coast_pressure_angle_deg", coast)):
        if not 8.0 <= angle <= 35.0:
            raise GeometryError(f"{label} must be between 8 and 35 degrees.")
    if spec.get("profile_shift") not in (None, 0, 0.0):
        raise GeometryError("A hypoid tooth is cut by the blade angles. Do not pass profile_shift.")

    cutter = _cutter_spec(spec.get("cutter"), geom)
    bore = spec.get("bore_mm")
    if bore is not None:
        bore = geom._number(bore, "bore_mm")
        if bore <= 0:
            raise GeometryError("bore_mm must be positive.")
    hub = geom._hub(spec.get("hub"))
    keyway = geom._keyway(spec.get("keyway"), bore)
    spline = geom._spline(spec.get("spline"))
    if keyway and spline:
        raise GeometryError("Use a keyway or a spline, not both.")
    if keyway and bore is None:
        raise GeometryError("A keyway needs bore_mm.")

    return {
        "type": "hypoid",
        "role": role,
        "teeth": teeth,
        "mate_teeth": ring_teeth if role == "pinion" else pinion_teeth,
        "pinion_teeth": pinion_teeth,
        "ring_teeth": ring_teeth,
        "ratio": pair_ratio,
        "offset_mm": offset,
        "shaft_angle_deg": shaft,
        "ring_diameter_mm": diameter,
        "face_width_mm": face,
        "spiral_angle_deg": spiral,
        "hand": member_hand,
        "ring_hand": ring_hand,
        "pressure_angle_deg": pressure,
        "drive_pressure_angle_deg": drive,
        "coast_pressure_angle_deg": coast,
        "cutter": cutter,
        "bore_mm": bore,
        "hub": hub,
        "keyway": keyway,
        "spline": spline,
        "module_mm": None,
        "helix_angle_deg": 0.0,
        "profile_shift": 0.0,
        "rack": None,
        "addendum_coefficient": None,
        "dedendum_coefficient": None,
        "fillet_radius_coefficient": None,
        "pitch_angle_deg": None,
    }


def _cutter_spec(value, geom) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise GeometryError("cutter must be an object.")
    process = str(value.get("process") or "face_mill").strip().lower().replace("-", "_").replace(" ", "_")
    if process in {"facemill", "face_milling"}:
        process = "face_mill"
    if process != "face_mill":
        raise GeometryError(
            "cutter.process must be face_mill. The tooth is the cone of a circular face-mill blade. "
            "Face-hobbing, a timed blade group, is not generated."
        )
    radius = value.get("radius_mm")
    if radius is not None:
        radius = geom._number(radius, "cutter.radius_mm")
        if radius <= 0:
            raise GeometryError("cutter.radius_mm must be positive.")
    point = value.get("point_width_mm")
    if point is not None:
        point = geom._number(point, "cutter.point_width_mm")
        if point <= 0:
            raise GeometryError("cutter.point_width_mm must be positive.")
    edge = value.get("edge_radius_mm")
    if edge is not None:
        edge = geom._number(edge, "cutter.edge_radius_mm")
        if edge < 0:
            raise GeometryError("cutter.edge_radius_mm cannot be negative.")
    return {
        "process": "face_mill",
        "radius_mm": radius,
        "point_width_mm": point,
        "edge_radius_mm": edge,
    }


# --- pitch cones -----------------------------------------------------------


def _pitch_key(spec: dict) -> tuple:
    cutter = spec["cutter"]
    return (
        spec["pinion_teeth"],
        spec["ring_teeth"],
        round(spec["offset_mm"], 8),
        round(spec["shaft_angle_deg"], 8),
        round(spec["ring_diameter_mm"], 8),
        round(spec["face_width_mm"], 8),
        round(spec["spiral_angle_deg"], 8),
        round(spec["drive_pressure_angle_deg"], 8),
        round(spec["coast_pressure_angle_deg"], 8),
        None if cutter["radius_mm"] is None else round(cutter["radius_mm"], 8),
        None if cutter["point_width_mm"] is None else round(cutter["point_width_mm"], 8),
        None if cutter["edge_radius_mm"] is None else round(cutter["edge_radius_mm"], 8),
    )


def pitch_design(spec: dict) -> dict:
    """Solved cones, cutter, and the index pose. Cached on the numeric inputs."""
    return _pitch_cached(_pitch_key(spec))


@functools.lru_cache(maxsize=16)
def _pitch_cached(key: tuple) -> dict:
    (
        z1,
        z2,
        offset,
        shaft_deg,
        diameter,
        face,
        spiral_deg,
        drive_deg,
        coast_deg,
        cutter_radius,
        point_width,
        edge_radius,
    ) = key
    design = _solve_pitch(z1, z2, offset, shaft_deg, diameter / 2.0, math.radians(spiral_deg))
    _proportion(design, face)
    _place_cutter(design, cutter_radius, point_width, edge_radius, drive_deg, coast_deg)
    _index_pose(design)
    return design


def _solve_pitch(z1: int, z2: int, offset: float, shaft_deg: float, rm2: float, beta2: float) -> dict:
    shaft = math.radians(shaft_deg)
    k1 = (math.sin(shaft), 0.0, math.cos(shaft))
    k2 = (0.0, 0.0, 1.0)
    origin_a = (0.0, offset, 0.0)
    ratio = z2 / float(z1)
    delta1_ref = math.atan(math.sin(shaft) / (ratio + math.cos(shaft)))
    delta2_ref = shaft - delta1_ref
    h_ref = rm2 / math.tan(delta2_ref) if abs(math.tan(delta2_ref)) > 1.0e-6 else 0.0
    seeds = []
    for xi in (0.0, 0.6, -0.6):
        for cos_sign in (1.0, -1.0):
            for sigma in (1.0, -1.0):
                for h0 in (h_ref, 0.0, -h_ref * 0.5):
                    for d2 in (delta2_ref, min(delta2_ref + 0.35, 2.2), max(delta2_ref - 0.35, 0.4)):
                        seeds.append((xi, h0, d2, cos_sign, sigma))
    best = None
    seen = []
    for xi, h0, d2, cos_sign, sigma in seeds:
        solved = _newton_pitch(
            z1, z2, rm2, beta2, ratio, k1, k2, origin_a, xi, h0, d2, cos_sign, sigma
        )
        if solved is None:
            continue
        seen.append(solved)
        rank = _pitch_rank(solved, delta1_ref, delta2_ref)
        if best is None or rank < best[0]:
            best = (rank, solved)
    if best is None or best[0] >= 1.0e8:
        raise GeometryError(
            "No hypoid pitch point fits this offset, shaft angle, ring diameter, and spiral. "
            "A smaller offset or a milder spiral angle still has a tangent cone."
        )
    solved = best[1]
    solved.update(
        {
            "z1": z1,
            "z2": z2,
            "ratio": ratio,
            "offset_mm": offset,
            "shaft_deg": shaft_deg,
            "k1": k1,
            "k2": k2,
            "A1": origin_a,
            "beta2": beta2,
            "delta1_ref_deg": math.degrees(delta1_ref),
            "delta2_ref_deg": math.degrees(delta2_ref),
        }
    )
    _axes(solved)
    return solved


def _pitch_rank(solved: dict, delta1_ref: float, delta2_ref: float) -> float:
    if solved["err"] > 1.0e-7:
        return 1.0e9
    delta1 = solved["delta1"]
    delta2 = solved["delta2"]
    beta1 = solved["beta1"]
    if not (math.radians(4.0) < delta1 < math.radians(80.0)):
        return 1.0e9
    if not (math.radians(25.0) < delta2 < math.radians(145.0)):
        return 1.0e9
    if beta1 * solved["beta2"] >= 0:
        return 1.0e9
    if abs(beta1) > math.radians(72.0) or abs(math.cos(beta1)) < 0.25:
        return 1.0e9
    if solved["rm1"] < solved["mn"] * 0.8:
        return 1.0e9
    angle_gap = abs(delta1 - delta1_ref) + abs(min(delta2, math.pi - delta2) - min(delta2_ref, math.pi - delta2_ref))
    return solved["sliding"] + 0.15 * solved["rm2"] * angle_gap


def _newton_pitch(z1, z2, rm2, beta2, ratio, k1, k2, origin_a, xi, h, delta2, cos_sign, sigma):
    def residual(xi_value, h_value, d2_value):
        return _pitch_residual(
            z1, z2, rm2, beta2, ratio, k1, k2, origin_a, xi_value, h_value, d2_value, cos_sign, sigma
        )

    for _ in range(22):
        current = residual(xi, h, delta2)
        if current is None:
            return None
        error = current["res"]
        if _norm(error) < 1.0e-10:
            break
        step = 1.0e-6
        bumped = []
        for index in range(3):
            trial = [xi, h, delta2]
            trial[index] += step
            bumped_state = residual(*trial)
            if bumped_state is None:
                return None
            bumped.append(_scale(_sub(bumped_state["res"], error), 1.0 / step))
        dxi, dh, dd = _least_squares_n(error, bumped)
        jump = math.sqrt(dxi * dxi + dh * dh + dd * dd)
        if jump > 0.35:
            scale = 0.35 / jump
            dxi *= scale
            dh *= scale
            dd *= scale
        xi += dxi
        h += dh
        delta2 = min(max(delta2 + dd, 0.12), math.pi - 0.12)
        while xi > math.pi:
            xi -= 2.0 * math.pi
        while xi < -math.pi:
            xi += 2.0 * math.pi
        if abs(h) > rm2 * 8.0:
            return None
    current = residual(xi, h, delta2)
    if current is None or _norm(current["res"]) > 1.0e-6:
        return None
    current["err"] = _norm(current["res"])
    current["cos_sign"] = cos_sign
    current["sigma"] = sigma
    current["xi"] = xi
    return current


def _pitch_residual(z1, z2, rm2, beta2, ratio, k1, k2, origin_a, xi, h, delta2, cos_sign, sigma):
    r2 = (math.cos(xi), math.sin(xi), 0.0)
    c2 = _cross(k2, r2)
    g2 = _add(_scale(r2, math.sin(delta2)), _scale(k2, math.cos(delta2)))
    n2 = _add(_scale(r2, -math.cos(delta2)), _scale(k2, math.sin(delta2)))
    trace = _add(_scale(g2, math.cos(beta2)), _scale(c2, math.sin(beta2)))
    trace_length = _norm(trace)
    if trace_length < 1.0e-12:
        return None
    trace = _scale(trace, 1.0 / trace_length)
    pitch_point = _add(_scale(r2, rm2), (0.0, 0.0, h))
    # External contact: the pinion normal is opposite the ring normal.
    sine1 = -_dot(n2, k1)
    if abs(sine1) > 0.999:
        return None
    cosine1 = cos_sign * math.sqrt(max(0.0, 1.0 - sine1 * sine1))
    if abs(cosine1) < 1.0e-8:
        return None
    delta1 = math.atan2(sine1, cosine1)
    radial = _scale(_add(n2, _scale(k1, sine1)), 1.0 / cosine1)
    if abs(_norm(radial) - 1.0) > 1.0e-6:
        return None
    along = _dot(_sub(pitch_point, origin_a), k1)
    foot = _add(origin_a, _scale(k1, along))
    arm = _sub(pitch_point, foot)
    rm1 = _norm(arm)
    if rm1 < 1.0e-6:
        return None
    radial_geom = _scale(arm, 1.0 / rm1)
    g1 = _add(_scale(radial_geom, math.sin(delta1)), _scale(k1, math.cos(delta1)))
    reference = _cross(_scale(n2, -1.0), g1)
    reference_length = _norm(reference)
    if reference_length < 1.0e-8:
        return None
    reference = _scale(reference, 1.0 / reference_length)
    beta1 = math.atan2(_dot(trace, reference), _dot(trace, g1))
    if abs(math.cos(beta1)) < 1.0e-6 or abs(math.cos(beta2)) < 1.0e-6:
        return None
    mn2 = 2.0 * rm2 * math.cos(beta2) / z2
    mn1 = 2.0 * rm1 * math.cos(beta1) / z1
    velocity2 = _cross(k2, _sub(pitch_point, (0.0, 0.0, h)))
    velocity1 = _scale(_cross(k1, arm), sigma * ratio)
    relative = _sub(velocity1, velocity2)
    speed = _norm(relative)
    if speed < 1.0e-9:
        return None
    parallel = _scale(_cross(relative, trace), 1.0 / speed)
    align = _sub(radial, radial_geom)
    module = ((mn1 - mn2) / mn2,)
    residual = align + parallel + module
    return {
        "res": residual,
        "delta1": delta1,
        "delta2": delta2,
        "beta1": beta1,
        "beta2": beta2,
        "rm1": rm1,
        "rm2": rm2,
        "mn": mn2,
        "h": h,
        "P": pitch_point,
        "n2": n2,
        "t": trace,
        "g2": g2,
        "g1": g1,
        "r2": r2,
        "sigma": sigma,
        "sliding": speed,
        "Q1": foot,
    }


def _least_squares_n(error, columns):
    """Solve J dx = -error in the least-squares sense. ``columns`` are J's columns."""
    size = len(columns)
    matrix = [[_dot(columns[row], columns[col]) for col in range(size)] for row in range(size)]
    rhs = [-_dot(column, error) for column in columns]
    return _solve_symmetric(matrix, rhs)


def _solve_symmetric(matrix, rhs):
    size = len(rhs)
    rows = [list(matrix[index]) + [rhs[index]] for index in range(size)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(rows[row][col]))
        if abs(rows[pivot][col]) < 1.0e-18:
            return tuple(0.0 for _ in range(size))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = rows[col][col]
        for index in range(col, size + 1):
            rows[col][index] /= scale
        for row in range(size):
            if row == col:
                continue
            factor = rows[row][col]
            if factor == 0.0:
                continue
            for index in range(col, size + 1):
                rows[row][index] -= factor * rows[col][index]
    return tuple(rows[index][size] for index in range(size))


def _linspace(start: float, stop: float, count: int) -> list[float]:
    if count < 2:
        return [start]
    return [start + (stop - start) * index / (count - 1) for index in range(count)]


def _axes(design: dict) -> None:
    k1 = design["k1"]
    toward = _unit(_scale(design["A1"], -1.0))
    toward = _sub(toward, _scale(k1, _dot(toward, k1)))
    y_axis = _unit(toward, "offset")
    x_axis = _unit(_cross(y_axis, k1), "pinion x")
    z_axis = k1
    # Right-handed: x × y = z.
    if _dot(_cross(x_axis, y_axis), z_axis) < 0:
        x_axis = _scale(x_axis, -1.0)
    design["x_b"] = x_axis
    design["y_b"] = y_axis
    design["z_b"] = z_axis
    length = design["rm2"] / math.sin(design["delta2"])
    design["L2"] = length
    design["apex2"] = _sub(design["P"], _scale(design["g2"], length))
    length1 = design["rm1"] / math.sin(design["delta1"])
    design["L1"] = length1
    design["apex1"] = _sub(design["P"], _scale(design["g1"], length1))


def _proportion(design: dict, face: float) -> None:
    module = design["mn"]
    if module <= 0:
        raise GeometryError("The hypoid normal module is not positive.")
    # Working depth follows the spiral-bevel split loosely: the ring addendum
    # is the smaller one. Clearance is extra depth past the mate tip.
    addendum_ring = 0.85 * module
    addendum_pinion = 1.00 * module
    clearance = 0.30 * module
    design["face"] = face
    design["ha_ring"] = addendum_ring
    design["ha_pinion"] = addendum_pinion
    design["clearance"] = clearance
    design["hf_ring"] = addendum_pinion + clearance
    design["hf_pinion"] = addendum_ring + clearance
    if face >= design["L2"] * 0.8:
        raise GeometryError("face_width_mm is too long for the ring pitch cone.")
    design["theta_face"] = _face_angle(design, face)


def _face_angle(design: dict, face: float) -> float:
    """Cutter angle that reaches half the face, found once the cutter exists.

    A placeholder span is refined in ``_place_cutter``. This first value only
    has to be positive.
    """
    return max(face / max(design["rm2"], 1.0), 0.05)


def _cone_distance(point, design: dict) -> float:
    vector = _sub(point, design["apex2"])
    rho = math.hypot(vector[0], vector[1])
    if rho < 1.0e-9:
        return 0.0
    radial = (vector[0] / rho, vector[1] / rho, 0.0)
    delta = design["delta2"]
    generator = (math.sin(delta) * radial[0], math.sin(delta) * radial[1], math.cos(delta))
    return _dot(vector, generator)


# --- face-mill cutter ------------------------------------------------------


def _place_cutter(design, radius, point_width, edge_radius, drive_deg, coast_deg) -> None:
    module = design["mn"]
    if radius is None:
        # A face-mill circle large beside the face keeps the blade from crossing the tooth.
        radius = max(design["rm2"] * 1.05, design["face"] * 3.0)
    if radius < design["face"] * 0.55:
        raise GeometryError("cutter.radius_mm is shorter than the face. The blade arc would hook inside the tooth.")
    if point_width is None:
        point_width = math.pi * module / 2.0
    normal_pitch = math.pi * module
    if point_width >= normal_pitch * 0.92:
        raise GeometryError("cutter.point_width_mm leaves no ring tooth. It has to stay under the normal circular pitch.")
    if point_width >= 2.0 * radius:
        raise GeometryError("cutter.point_width_mm is wider than the cutter diameter.")
    if edge_radius is None:
        edge_radius = 0.12 * module
    if edge_radius > 0.4 * module:
        raise GeometryError("cutter.edge_radius_mm is larger than the tooth depth can hold.")
    design["cutter_radius"] = radius
    design["point_width"] = point_width
    design["edge_radius"] = edge_radius
    design["drive_deg"] = drive_deg
    design["coast_deg"] = coast_deg
    pitch = design["P"]
    normal = _unit(design["n2"], "pitch normal")
    trace = design["t"]
    # From the cutter centre, through the pitch point, perpendicular to the tooth trace.
    across = _unit(_cross(normal, trace), "tooth normal")
    design["q"] = across
    center = _sub(pitch, _scale(across, radius))
    around = _unit(_cross(normal, across), "cutter")
    drive = _blade_cone(center, normal, across, around, radius + point_width / 2.0, math.radians(drive_deg), "outer")
    coast = _blade_cone(center, normal, across, around, radius - point_width / 2.0, math.radians(coast_deg), "inner")
    _check_blade(drive, _add(pitch, _scale(across, point_width / 2.0)), _scale(across, -1.0))
    _check_blade(coast, _sub(pitch, _scale(across, point_width / 2.0)), across)
    design["drive_cone"] = drive
    design["coast_cone"] = coast
    design["theta_face"] = _theta_for_face(design)


def _blade_cone(center, axis, across, around, radius0: float, alpha: float, kind: str) -> dict:
    """One side of a face-mill blade. Inner and outer cones share the cutter axis.

    ``along`` is distance along the pitch normal, positive into the ring metal.
    The cones meet toward that side, so the tooth space is wider at the tip.
    """
    if radius0 <= 1.0:
        raise GeometryError("The face-mill blade radius is not positive. Reduce cutter.point_width_mm.")
    return {
        "center": center,
        "axis": _unit(axis),
        "across": _unit(across),
        "around": _unit(around),
        "radius0": radius0,
        "alpha": alpha,
        "kind": kind,
    }


def _check_blade(cone: dict, point, into_space) -> None:
    landed = _cone_point(cone, 0.0, 0.0)
    if _norm(_sub(landed, point)) > 1.0e-6:
        raise GeometryError("The face-mill blade does not pass through its pitch-plane point.")
    normal = _cone_normal(cone, 0.0, 0.0)
    # Out of the metal: mostly into the tooth space, tilted by the blade angle.
    wanted = _add(_scale(_unit(into_space), math.cos(cone["alpha"])), _scale(cone["axis"], -math.sin(cone["alpha"])))
    if _norm(_sub(normal, wanted)) > 1.0e-5:
        raise GeometryError("The face-mill blade normal does not match the pressure angle.")


def _blade_radius(cone: dict, along: float) -> float:
    taper = math.tan(cone["alpha"])
    if cone["kind"] == "outer":
        return cone["radius0"] - along * taper
    return cone["radius0"] + along * taper


def _cone_radial(cone: dict, theta: float):
    return _add(_scale(cone["across"], math.cos(theta)), _scale(cone["around"], math.sin(theta)))


def _cone_point(cone: dict, along: float, theta: float):
    radius = _blade_radius(cone, along)
    return _add(cone["center"], _add(_scale(cone["axis"], along), _scale(_cone_radial(cone, theta), radius)))


def _cone_normal(cone: dict, along: float, theta: float):
    radial = _cone_radial(cone, theta)
    alpha = cone["alpha"]
    if cone["kind"] == "outer":
        return _add(_scale(radial, -math.cos(alpha)), _scale(cone["axis"], -math.sin(alpha)))
    return _add(_scale(radial, math.cos(alpha)), _scale(cone["axis"], -math.sin(alpha)))


def _rotate_cone(cone: dict, angle: float) -> dict:
    return {
        "center": _rot_z(cone["center"], angle),
        "axis": _rot_z(cone["axis"], angle),
        "across": _rot_z(cone["across"], angle),
        "around": _rot_z(cone["around"], angle),
        "radius0": cone["radius0"],
        "alpha": cone["alpha"],
        "kind": cone["kind"],
    }


def _theta_for_face(design: dict) -> float:
    target = design["face"] / 2.0
    base = _cone_distance(_cone_point(design["drive_cone"], 0.0, 0.0), design)

    def gap(theta: float) -> float:
        return abs(_cone_distance(_cone_point(design["drive_cone"], 0.0, theta), design) - base)

    theta = target / design["cutter_radius"]
    for _ in range(12):
        current = gap(theta)
        if current < 1.0e-6:
            break
        derivative = (gap(theta + 1.0e-4) - current) / 1.0e-4
        if abs(derivative) < 1.0e-8:
            break
        theta -= (current - target) / derivative
        theta = min(max(theta, 0.02), 1.2)
    if gap(theta) > target * 0.25 + 0.2:
        theta = target / max(design["cutter_radius"], 1.0)
    return theta


def _gear_offset(point, design: dict) -> float:
    """Signed distance off the ring pitch cone. Positive is into the metal."""
    vector = _sub(point, design["apex2"])
    rho = math.hypot(vector[0], vector[1])
    axial = vector[2]
    delta = design["delta2"]
    outside = rho * math.cos(delta) - axial * math.sin(delta)
    return -outside


def _solve_along(cone: dict, theta: float, target: float, design: dict) -> float:
    def value(along: float) -> float:
        if _blade_radius(cone, along) <= 1.0:
            return math.copysign(1.0e6, along)
        return _gear_offset(_cone_point(cone, along, theta), design) - target

    lo, hi = -design["hf_ring"] * 4.0, design["ha_ring"] * 4.0
    flo, fhi = value(lo), value(hi)
    if flo * fhi > 0:
        # Widen once. A face station past the cone still has to land on the offset.
        lo, hi = -design["L2"], design["L2"]
        flo, fhi = value(lo), value(hi)
    if flo * fhi > 0:
        raise GeometryError(
            "The face-mill blade misses the tooth depth at this station. "
            "Increase cutter.radius_mm or shorten face_width_mm."
        )
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        fmid = value(mid)
        if abs(fmid) < 1.0e-9:
            return mid
        if flo * fmid <= 0:
            hi, fhi = mid, fmid
        else:
            lo, flo = mid, fmid
    return 0.5 * (lo + hi)


def _flank_samples(cone: dict, theta: float, design: dict, count: int, start=None, stop=None):
    """Points from a root offset to a tip offset, and their normals.

    Offsets are signed distances off the ring pitch cone. Positive is into
    the ring metal. The default runs from the ring dedendum to the ring tip.
    """
    if start is None:
        start = design["hf_ring"]
    if stop is None:
        stop = -design["ha_ring"]
    points = []
    normals = []
    for index in range(count):
        target = start + (stop - start) * index / (count - 1)
        along = _solve_along(cone, theta, target, design)
        points.append(_cone_point(cone, along, theta))
        normals.append(_cone_normal(cone, along, theta))
    return points, normals


# --- envelope and solids ---------------------------------------------------


def _vrel(point, design: dict):
    velocity_gear = _cross(design["k2"], point)
    velocity_pinion = _scale(_cross(design["k1"], _sub(point, design["A1"])), design["sigma"] * design["ratio"])
    return _sub(velocity_pinion, velocity_gear)


def _mesh_angle(point, normal, design: dict) -> float:
    def value(angle: float) -> float:
        moved = _rot_z(point, angle)
        turned = _rot_z(normal, angle)
        return _dot(turned, _vrel(moved, design))

    lo, hi = -0.85, 0.85
    samples = 28
    previous_angle = lo
    previous = value(lo)
    bracket = None
    closest = (abs(previous), lo)
    for index in range(1, samples + 1):
        angle = lo + (hi - lo) * index / samples
        current = value(angle)
        closest = min(closest, (abs(current), angle))
        if previous * current <= 0:
            bracket = (previous_angle, angle, previous, current)
            break
        previous_angle, previous = angle, current
    if bracket is None:
        if closest[0] > 1.0e-2 * max(design["rm2"], 1.0):
            raise GeometryError(
                "A flank point never satisfies the cutting mesh. "
                "Increase cutter.radius_mm or shorten the face."
            )
        return closest[1]
    left, right, f_left, f_right = bracket
    for _ in range(28):
        mid = 0.5 * (left + right)
        f_mid = value(mid)
        if abs(f_mid) < 1.0e-10:
            return mid
        if f_left * f_mid <= 0:
            right, f_right = mid, f_mid
        else:
            left, f_left = mid, f_mid
    return 0.5 * (left + right)


def _index_pose(design: dict) -> None:
    cone = design["drive_cone"]
    origin = _cone_point(cone, 0.0, 0.0)
    normal = _cone_normal(cone, 0.0, 0.0)
    phi = _mesh_angle(origin, normal, design)
    design["phi_star"] = phi
    design["psi_star"] = design["sigma"] * design["ratio"] * phi


def _shift_inward(point, outward, amount: float):
    return _sub(point, _scale(_unit(outward, "flank normal"), amount))


def _pinion_apex_z(design: dict) -> float:
    local = _sub(design["apex1"], design["A1"])
    body = (_dot(local, design["x_b"]), _dot(local, design["y_b"]), _dot(local, design["z_b"]))
    if math.hypot(body[0], body[1]) > 0.05:
        raise GeometryError("The pinion pitch apex is not on the pinion axis.")
    return body[2]


def _offset_about_z(point, apex_z: float, delta: float) -> float:
    """Signed distance off a pitch cone about Z. Positive is into the metal."""
    rho = math.hypot(point[0], point[1])
    axial = point[2] - apex_z
    outside = rho * math.cos(delta) - axial * math.sin(delta)
    return -outside


def _point_about_z(angle: float, z_value: float, target: float, apex_z: float, delta: float):
    cosine = math.cos(delta)
    if abs(cosine) < 1.0e-6:
        raise GeometryError("The pitch cone is flat, so the blank radius is not determined.")
    # target is positive into the metal, which is toward the axis from the pitch cone.
    rho = (-target + (z_value - apex_z) * math.sin(delta)) / cosine
    if rho <= 0.3:
        raise GeometryError(
            "The hypoid blank crosses its axis. Shorten face_width_mm or reduce the tooth depth."
        )
    return (rho * math.cos(angle), rho * math.sin(angle), z_value)


def _arc(start_angle, end_angle, z_start, z_end, count, target, apex_z, delta):
    """Interior points strictly between the two flank ends."""
    if count <= 0:
        return []
    points = []
    span = end_angle - start_angle
    for index in range(1, count + 1):
        fraction = index / (count + 1)
        points.append(
            _point_about_z(
                start_angle + span * fraction,
                z_start + (z_end - z_start) * fraction,
                target,
                apex_z,
                delta,
            )
        )
    return points


def _station_counts(design: dict) -> tuple[int, int, int, int]:
    radius = design["cutter_radius"]
    arc = 2.0 * design["theta_face"]
    step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - _SAGITTA_MM / radius)))
    face_count = int(math.ceil(arc / max(step, 1.0e-3))) + 1
    face_count = max(7, min(face_count, 11))
    return face_count, 8, 2, 3


def _tooth_flanks(design: dict, theta: float, pinion: bool, bias=None, start=None, stop=None, flank_count=None):
    """Two flanks of one tooth at one station along the face.

    The ring space is one cutter pose. The tooth between that space and the
    next is bounded by one blade of each pose. Points run from root to tip
    in the member frame. ``bias`` moves each point along the outward normal,
    into the metal when positive.
    """
    if pinion:
        raise GeometryError("The pinion is cut from a blank. It has no section loft.")
    if bias is None:
        bias = _BIAS_MM
    pitch = 2.0 * math.pi / design["z2"]
    count = _station_counts(design)[1] if flank_count is None else flank_count
    selected = (
        _rotate_cone(design["drive_cone"], 0.0),
        _rotate_cone(design["coast_cone"], 0.0),
        _rotate_cone(design["drive_cone"], pitch),
        _rotate_cone(design["coast_cone"], pitch),
    )
    options = []
    for index, cone in enumerate(selected):
        points, normals = _flank_samples(cone, theta, design, count, start, stop)
        points = [_shift_inward(point, normal, bias) for point, normal in zip(points, normals)]
        options.append((points, normals, index // 2))
    return _pick_tooth(options, pitch)


def _pick_tooth(options, pitch: float):
    """Choose the blade pair that bounds one tooth, ordered by polar angle."""
    tips = []
    for points, normals, space in options:
        tips.append(_polar(points[-1]))
    best = None
    for left in range(4):
        for right in range(4):
            if options[left][2] == options[right][2]:
                continue
            span = _unwrap(tips[right], tips[left]) - tips[left]
            # A copy of the same blade, one tooth over, is exactly one pitch.
            # The metal between two different blades is shorter than that.
            if span <= 0.02 or span >= pitch - 1.0e-3:
                continue
            score = abs(span - pitch * 0.5)
            if best is None or score < best[0]:
                best = (score, left, right)
    if best is None:
        raise GeometryError(
            "The face-mill blades do not bound a tooth. Increase cutter.radius_mm or reduce the point width."
        )
    left, right = best[1], best[2]
    return [(options[left][0], options[left][1]), (options[right][0], options[right][1])]


def _profile_from_flanks(
    flanks, apex_z: float, delta: float, tooth_pitch: float, tip_target: float, root_target: float, counts, across_space: bool = True
):
    left, right = flanks
    left_points, right_points = left[0], right[0]
    # Walk counter-clockwise: the right tip is ahead of the left tip by less than one pitch.
    left_tip = _polar(left_points[-1])
    right_tip = _unwrap(_polar(right_points[-1]), left_tip)
    if not 0.0 < right_tip - left_tip < tooth_pitch:
        left_points, right_points = right_points, left_points
        left_tip = _polar(left_points[-1])
        right_tip = _unwrap(_polar(right_points[-1]), left_tip)
    if not 0.0 < right_tip - left_tip < tooth_pitch:
        raise GeometryError(
            "The two flanks do not bound one tooth. Increase cutter.radius_mm or shorten face_width_mm."
        )
    left_root = _unwrap(_polar(left_points[0]), left_tip)
    right_root = _unwrap(_polar(right_points[0]), left_tip)
    _face_n, _flank_n, tip_n, root_n = counts
    tip = _arc(
        left_tip,
        right_tip,
        left_points[-1][2],
        right_points[-1][2],
        tip_n,
        tip_target,
        apex_z,
        delta,
    )
    # Across the space, the root runs on to the next tooth. A cutting tooth
    # closes on itself, along the short root between its own flanks.
    end_angle = left_root + tooth_pitch if across_space else left_root
    root = _arc(
        right_root,
        end_angle,
        right_points[0][2],
        left_points[0][2],
        root_n,
        root_target,
        apex_z,
        delta,
    )
    down = list(reversed(right_points))
    profile = list(left_points) + tip + down + root
    _assert_progress(profile)
    return profile


def _assert_progress(profile) -> None:
    for previous, point in zip(profile, profile[1:]):
        if _norm(_sub(point, previous)) < 1.0e-6:
            raise GeometryError("The tooth outline folded back on itself. Increase cutter.radius_mm.")


def _tile(profile, teeth: int, turn):
    step = 2.0 * math.pi / teeth
    ring = []
    for tooth in range(teeth):
        angle = tooth * step
        for point in profile:
            ring.append(turn(point, angle))
    return ring


def _align_tooth_phase(rings, teeth: int):
    """Rotate each station by whole teeth so the outline does not jump along the face."""
    per = len(rings[0]) // teeth
    aligned = [rings[0]]
    reference = _polar(rings[0][0])
    for ring in rings[1:]:
        best = None
        for shift in range(teeth):
            spun = ring[shift * per :] + ring[: shift * per]
            angle = _unwrap(_polar(spun[0]), reference)
            gap = abs(angle - reference)
            if best is None or gap < best[0]:
                best = (gap, spun, angle)
        aligned.append(best[1])
        reference = best[2]
    return aligned


def _closed_tooth_profiles(design: dict, thetas, bias: float, tip_target: float, root_target: float, counts):
    """One ring tooth at each cutter angle, closed across its own root."""
    apex_z = design["apex2"][2]
    pitch = 2.0 * math.pi / design["z2"]
    profiles = []
    for theta in thetas:
        flanks = _tooth_flanks(
            design,
            theta,
            False,
            bias=bias,
            start=root_target,
            stop=tip_target,
            flank_count=counts[1],
        )
        profiles.append(
            _profile_from_flanks(
                flanks,
                apex_z,
                design["delta2"],
                pitch,
                tip_target,
                root_target,
                counts,
                across_space=False,
            )
        )
    return profiles


def ring_pitch_flank_points(spec: dict) -> list[tuple[float, float, float]]:
    """Ring flank points at the pitch cone, three stations along the face.

    Both flanks of the generated tooth are included, shifted into the metal by
    the same bias the solid uses. Coordinates are in the ring frame, before
    the mesh placement. The stations are ordered from one end of the face,
    through the middle, to the other end.
    """
    # A mesh record has pinion_teeth and a cutter, but not ring_diameter_mm.
    # Only a normalized hypoid spec has both, and that is what the pitch solve reads.
    ready = (
        "ring_diameter_mm" in spec
        and "pinion_teeth" in spec
        and isinstance(spec.get("cutter"), dict)
    )
    clean = spec if ready else normalize_hypoid(spec)
    design = pitch_design(clean)
    thetas = (-0.6 * design["theta_face"], 0.0, 0.6 * design["theta_face"])
    points: list[tuple[float, float, float]] = []
    for theta in thetas:
        flanks = _tooth_flanks(
            design,
            theta,
            False,
            bias=_BIAS_MM,
            start=0.0,
            stop=0.0,
            flank_count=2,
        )
        for flank in flanks:
            points.append(tuple(flank[0][0]))
    return points


def ring_tooth_rings(design: dict) -> list[list[tuple[float, float, float]]]:
    """The ring tooth the solid is lofted from. Flanks are set in by the bias."""
    face_n, flank_n, tip_n, _root_n = _station_counts(design)
    counts = (face_n, flank_n, tip_n, 2)
    thetas = _linspace(-design["theta_face"], design["theta_face"], face_n)
    return _closed_tooth_profiles(
        design, thetas, _BIAS_MM, -design["ha_ring"], design["hf_ring"] + 0.6, counts
    )


def ring_core_rings(design: dict, z_start: float, z_end: float) -> list[list[tuple[float, float, float]]]:
    """Root cone of the ring. The tooth root reaches past it so the two overlap."""
    apex_z = design["apex2"][2]
    delta = design["delta2"]
    # Deeper than the design root, so the pinion tip keeps its clearance.
    # The tooth root reaches past this surface and the two solids overlap.
    target = design["hf_ring"] + 0.25
    count = 48
    rings = []
    for z_value in (z_start, z_end):
        rings.append(
            [
                _point_about_z(2.0 * math.pi * index / count, z_value, target, apex_z, delta)
                for index in range(count)
            ]
        )
    return rings


def tool_tooth_rings(design: dict) -> list[list[tuple[float, float, float]]]:
    """One ring tooth, closed on its root and slightly larger than the blade.

    The extra stock is what the pinion blank loses. The real ring tooth is
    smaller by ``_BIAS_MM``, so the two keep a gap after the cut.
    """
    counts = (7, 6, 2, 2)
    extra = min(0.05, design["theta_face"] * 0.3)
    thetas = _linspace(-design["theta_face"] - extra, design["theta_face"] + extra, counts[0])
    # Longer than the real tip, so the pinion root is deeper than the ring tip.
    tip_target = -design["ha_ring"] - design["clearance"]
    root_target = design["hf_ring"] + 0.6
    return _closed_tooth_profiles(design, thetas, -_TOOL_STOCK_MM, tip_target, root_target, counts)


def pinion_blank_rings(design: dict) -> list[list[tuple[float, float, float]]]:
    """Tip cone of the pinion, from toe to heel, filled to the axis."""
    apex_z = _pinion_apex_z(design)
    delta = design["delta1"]
    half = design["face"] / 2.0
    count = 48
    rings = []
    for cone_distance in (design["L1"] - half, design["L1"] + half):
        z_value = apex_z + cone_distance * math.cos(delta)
        rings.append(
            [
                _point_about_z(
                    2.0 * math.pi * index / count,
                    z_value,
                    -design["ha_pinion"],
                    apex_z,
                    delta,
                )
                for index in range(count)
            ]
        )
    return rings


def roll_angles(design: dict) -> list[float]:
    """Gear angles, around the index pose, at which the tooth is subtracted."""
    pitch = 2.0 * math.pi / design["z2"]
    half = _ROLL_HALF_PITCHES * pitch
    step = math.radians(_ROLL_STEP_DEG)
    count = int(math.ceil((2.0 * half) / step)) + 1
    return [design["phi_star"] - half + (2.0 * half) * index / (count - 1) for index in range(count)]


def map_to_pinion(point, phi: float, design: dict):
    """Ring-frame point to the pinion body frame after a conjugate roll ``phi``."""
    world = _rot_z(point, phi)
    psi = design["sigma"] * design["ratio"] * phi
    local = _rot_axis(_sub(world, design["A1"]), design["k1"], -psi)
    return (
        _dot(local, design["x_b"]),
        _dot(local, design["y_b"]),
        _dot(local, design["z_b"]),
    )


def pinion_trsf(phi: float, design: dict):
    """Rigid map of the ring frame into the pinion body frame at gear angle ``phi``."""
    from OCP.gp import gp_Trsf

    origin = map_to_pinion((0.0, 0.0, 0.0), phi, design)
    column_x = _sub(map_to_pinion((1.0, 0.0, 0.0), phi, design), origin)
    column_y = _sub(map_to_pinion((0.0, 1.0, 0.0), phi, design), origin)
    column_z = _sub(map_to_pinion((0.0, 0.0, 1.0), phi, design), origin)
    transform = gp_Trsf()
    transform.SetValues(
        column_x[0], column_y[0], column_z[0], origin[0],
        column_x[1], column_y[1], column_z[1], origin[1],
        column_x[2], column_y[2], column_z[2], origin[2],
    )
    return transform


def member_rings(spec: dict) -> list[list[tuple[float, float, float]]]:
    """Closed outlines along the face, one ring per station, in the member frame."""
    design = pitch_design(spec)
    counts = _station_counts(design)
    thetas = _linspace(-design["theta_face"], design["theta_face"], counts[0])
    if spec["role"] == "pinion":
        raise GeometryError("The pinion solid is the blank left by the rolled tooth, not a loft of sections.")
    apex_z = design["apex2"][2]
    delta = design["delta2"]
    teeth = design["z2"]
    profiles = []
    for theta in thetas:
        flanks = _tooth_flanks(design, theta, False)
        profiles.append(
            _profile_from_flanks(
                flanks,
                apex_z,
                delta,
                2.0 * math.pi / teeth,
                -design["ha_ring"],
                design["hf_ring"],
                counts,
            )
        )
    rings = [_tile(profile, teeth, _rot_z) for profile in profiles]
    return _align_tooth_phase(rings, teeth)


def root_radius(spec: dict) -> float:
    """Smallest cylindrical radius of the root surface, for the bore check."""
    design = pitch_design(spec)
    if spec["role"] == "pinion":
        apex_z = _pinion_apex_z(design)
        delta = design["delta1"]
        target = design["hf_pinion"]
        length = design["L1"]
    else:
        apex_z = design["apex2"][2]
        delta = design["delta2"]
        target = design["hf_ring"]
        length = design["L2"]
    radii = []
    for fraction in (0.15, 0.5, 0.85):
        # z of the pitch cone at this cone distance, then the root radius there.
        cone_distance = length - design["face"] / 2.0 + design["face"] * fraction
        z_pitch = apex_z + cone_distance * math.cos(delta)
        point = _point_about_z(0.0, z_pitch, target, apex_z, delta)
        radii.append(math.hypot(point[0], point[1]))
    return min(radii)


def solid_triangles(spec: dict) -> list[tuple]:
    rings = member_rings(spec)
    if len(rings) < 2:
        raise GeometryError("The hypoid face needs at least two stations.")
    count = len(rings[0])
    if any(len(ring) != count for ring in rings):
        raise GeometryError("The hypoid stations do not share an outline.")
    interior = _scale(
        tuple(sum(point[axis] for ring in rings for point in ring) for axis in range(3)),
        1.0 / (len(rings) * count),
    )
    z_start = sum(point[2] for point in rings[0]) / count
    z_end = sum(point[2] for point in rings[-1]) / count
    span = max(abs(z_end - z_start), 0.5)
    if z_start <= z_end:
        start_center = (0.0, 0.0, z_start - 0.04 * span)
        end_center = (0.0, 0.0, z_end + 0.04 * span)
    else:
        start_center = (0.0, 0.0, z_start + 0.04 * span)
        end_center = (0.0, 0.0, z_end - 0.04 * span)
    triangles = []

    def wind(a, b, c):
        normal = _cross(_sub(b, a), _sub(c, a))
        if _norm(normal) < 1.0e-16:
            return None
        midpoint = _scale(_add(_add(a, b), c), 1.0 / 3.0)
        if _dot(normal, _sub(midpoint, interior)) < 0:
            return (a, c, b)
        return (a, b, c)

    for ring, center in ((rings[0], start_center), (rings[-1], end_center)):
        for index in range(count):
            nxt = (index + 1) % count
            triangle = wind(center, ring[index], ring[nxt])
            if triangle is not None:
                triangles.append(triangle)
    for band in range(len(rings) - 1):
        inner = rings[band]
        outer = rings[band + 1]
        for index in range(count):
            nxt = (index + 1) % count
            first = wind(outer[index], outer[nxt], inner[nxt])
            second = wind(outer[index], inner[nxt], inner[index])
            if first is not None:
                triangles.append(first)
            if second is not None:
                triangles.append(second)
    if len(triangles) < 8:
        raise GeometryError("The hypoid tooth did not produce a surface.")
    return triangles


# --- reports and placement -------------------------------------------------


def compute_hypoid(spec: dict) -> dict:
    design = pitch_design(spec)
    role = spec["role"]
    warnings = []
    if abs(spec["offset_mm"]) > 0.35 * (spec["ring_diameter_mm"] / 2.0):
        warnings.append("The offset is more than about a third of the ring radius. The pinion spiral gets steep.")
    if abs(spec["spiral_angle_deg"]) > 50.0:
        warnings.append("The ring spiral is above 50 degrees.")
    if spec["pinion_teeth"] < 7:
        warnings.append("A pinion with fewer than 7 teeth is easily undercut by this blade.")
    if role == "pinion":
        pitch_angle = math.degrees(design["delta1"])
        spiral = math.degrees(design["beta1"])
        radius = design["rm1"]
        addendum = design["ha_pinion"]
        dedendum = design["hf_pinion"]
    else:
        pitch_angle = math.degrees(design["delta2"])
        spiral = math.degrees(design["beta2"])
        radius = design["rm2"]
        addendum = design["ha_ring"]
        dedendum = design["hf_ring"]
    return {
        "type": "hypoid",
        "axis_layout": "offset",
        "role": role,
        "teeth": spec["teeth"],
        "mate_teeth": spec["mate_teeth"],
        "pinion_teeth": spec["pinion_teeth"],
        "ring_teeth": spec["ring_teeth"],
        "ratio": spec["ratio"],
        "offset_mm": spec["offset_mm"],
        "shaft_angle_deg": spec["shaft_angle_deg"],
        "hand": spec["hand"],
        "ring_hand": spec["ring_hand"],
        "pinion_hand": "right" if spec["ring_hand"] == "left" else "left",
        "spiral_angle_deg": spiral,
        "ring_spiral_angle_deg": spec["spiral_angle_deg"],
        "pinion_spiral_angle_deg": math.degrees(design["beta1"]),
        "pressure_angle_deg": spec["pressure_angle_deg"],
        "drive_pressure_angle_deg": spec["drive_pressure_angle_deg"],
        "coast_pressure_angle_deg": spec["coast_pressure_angle_deg"],
        "pitch_angle_deg": pitch_angle,
        "pinion_pitch_angle_deg": math.degrees(design["delta1"]),
        "ring_pitch_angle_deg": math.degrees(design["delta2"]),
        "normal_module_mm": design["mn"],
        "module_mm": design["mn"],
        "pitch_diameter_mm": 2.0 * radius,
        "reference_diameter_mm": 2.0 * radius,
        "ring_pitch_diameter_mm": spec["ring_diameter_mm"],
        "pinion_pitch_diameter_mm": 2.0 * design["rm1"],
        "face_width_mm": spec["face_width_mm"],
        "addendum_mm": addendum,
        "dedendum_mm": dedendum,
        "clearance_mm": design["clearance"],
        "whole_depth_mm": addendum + dedendum,
        "cutter": {
            "process": "face_mill",
            "radius_mm": design["cutter_radius"],
            "point_width_mm": design["point_width"],
            "edge_radius_mm": design["edge_radius"],
        },
        "assembly": _assembly_record(design),
        "warnings": warnings,
        "standards": [],
        "construction": (
            "Face-mill generation. The ring flank is the cone of one circular blade. "
            "The pinion is the blank left after that same tooth is rolled about the "
            "offset shafts at the tooth ratio and cut out. The ring flank is set into "
            "the metal, and the cutting tooth is set out into the space, so a flat "
            "facet of the blade cannot cross the mate."
        ),
        "assumptions": [
            "The pitch point is the least-sliding cone that shares a tangent plane and a normal module at the ring radius.",
            "spiral_angle_deg is the ring mean spiral. The pinion spiral is a result, and the hands are opposite.",
            "ratio is ring teeth divided by pinion teeth.",
            "offset_mm is the shortest distance from the ring axis to the pinion axis. Positive puts the pinion axis on +Y.",
            "ring_diameter_mm is the ring mean pitch diameter.",
            "Drive and coast blade angles default to pressure_angle_deg.",
            "point_width_mm is the gap between the blade faces on the pitch plane. The default is half the normal circular pitch.",
            "The ring flank moves into the metal and the cutting tooth moves into the space. The nominal gap is those two shifts.",
            "The pinion root is the path of the ring tip, plus clearance. The edge radius is recorded and is not a generated trochoid.",
            "This is a face-mill blade cone, not a face-hob with a timed blade group, and not a spiral bevel moved sideways.",
        ],
    }


def _assembly_record(design: dict) -> dict:
    return {
        "pinion_axis_point_mm": list(design["A1"]),
        "pinion_axis_direction": list(design["k1"]),
        "gear_axis_point_mm": [0.0, 0.0, 0.0],
        "gear_axis_direction": [0.0, 0.0, 1.0],
        "pinion_x_axis": list(design["x_b"]),
        "pinion_y_axis": list(design["y_b"]),
        "index_gear_deg": math.degrees(design["phi_star"]),
        "index_pinion_deg": math.degrees(design["psi_star"]),
        "pinion_turns_per_gear_turn": design["sigma"] * design["ratio"],
    }


def pair_hypoid(left: dict, right: dict, gear_angle_deg: float = 0.0) -> dict:
    pinion, ring = _order_pair(left, right)
    _same_pair(pinion, ring)
    assembly = pinion["assembly"]
    extra = float(gear_angle_deg)
    gear_angle = assembly["index_gear_deg"] + extra
    pinion_angle = assembly["index_pinion_deg"] + assembly["pinion_turns_per_gear_turn"] * extra
    rotation = _pinion_rotation(assembly, pinion_angle)
    return {
        "axis_layout": "offset",
        "standards": [],
        "ratio": pinion["ratio"],
        "offset_mm": pinion["offset_mm"],
        "shaft_angle_deg": pinion["shaft_angle_deg"],
        "centre_distance_mm": None,
        "normal_module_mm": pinion["normal_module_mm"],
        "pinion_spiral_angle_deg": pinion["pinion_spiral_angle_deg"],
        "ring_spiral_angle_deg": pinion["ring_spiral_angle_deg"],
        "pinion_pitch_angle_deg": pinion["pinion_pitch_angle_deg"],
        "ring_pitch_angle_deg": pinion["ring_pitch_angle_deg"],
        "backlash_mm": _BIAS_MM + _TOOL_STOCK_MM,
        "warnings": list(pinion["warnings"]) + [item for item in ring["warnings"] if item not in pinion["warnings"]],
        "placement": {
            "pinion": {
                "position_mm": list(assembly["pinion_axis_point_mm"]),
                "rotation_deg": list(rotation),
            },
            "gear": {
                "position_mm": [0.0, 0.0, 0.0],
                "rotation_deg": [0.0, 0.0, gear_angle],
            },
            "note": (
                "The ring axis is +Z through the origin. The pinion axis is the reported direction "
                "through pinion_axis_point_mm, and the shortest distance between those axes is offset_mm. "
                "Rotation is degrees about X, then Y, then Z, matching the other families, with the "
                "pinion spin about its own axis included. gear_angle_deg on mesh_placement advances "
                "the ring and the pinion together at the tooth ratio."
            ),
        },
        "motion": {
            "pinion_turns_per_gear_turn": assembly["pinion_turns_per_gear_turn"],
            "gear_axis_direction": assembly["gear_axis_direction"],
            "pinion_axis_direction": assembly["pinion_axis_direction"],
        },
        "assumptions": [
            "Zero geometric backlash aside from the flank shift that keeps facets out of the mate.",
            "Both members were cut by the same face-mill settings.",
            "Hands are opposite.",
        ],
    }


def _order_pair(left: dict, right: dict):
    if left["role"] == "pinion" and right["role"] == "ring":
        return left, right
    if left["role"] == "ring" and right["role"] == "pinion":
        return right, left
    raise GeometryError("A hypoid pair needs one pinion and one ring.")


def _same_pair(pinion: dict, ring: dict) -> None:
    if pinion["pinion_teeth"] != ring["pinion_teeth"] or pinion["ring_teeth"] != ring["ring_teeth"]:
        raise GeometryError("The hypoid tooth counts do not describe one pair.")
    checks = (
        "offset_mm",
        "shaft_angle_deg",
        "ring_pitch_diameter_mm",
        "face_width_mm",
        "ring_spiral_angle_deg",
        "drive_pressure_angle_deg",
        "coast_pressure_angle_deg",
    )
    for key in checks:
        if abs(pinion[key] - ring[key]) > 1.0e-4:
            raise GeometryError(f"The hypoid members disagree on {key}.")
    for key in ("radius_mm", "point_width_mm", "edge_radius_mm"):
        if abs(pinion["cutter"][key] - ring["cutter"][key]) > 1.0e-4:
            raise GeometryError(f"The hypoid members disagree on cutter.{key}.")
    if pinion["cutter"]["process"] != ring["cutter"]["process"]:
        raise GeometryError("The hypoid members do not use the same cutter.")


def _pinion_rotation(assembly: dict, pinion_angle_deg: float):
    from OCP.gp import gp_EulerSequence, gp_Mat, gp_Quaternion

    spin = math.radians(pinion_angle_deg)
    cosine, sine = math.cos(spin), math.sin(spin)
    x_axis = tuple(assembly["pinion_x_axis"])
    y_axis = tuple(assembly["pinion_y_axis"])
    z_axis = tuple(assembly["pinion_axis_direction"])
    column0 = _add(_scale(x_axis, cosine), _scale(y_axis, sine))
    column1 = _add(_scale(x_axis, -sine), _scale(y_axis, cosine))
    column2 = z_axis
    matrix = gp_Mat(
        column0[0], column1[0], column2[0],
        column0[1], column1[1], column2[1],
        column0[2], column1[2], column2[2],
    )
    angles = gp_Quaternion(matrix).GetEulerAngles(gp_EulerSequence.gp_Intrinsic_XYZ)
    return tuple(math.degrees(angle) for angle in angles)


def line_distance(point_a, direction_a, point_b, direction_b) -> float:
    """Shortest distance between two axes. Used by the mesh report and tests."""
    normal = _cross(_unit(direction_a), _unit(direction_b))
    length = _norm(normal)
    if length < 1.0e-12:
        arm = _sub(point_b, point_a)
        along = _unit(direction_a)
        return _norm(_sub(arm, _scale(along, _dot(arm, along))))
    return abs(_dot(_sub(point_b, point_a), _scale(normal, 1.0 / length)))
