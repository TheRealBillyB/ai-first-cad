"""Gear geometry.

Cylindrical teeth follow ISO 21771:2007 and the ISO 53:1998 basic rack.
Straight and spiral bevel teeth use the intersecting-axis pitch cones of
ISO 23509 and the virtual cylindrical gear of ANSI/AGMA 2005-D03 (Tredgold's
back-cone approximation). Bevel tooth proportions are the Gleason system
figures that reproduce the published 20/40 worked examples: whole depth
2.188 times the outer module for straight bevels, and 1.888 times that module
for spiral bevels. The flank is the involute of that virtual gear, wrapped
onto the back cone and ruled to the pitch apex (an octoid). A spiral tooth
trace is a constant-angle spiral on the pitch cone at the mean spiral angle.

Hypoid gears are not built. They need a nonzero axis offset and a tooth
surface from a generating process. :func:`register_family` accepts an
``offset`` layout so that generator can be added without changing bores, hubs,
or the metadata shared by every family.
"""

from __future__ import annotations

import math
from typing import Callable

from aicad.errors import GeometryError
from aicad.gears import tables

# ISO 53:1998 basic rack. Fillet values are coefficients of the normal module.
ISO_53 = {
    "A": {"addendum": 1.0, "dedendum": 1.25, "fillet": 0.38},
    "B": {"addendum": 1.0, "dedendum": 1.25, "fillet": 0.30},
    "C": {"addendum": 1.0, "dedendum": 1.25, "fillet": 0.25},
    "D": {"addendum": 1.0, "dedendum": 1.40, "fillet": 0.39},
}

PARALLEL = "parallel"
INTERSECTING = "intersecting"
OFFSET = "offset"

CYLINDRICAL = frozenset({"spur", "helical"})
BEVEL = frozenset({"straight_bevel", "spiral_bevel"})

_FAMILIES: dict[str, dict] = {}


def register_family(
    name: str,
    axis_layout: str,
    compute: Callable[[dict], dict],
    build: Callable[[dict], object] | None = None,
    summary: str = "",
) -> None:
    """Register a gear family.

    ``axis_layout`` is ``parallel`` (spur, helical), ``intersecting``
    (straight and spiral bevel), or ``offset`` (hypoid: a shaft angle plus
    a hypoid offset). No offset family is registered.
    """
    if axis_layout not in {PARALLEL, INTERSECTING, OFFSET}:
        raise GeometryError(
            f"Gear family {name!r} has axis layout {axis_layout!r}. "
            "Use 'parallel', 'intersecting', or 'offset'."
        )
    if name in _FAMILIES:
        raise GeometryError(f"Gear family {name!r} is already registered.")
    _FAMILIES[name] = {
        "name": name,
        "axis_layout": axis_layout,
        "summary": summary,
        "compute": compute,
        "build": build,
    }


def families() -> dict[str, dict]:
    """Registered families, without the builder callables."""
    return {
        name: {"name": item["name"], "axis_layout": item["axis_layout"], "summary": item["summary"]}
        for name, item in _FAMILIES.items()
    }


def gear_data(spec: dict) -> dict:
    """Computed geometry for one gear. This does not build a solid."""
    clean = normalize_spec(spec)
    family = _FAMILIES.get(clean["type"])
    if family is None:
        _reject_unknown(clean["type"])
    data = family["compute"](clean)
    data["spec"] = clean
    return _round_tree(data)


def require_builder(spec: dict) -> tuple[dict, Callable]:
    clean = normalize_spec(spec)
    family = _FAMILIES.get(clean["type"])
    if family is None or family["build"] is None:
        _reject_unknown(clean["type"])
    return clean, family["build"]


def pair_geometry(pinion: dict, gear: dict) -> dict:
    """Centre distance or shaft angle, contact ratio, and a zero-backlash pose."""
    left = gear_data(pinion)
    right = gear_data(gear)
    if left["axis_layout"] != right["axis_layout"]:
        raise GeometryError(
            f"A {left['type']} ({left['axis_layout']} axes) cannot mesh with "
            f"a {right['type']} ({right['axis_layout']} axes)."
        )
    if left["axis_layout"] == PARALLEL:
        report = _cylindrical_pair(left, right)
    elif left["axis_layout"] == INTERSECTING:
        report = _bevel_pair(left, right)
    else:
        raise GeometryError("An offset (hypoid) pair is not generated.")
    report["pinion"] = {"type": left["type"], "teeth": left["teeth"], "hand": left["hand"]}
    report["gear"] = {"type": right["type"], "teeth": right["teeth"], "hand": right["hand"]}
    return _round_tree(report)


def mesh_placement(pinion: dict, gear: dict) -> dict:
    """Rigid placement of a zero-backlash pair. Rotations are degrees about X, then Y, then Z."""
    report = pair_geometry(pinion, gear)
    return {
        "axis_layout": report["axis_layout"],
        "centre_distance_mm": report.get("centre_distance_mm"),
        "shaft_angle_deg": report.get("shaft_angle_deg"),
        "pinion": report["placement"]["pinion"],
        "gear": report["placement"]["gear"],
        "note": report["placement"]["note"],
    }


def profile_points(spec: dict) -> list[tuple[float, float]]:
    """Closed transverse outline of a cylindrical gear. Tooth 0 is on +X."""
    clean = normalize_spec(spec)
    if clean["type"] in BEVEL:
        raise GeometryError("profile_points is the transverse outline of a spur or helical gear.")
    return transverse_outline(clean)


def tooth_profile(
    spec: dict, involute_count: int = 16, fillet_count: int = 8
) -> list[tuple[float, float]]:
    """One transverse tooth, from the left space centre to the right.

    The tooth centre is on +X. A virtual spur spec (the Tredgold section of a
    bevel) is accepted when ``virtual`` is set, so the mesh check can sweep
    the same outline the solid is drawn from.
    """
    if spec.get("virtual"):
        scalars = cylindrical_scalars(spec)
    else:
        scalars = cylindrical_scalars(normalize_spec(spec))
    return _tooth_sector(scalars, involute_count, fillet_count)


def transverse_outline(
    spec: dict, involute_count: int = 28, fillet_count: int = 20
) -> list[tuple[float, float]]:
    """Closed transverse outline. ``spec`` is a cylindrical gear or a virtual spur."""
    scalars = cylindrical_scalars(spec)
    teeth = int(round(float(scalars["z"])))
    return _tile(_tooth_sector(scalars, involute_count, fillet_count), teeth)


def _require_consistent_bevel(
    teeth: int,
    shaft_deg: float,
    mate: int | None,
    ratio: float | None,
    pitch_angle: float | None,
) -> None:
    """Refuse a mate, a ratio, and a pitch angle that are not the same cone."""
    implied = None
    if mate is not None and ratio is not None:
        expected = mate / teeth
        if abs(ratio - expected) > 1e-6 * max(1.0, abs(expected)):
            raise GeometryError(
                "ratio does not match mate_teeth / teeth. Pass one, or pass values for the same pair."
            )
        implied = expected
    elif mate is not None:
        implied = mate / teeth
    elif ratio is not None:
        implied = ratio
    if pitch_angle is None or implied is None:
        return
    shaft = math.radians(shaft_deg)
    denominator = implied + math.cos(shaft)
    if abs(denominator) < 1e-12:
        raise GeometryError(
            "pitch_angle_deg does not match the pitch cone from mate_teeth or ratio."
        )
    delta = math.degrees(math.atan(math.sin(shaft) / denominator))
    if abs(delta - pitch_angle) > 1e-4:
        raise GeometryError(
            "pitch_angle_deg does not match the pitch cone from mate_teeth or ratio. "
            "Pass one, or pass values for the same cone."
        )


def normalize_spec(spec: dict) -> dict:
    """Fill defaults and reject a hypoid or an unknown family."""
    if not isinstance(spec, dict):
        raise GeometryError("A gear spec must be an object.")
    raw_type = str(spec.get("type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "straight": "straight_bevel",
        "straightbevel": "straight_bevel",
        "spiral": "spiral_bevel",
        "spiralbevel": "spiral_bevel",
        "bevel": "straight_bevel",
    }
    gear_type = aliases.get(raw_type, raw_type)
    offset = _number(spec.get("offset_mm"), "offset_mm", default=0.0)
    if gear_type in {"hypoid", "hypoid_bevel"} or abs(offset) > 1e-12:
        raise GeometryError(
            "Hypoid gears are not generated. They need a shaft angle and a nonzero "
            "offset, and the tooth surface comes from simulating the generating cut. "
            "The gear module reserves axis layout 'offset' for that family. "
            "Spur, helical, straight bevel, and spiral bevel are available."
        )
    if gear_type not in CYLINDRICAL | BEVEL:
        _reject_unknown(gear_type or "(missing)")

    module = _required_positive(spec, "module_mm")
    teeth = _required_teeth(spec)
    pressure = _number(spec.get("pressure_angle_deg"), "pressure_angle_deg", default=20.0)
    if not 1.0 < pressure < 45.0:
        raise GeometryError("pressure_angle_deg must be between 1 and 45 degrees.")
    face = _required_positive(spec, "face_width_mm")
    shift = _number(spec.get("profile_shift"), "profile_shift", default=0.0)
    if abs(shift) > 1.5:
        raise GeometryError("profile_shift must be between -1.5 and 1.5.")

    helix = _number(spec.get("helix_angle_deg"), "helix_angle_deg", default=0.0)
    spiral = _number(spec.get("spiral_angle_deg"), "spiral_angle_deg", default=0.0)
    hand = spec.get("hand")
    if hand is not None:
        hand = str(hand).strip().lower()
        if hand not in {"right", "left"}:
            raise GeometryError("hand must be 'right' or 'left'.")

    if gear_type == "spur":
        if abs(helix) > 1e-9:
            raise GeometryError("A spur gear has helix_angle_deg of 0. Use type 'helical' for a helix.")
        helix = 0.0
    elif gear_type == "helical":
        if abs(helix) < 1e-9:
            raise GeometryError("A helical gear needs a nonzero helix_angle_deg.")
        if abs(helix) >= 75.0:
            raise GeometryError("helix_angle_deg must be between -75 and 75 degrees, and not 0.")
        helix = _apply_hand(helix, hand, "helix_angle_deg")
    elif gear_type == "straight_bevel":
        if abs(spiral) > 1e-9:
            raise GeometryError(
                "A straight bevel has spiral_angle_deg of 0. Use type 'spiral_bevel' for a spiral."
            )
        spiral = 0.0
    else:
        if abs(spiral) < 1e-9:
            raise GeometryError("A spiral bevel needs a nonzero spiral_angle_deg (the mean spiral angle).")
        if abs(spiral) >= 60.0:
            raise GeometryError("spiral_angle_deg must be between -60 and 60 degrees, and not 0.")
        spiral = _apply_hand(spiral, hand, "spiral_angle_deg")

    rack = str(spec.get("rack") or "A").strip().upper()
    if rack not in ISO_53:
        raise GeometryError("rack must be ISO 53 profile A, B, C, or D.")
    addendum = _optional_coeff(spec.get("addendum_coefficient"), "addendum_coefficient", 0.2, 2.0)
    dedendum = _optional_coeff(spec.get("dedendum_coefficient"), "dedendum_coefficient", 0.4, 2.5)
    fillet = _optional_coeff(spec.get("fillet_radius_coefficient"), "fillet_radius_coefficient", 0.0, 0.8)

    shaft = _number(spec.get("shaft_angle_deg"), "shaft_angle_deg", default=90.0 if gear_type in BEVEL else 0.0)
    mate = spec.get("mate_teeth")
    ratio = spec.get("ratio")
    pitch_angle = spec.get("pitch_angle_deg")
    if gear_type in BEVEL:
        if not 1.0 < shaft < 179.0:
            raise GeometryError("shaft_angle_deg for a bevel gear must be between 1 and 179 degrees.")
        if mate is None and ratio is None and pitch_angle is None:
            raise GeometryError(
                "A bevel gear needs mate_teeth or ratio so the pitch cone can be calculated, "
                "or an explicit pitch_angle_deg."
            )
        if mate is not None:
            if isinstance(mate, bool) or not float(mate).is_integer():
                raise GeometryError("mate_teeth must be an integer.")
            mate = int(mate)
            if mate < 4:
                raise GeometryError("mate_teeth must be at least 4.")
        if ratio is not None:
            ratio = _number(ratio, "ratio")
            if ratio <= 0:
                raise GeometryError("ratio must be positive.")
        if pitch_angle is not None:
            pitch_angle = _number(pitch_angle, "pitch_angle_deg")
            if not 0.5 < pitch_angle < 179.0:
                raise GeometryError("pitch_angle_deg must be between 0.5 and 179 degrees.")
        if spec.get("profile_shift") not in (None, 0, 0.0):
            raise GeometryError(
                "Bevel profile shift is the Gleason addendum split for the ratio. "
                "Do not pass profile_shift on a bevel; it is computed."
            )
        _require_consistent_bevel(teeth, shaft, mate, ratio, pitch_angle)
    else:
        mate = None
        ratio = None
        pitch_angle = None
        shaft = 0.0

    bore = spec.get("bore_mm")
    if bore is not None:
        bore = _number(bore, "bore_mm")
        if bore <= 0:
            raise GeometryError("bore_mm must be positive.")
    hub = _hub(spec.get("hub"))
    keyway = _keyway(spec.get("keyway"), bore)
    spline = _spline(spec.get("spline"))
    if keyway and spline:
        raise GeometryError("Use a keyway or a spline, not both.")
    if keyway and bore is None and spline is None:
        raise GeometryError("A keyway needs bore_mm. A DIN 5480 spline sets its own minor diameter.")
    if keyway and bore is None:
        raise GeometryError("A keyway needs bore_mm.")

    return {
        "type": gear_type,
        "module_mm": module,
        "teeth": teeth,
        "pressure_angle_deg": pressure,
        "face_width_mm": face,
        "profile_shift": shift,
        "helix_angle_deg": helix if gear_type == "helical" else 0.0,
        "spiral_angle_deg": spiral if gear_type == "spiral_bevel" else 0.0,
        "hand": "left" if (helix < 0 or spiral < 0) else "right",
        "rack": rack,
        "addendum_coefficient": addendum,
        "dedendum_coefficient": dedendum,
        "fillet_radius_coefficient": fillet,
        "shaft_angle_deg": shaft,
        "mate_teeth": mate,
        "ratio": ratio,
        "pitch_angle_deg": pitch_angle,
        "bore_mm": bore,
        "hub": hub,
        "keyway": keyway,
        "spline": spline,
    }


# --- cylindrical scalars and tooth outline ---------------------------------


def cylindrical_scalars(spec: dict) -> dict:
    """ISO 21771 diameters and the ISO 53 rack used to cut the fillet."""
    mn = float(spec["module_mm"])
    z = float(spec["teeth"])
    beta = math.radians(float(spec.get("helix_angle_deg") or 0.0))
    alpha_n = math.radians(float(spec["pressure_angle_deg"]))
    x = float(spec.get("profile_shift") or 0.0)
    rack = ISO_53[str(spec.get("rack") or "A")]
    ha_star = rack["addendum"] if spec.get("addendum_coefficient") is None else float(spec["addendum_coefficient"])
    hf_star = rack["dedendum"] if spec.get("dedendum_coefficient") is None else float(spec["dedendum_coefficient"])
    fillet_star = rack["fillet"] if spec.get("fillet_radius_coefficient") is None else float(spec["fillet_radius_coefficient"])
    cos_b = math.cos(beta)
    mt = mn / cos_b
    alpha_t = math.atan(math.tan(alpha_n) / cos_b)
    d = z * mt
    db = d * math.cos(alpha_t)
    ha = mn * (ha_star + x)
    hf = mn * (hf_star - x)
    if hf <= 0.05 * mn:
        raise GeometryError(
            "Profile shift leaves almost no dedendum. Reduce profile_shift or raise the dedendum coefficient."
        )
    da = d + 2.0 * ha
    df = d - 2.0 * hf
    if df <= 0:
        raise GeometryError("The root diameter is not positive.")
    st = mt * (math.pi / 2.0 + 2.0 * x * math.tan(alpha_n))
    if st <= 0:
        raise GeometryError("Tooth thickness at the reference cylinder is not positive.")
    sn = st * cos_b
    pt = math.pi * mt
    pbt = pt * math.cos(alpha_t)
    pbn = math.pi * mn * math.cos(alpha_n)
    rp = d / 2.0
    rb = db / 2.0
    ra = da / 2.0
    rf = df / 2.0
    if ra <= rp:
        raise GeometryError("The tip diameter is not outside the reference cylinder.")
    beta_b = math.atan(math.tan(beta) * math.cos(alpha_t)) if abs(beta) > 1e-15 else 0.0
    lead = math.pi * d / math.tan(beta) if abs(beta) > 1e-12 else 0.0
    face = float(spec["face_width_mm"])
    twist = math.degrees(face * math.tan(beta) / rp) if abs(beta) > 1e-12 else 0.0
    overlap = face * abs(math.sin(beta)) / (math.pi * mn) if abs(beta) > 1e-12 else 0.0
    sin_t = math.sin(alpha_t)
    effective = hf_star - fillet_star * (1.0 - sin_t)
    x_min = effective - z * sin_t**2 / (2.0 * max(cos_b, 1e-6))
    undercut = x < x_min - 1e-9
    rho = fillet_star * mn
    rho = _fit_fillet(rp, mt, alpha_t, x, alpha_n, hf, rho)
    form = _form_radius(rp, rb, alpha_t, hf, rho)
    warnings: list[str] = []
    if undercut:
        warnings.append(
            f"Profile shift {x:.3f} is below the undercut limit {x_min:.3f}. "
            "The root fillet cuts into the involute."
        )
    return {
        "mn": mn,
        "mt": mt,
        "z": z,
        "teeth": int(round(z)) if abs(z - round(z)) < 1e-9 else z,
        "beta": beta,
        "alpha_n": alpha_n,
        "alpha_t": alpha_t,
        "alpha_n_deg": math.degrees(alpha_n),
        "alpha_t_deg": math.degrees(alpha_t),
        "beta_b_deg": math.degrees(beta_b),
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
        "sn": sn,
        "pt": pt,
        "pbt": pbt,
        "pbn": pbn,
        "rp": rp,
        "rb": rb,
        "ra": ra,
        "rf": rf,
        "rho": rho,
        "lead": lead,
        "twist_deg": twist,
        "overlap_ratio": overlap,
        "x_min": x_min,
        "undercut": undercut,
        "warnings": warnings,
        "form_radius": form,
        "virtual": bool(spec.get("virtual")),
    }


def compute_cylindrical(spec: dict) -> dict:
    scalars = cylindrical_scalars(spec)
    warnings = list(scalars["warnings"])
    if abs(spec["helix_angle_deg"]) > 45.0:
        warnings.append(
            "Helix angle is above 45 degrees. The transverse fillet uses the ISO 53 normal fillet radius as a circle."
        )
    if abs(spec["profile_shift"]) > 1.0:
        warnings.append("Profile shift is outside the usual range of about -0.5 to 1.")
    return {
        "type": spec["type"],
        "axis_layout": PARALLEL,
        "standards": ["ISO 21771:2007", "ISO 53:1998"],
        "module_mm": spec["module_mm"],
        "normal_module_mm": scalars["mn"],
        "transverse_module_mm": scalars["mt"],
        "teeth": spec["teeth"],
        "pressure_angle_deg": spec["pressure_angle_deg"],
        "normal_pressure_angle_deg": scalars["alpha_n_deg"],
        "transverse_pressure_angle_deg": scalars["alpha_t_deg"],
        "helix_angle_deg": spec["helix_angle_deg"],
        "hand": spec["hand"],
        "base_helix_angle_deg": scalars["beta_b_deg"],
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
        "addendum_mm": scalars["ha"],
        "dedendum_mm": scalars["hf"],
        "tooth_thickness_normal_mm": scalars["sn"],
        "tooth_thickness_transverse_mm": scalars["st"],
        "circular_pitch_transverse_mm": scalars["pt"],
        "base_pitch_transverse_mm": scalars["pbt"],
        "base_pitch_normal_mm": scalars["pbn"],
        "form_radius_mm": scalars["form_radius"],
        "undercut": scalars["undercut"],
        "undercut_limit_profile_shift": scalars["x_min"],
        "lead_mm": scalars["lead"],
        "twist_over_face_deg": scalars["twist_deg"],
        "overlap_ratio": scalars["overlap_ratio"],
        "warnings": warnings,
        "assumptions": [
            "External gear with zero-backlash tooth thickness at the reference cylinder (ISO 21771).",
            "Addendum and dedendum use the normal module and are measured from the reference cylinder.",
            "The flank is the transverse involute. A helical tooth carries that profile on a constant-lead helicoid.",
            "The root fillet is the envelope of the ISO 53 rack tip radius in the transverse plane.",
            "For a helical gear that fillet radius is the normal-plane ISO 53 radius, drawn as a circle in the transverse plane.",
        ],
    }


def _form_radius(rp: float, rb: float, alpha_t: float, hf: float, rho: float) -> float:
    """Radius at which the rack's straight flank hands over to the tip fillet."""
    sin_a = math.sin(alpha_t)
    if sin_a < 1e-9:
        return rb
    eta_tip = -hf
    eta_c = eta_tip + rho
    reach = hf if rho <= 1e-9 else max(0.0, -(eta_c - rho * sin_a))
    along = rp * sin_a - reach / sin_a
    if along <= 0:
        return rb
    return math.sqrt(rb * rb + along * along)


def _fit_fillet(rp: float, mt: float, alpha_t: float, x: float, alpha_n: float, hf: float, rho: float) -> float:
    """Shrink the rack tip radius until the fillet centres stay apart."""
    if rho <= 0:
        return 0.0
    st = mt * (math.pi / 2.0 + 2.0 * x * math.tan(alpha_n))
    for _ in range(12):
        eta_c = -hf + rho
        xi_c = st / 2.0 - eta_c * math.tan(alpha_t) + rho / math.cos(alpha_t)
        if xi_c > rho * 0.15 and rho < hf * 0.95:
            return rho
        rho *= 0.8
        if rho < 1e-4:
            return 0.0
    return rho


def _tooth_sector(
    scalars: dict, involute_count: int = 28, fillet_count: int = 20
) -> list[tuple[float, float]]:
    """One tooth, from the left space centre to the right space centre.

    The tooth centre is on +X. The outline walks counterclockwise.
    """
    z = float(scalars["z"])
    rp = scalars["rp"]
    rb = scalars["rb"]
    ra = scalars["ra"]
    rf = scalars["rf"]
    alpha = scalars["alpha_t"]
    st = scalars["st"]
    rho = scalars["rho"]
    hf = scalars["hf"]
    half_pitch = math.pi / z
    right = _right_side(rp, rb, ra, rf, alpha, st, rho, hf, involute_count, fillet_count)
    # Mirror across the tooth centreline (+X) for the left side, root to tip.
    left = [(x, -y) for x, y in reversed(right)]
    tip_a0 = math.atan2(left[-1][1], left[-1][0])
    tip_a1 = math.atan2(right[0][1], right[0][0])
    root_left = math.atan2(left[0][1], left[0][0])
    root_right = math.atan2(right[-1][1], right[-1][0])
    if root_left <= -half_pitch or root_right >= half_pitch:
        # Adjacent fillets meet. Close the space at the pitch line between teeth.
        root_left = max(root_left, -half_pitch + 1e-4)
        root_right = min(root_right, half_pitch - 1e-4)
    points: list[tuple[float, float]] = []
    points.extend(_arc(rf, -half_pitch, root_left, 4))
    points.extend(left[1:])
    points.extend(_arc(ra, tip_a0, tip_a1, 6)[1:])
    points.extend(right[1:])
    points.extend(_arc(rf, root_right, half_pitch, 4)[1:])
    return _dedupe(points)


def _right_side(
    rp: float,
    rb: float,
    ra: float,
    rf: float,
    alpha: float,
    st: float,
    rho: float,
    hf: float,
    involute_count: int = 28,
    fillet_count: int = 20,
) -> list[tuple[float, float]]:
    """Right flank from the tip down to the root, angles positive."""
    r_form = _form_radius(rp, rb, alpha, hf, rho)
    r_form = min(max(r_form, rb), ra * 0.999)
    involute = _involute_flank(rp, rb, alpha, st, r_form, ra, involute_count)
    fillet = _fillet_flank(rp, alpha, st, rho, hf, fillet_count)
    # involute is form -> tip. Fillet is root -> junction. Walk tip to root.
    side = list(reversed(involute))
    if fillet:
        downward = list(reversed(fillet))
        if side and math.hypot(side[-1][0] - downward[0][0], side[-1][1] - downward[0][1]) < 1e-4:
            downward = downward[1:]
        side.extend(downward)
    else:
        phi = _half_angle(r_form, rp, rb, alpha, st)
        side.append((rf * math.cos(max(phi, 0.0)), rf * math.sin(max(phi, 0.0))))
    side = [(x, y) for x, y in side if y >= -1e-9]
    side = _decreasing_radius(side)
    if side:
        end_radius = math.hypot(*side[-1])
        if end_radius - rf > 0.02:
            angle = math.atan2(side[-1][1], side[-1][0])
            side.append((rf * math.cos(angle), rf * math.sin(angle)))
    return side


def _decreasing_radius(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop a loop where an undercut fillet climbs back out through the involute."""
    if not points:
        return points
    kept = [points[0]]
    last = math.hypot(*points[0])
    for point in points[1:]:
        radius = math.hypot(*point)
        if radius <= last + 1e-5:
            kept.append(point)
            last = radius
    return kept


def _involute_flank(
    rp: float, rb: float, alpha: float, st: float, r_lo: float, r_hi: float, count: int
) -> list[tuple[float, float]]:
    points = []
    r_lo = max(r_lo, rb * (1.0 + 1e-6))
    if r_hi <= r_lo:
        r_hi = r_lo
    for index in range(count):
        t = index / (count - 1)
        radius = r_lo + (r_hi - r_lo) * t
        phi = _half_angle(radius, rp, rb, alpha, st)
        if phi <= 0:
            # The tooth came to a point before this radius.
            break
        points.append((radius * math.cos(phi), radius * math.sin(phi)))
    if not points:
        phi = _half_angle(r_lo, rp, rb, alpha, st)
        points.append((r_lo * math.cos(max(phi, 0.0)), r_lo * math.sin(max(phi, 0.0))))
    return points


def _fillet_flank(
    rp: float, alpha: float, st: float, rho: float, hf: float, count: int
) -> list[tuple[float, float]]:
    """Envelope of the rack tip fillet, from the root up to the involute.

    The rack space is centred on the tooth. η is positive away from the gear
    centre. The contact used is the point on the fillet circle opposite the
    pitch point, which is the root-side envelope.
    """
    if rho <= 1e-9:
        return []
    eta_tip = -hf
    eta_c = eta_tip + rho
    xi_c = st / 2.0 - eta_c * math.tan(alpha) + rho / math.cos(alpha)
    if xi_c <= 0:
        return []
    phi_tip = xi_c / rp
    phi_flank = (xi_c - eta_c / math.tan(alpha)) / rp
    points = []
    for index in range(count):
        t = index / (count - 1)
        phi = phi_tip + (phi_flank - phi_tip) * t
        away_x = xi_c - rp * phi
        away_y = eta_c
        norm = math.hypot(away_x, away_y)
        if norm < 1e-12:
            continue
        contact_xi = xi_c + rho * away_x / norm
        contact_eta = eta_c + rho * away_y / norm
        x, y = _rack_to_gear(contact_xi, contact_eta, phi, rp)
        if y < -1e-6:
            continue
        points.append((x, y))
    return points


def _rack_to_gear(xi: float, eta: float, phi: float, rp: float) -> tuple[float, float]:
    """Roll the rack on the reference circle. At phi 0 the tooth centre is on +X."""
    cosine = math.cos(phi)
    sine = math.sin(phi)
    slide = xi - rp * phi
    x = rp * cosine + slide * (-sine) + eta * cosine
    y = rp * sine + slide * cosine + eta * sine
    return x, y


def _half_angle(radius: float, rp: float, rb: float, alpha_t: float, st: float) -> float:
    """ISO 21771 half-angle of the transverse tooth thickness at ``radius``."""
    if radius < rb:
        radius = rb
    cosine = min(1.0, max(-1.0, rb / radius))
    alpha_r = math.acos(cosine)
    return st / (2.0 * rp) + _inv(alpha_t) - _inv(alpha_r)


def _inv(alpha: float) -> float:
    return math.tan(alpha) - alpha


def _inv_inverse(target: float) -> float:
    if target <= 0:
        return 0.0
    guess = min(1.2, max(1e-4, (3.0 * target) ** (1.0 / 3.0)))
    for _ in range(25):
        slope = math.tan(guess) ** 2
        if slope < 1e-14:
            break
        guess -= (_inv(guess) - target) / slope
        guess = min(max(guess, 1e-6), math.pi / 2.0 - 1e-6)
    return guess


def _arc(radius: float, a0: float, a1: float, count: int) -> list[tuple[float, float]]:
    if count < 2:
        count = 2
    points = []
    for index in range(count):
        angle = a0 + (a1 - a0) * index / (count - 1)
        points.append((radius * math.cos(angle), radius * math.sin(angle)))
    return points


def _tile(sector: list[tuple[float, float]], teeth: int) -> list[tuple[float, float]]:
    step = 2.0 * math.pi / teeth
    body = sector[:-1]
    points: list[tuple[float, float]] = []
    for tooth in range(teeth):
        cosine = math.cos(tooth * step)
        sine = math.sin(tooth * step)
        for x, y in body:
            points.append((x * cosine - y * sine, x * sine + y * cosine))
    return _dedupe(points)


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    cleaned = [points[0]]
    for point in points[1:]:
        if math.hypot(point[0] - cleaned[-1][0], point[1] - cleaned[-1][1]) > 1e-7:
            cleaned.append(point)
    if math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) < 1e-6:
        cleaned.pop()
    return cleaned


# --- bevel -----------------------------------------------------------------


def compute_bevel(spec: dict) -> dict:
    cone = bevel_cones(spec)
    warnings = list(cone["warnings"])
    if spec["face_width_mm"] > cone["outer_cone_distance_mm"] / 3.0:
        warnings.append(
            "Face width is longer than one third of the outer cone distance. "
            "AGMA 2005 recommends a shorter face."
        )
    standards = ["ISO 23509:2016", "ANSI/AGMA 2005-D03"]
    return {
        "type": spec["type"],
        "axis_layout": INTERSECTING,
        "standards": standards,
        "module_mm": spec["module_mm"],
        "module_convention": "outer transverse module m_et",
        "outer_transverse_module_mm": cone["m_et"],
        "mean_transverse_module_mm": cone["m_mt"],
        "mean_normal_module_mm": cone["m_mn"],
        "teeth": spec["teeth"],
        "mate_teeth": cone["mate_teeth"],
        "ratio": cone["ratio"],
        "pressure_angle_deg": spec["pressure_angle_deg"],
        "normal_pressure_angle_deg": cone["alpha_n_deg"],
        "transverse_pressure_angle_deg": cone["alpha_t_deg"],
        "spiral_angle_deg": spec["spiral_angle_deg"],
        "hand": spec["hand"],
        "shaft_angle_deg": cone["shaft_angle_deg"],
        "pitch_angle_deg": cone["pitch_angle_deg"],
        "mate_pitch_angle_deg": cone["mate_pitch_angle_deg"],
        "face_angle_deg": cone["face_angle_deg"],
        "root_angle_deg": cone["root_angle_deg"],
        "addendum_angle_deg": cone["addendum_angle_deg"],
        "dedendum_angle_deg": cone["dedendum_angle_deg"],
        "outer_cone_distance_mm": cone["outer_cone_distance_mm"],
        "mean_cone_distance_mm": cone["mean_cone_distance_mm"],
        "inner_cone_distance_mm": cone["inner_cone_distance_mm"],
        "face_width_mm": spec["face_width_mm"],
        "pitch_diameter_mm": cone["pitch_diameter_mm"],
        "reference_diameter_mm": cone["pitch_diameter_mm"],
        "tip_diameter_mm": cone["tip_diameter_mm"],
        "addendum_mm": cone["addendum_mm"],
        "dedendum_mm": cone["dedendum_mm"],
        "whole_depth_mm": cone["whole_depth_mm"],
        "clearance_mm": cone["clearance_mm"],
        "profile_shift": cone["profile_shift"],
        "back_cone_distance_mm": cone["back_cone_distance_mm"],
        "virtual_teeth": cone["virtual_teeth"],
        "virtual_base_diameter_mm": cone["virtual_base_diameter_mm"],
        "tooth_thickness_transverse_mm": cone["tooth_thickness_mm"],
        "pitch_apex_to_crown_mm": cone["pitch_apex_to_crown_mm"],
        "form_radius_mm": cone["form_radius_mm"],
        "undercut": cone["undercut"],
        "warnings": warnings,
        "construction": (
            "Outer transverse module. Pitch cones from the shaft angle and ratio (ISO 23509, "
            "offset zero). Tooth profile is the Tredgold virtual spur on the back cone "
            "(AGMA 2005-D03), ruled to the pitch apex. Spiral angle is constant along the face."
        ),
        "assumptions": [
            "Axes intersect. Hypoid offset is zero.",
            "Gleason whole depth is 2.188 m_et for straight bevels and 1.888 m_et for spiral bevels, with no added constant.",
            "Addendum is split by the Gleason ratio term so the pinion and the gear shifts sum to zero.",
            "The reported face and root angles are the Gleason angles (parallel clearance). "
            "The solid rules the outer back-cone profile to the pitch apex.",
            "A spiral is a logarithmic spiral of the mean spiral angle, not a circular face-mill path.",
            "Virtual fillet radius is 0.30 of the local transverse module when it fits the dedendum.",
        ],
    }


def bevel_cones(spec: dict) -> dict:
    """Pitch cones, Gleason addendum, and the outer virtual spur."""
    z = int(spec["teeth"])
    shaft = math.radians(float(spec["shaft_angle_deg"]))
    if spec.get("pitch_angle_deg") is not None and spec.get("mate_teeth") is None and spec.get("ratio") is None:
        delta = math.radians(float(spec["pitch_angle_deg"]))
        mate = None
        # Ratio implied by a mate that closes the shaft angle, when both cones sum to the shaft angle.
        if delta >= shaft:
            raise GeometryError("pitch_angle_deg must be less than shaft_angle_deg when no mate is given.")
        delta_mate = shaft - delta
        ratio = math.sin(delta_mate) / math.sin(delta)
        mate_teeth = ratio * z
    else:
        if spec.get("mate_teeth") is not None:
            mate = int(spec["mate_teeth"])
            ratio = mate / z
        else:
            ratio = float(spec["ratio"])
            mate = ratio * z
            mate_teeth = mate
        delta = math.atan(math.sin(shaft) / (ratio + math.cos(shaft)))
        delta_mate = shaft - delta
        mate_teeth = mate
    if not 0.0 < delta < math.pi:
        raise GeometryError("The pitch cone angle is not between 0 and 180 degrees.")
    m_et = float(spec["module_mm"])
    beta_m = math.radians(float(spec.get("spiral_angle_deg") or 0.0))
    alpha_n = math.radians(float(spec["pressure_angle_deg"]))
    alpha_t = math.atan(math.tan(alpha_n) / math.cos(beta_m)) if abs(beta_m) > 1e-12 else alpha_n
    d = m_et * z
    outer = d / (2.0 * math.sin(delta))
    face = float(spec["face_width_mm"])
    if face >= outer * 0.95:
        raise GeometryError("face_width_mm must be shorter than the outer cone distance.")
    mean = outer - face / 2.0
    inner = outer - face
    spiral = spec["type"] == "spiral_bevel"
    # Gleason addendum split. Member 2 is the one with more teeth.
    z_self = float(z)
    z_other = float(mate_teeth)
    if z_other >= z_self:
        z1, z2 = z_self, z_other
        self_is_pinion = True
    else:
        z1, z2 = z_other, z_self
        self_is_pinion = False
    if spiral:
        ha_star, whole = 0.85, 1.888
        ha2 = m_et * (0.460 + 0.390 * (z1 / z2) ** 2)
        working = 1.700 * m_et
    else:
        ha_star, whole = 1.0, 2.188
        ha2 = m_et * (0.540 + 0.460 * (z1 / z2) ** 2)
        working = 2.000 * m_et
    ha1 = working - ha2
    ha = ha1 if self_is_pinion else ha2
    hf = whole * m_et - ha
    x = ha / m_et - ha_star
    theta_f = math.atan(hf / outer)
    theta_a_mate_hf = whole * m_et - (ha1 if not self_is_pinion else ha2)
    # Parallel clearance: this gear's addendum angle equals the mate's dedendum angle.
    theta_a = math.atan(theta_a_mate_hf / outer)
    face_angle = delta + theta_a
    root_angle = delta - theta_f
    tip_diameter = d + 2.0 * ha * math.cos(delta)
    crown = outer * math.cos(delta) - ha * math.sin(delta)
    r_back = (d / 2.0) / math.cos(delta)
    z_v = z / math.cos(delta)
    virtual = {
        "type": "virtual",
        "virtual": True,
        "module_mm": m_et,
        "teeth": z_v,
        "pressure_angle_deg": math.degrees(alpha_t),
        "helix_angle_deg": 0.0,
        "profile_shift": x,
        "face_width_mm": face,
        "rack": "A",
        "addendum_coefficient": ha_star,
        "dedendum_coefficient": whole - ha_star,
        "fillet_radius_coefficient": spec.get("fillet_radius_coefficient")
        if spec.get("fillet_radius_coefficient") is not None
        else 0.30,
    }
    scalars = cylindrical_scalars(virtual)
    m_mt = m_et * mean / outer
    m_mn = m_mt * math.cos(beta_m)
    warnings: list[str] = []
    if scalars["undercut"]:
        warnings.append("The virtual spur on the back cone is undercut.")
    if spec.get("pitch_angle_deg") is not None and spec.get("mate_teeth") is None and spec.get("ratio") is None:
        warnings.append(
            "Pitch angle was given directly, so no integer mate tooth count is stored. "
            "The ratio is sin of the mate pitch angle over sin of this pitch angle."
        )
    return {
        "m_et": m_et,
        "m_mt": m_mt,
        "m_mn": m_mn,
        "mate_teeth": None if mate is None else mate,
        "ratio": (z_other / z_self) if mate is not None or spec.get("ratio") is not None else ratio,
        "alpha_n_deg": math.degrees(alpha_n),
        "alpha_t_deg": math.degrees(alpha_t),
        "shaft_angle_deg": math.degrees(shaft),
        "pitch_angle_deg": math.degrees(delta),
        "mate_pitch_angle_deg": math.degrees(delta_mate),
        "pitch_angle_rad": delta,
        "face_angle_deg": math.degrees(face_angle),
        "root_angle_deg": math.degrees(root_angle),
        "addendum_angle_deg": math.degrees(theta_a),
        "dedendum_angle_deg": math.degrees(theta_f),
        "outer_cone_distance_mm": outer,
        "mean_cone_distance_mm": mean,
        "inner_cone_distance_mm": inner,
        "pitch_diameter_mm": d,
        "tip_diameter_mm": tip_diameter,
        "addendum_mm": ha,
        "dedendum_mm": hf,
        "whole_depth_mm": whole * m_et,
        "clearance_mm": whole * m_et - working,
        "profile_shift": x,
        "back_cone_distance_mm": r_back,
        "virtual_teeth": z_v,
        "virtual_base_diameter_mm": scalars["db"],
        "tooth_thickness_mm": scalars["st"],
        "pitch_apex_to_crown_mm": crown,
        "form_radius_mm": scalars["form_radius"],
        "undercut": scalars["undercut"],
        "warnings": warnings,
        "virtual_spec": virtual,
        "scalars": scalars,
        "beta_m": beta_m,
    }


def bevel_station_points(
    spec: dict,
    cone: dict,
    cone_distance: float,
    spiral: float,
    involute_count: int = 28,
    fillet_count: int = 20,
) -> list[tuple[float, float, float]]:
    """Wrapped tooth outline on the back cone at one cone distance.

    Points run around the gear. The pitch apex is the origin and the axis is +Z.
    """
    outer = cone["outer_cone_distance_mm"]
    scale = cone_distance / outer
    virtual = dict(cone["virtual_spec"])
    virtual["module_mm"] = cone["m_et"] * scale
    scalars = cylindrical_scalars(virtual)
    sector = _tooth_sector(scalars, involute_count, fillet_count)
    delta = cone["pitch_angle_rad"]
    teeth = int(spec["teeth"])
    step = 2.0 * math.pi / teeth
    body = sector[:-1]
    points = []
    for tooth in range(teeth):
        for x, y in body:
            rho = math.hypot(x, y)
            psi = math.atan2(y, x)
            theta = psi / math.cos(delta) + tooth * step + spiral
            points.append(_wrap(rho, theta, cone_distance, delta))
    return points


def bevel_profile_rings(
    spec: dict, stations: int = 2, involute_count: int = 18, fillet_count: int = 10
) -> tuple[dict, list[list[tuple[float, float, float]]]]:
    """Tooth outlines from the inner cone distance to the outer one.

    Every ring is the outer back-cone profile scaled toward the pitch apex and
    rotated by the constant spiral angle. The rings share one point order.
    """
    if stations < 2:
        raise GeometryError("A bevel blank needs at least two stations along the face.")
    cone = bevel_cones(spec)
    outer_distance = cone["outer_cone_distance_mm"]
    inner_distance = cone["inner_cone_distance_mm"]
    beta = cone["beta_m"]
    outer_spin = spiral_offset(cone, outer_distance, beta)
    outer = bevel_station_points(spec, cone, outer_distance, outer_spin, involute_count, fillet_count)
    outer = _clean_ring(outer)
    rings: list[list[tuple[float, float, float]]] = []
    for index in range(stations):
        fraction = index / (stations - 1)
        distance = inner_distance + (outer_distance - inner_distance) * fraction
        turn = spiral_offset(cone, distance, beta) - outer_spin
        scale = distance / outer_distance
        cosine = math.cos(turn)
        sine = math.sin(turn)
        ring = []
        for x, y, z in outer:
            sx = x * scale
            sy = y * scale
            sz = z * scale
            ring.append((sx * cosine - sy * sine, sx * sine + sy * cosine, sz))
        rings.append(ring)
    return cone, rings


def _clean_ring(points: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    cleaned = [points[0]]
    for point in points[1:]:
        if math.dist(point, cleaned[-1]) > 1e-6:
            cleaned.append(point)
    if math.dist(cleaned[0], cleaned[-1]) < 1e-6:
        cleaned.pop()
    return cleaned


def spiral_offset(cone: dict, cone_distance: float, beta: float) -> float:
    """Gear rotation of a constant spiral angle between the mean section and ``cone_distance``."""
    if abs(beta) < 1e-12:
        return 0.0
    mean = cone["mean_cone_distance_mm"]
    delta = cone["pitch_angle_rad"]
    return (math.tan(beta) / math.sin(delta)) * math.log(cone_distance / mean)


def _wrap(rho: float, theta: float, cone_distance: float, delta: float) -> tuple[float, float, float]:
    z_apex = cone_distance / math.cos(delta)
    z = z_apex - rho * math.sin(delta)
    radial = rho * math.cos(delta)
    return (radial * math.cos(theta), radial * math.sin(theta), z)


# --- pairs -----------------------------------------------------------------


def cylindrical_mount(left: dict, right: dict, centre_mm: float | None = None) -> dict:
    """Operating centre, backlash, and contact ratio for a spur or helical pair.

    The zero-backlash centre is the ISO 21771 relation :func:`pair_geometry`
    already uses. ``centre_mm`` is the actual distance between the axes. Omit
    it to sit on that centre, where both backlash numbers are zero.
    """
    if abs(left["normal_module_mm"] - right["normal_module_mm"]) > 1e-6:
        raise GeometryError("A cylindrical pair needs the same normal module.")
    if abs(left["normal_pressure_angle_deg"] - right["normal_pressure_angle_deg"]) > 1e-6:
        raise GeometryError("A cylindrical pair needs the same normal pressure angle.")
    if abs(abs(left["helix_angle_deg"]) - abs(right["helix_angle_deg"])) > 1e-6:
        raise GeometryError("A cylindrical pair needs helix angles of equal magnitude.")
    warnings = []
    if left["helix_angle_deg"] * right["helix_angle_deg"] > 1e-9:
        raise GeometryError(
            "External helical gears need opposite hands, so these two do not mesh on parallel shafts."
        )
    if abs(left["helix_angle_deg"]) > 1e-9 and abs(left["helix_angle_deg"] + right["helix_angle_deg"]) > 1e-6:
        warnings.append("The helix angles are not equal and opposite, so the shafts are not parallel.")
    alpha_t = math.radians(left["transverse_pressure_angle_deg"])
    alpha_n = math.radians(left["normal_pressure_angle_deg"])
    x_sum = left["profile_shift"] + right["profile_shift"]
    z_sum = float(left["teeth"]) + float(right["teeth"])
    inv_w = _inv(alpha_t) + 2.0 * math.tan(alpha_n) * x_sum / z_sum
    # A negative involute has no working pressure angle. Reporting angle 0
    # would place the gears on the base circles and claim backlash is zero.
    if inv_w < -1e-12:
        raise GeometryError(
            "These profile shifts leave backlash even when the base circles touch, "
            "so there is no zero-backlash centre distance."
        )
    alpha_w0 = _inv_inverse(inv_w)
    d_sum = left["reference_diameter_mm"] + right["reference_diameter_mm"]
    ideal = 0.5 * d_sum * math.cos(alpha_t) / math.cos(alpha_w0)
    rb_sum = 0.5 * (left["base_diameter_mm"] + right["base_diameter_mm"])
    face = min(left["face_width_mm"], right["face_width_mm"])
    beta = math.radians(left["helix_angle_deg"])
    eps_b = face * abs(math.sin(beta)) / (math.pi * left["normal_module_mm"]) if abs(beta) > 1e-12 else 0.0
    # A centre that matches the zero-backlash figure is that mount. Rounding
    # the reported centre and passing it back must not invent backlash.
    if centre_mm is not None and abs(float(centre_mm) - ideal) <= 1e-4:
        centre_mm = None
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
        # cos α' = (rb1 + rb2) / a'. Inside the base circles there is no working angle.
        cosine = rb_sum / centre
        if cosine >= 1.0 - 1e-12:
            alpha_w = 0.0
            backlash_t = None
            backlash_n = None
            defined = False
        else:
            alpha_w = math.acos(min(1.0, cosine))
            backlash_t = 2.0 * centre * (_inv(alpha_w) - inv_w)
            ratio = (2.0 * centre * float(left["teeth"]) / z_sum) / left["reference_diameter_mm"]
            beta_w = math.atan(math.tan(beta) * ratio) if abs(beta) > 1e-12 else 0.0
            backlash_n = backlash_t * math.cos(alpha_w) * math.cos(beta_w)
            defined = True
    if defined:
        eps_a = _contact_ratio(
            left["tip_diameter_mm"] / 2.0,
            left["base_diameter_mm"] / 2.0,
            right["tip_diameter_mm"] / 2.0,
            right["base_diameter_mm"] / 2.0,
            centre,
            alpha_w,
            left["transverse_module_mm"],
            alpha_t,
        )
    else:
        eps_a = None
    return {
        "ideal_centre_distance_mm": ideal,
        "centre_distance_mm": centre,
        "reference_centre_distance_mm": d_sum / 2.0,
        "working_transverse_pressure_angle_deg": math.degrees(alpha_w),
        "zero_backlash_transverse_pressure_angle_deg": math.degrees(alpha_w0),
        "circumferential_backlash_mm": backlash_t,
        "normal_backlash_mm": backlash_n,
        "backlash_defined": defined,
        "base_circles_overlap": not defined,
        "transverse_contact_ratio": eps_a,
        "overlap_ratio": eps_b,
        "total_contact_ratio": None if eps_a is None else eps_a + eps_b,
        "warnings": warnings,
    }


def _cylindrical_pair(left: dict, right: dict) -> dict:
    mount = cylindrical_mount(left, right, None)
    spin = 180.0 * (1.0 + 1.0 / right["teeth"])
    return {
        "axis_layout": PARALLEL,
        "standards": ["ISO 21771:2007"],
        "centre_distance_mm": mount["centre_distance_mm"],
        "reference_centre_distance_mm": mount["reference_centre_distance_mm"],
        "working_transverse_pressure_angle_deg": mount["working_transverse_pressure_angle_deg"],
        "transverse_contact_ratio": mount["transverse_contact_ratio"],
        "overlap_ratio": mount["overlap_ratio"],
        "total_contact_ratio": mount["total_contact_ratio"],
        "backlash_mm": 0.0,
        "warnings": mount["warnings"],
        "placement": {
            "pinion": {"position_mm": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0]},
            "gear": {"position_mm": [mount["centre_distance_mm"], 0.0, 0.0], "rotation_deg": [0.0, 0.0, spin]},
            "note": (
                "Pinion axis +Z, tooth 0 on +X, mid-face on z=0. "
                "The gear sits on +X at the zero-backlash centre distance and is turned "
                "so a tooth space faces that pinion tooth. Helical hands must be opposite."
            ),
        },
        "assumptions": [
            "Zero backlash at the working centre distance from ISO 21771.",
            "Contact ratio uses the tip cylinders as the ends of the path of contact.",
            "Overlap ratio uses the shorter face width.",
        ],
    }


def _bevel_pair(left: dict, right: dict) -> dict:
    if abs(left["outer_transverse_module_mm"] - right["outer_transverse_module_mm"]) > 1e-6:
        raise GeometryError("A bevel pair needs the same outer transverse module.")
    if abs(left["shaft_angle_deg"] - right["shaft_angle_deg"]) > 1e-4:
        raise GeometryError("Both bevel gears must use the same shaft angle.")
    if abs(left["normal_pressure_angle_deg"] - right["normal_pressure_angle_deg"]) > 1e-6:
        raise GeometryError("A bevel pair needs the same normal pressure angle.")
    # Each gear's mate tooth count should be the other's tooth count when both were specified that way.
    warnings = []
    if left.get("mate_teeth") not in (None, right["teeth"]) or right.get("mate_teeth") not in (None, left["teeth"]):
        warnings.append("mate_teeth on one gear does not equal the tooth count of the other.")
    if abs(left["pitch_angle_deg"] + right["pitch_angle_deg"] - left["shaft_angle_deg"]) > 1e-3:
        warnings.append("The pitch angles do not sum to the shaft angle. Check ratio and mate_teeth.")
    if left["type"] == "spiral_bevel" or right["type"] == "spiral_bevel":
        if abs(abs(left["spiral_angle_deg"]) - abs(right["spiral_angle_deg"])) > 1e-6:
            raise GeometryError("A spiral bevel pair needs equal mean spiral angles.")
        if left["spiral_angle_deg"] * right["spiral_angle_deg"] > 1e-9:
            raise GeometryError("A spiral bevel pair needs opposite hands, so these two do not mesh.")
    eps_a, eps_b = _bevel_contact(left, right)
    shaft = left["shaft_angle_deg"]
    spin = 180.0 * (1.0 + 1.0 / right["teeth"])
    return {
        "axis_layout": INTERSECTING,
        "standards": ["ISO 23509:2016", "ANSI/AGMA 2005-D03"],
        "shaft_angle_deg": shaft,
        "centre_distance_mm": 0.0,
        "transverse_contact_ratio": eps_a,
        "overlap_ratio": eps_b,
        "total_contact_ratio": eps_a + eps_b,
        "backlash_mm": 0.0,
        "warnings": warnings,
        "placement": {
            "pinion": {"position_mm": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0]},
            "gear": {"position_mm": [0.0, 0.0, 0.0], "rotation_deg": [0.0, shaft, spin]},
            "note": (
                "Both pitch apexes are at the origin. The pinion axis is +Z and tooth 0 is on +X. "
                "The gear is turned about Y by the shaft angle, then about its own axis so a tooth "
                "space lies on the common pitch generator. Rotation order is X, then Y, then Z "
                "in the gear's placement record; apply the Z spin in the gear frame first."
            ),
        },
        "assumptions": [
            "Zero backlash. Pitch apexes coincide.",
            "Transverse contact ratio is the Tredgold virtual spur pair at mid-face.",
            "Overlap ratio is the rotation of a constant spiral angle along the face, divided by the angular pitch.",
        ],
    }


def virtual_spur_at(data: dict, cone_distance_mm: float) -> dict:
    """Tredgold virtual spur at one cone distance.

    Module and addendum scale by cone distance over the outer cone distance,
    the same scale the mid-face contact ratio uses. The returned spec is the
    transverse outline of that section.
    """
    outer = float(data["outer_cone_distance_mm"])
    if cone_distance_mm <= 0 or outer <= 0:
        raise GeometryError("Cone distance must be positive.")
    scale = float(cone_distance_mm) / outer
    module = float(data["outer_transverse_module_mm"]) * scale
    addendum = float(data["addendum_mm"]) * scale
    dedendum = float(data["dedendum_mm"]) * scale
    teeth = float(data["virtual_teeth"])
    alpha_deg = float(data["transverse_pressure_angle_deg"])
    alpha = math.radians(alpha_deg)
    shift = float(data["profile_shift"])
    if module <= 0:
        raise GeometryError("The virtual module is not positive.")
    pitch = teeth * module / 2.0
    spec = {
        "virtual": True,
        "module_mm": module,
        "teeth": teeth,
        "pressure_angle_deg": alpha_deg,
        "helix_angle_deg": 0.0,
        "profile_shift": shift,
        "face_width_mm": max(float(data["face_width_mm"]) * scale, module),
        "rack": "A",
        "addendum_coefficient": addendum / module - shift,
        "dedendum_coefficient": dedendum / module + shift,
        "fillet_radius_coefficient": 0.30,
    }
    scalars = cylindrical_scalars(spec)
    return {
        "spec": spec,
        "scalars": scalars,
        "ra": pitch + addendum,
        "rb": pitch * math.cos(alpha),
        "rf": pitch - dedendum,
        "rp": pitch,
        "m": module,
        "mt": module,
        "alpha": alpha,
        "alpha_t": alpha,
        "alpha_t_deg": alpha_deg,
        "x": shift,
        "z": teeth,
        "d": 2.0 * pitch,
        "db": 2.0 * pitch * math.cos(alpha),
        "da": 2.0 * (pitch + addendum),
        "form_radius": scalars["form_radius"],
        "undercut": scalars["undercut"],
    }


def _bevel_contact(left: dict, right: dict) -> tuple[float, float]:
    """Virtual spur contact ratio at mid-face, plus spiral overlap."""
    a = virtual_spur_at(left, left["mean_cone_distance_mm"])
    b = virtual_spur_at(right, right["mean_cone_distance_mm"])
    centre = a["rp"] + b["rp"]
    alpha_w = a["alpha"]
    # Shifts sum to zero in the Gleason split, so the working angle stays the transverse angle.
    eps_a = _contact_ratio(a["ra"], a["rb"], b["ra"], b["rb"], centre, alpha_w, a["m"], a["alpha"])
    beta = math.radians(left["spiral_angle_deg"])
    if abs(beta) < 1e-12:
        return eps_a, 0.0
    # Angular travel of the constant spiral from toe to heel, divided by the angular pitch.
    outer = left["outer_cone_distance_mm"]
    inner = left["inner_cone_distance_mm"]
    delta = math.radians(left["pitch_angle_deg"])
    travel = abs((math.tan(beta) / math.sin(delta)) * math.log(outer / inner))
    eps_b = travel / (2.0 * math.pi / left["teeth"])
    return eps_a, eps_b


def _contact_ratio(
    ra1: float, rb1: float, ra2: float, rb2: float, centre: float, alpha_w: float, mt: float, alpha_t: float
) -> float:
    path = math.sqrt(max(ra1 * ra1 - rb1 * rb1, 0.0)) + math.sqrt(max(ra2 * ra2 - rb2 * rb2, 0.0))
    path -= centre * math.sin(alpha_w)
    base = math.pi * mt * math.cos(alpha_t)
    if base <= 0:
        return 0.0
    return path / base


# --- spec parsing helpers --------------------------------------------------


def _reject_unknown(name: str) -> None:
    known = ", ".join(sorted(set(CYLINDRICAL | BEVEL)))
    raise GeometryError(
        f"Unknown gear type {name!r}. Use one of: {known}. "
        "Hypoid is an offset-axis family and is not registered."
    )


def _required_positive(spec: dict, key: str) -> float:
    if key not in spec or spec[key] is None:
        raise GeometryError(f"A gear spec needs {key}.")
    value = _number(spec[key], key)
    if value <= 0:
        raise GeometryError(f"{key} must be positive.")
    return value


def _required_teeth(spec: dict) -> int:
    if "teeth" not in spec or spec["teeth"] is None:
        raise GeometryError("A gear spec needs teeth.")
    value = spec["teeth"]
    if isinstance(value, bool) or not float(value).is_integer():
        raise GeometryError("teeth must be an integer.")
    teeth = int(value)
    if teeth < 6:
        raise GeometryError("teeth must be at least 6.")
    if teeth > 500:
        raise GeometryError("teeth must be at most 500.")
    return teeth


def _number(value, label: str, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise GeometryError(f"{label} is required.")
        return float(default)
    if isinstance(value, bool):
        raise GeometryError(f"{label} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a number.") from exc
    if not math.isfinite(number):
        raise GeometryError(f"{label} must be a finite number.")
    return number


def _optional_coeff(value, label: str, low: float, high: float):
    if value is None:
        return None
    number = _number(value, label)
    if not low <= number <= high:
        raise GeometryError(f"{label} must be between {low} and {high}.")
    return number


def _apply_hand(angle: float, hand: str | None, label: str) -> float:
    if hand is None:
        return angle
    signed = abs(angle) if hand == "right" else -abs(angle)
    if angle * signed < -1e-9:
        raise GeometryError(f"{label} and hand disagree.")
    return signed


def _hub(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise GeometryError("hub must be an object with diameter_mm and length_mm.")
    diameter = _number(value.get("diameter_mm"), "hub.diameter_mm")
    length = _number(value.get("length_mm"), "hub.length_mm")
    if diameter <= 0 or length <= 0:
        raise GeometryError("Hub diameter and length must be positive.")
    side = str(value.get("side") or "both").strip().lower()
    if side not in {"front", "back", "both", "heel", "toe"}:
        raise GeometryError("hub.side must be front, back, both, heel, or toe.")
    return {"diameter_mm": diameter, "length_mm": length, "side": side}


def _keyway(value, bore: float | None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise GeometryError("keyway must be an object.")
    standard = str(value.get("standard") or "din6885").strip().lower().replace("-", "").replace("_", "")
    if standard not in {"din6885", "din68851"}:
        raise GeometryError("keyway.standard must be din6885 (DIN 6885-1).")
    if bore is None:
        raise GeometryError("A DIN 6885-1 keyway needs bore_mm.")
    try:
        row = tables.din6885_key(bore)
    except ValueError as exc:
        raise GeometryError("DIN 6885-1 covers bore diameters from 6 mm through 230 mm.") from exc
    width = _number(value.get("width_mm"), "keyway.width_mm", default=row["width_mm"])
    depth = _number(value.get("depth_mm"), "keyway.depth_mm", default=row["hub_depth_mm"])
    if width <= 0 or depth <= 0:
        raise GeometryError("Keyway width and depth must be positive.")
    if width >= bore:
        raise GeometryError("Keyway width must be smaller than the bore.")
    return {
        "standard": "DIN 6885-1:1968",
        "width_mm": width,
        "depth_mm": depth,
        "table_width_mm": row["width_mm"],
        "table_hub_depth_mm": row["hub_depth_mm"],
        "table_shaft_depth_mm": row["shaft_depth_mm"],
        "bore_to_mm": row["bore_to_mm"],
    }


def _spline(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise GeometryError("spline must be an object.")
    standard = str(value.get("standard") or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    if standard in {"iso14", "iso14:1982"}:
        series = str(value.get("series") or "medium").strip().lower()
        minor = value.get("minor_mm", value.get("minor_diameter_mm"))
        if minor is None:
            raise GeometryError("An ISO 14 spline needs minor_mm and series ('light' or 'medium').")
        try:
            row = tables.iso14_spline(series, float(minor))
        except (ValueError, TypeError) as exc:
            raise GeometryError(
                f"No ISO 14 {series} spline with minor diameter {minor}. "
                "Use a minor diameter from ISO 14:1982 Table 1."
            ) from exc
        return {
            "standard": "ISO 14:1982",
            "series": row["series"],
            "count": row["count"],
            "minor_mm": row["minor_mm"],
            "major_mm": row["major_mm"],
            "width_mm": row["width_mm"],
            "designation": row["designation"],
        }
    if standard in {"din5480", "din54801"}:
        module = _number(value.get("module_mm"), "spline.module_mm")
        teeth = value.get("teeth")
        if teeth is None or isinstance(teeth, bool) or not float(teeth).is_integer():
            raise GeometryError("A DIN 5480 spline needs an integer teeth count.")
        teeth = int(teeth)
        if teeth < 6 or teeth > 82:
            raise GeometryError("DIN 5480-1 tooth counts run from 6 to 82.")
        if module not in tables.DIN_5480_MODULES and not any(abs(module - item) < 1e-9 for item in tables.DIN_5480_MODULES):
            raise GeometryError(
                "DIN 5480-1 modules are 0.5, 0.6, 0.75, 0.8, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5, 6, 8, and 10."
            )
        reference = value.get("reference_diameter_mm")
        if reference is None:
            x = 0.0
            reference_diameter = module * (teeth + 1.1)
        else:
            reference_diameter = _number(reference, "spline.reference_diameter_mm")
            # d_B = m * (z + 2 x + 1.1)
            x = (reference_diameter / module - teeth - 1.1) / 2.0
        return {
            "standard": "DIN 5480-1:2006",
            "module_mm": module,
            "teeth": teeth,
            "profile_shift": x,
            "reference_diameter_mm": reference_diameter,
            "pressure_angle_deg": 30.0,
            "hub_tip_diameter_mm": module * (teeth + 2.0 * x - 0.9),
            "hub_root_diameter_mm": module * (teeth + 2.0 * x + 1.1),
            "fillet_radius_mm": 0.16 * module,
        }
    raise GeometryError("spline.standard must be 'iso14' or 'din5480'.")


def _round_tree(value, digits: int = 6):
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {key: _round_tree(item, digits) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_tree(item, digits) for item in value]
    if isinstance(value, tuple):
        return [_round_tree(item, digits) for item in value]
    return value
