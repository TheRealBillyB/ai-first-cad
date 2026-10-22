"""Bolted-joint checks for library fasteners.

Preload, tightening torque, clamp versus external load, slip, thread
stripping, and bolt yield. The slip margin is the number that matters for
vibration self-loosening: once the clamped plates slide, the thread can walk
even though the bolt has not yielded. Every report lists the assumptions
behind those numbers.
"""

from __future__ import annotations

import math

from aicad.errors import GeometryError
from aicad.honesty import ROUGH_ESTIMATE, finish

# Coarse pitches, mm. A size written M8-1.25 uses the explicit pitch.
_COARSE_PITCH = {
    "M3": 0.5,
    "M4": 0.7,
    "M5": 0.8,
    "M6": 1.0,
    "M8": 1.25,
    "M10": 1.5,
    "M12": 1.75,
    "M14": 2.0,
    "M16": 2.0,
    "M20": 2.5,
    "M24": 3.0,
}

# Proof stress Sp, yield Sy, ultimate Sut, in MPa. Handbook values for ISO 898-1.
_GRADES = {
    "8.8": {"proof_mpa": 580.0, "yield_mpa": 640.0, "ultimate_mpa": 800.0, "name": "property class 8.8"},
    "10.9": {"proof_mpa": 830.0, "yield_mpa": 940.0, "ultimate_mpa": 1040.0, "name": "property class 10.9"},
    "12.9": {"proof_mpa": 970.0, "yield_mpa": 1100.0, "ultimate_mpa": 1220.0, "name": "property class 12.9"},
    "a2-70": {
        "proof_mpa": 450.0,
        "yield_mpa": 450.0,
        "ultimate_mpa": 700.0,
        "name": "austenitic A2-70",
    },
}

# Nut factor K in T = K * F * d, and plate friction for the under-head / thread condition.
_CONDITIONS = {
    "dry": {"nut_factor": 0.20, "friction": 0.15},
    "zinc": {"nut_factor": 0.22, "friction": 0.15},
    "lubricated": {"nut_factor": 0.15, "friction": 0.10},
    "threadlocker": {"nut_factor": 0.18, "friction": 0.15},
}

_RATINGS = ("low", "moderate", "high")


def check_joint(spec: dict) -> dict:
    """One fastener, or a list under ``bolts``. The worst loosening rating leads."""
    if not isinstance(spec, dict):
        raise GeometryError("A bolt check is an object with size, grade, and loads.")
    rows = spec.get("bolts")
    if rows is None:
        return _one(spec)
    if not isinstance(rows, list) or not rows:
        raise GeometryError("bolts must be a list of fastener checks.")
    checked = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise GeometryError(f"Bolt {index} must be an object.")
        merged = {**spec, **row}
        merged.pop("bolts", None)
        checked.append(_one(merged))
    order = {name: index for index, name in enumerate(_RATINGS)}
    worst = max(checked, key=lambda item: order[item["loosening"]["rating"]])
    return finish(
        {
            "bolts": checked,
            "worst": worst["loosening"]["rating"],
            "assumptions": checked[0]["assumptions"],
            "note": f"{len(checked)} fasteners. The joint is rated by the highest loosening risk.",
        },
        kind=ROUGH_ESTIMATE,
        name="Bolted joint",
        what="Preload, torque, and slip are handbook estimates. The worst loosening rating leads.",
        limits=[
            "The nut factor is not a measured torque-tension test.",
            "A locking feature resists turning. It does not create clamp force.",
            "Slip below a margin of 1 is the loosening case, even when the bolt is below yield.",
        ],
    )


def _one(spec: dict) -> dict:
    size, pitch = _size_and_pitch(spec.get("size"))
    grade = _grade(spec.get("grade") or "8.8")
    condition = str(spec.get("condition") or "dry").lower()
    if condition not in _CONDITIONS:
        known = ", ".join(_CONDITIONS)
        raise GeometryError(f"Friction condition {condition!r} is unknown. Use {known}.")
    defaults = _CONDITIONS[condition]
    fraction = _number(spec.get("preload_fraction"), 0.70, "preload_fraction")
    if not 0.3 <= fraction <= 0.95:
        raise GeometryError("preload_fraction is the share of proof load, from 0.3 to 0.95.")
    nut_factor = _number(spec.get("nut_factor"), defaults["nut_factor"], "nut_factor")
    if not 0.05 <= nut_factor <= 0.40:
        raise GeometryError("nut_factor K in T = K*F*d is expected between 0.05 and 0.40.")
    friction = _number(spec.get("friction"), defaults["friction"], "friction")
    if not 0.02 <= friction <= 0.50:
        raise GeometryError("friction is the plate coefficient, expected between 0.02 and 0.50.")
    interfaces = int(_number(spec.get("interfaces"), 1, "interfaces"))
    if interfaces < 1 or interfaces > 6:
        raise GeometryError("interfaces is how many slip planes the clamp squeezes, from 1 to 6.")
    axial = _number(spec.get("axial_n"), 0.0, "axial_n")
    shear = abs(_number(spec.get("shear_n"), 0.0, "shear_n"))
    diameter = _diameter(size)
    area = _tensile_area(diameter, pitch)
    grip = _number(spec.get("grip_mm"), None, "grip_mm")
    if grip is None:
        grip = _number(spec.get("length"), 2.5 * diameter, "grip_mm")
    if grip <= 0:
        raise GeometryError("grip_mm is the clamped thickness and must be positive.")
    engagement = _number(spec.get("engagement_mm"), 0.8 * diameter, "engagement_mm")
    if engagement <= 0:
        raise GeometryError("engagement_mm must be positive.")
    member_modulus = _number(spec.get("member_modulus_mpa"), 210000.0, "member_modulus_mpa")
    bolt_modulus = _number(spec.get("bolt_modulus_mpa"), 210000.0, "bolt_modulus_mpa")
    vibration = str(spec.get("vibration") or "moderate").lower()
    if vibration not in ("mild", "moderate", "intense"):
        raise GeometryError("vibration is mild, moderate, or intense.")
    locking = [str(item) for item in spec.get("locking") or []]

    proof_load = grade["proof_mpa"] * area
    preload = fraction * proof_load
    torque_nm = nut_factor * preload * diameter / 1000.0
    bolt_stiffness = area * bolt_modulus / grip
    member_area = math.pi / 4.0 * (1.5 * diameter) ** 2
    member_stiffness = member_area * member_modulus / grip
    phi = bolt_stiffness / (bolt_stiffness + member_stiffness)
    separating = max(axial, 0.0)
    bolt_force = preload + phi * separating
    residual = preload - (1.0 - phi) * separating
    friction_capacity = friction * max(residual, 0.0) * interfaces
    if shear <= 1e-9:
        slip_margin = None
        slip_status = "no_shear"
    else:
        slip_margin = friction_capacity / shear
        slip_status = "slips" if slip_margin < 1.0 else "holds"
    yield_capacity = grade["yield_mpa"] * area
    yield_margin = yield_capacity / bolt_force if bolt_force > 1e-9 else None
    proof_margin = proof_load / bolt_force if bolt_force > 1e-9 else None
    minor = diameter - 1.226869 * pitch
    major = diameter
    # Half of a cylinder over the engagement. The real flank is a helix; this is the stated simplification.
    bolt_shear_area = math.pi * minor * engagement * 0.5
    nut_shear_area = math.pi * major * engagement * 0.5
    shear_yield = 0.5 * grade["yield_mpa"]
    bolt_strip = shear_yield * bolt_shear_area
    nut_strip = shear_yield * nut_shear_area
    strip_capacity = min(bolt_strip, nut_strip)
    strip_margin = strip_capacity / bolt_force if bolt_force > 1e-9 else None
    loosening = _loosening(
        vibration=vibration,
        residual=residual,
        preload=preload,
        slip_margin=slip_margin,
        slip_status=slip_status,
        yield_margin=yield_margin,
        locking=locking,
    )
    status = "fail" if loosening["rating"] == "high" or (yield_margin is not None and yield_margin < 1.0) else (
        "marginal" if loosening["rating"] == "moderate" else "pass"
    )
    report = {
        "size": size,
        "pitch_mm": pitch,
        "grade": spec.get("grade") or "8.8",
        "grade_name": grade["name"],
        "condition": condition,
        "status": status,
        "tensile_stress_area_mm2": round(area, 3),
        "proof_load_n": round(proof_load, 1),
        "preload_n": round(preload, 1),
        "preload_fraction": fraction,
        "tightening_torque_nm": round(torque_nm, 3),
        "nut_factor": nut_factor,
        "grip_mm": grip,
        "load_factor": round(phi, 4),
        "bolt_force_n": round(bolt_force, 1),
        "residual_clamp_n": round(residual, 1),
        "separates": residual <= 0.0,
        "friction": friction,
        "interfaces": interfaces,
        "friction_capacity_n": round(friction_capacity, 1),
        "shear_n": round(shear, 1),
        "slip_margin": None if slip_margin is None else round(slip_margin, 3),
        "slip_status": slip_status,
        "yield_margin": None if yield_margin is None else round(yield_margin, 3),
        "proof_margin": None if proof_margin is None else round(proof_margin, 3),
        "stripping": {
            "engagement_mm": engagement,
            "bolt_shear_area_mm2": round(bolt_shear_area, 2),
            "nut_shear_area_mm2": round(nut_shear_area, 2),
            "capacity_n": round(strip_capacity, 1),
            "margin": None if strip_margin is None else round(strip_margin, 3),
            "status": "strips" if strip_margin is not None and strip_margin < 1.0 else "holds",
        },
        "locking": locking,
        "loosening": loosening,
        "assumptions": _assumptions(condition, fraction, nut_factor, friction),
        "note": loosening["summary"],
    }
    return finish(
        report,
        kind=ROUGH_ESTIMATE,
        name="Bolted joint",
        what="Preload, torque, slip, and thread strength are handbook estimates, not a measured joint.",
        limits=[
            "The nut factor is not a measured torque-tension test.",
            "Member stiffness is a stand-in cylinder, not the real clamped parts.",
            "A locking nut, wedge washer, threadlocker, or safety wire resists turning and does not create clamp force.",
        ],
    )


def _loosening(
    vibration: str,
    residual: float,
    preload: float,
    slip_margin: float | None,
    slip_status: str,
    yield_margin: float | None,
    locking: list[str],
) -> dict:
    reasons = []
    rating = "low"
    if residual <= 0.0:
        rating = "high"
        reasons.append("The external axial load separates the joint, so there is no clamp left to resist slip.")
    elif slip_status == "slips":
        rating = "high"
        reasons.append(
            "Transverse load exceeds friction. Plates that slip let the thread walk open. "
            "That is the usual way a bolt loosens under vibration, and it happens before the bolt yields."
        )
    elif slip_margin is not None and slip_margin < 1.5:
        rating = "moderate"
        reasons.append(
            f"Slip margin is {slip_margin:.2f}. Gross slip has not started, but there is little reserve "
            "once preload relaxes from embedment."
        )
    elif slip_margin is not None and vibration == "intense" and slip_margin < 3.0:
        rating = "moderate"
        reasons.append(
            f"Slip margin is {slip_margin:.2f}. Under intense vibration a joint can ratchet from micro-slip "
            "even when the average friction still exceeds the average shear."
        )
    else:
        reasons.append("Clamp friction covers the stated shear with reserve, and the joint does not separate.")
    if vibration == "intense" and rating == "moderate" and (slip_margin is not None and slip_margin < 1.5):
        rating = "high"
        reasons.append("The stated environment is intense vibration, so a thin slip margin is treated as a loosening risk.")
    if yield_margin is not None and yield_margin < 1.0:
        reasons.append("The bolt force is past yield. A yielded bolt loses preload as it stretches.")
    has_lock = bool(locking)
    if rating != "low" and not has_lock:
        reasons.append("No locking feature is declared. Preload alone is what is holding the nut.")
    countermeasures = _countermeasures(rating, yield_margin, residual, preload)
    summary = {
        "low": "Low loosening risk for the stated loads. A drilling rig still wants a locking feature, because embedment and shock are not in this static margin.",
        "moderate": "Moderate loosening risk. Raise preload if the yield margin allows, and add a locking feature. Do not rely on friction alone.",
        "high": "High loosening risk. The plates can slip or the joint can separate. A stronger bolt does not fix slip. The clamp and a locking feature do.",
    }[rating]
    return {
        "rating": rating,
        "vibration": vibration,
        "reasons": reasons,
        "countermeasures": countermeasures,
        "summary": summary,
    }


def _countermeasures(rating: str, yield_margin: float | None, residual: float, preload: float) -> list[dict]:
    room = yield_margin is None or yield_margin > 1.3
    items = [
        {
            "name": "higher preload",
            "when": "The slip margin is thin and the bolt is still below yield.",
            "effect": (
                "Friction capacity is the preload times the plate coefficient. More preload, more force "
                "before the plates slip. Stay under proof load. This check used a fraction of proof load "
                "for that reason."
            ),
            "recommended": rating != "low" and room and residual > 0,
        },
        {
            "name": "longer grip length",
            "when": "Preload disappears after tightening, or the joint sees shock.",
            "effect": (
                "A longer stretch stores more elastic energy, so a little embedment or a thermal change "
                "removes a smaller share of the preload. A sleeve or a thicker stack does this. It does "
                "not add friction by itself."
            ),
            "recommended": rating != "low",
        },
        {
            "name": "nyloc nut",
            "when": "The nut must resist rotation after clamp friction is disturbed.",
            "effect": (
                "The nylon insert adds a prevailing torque. The nut is harder to turn even if the plates "
                "shuffle. It does not stop the plates from slipping, and it does not replace preload. "
                "Heat and reuse wear the insert down."
            ),
            "recommended": rating != "low",
        },
        {
            "name": "wedge-locking washers",
            "when": "The joint sees transverse vibration and the mating face is hard enough for the cams.",
            "effect": (
                "The cams increase bolt tension if the nut tries to rotate loose, so loosening raises "
                "the clamp instead of dropping it. They need preload to start with, and they want a "
                "surface that does not crush."
            ),
            "recommended": rating == "high" or rating == "moderate",
        },
        {
            "name": "threadlocker",
            "when": "A chemical bond in the threads is acceptable for service.",
            "effect": (
                "Liquid locker fills the clearance and bonds the threads, so the nut resists turning. "
                "It is a supplement. The plates can still slip if preload is low, and a high-strength "
                "locker is awkward to service. Use the lubricated torque for the wet threads, not the dry one."
            ),
            "recommended": rating != "low",
        },
        {
            "name": "safety wire",
            "when": "Someone can see the joint, and a positive lock is required.",
            "effect": (
                "Wire through the bolt head or the nut stops it from turning. A broken wire is obvious. "
                "Wire does not increase clamp force, so it does not fix a joint that is already slipping."
            ),
            "recommended": rating == "high",
        },
    ]
    if preload <= 0:
        for item in items:
            item["recommended"] = item["name"] != "higher preload"
    return items


def _assumptions(condition: str, fraction: float, nut_factor: float, friction: float) -> list[str]:
    return [
        "Sizes use the ISO coarse pitch unless the size string gives a pitch, as in M8-1.25.",
        "Tensile stress area is π/4 * (d - 0.9382*p)^2, the usual ISO approximation.",
        f"Recommended preload is {fraction:.0%} of proof load. Proof stress is the ISO 898-1 value for the grade. A2-70 uses 450 MPa as a conservative stand-in for proof.",
        f"Tightening torque is K*F*d with K = {nut_factor:.2f} for the {condition} condition. K is a friction lump, not a measured torque-tension test. Lubricant, plating, and reuse move it.",
        "Bolt stiffness is At*E/grip. Member stiffness is a steel cylinder of diameter 1.5*d over the same grip. The load factor is bolt stiffness over the sum. It is not a finite-element joint.",
        f"Friction capacity is μ*residual clamp*interfaces with μ = {friction:.2f}. Residual clamp is preload minus the share of a separating axial load carried by the members.",
        "A separating axial load unloads the clamp. A compressive external load is ignored. Shear is transverse to the bolt.",
        "Slip margin below 1 means the plates slide. Transverse slip is the Junker mechanism of self-loosening. The bolt can be well below yield while the nut still walks.",
        "Thread shear area is half the minor-diameter cylinder (bolt) or major-diameter cylinder (nut) over the engagement. Shear yield is taken as half of tensile yield. A short engagement fails this before the bolt yields.",
        "Intense vibration can ratchet a joint with a modest slip margin. Micro-slip, embedment, and relaxation are not simulated. The rating says so by raising the risk when the environment is intense.",
        "Locking features resist nut rotation. They do not create clamp force. Wedge washers, nyloc nuts, threadlocker, and safety wire are described on the report.",
    ]


def _size_and_pitch(value) -> tuple[str, float]:
    if not isinstance(value, str) or not value.strip():
        known = ", ".join(_COARSE_PITCH)
        raise GeometryError(f"A bolt size is required, for example M8. Known coarse sizes: {known}.")
    token = value.strip().upper().replace(" ", "")
    pitch = None
    if "-" in token:
        head, tail = token.split("-", 1)
        token = head
        try:
            pitch = float(tail)
        except ValueError as exc:
            raise GeometryError(f"Could not read the pitch in {value!r}.") from exc
    if token not in _COARSE_PITCH:
        known = ", ".join(_COARSE_PITCH)
        raise GeometryError(f"No coarse series for {value!r}. Known sizes: {known}.")
    if pitch is None:
        pitch = _COARSE_PITCH[token]
    if pitch <= 0 or pitch >= _diameter(token):
        raise GeometryError("Pitch must be positive and smaller than the diameter.")
    return token, pitch


def _diameter(size: str) -> float:
    return float(size[1:])


def _grade(name: str) -> dict:
    key = str(name).strip().lower().replace(" ", "")
    if key not in _GRADES:
        known = ", ".join(_GRADES)
        raise GeometryError(f"No bolt grade {name!r}. Known grades: {known}.")
    return _GRADES[key]


def _tensile_area(diameter: float, pitch: float) -> float:
    equivalent = diameter - 0.938194 * pitch
    return math.pi / 4.0 * equivalent * equivalent


def _number(value, default, label: str) -> float:
    if value is None:
        if default is None:
            raise GeometryError(f"{label} is required.")
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a number.") from exc
