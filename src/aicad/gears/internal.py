"""Internal spur ring.

The tooth is the ISO 21771 internal involute: the tip is inside the reference
cylinder and the root is outside it. A rim outside the root closes the solid.
The root corner is a circular blend of the ISO 53 tip radius, tangent to the
root circle, not a pinion-cutter trochoid.

Zero-backlash centre distance of an external gear 1 and this ring 2:

inv α_wt = inv α_t + 2 tan α_n (x1 − x2) / (z2 − z1)
a = (d2 − d1) / 2 · cos α_t / cos α_wt

Tip-to-root clearance at that centre is the root radius of the ring, minus the
centre, minus the external tip radius.
"""

from __future__ import annotations

import math

from aicad.errors import GeometryError
from aicad.gears import geometry as geom

# Metal outside the root when the spec omits rim_thickness_mm. Four normal modules.
_DEFAULT_RIM_MODULES = 4.0
# The full ISO 53 addendum reaches a mating pinion's root fillet. The generated
# tip is this much shorter unless addendum_coefficient is set.
_TIP_RELIEF_MODULES = 0.1


def normalize_internal(spec: dict) -> dict:
    """Canonical spec for an internal spur ring."""
    offset = geom._number(spec.get("offset_mm"), "offset_mm", default=0.0)
    if abs(offset) > 1e-12:
        raise GeometryError(
            "offset_mm is the hypoid pinion offset. Use type 'hypoid' for that pair. "
            "An internal ring keeps the offset at zero."
        )
    if spec.get("helix_angle_deg") not in (None, 0, 0.0):
        raise GeometryError("An internal ring is a spur. helix_angle_deg must be 0.")
    if spec.get("spiral_angle_deg") not in (None, 0, 0.0):
        raise GeometryError("An internal ring is a spur. spiral_angle_deg must be 0.")
    module = geom._required_positive(spec, "module_mm")
    teeth = geom._required_teeth(spec)
    pressure = geom._number(spec.get("pressure_angle_deg"), "pressure_angle_deg", default=20.0)
    if not 1.0 < pressure < 45.0:
        raise GeometryError("pressure_angle_deg must be between 1 and 45 degrees.")
    face = geom._required_positive(spec, "face_width_mm")
    shift = geom._number(spec.get("profile_shift"), "profile_shift", default=0.0)
    if abs(shift) > 1.5:
        raise GeometryError("profile_shift must be between -1.5 and 1.5.")
    rack = str(spec.get("rack") or "A").strip().upper()
    if rack not in geom.ISO_53:
        raise GeometryError("rack must be ISO 53 profile A, B, C, or D.")
    addendum = geom._optional_coeff(spec.get("addendum_coefficient"), "addendum_coefficient", 0.2, 2.0)
    dedendum = geom._optional_coeff(spec.get("dedendum_coefficient"), "dedendum_coefficient", 0.4, 2.5)
    fillet = geom._optional_coeff(spec.get("fillet_radius_coefficient"), "fillet_radius_coefficient", 0.0, 0.8)
    if spec.get("bore_mm") is not None or spec.get("hub") is not None:
        raise GeometryError("An internal ring is a toothed rim. It does not take a bore or a hub.")
    if spec.get("keyway") is not None or spec.get("spline") is not None:
        raise GeometryError("An internal ring is a toothed rim. It does not take a keyway or a spline.")
    rim = spec.get("rim_thickness_mm")
    if rim is None:
        rim = _DEFAULT_RIM_MODULES * module
    else:
        rim = geom._number(rim, "rim_thickness_mm")
        if rim <= 0:
            raise GeometryError("rim_thickness_mm must be positive.")
    transverse = module
    geom._reject_oversize(teeth * transverse, "pitch diameter")
    geom._reject_oversize(face, "face_width_mm")
    geom._reject_oversize(teeth * transverse + 2.0 * module * 3.0 + 2.0 * rim, "outside diameter")
    return {
        "type": "internal",
        "module_mm": module,
        "teeth": teeth,
        "pressure_angle_deg": pressure,
        "face_width_mm": face,
        "profile_shift": shift,
        "helix_angle_deg": 0.0,
        "spiral_angle_deg": 0.0,
        "hand": "right",
        "rack": rack,
        "addendum_coefficient": addendum,
        "dedendum_coefficient": dedendum,
        "fillet_radius_coefficient": fillet,
        "shaft_angle_deg": 0.0,
        "mate_teeth": None,
        "ratio": None,
        "pitch_angle_deg": None,
        "bore_mm": None,
        "hub": None,
        "keyway": None,
        "spline": None,
        "rim_thickness_mm": rim,
    }


def internal_scalars(spec: dict) -> dict:
    """ISO 21771 diameters for an internal spur. ``spec`` is already normalized."""
    mn = float(spec["module_mm"])
    z = float(spec["teeth"])
    alpha_n = math.radians(float(spec["pressure_angle_deg"]))
    x = float(spec.get("profile_shift") or 0.0)
    rack = geom.ISO_53[str(spec.get("rack") or "A")]
    if spec.get("addendum_coefficient") is None:
        ha_star = rack["addendum"] - _TIP_RELIEF_MODULES
        shortened = True
    else:
        ha_star = float(spec["addendum_coefficient"])
        shortened = False
    hf_star = rack["dedendum"] if spec.get("dedendum_coefficient") is None else float(spec["dedendum_coefficient"])
    fillet_star = rack["fillet"] if spec.get("fillet_radius_coefficient") is None else float(spec["fillet_radius_coefficient"])
    mt = mn
    alpha_t = alpha_n
    d = z * mt
    db = d * math.cos(alpha_t)
    # Positive profile shift thickens the tooth and shortens the inward addendum.
    ha = mn * (ha_star - x)
    hf = mn * (hf_star + x)
    if ha <= 0.05 * mn:
        raise GeometryError(
            "Profile shift leaves almost no internal addendum. Reduce profile_shift or raise the addendum coefficient."
        )
    if hf <= 0.05 * mn:
        raise GeometryError(
            "Profile shift leaves almost no internal dedendum. Increase profile_shift or raise the dedendum coefficient."
        )
    da = d - 2.0 * ha
    df = d + 2.0 * hf
    if da <= db * 1.001:
        raise GeometryError(
            "The internal tip is inside the base circle, so there is no involute. "
            "Increase teeth or profile_shift, or reduce the addendum."
        )
    st = mt * (math.pi / 2.0 + 2.0 * x * math.tan(alpha_n))
    if st <= 0:
        raise GeometryError("Tooth thickness at the reference cylinder is not positive.")
    rp = d / 2.0
    rb = db / 2.0
    ra = da / 2.0
    rf = df / 2.0
    if not ra < rp < rf:
        raise GeometryError("An internal tooth needs the tip inside the reference cylinder and the root outside it.")
    half = math.pi / z
    psi_tip = _half_angle(ra, rp, rb, alpha_t, st)
    psi_root = _half_angle(rf, rp, rb, alpha_t, st)
    if psi_tip <= 1e-4:
        raise GeometryError(
            "The internal tooth is pointed before the tip. Increase teeth or profile_shift."
        )
    if psi_root >= half - 1e-4:
        raise GeometryError(
            "Internal teeth meet at the root. Reduce profile_shift or increase the tooth count."
        )
    rim = float(spec["rim_thickness_mm"])
    rho = fillet_star * mn
    gap = rf * (half - psi_root)
    if rho > gap * 0.45:
        rho = gap * 0.45
    warnings: list[str] = []
    if shortened:
        warnings.append(
            "The internal tip is 0.1 module shorter than the ISO 53 addendum, "
            "so it clears a mating root fillet. Set addendum_coefficient for the full addendum."
        )
    if rim < 0.5 * mn:
        warnings.append("The rim is thinner than half a module.")
    if abs(x) > 1.0:
        warnings.append("Profile shift is outside the usual range of about -0.5 to 1.")
    return {
        "mn": mn,
        "mt": mt,
        "z": z,
        "teeth": int(round(z)),
        "alpha_n": alpha_n,
        "alpha_t": alpha_t,
        "alpha_n_deg": math.degrees(alpha_n),
        "alpha_t_deg": math.degrees(alpha_t),
        "x": x,
        "ha_star": ha_star,
        "hf_star": hf_star,
        "fillet_star": rho / mn if mn else 0.0,
        "d": d,
        "db": db,
        "da": da,
        "df": df,
        "ha": ha,
        "hf": hf,
        "st": st,
        "rp": rp,
        "rb": rb,
        "ra": ra,
        "rf": rf,
        "ro": rf + rim,
        "rim": rim,
        "rho": rho,
        "psi_tip": psi_tip,
        "psi_root": psi_root,
        "warnings": warnings,
    }


def compute_internal(spec: dict) -> dict:
    scalars = internal_scalars(spec)
    return {
        "type": "internal",
        "internal": True,
        "axis_layout": geom.PARALLEL,
        "standards": ["ISO 21771:2007", "ISO 53:1998"],
        "module_mm": spec["module_mm"],
        "normal_module_mm": scalars["mn"],
        "transverse_module_mm": scalars["mt"],
        "teeth": spec["teeth"],
        "pressure_angle_deg": spec["pressure_angle_deg"],
        "normal_pressure_angle_deg": scalars["alpha_n_deg"],
        "transverse_pressure_angle_deg": scalars["alpha_t_deg"],
        "helix_angle_deg": 0.0,
        "hand": "right",
        "base_helix_angle_deg": 0.0,
        "profile_shift": spec["profile_shift"],
        "rack": spec["rack"],
        "addendum_coefficient": scalars["ha_star"],
        "dedendum_coefficient": scalars["hf_star"],
        "fillet_radius_coefficient": scalars["fillet_star"],
        "face_width_mm": spec["face_width_mm"],
        "pitch_diameter_mm": scalars["d"],
        "reference_diameter_mm": scalars["d"],
        "base_diameter_mm": scalars["db"],
        "tip_diameter_mm": scalars["da"],
        "root_diameter_mm": scalars["df"],
        "outside_diameter_mm": scalars["ro"] * 2.0,
        "rim_thickness_mm": scalars["rim"],
        "addendum_mm": scalars["ha"],
        "dedendum_mm": scalars["hf"],
        "tooth_thickness_normal_mm": scalars["st"],
        "tooth_thickness_transverse_mm": scalars["st"],
        "circular_pitch_transverse_mm": math.pi * scalars["mt"],
        "base_pitch_transverse_mm": math.pi * scalars["mt"] * math.cos(scalars["alpha_t"]),
        "base_pitch_normal_mm": math.pi * scalars["mn"] * math.cos(scalars["alpha_n"]),
        "undercut": False,
        "overlap_ratio": 0.0,
        "warnings": list(scalars["warnings"]),
        "assumptions": [
            "Internal spur. The tip is inside the reference cylinder and the root is outside it (ISO 21771).",
            "Positive profile shift thickens the tooth and shortens the inward addendum.",
            "The flank is the transverse involute. The half-angle of the tooth metal is st/(2 rp) − inv αt + inv αr.",
            "The root fillet is a circular blend of the ISO 53 tip radius, tangent to the root circle. It is not a pinion-cutter trochoid.",
            "The rim is the metal outside the root cylinder. Omitting rim_thickness_mm uses four normal modules.",
            "Omitting addendum_coefficient shortens the tip by 0.1 module so it clears a mating root fillet.",
            "Undercut from a pinion cutter is not evaluated. The tip has to stay outside the base circle.",
        ],
    }


def internal_tooth_sector(
    spec: dict, involute_count: int = 16, fillet_count: int = 8
) -> list[tuple[float, float]]:
    """One internal tooth, from the left space centre to the right. Tooth 0 is on +X."""
    scalars = internal_scalars(spec)
    flank = _flank_polar(scalars, involute_count, fillet_count)
    half = math.pi / float(scalars["z"])
    rf = scalars["rf"]
    ra = scalars["ra"]
    right = [(radius * math.cos(angle), radius * math.sin(angle)) for radius, angle in flank]
    left = [(radius * math.cos(-angle), radius * math.sin(-angle)) for radius, angle in reversed(flank)]
    root_left = -flank[-1][1]
    root_right = flank[-1][1]
    tip_left = -flank[0][1]
    tip_right = flank[0][1]
    points: list[tuple[float, float]] = []
    points.extend(geom._arc(rf, -half, root_left, 4))
    points.extend(left[1:])
    points.extend(geom._arc(ra, tip_left, tip_right, 5)[1:])
    points.extend(right[1:])
    points.extend(geom._arc(rf, root_right, half, 4)[1:])
    return geom._dedupe(points)


def internal_hole_outline(
    spec: dict, involute_count: int = 16, fillet_count: int = 6
) -> list[tuple[float, float]]:
    """Closed inner boundary of the ring. The hole is inside this curve."""
    scalars = internal_scalars(spec)
    flank = _flank_polar(scalars, involute_count, fillet_count)
    teeth = int(scalars["teeth"])
    step = 2.0 * math.pi / teeth
    polar: list[tuple[float, float]] = []
    for tooth in range(teeth):
        centre = tooth * step
        left = [(radius, centre - angle) for radius, angle in reversed(flank)]
        right = [(radius, centre + angle) for radius, angle in flank]
        if polar:
            polar.extend(_polar_arc(polar[-1], left[0], 3))
        polar.append(left[0])
        polar.extend(left[1:])
        polar.extend(_polar_arc(polar[-1], right[0], 4))
        polar.extend(right[1:])
    closing = (polar[0][0], polar[0][1] + 2.0 * math.pi)
    polar.extend(_polar_arc(polar[-1], closing, 3))
    _require_increasing(polar)
    points = [(radius * math.cos(angle), radius * math.sin(angle)) for radius, angle in polar]
    return geom._dedupe(points)


def split_members(left: dict, right: dict) -> tuple[dict, dict]:
    """Return ``(external, ring)`` from a parallel pair."""
    if bool(left.get("internal")) == bool(right.get("internal")):
        if left.get("internal"):
            raise GeometryError(
                "Two internal gears do not form a pair. Mesh an internal ring with an external spur."
            )
        raise GeometryError("An internal mesh needs one internal ring.")
    if left.get("internal"):
        return right, left
    return left, right


def internal_mount(external: dict, ring: dict, centre_mm: float | None) -> dict:
    """Operating centre, backlash, contact ratio, and tip clearance."""
    if external.get("internal") or not ring.get("internal"):
        raise GeometryError("An internal mesh needs one external gear and one internal ring.")
    if abs(external["normal_module_mm"] - ring["normal_module_mm"]) > 1e-6:
        raise GeometryError("An internal pair needs the same normal module.")
    if abs(external["normal_pressure_angle_deg"] - ring["normal_pressure_angle_deg"]) > 1e-6:
        raise GeometryError("An internal pair needs the same normal pressure angle.")
    if abs(external["helix_angle_deg"]) > 1e-6 or abs(ring["helix_angle_deg"]) > 1e-6:
        raise GeometryError(
            "An internal ring is a spur. It does not mesh with a helical gear in this generator."
        )
    z1 = float(external["teeth"])
    z2 = float(ring["teeth"])
    if z2 <= z1:
        raise GeometryError(
            "The internal ring needs more teeth than the external gear, so the external gear fits inside it."
        )
    alpha_t = math.radians(external["transverse_pressure_angle_deg"])
    alpha_n = math.radians(external["normal_pressure_angle_deg"])
    x1 = float(external["profile_shift"])
    x2 = float(ring["profile_shift"])
    inv_w = geom._inv(alpha_t) + 2.0 * math.tan(alpha_n) * (x1 - x2) / (z2 - z1)
    if inv_w < -1e-12:
        raise GeometryError(
            "These profile shifts leave backlash even when the base circles reach their limit, "
            "so there is no zero-backlash centre distance."
        )
    alpha_w0 = geom._inv_inverse(inv_w)
    d1 = float(external["reference_diameter_mm"])
    d2 = float(ring["reference_diameter_mm"])
    ideal = 0.5 * (d2 - d1) * math.cos(alpha_t) / math.cos(alpha_w0)
    if ideal <= 0:
        raise GeometryError("The internal centre distance is not positive.")
    rb1 = float(external["base_diameter_mm"]) / 2.0
    rb2 = float(ring["base_diameter_mm"]) / 2.0
    ra1 = float(external["tip_diameter_mm"]) / 2.0
    ra2 = float(ring["tip_diameter_mm"]) / 2.0
    rf1 = float(external["root_diameter_mm"]) / 2.0
    rf2 = float(ring["root_diameter_mm"]) / 2.0
    mt = float(external["transverse_module_mm"])
    if centre_mm is not None and abs(float(centre_mm) - ideal) <= 1e-4:
        centre_mm = None
    warnings: list[str] = []
    if centre_mm is None:
        centre = ideal
        alpha_w = alpha_w0
        backlash_t = 0.0
        backlash_n = 0.0
        defined = True
    else:
        centre = float(centre_mm)
        if not math.isfinite(centre) or centre <= 0:
            raise GeometryError("centre_distance_mm must be a positive finite number.")
        separation = rb2 - rb1
        if separation <= 0:
            raise GeometryError("The internal base circle is not outside the external base circle.")
        cosine = separation / centre
        if cosine >= 1.0 - 1e-12:
            alpha_w = 0.0
            backlash_t = None
            backlash_n = None
            defined = False
        else:
            alpha_w = math.acos(min(1.0, cosine))
            backlash_t = 2.0 * centre * (geom._inv(alpha_w) - inv_w)
            backlash_n = backlash_t * math.cos(alpha_w)
            defined = True
    if defined:
        path = math.sqrt(max(ra1 * ra1 - rb1 * rb1, 0.0)) - math.sqrt(max(ra2 * ra2 - rb2 * rb2, 0.0))
        path += centre * math.sin(alpha_w)
        base = math.pi * mt * math.cos(alpha_t)
        contact = 0.0 if base <= 0 else path / base
    else:
        contact = None
    # Planet tip against the ring root, and ring tip against the planet root.
    tip_clearance = rf2 - centre - ra1
    ring_tip_clearance = (ra2 - centre) - rf1
    if tip_clearance < -1e-6:
        warnings.append("The external tip crosses the ring root.")
    if ring_tip_clearance < -1e-6:
        warnings.append("The ring tip crosses the external root.")
    return {
        "ideal_centre_distance_mm": ideal,
        "centre_distance_mm": centre,
        "reference_centre_distance_mm": (d2 - d1) / 2.0,
        "working_transverse_pressure_angle_deg": math.degrees(alpha_w),
        "zero_backlash_transverse_pressure_angle_deg": math.degrees(alpha_w0),
        "circumferential_backlash_mm": backlash_t,
        "normal_backlash_mm": backlash_n,
        "backlash_defined": defined,
        "base_circles_overlap": not defined,
        "transverse_contact_ratio": contact,
        "overlap_ratio": 0.0,
        "total_contact_ratio": contact,
        "tip_clearance_mm": tip_clearance,
        "ring_tip_clearance_mm": ring_tip_clearance,
        "warnings": warnings,
    }


def internal_pair(left: dict, right: dict) -> dict:
    """Placement record for ``pair_geometry``. The first gear stays at the origin."""
    external, ring = split_members(left, right)
    mount = internal_mount(external, ring, None)
    centre = mount["centre_distance_mm"]
    if left.get("internal"):
        pinion_spin = 180.0 / float(left["teeth"])
        gear_spin = 180.0
        note = (
            "The internal ring is at the origin with a tooth space on +X. "
            "The external gear sits on +X at the zero-backlash centre distance, "
            "tooth pointing away from the ring centre so it meets that space. "
            "Both members turn the same way."
        )
    else:
        pinion_spin = 180.0
        gear_spin = 180.0 * (1.0 + 1.0 / float(right["teeth"]))
        note = (
            "The external gear is at the origin with its tooth pointing away from the ring. "
            "The ring sits on +X at the zero-backlash centre distance, "
            "a tooth space facing that tooth. Both members turn the same way."
        )
    warnings = list(mount["warnings"])
    return {
        "axis_layout": geom.PARALLEL,
        "mesh": "internal",
        "standards": ["ISO 21771:2007"],
        "centre_distance_mm": centre,
        "reference_centre_distance_mm": mount["reference_centre_distance_mm"],
        "working_transverse_pressure_angle_deg": mount["working_transverse_pressure_angle_deg"],
        "transverse_contact_ratio": mount["transverse_contact_ratio"],
        "overlap_ratio": 0.0,
        "total_contact_ratio": mount["total_contact_ratio"],
        "backlash_mm": 0.0,
        "tip_clearance_mm": mount["tip_clearance_mm"],
        "ring_tip_clearance_mm": mount["ring_tip_clearance_mm"],
        "warnings": warnings,
        "placement": {
            "pinion": {"position_mm": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, pinion_spin]},
            "gear": {"position_mm": [centre, 0.0, 0.0], "rotation_deg": [0.0, 0.0, gear_spin]},
            "note": note,
        },
        "assumptions": [
            "Zero backlash at the internal working centre from ISO 21771.",
            "inv α_wt = inv α_t + 2 tan α_n (x_external − x_internal) / (z_internal − z_external).",
            "Tip clearance is the ring root radius, minus the centre, minus the external tip radius.",
            "Contact ratio uses the internal path of contact. Both members turn the same way.",
        ],
    }


def _half_angle(radius: float, rp: float, rb: float, alpha_t: float, st: float) -> float:
    """Half-angle of the internal tooth metal at ``radius``."""
    if radius < rb:
        radius = rb
    alpha_r = math.acos(min(1.0, rb / radius))
    return st / (2.0 * rp) - geom._inv(alpha_t) + geom._inv(alpha_r)


def _flank_polar(scalars: dict, involute_count: int, fillet_count: int) -> list[tuple[float, float]]:
    """Right flank as ``(radius, angle)``, tip first and root last."""
    rho = float(scalars["rho"])
    flank = None
    for _ in range(8):
        flank = _flank_try(scalars, rho, involute_count, fillet_count)
        if flank is not None:
            return flank
        rho *= 0.65
    sharp = _flank_try(scalars, 0.0, involute_count, fillet_count)
    if sharp is None:
        raise GeometryError("The internal flank does not reach the root without folding.")
    return sharp


def _flank_try(
    scalars: dict, rho: float, involute_count: int, fillet_count: int
) -> list[tuple[float, float]] | None:
    ra = float(scalars["ra"])
    rf = float(scalars["rf"])
    rb = float(scalars["rb"])
    rp = float(scalars["rp"])
    alpha = float(scalars["alpha_t"])
    st = float(scalars["st"])
    half = math.pi / float(scalars["z"])

    def psi(radius: float) -> float:
        return _half_angle(radius, rp, rb, alpha, st)

    r_join = rf
    fillet: list[tuple[float, float]] = []
    if rho > 1e-6:
        found = _fillet_join(psi, ra, rf, rho, half)
        if found is None:
            return None
        r_join, fillet = found
    count = max(int(involute_count), 8)
    points: list[tuple[float, float]] = []
    for index in range(count):
        radius = ra + (r_join - ra) * index / (count - 1)
        angle = psi(radius)
        if angle <= 0 or angle >= half:
            return None
        if points and angle <= points[-1][1] + 1e-9:
            return None
        points.append((radius, angle))
    if fillet:
        if fillet[0][1] + 1e-6 < points[-1][1]:
            return None
        for radius, angle in fillet[1:]:
            if angle <= points[-1][1] + 1e-9 or angle >= half - 1e-6:
                return None
            if radius > rf + 1e-4:
                return None
            points.append((radius, angle))
    else:
        angle = psi(rf)
        if angle <= points[-1][1] or angle >= half - 1e-6:
            return None
        points.append((rf, angle))
    if abs(points[-1][0] - rf) > 1e-4:
        return None
    return points


def _fillet_join(psi, ra: float, rf: float, rho: float, half: float):
    """Involute radius where a root-tangent fillet of radius ``rho`` can start, and the arc."""
    target = rf - rho
    if target <= ra:
        return None
    samples = 20
    for index in range(samples):
        radius = rf - rho * 0.2 - (rf - ra) * 0.85 * index / samples
        if radius <= ra * 1.002 or radius >= rf:
            continue
        angle = psi(radius)
        if not 0 < angle < half:
            continue
        arc = _fillet_arc(radius, angle, rf, rho, half)
        if arc is None:
            continue
        return radius, arc
    return None


def _fillet_arc(radius: float, angle: float, rf: float, rho: float, half: float):
    """Fillet from an involute point to the root circle, angles increasing."""
    target = rf - rho
    end_x = radius * math.cos(angle)
    end_y = radius * math.sin(angle)
    end_r = math.hypot(end_x, end_y)
    if target <= 0 or end_r <= 0:
        return None
    dot = (target * target + end_r * end_r - rho * rho) / 2.0
    cosine = dot / (target * end_r)
    if cosine > 1.0 or cosine < -1.0:
        return None
    delta = math.acos(cosine)
    # The centre toward the tooth space is the larger angle.
    phi = angle + delta
    if phi <= angle or phi >= half - 1e-4:
        return None
    cx = target * math.cos(phi)
    cy = target * math.sin(phi)
    start = math.atan2(end_y - cy, end_x - cx)
    root_x = rf * math.cos(phi)
    root_y = rf * math.sin(phi)
    stop = math.atan2(root_y - cy, root_x - cx)
    sweep = (stop - start + math.pi) % (2.0 * math.pi) - math.pi
    if abs(sweep) < 1e-6 or abs(sweep) > math.pi * 0.85:
        return None
    count = 6
    arc = []
    previous = angle
    for index in range(count):
        tee = index / (count - 1)
        theta = start + sweep * tee
        x = cx + rho * math.cos(theta)
        y = cy + rho * math.sin(theta)
        point_r = math.hypot(x, y)
        point_a = math.atan2(y, x)
        if point_a < previous - 1e-5 or point_r > rf + 1e-4:
            return None
        arc.append((point_r, point_a))
        previous = point_a
    arc[-1] = (rf, phi)
    return arc


def _polar_arc(start: tuple[float, float], end: tuple[float, float], count: int) -> list[tuple[float, float]]:
    """Interior points of a circular arc. ``end`` is not included."""
    radius = (start[0] + end[0]) / 2.0
    a0 = start[1]
    a1 = end[1]
    if a1 < a0:
        a1 += 2.0 * math.pi
    if count < 1:
        return []
    points = []
    for index in range(1, count + 1):
        if index == count + 1:
            break
        tee = index / (count + 1)
        points.append((radius, a0 + (a1 - a0) * tee))
    return points


def _require_increasing(polar: list[tuple[float, float]]) -> None:
    previous = polar[0][1]
    for _radius, angle in polar[1:]:
        if angle <= previous + 1e-8:
            raise GeometryError("The internal tooth outline folds back on itself.")
        previous = angle
