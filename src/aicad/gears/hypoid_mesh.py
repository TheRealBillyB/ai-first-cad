"""Mesh check for a hypoid ring and pinion.

The solids are the face-mill ring tooth and the pinion blank that tooth leaves.
The check places those solids on the offset shafts and reads the gap. It is not
a spiral bevel moved sideways, and it does not sweep a full turn.
"""

from __future__ import annotations

import math

from aicad.errors import GeometryError
from aicad.gears.hypoid import pair_hypoid, ring_pitch_flank_points
from aicad.gears.solid import build_gear
from aicad import kernel

# The same bar as the kernel: a smaller intersection is numerical noise.
_OVERLAP_MM3 = 1.0e-4
# A gap under this is contact. The generated flank shift is about this size.
_CONTACT_GAP_MM = 0.05
_OFFSET_TOLERANCE_MM = 1.0e-3
_SHAFT_TOLERANCE_DEG = 0.05

_WHAT = (
    "Hypoid mesh of the face-mill solids. The ring tooth is the blade cone and "
    "the pinion is the blank that blade leaves. The report gives the axis offset "
    "and the tooth ratio, backlash along the tooth, contact, and interference "
    "at a few mesh poses."
)

_LIMITS = [
    "This is not a tolerance grade or a load rating.",
    "The sweep is three poses across one ring pitch of the generated solids, not one full turn and not a 2 degree outline sweep.",
    "Backlash along the tooth is the gap from the ring pitch flank to the pinion solid at the heel, the middle, and the toe. It is smallest near the middle and larger toward the ends. The closest solid gap can be smaller than that pitch-flank gap.",
    "The edge radius is recorded and is not cut, so the root is not a trochoid.",
    "This is not a spiral bevel. The cone angles do not add up to the shaft angle.",
    "Undercut and an involute contact ratio are not evaluated.",
]


def check_offset_pair(pinion_spec, gear_spec, left, right, mounting, ranges, steps) -> dict:
    """Check one hypoid pair. Hands or tooth counts that disagree are refused."""
    if steps is not None and (isinstance(steps, bool) or not isinstance(steps, int)):
        raise GeometryError("steps must be an integer from 8 to 1440, covering one full turn.")
    if left["type"] != "hypoid" or right["type"] != "hypoid":
        raise GeometryError("The offset mesh check is the hypoid face-mill pair.")
    nominal = pair_hypoid(left, right)
    ratio = float(nominal["ratio"])
    if mounting.get("ratio") is not None and abs(float(mounting["ratio"]) - ratio) > 1.0e-6:
        raise GeometryError(
            f"The hypoid ratio is {ratio:g} from the tooth counts. "
            f"The requested ratio is {float(mounting['ratio']):g}."
        )
    design_offset = float(nominal["offset_mm"])
    placed_offset = _placed_offset(mounting, design_offset)
    design_shaft = float(nominal["shaft_angle_deg"])
    placed_shaft = design_shaft if mounting.get("shaft_angle_deg") is None else float(mounting["shaft_angle_deg"])
    if not math.isfinite(placed_offset) or not math.isfinite(placed_shaft):
        raise GeometryError("The hypoid offset and shaft angle have to be finite.")

    pinion_user = pinion_spec if left["role"] == "pinion" else gear_spec
    ring_user = gear_spec if right["role"] == "ring" else pinion_spec
    pinion_solid = kernel.adopt(build_gear(pinion_user))
    ring_solid = kernel.adopt(build_gear(ring_user))
    findings: list[str] = []
    for shape, label in ((pinion_solid, "pinion"), (ring_solid, "ring")):
        measured = kernel.measure(shape)
        if not (measured.watertight and measured.valid and measured.volume_mm3 > 0):
            findings.append(f"The generated {label} is not a closed solid.")

    angles = _pose_angles(int(left["ring_teeth"] if left["role"] == "pinion" else right["ring_teeth"]))
    poses = []
    worst_overlap = 0.0
    worst_gap = None
    worst_at = 0.0
    for angle in angles:
        placement = pair_hypoid(left, right, angle)["placement"]
        pinion_at = _with_offset(placement["pinion"]["position_mm"], design_offset, placed_offset)
        first = kernel.place(pinion_solid, tuple(pinion_at), tuple(placement["pinion"]["rotation_deg"]))
        second = kernel.place(
            ring_solid,
            tuple(placement["gear"]["position_mm"]),
            tuple(placement["gear"]["rotation_deg"]),
        )
        overlap, gap, *_ = kernel.clearance_between(first, second)
        poses.append({"gear_angle_deg": angle, "overlap_mm3": overlap, "gap_mm": gap})
        if overlap > worst_overlap:
            worst_overlap = overlap
            worst_at = angle
        if worst_gap is None or gap < worst_gap:
            worst_gap = gap

    along = _along_tooth(ring_user, nominal, pinion_solid, design_offset, placed_offset)
    gaps = [item["gap_mm"] for item in along]
    point_inside = any(item["inside"] for item in along)
    interfere = worst_overlap > _OVERLAP_MM3 or point_inside
    tight = min(gaps)
    loose = max(gaps)
    # Contact is the solids meeting. The pitch-flank gap is the backlash figure.
    contact = interfere or (worst_gap is not None and worst_gap < _CONTACT_GAP_MM)
    offset_error = placed_offset - design_offset
    shaft_error = placed_shaft - design_shaft

    if abs(offset_error) > _OFFSET_TOLERANCE_MM:
        findings.append(
            f"The axis offset is {placed_offset:.3f} mm. The pair was generated for {design_offset:.3f} mm."
        )
    if abs(shaft_error) > _SHAFT_TOLERANCE_DEG:
        findings.append(
            f"Shaft angle is {placed_shaft:.3f} degrees. The pair was generated for {design_shaft:.3f} degrees."
        )
    if worst_overlap > _OVERLAP_MM3:
        findings.append(
            f"The solids interfere. The largest intersection is {worst_overlap:.4f} mm³ "
            f"at {worst_at:.1f} degrees of ring rotation."
        )
    elif point_inside:
        findings.append("A ring pitch-flank point lies inside the pinion.")
    if not contact:
        findings.append(
            f"The teeth do not meet. The smallest gap along the sweep is {worst_gap:.3f} mm."
        )
    backlash = _backlash(tight, loose, gaps, float(nominal["backlash_mm"]), ranges, findings)
    ordered = []
    for item in findings:
        if item not in ordered:
            ordered.append(item)
    ring_teeth = int(left["ring_teeth"])
    pinion_teeth = int(left["pinion_teeth"])
    return {
        "status": "pass" if not ordered else "fail",
        "axis_layout": "offset",
        "result_name": f"hypoid {pinion_teeth}/{ring_teeth}",
        "what": _WHAT,
        "limits": list(_LIMITS),
        "pinion": {"type": "hypoid", "teeth": pinion_teeth, "hand": left["pinion_hand"], "undercut": False},
        "gear": {"type": "hypoid", "teeth": ring_teeth, "hand": left["ring_hand"], "undercut": False},
        "ratio": ratio,
        "offset_mm": placed_offset,
        "ideal_offset_mm": design_offset,
        "offset_error_mm": offset_error,
        "centre_distance_mm": placed_offset,
        "ideal_centre_distance_mm": design_offset,
        "centre_distance_error_mm": offset_error,
        "shaft_angle_deg": placed_shaft,
        "ideal_shaft_angle_deg": design_shaft,
        "shaft_angle_error_deg": shaft_error,
        "contact": contact,
        "interference": interfere,
        "undercut": False,
        "total_contact_ratio": None,
        "transverse_contact_ratio": None,
        "overlap_ratio": None,
        "smaller_gear": "pinion" if pinion_teeth < ring_teeth else "gear",
        "backlash": backlash,
        "sweep": {
            "member": "ring",
            "model": "face-mill solid",
            "rotation_deg": angles[-1] - angles[0],
            "step_deg": angles[1] - angles[0],
            "steps": len(angles),
            "poses": poses,
            "worst_overlap_mm3": worst_overlap,
            "worst_gap_mm": worst_gap,
            "worst_at_deg": worst_at,
        },
        "findings": ordered,
        "warnings": list(nominal.get("warnings") or []),
        "assumptions": [
            "The solids are the face-mill ring and the pinion blank that same tooth leaves when it is rolled at the tooth ratio.",
            "Three poses cover one ring pitch. A full turn of the solids is not run. A requested step count does not add poses.",
            "Backlash along the tooth is the gap from the ring pitch flank to the rolled pinion at the heel, the middle, and the toe. A backlash range is applied at all three.",
            "The designed flank shift keeps a flat facet out of the mate. The closest solid gap can be smaller than the pitch-flank gap, and the pitch-flank gap opens toward the ends of the face.",
            "Hands are opposite. The ratio is ring teeth divided by pinion teeth.",
            "The sweep uses the generated mesh phase. It does not read a clocking stored on an assembly.",
            "This is not a tolerance grade or a load rating, and not a spiral bevel.",
        ],
        "standards": [],
    }


def _placed_offset(mounting: dict, design_offset: float) -> float:
    if mounting.get("offset_mm") is not None:
        return float(mounting["offset_mm"])
    if mounting.get("centre_distance_mm") is not None:
        return float(mounting["centre_distance_mm"])
    return design_offset


def _pose_angles(ring_teeth: int) -> list[float]:
    # 0, 8, and 16 degrees on the 16-tooth sample, scaled by the ring pitch.
    pitch = 360.0 / float(ring_teeth)
    return [0.0, pitch * (8.0 / 22.5), pitch * (16.0 / 22.5)]


def _with_offset(position, design_offset: float, placed_offset: float) -> list[float]:
    moved = [float(component) for component in position]
    if abs(design_offset) < 1.0e-9:
        raise GeometryError("A hypoid needs a nonzero offset.")
    moved[1] = moved[1] / design_offset * placed_offset
    return moved


def _transform(point, rotation_deg, position) -> tuple[float, float, float]:
    x, y, z = (float(point[0]), float(point[1]), float(point[2]))
    rx, ry, rz = (math.radians(float(angle)) for angle in rotation_deg)
    y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
    x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
    x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
    return (x + float(position[0]), y + float(position[1]), z + float(position[2]))


def _along_tooth(ring_spec, nominal, pinion_solid, design_offset: float, placed_offset: float) -> list[dict]:
    placement = nominal["placement"]
    pinion_at = _with_offset(placement["pinion"]["position_mm"], design_offset, placed_offset)
    placed = kernel.place(pinion_solid, tuple(pinion_at), tuple(placement["pinion"]["rotation_deg"]))
    raw = ring_pitch_flank_points(ring_spec)
    stations = ("heel", "heel", "mid", "mid", "toe", "toe")
    grouped: dict[str, dict] = {}
    for point, station in zip(raw, stations):
        world = _transform(point, placement["gear"]["rotation_deg"], placement["gear"]["position_mm"])
        probed = kernel.distance_to_point(placed, world)
        current = grouped.get(station)
        if current is None or probed["distance_mm"] < current["gap_mm"]:
            grouped[station] = {
                "station": station,
                "gap_mm": probed["distance_mm"],
                "inside": bool(probed["inside"]),
            }
    return [grouped[name] for name in ("heel", "mid", "toe")]


def _backlash(tight: float, loose: float, gaps: list[float], designed: float, ranges, findings: list[str]) -> dict:
    block = {
        "circumferential_mm": tight,
        "normal_mm": tight,
        "along_tooth_mm": list(gaps),
        "along_tooth_min_mm": tight,
        "along_tooth_max_mm": loose,
        "designed_flank_shift_mm": designed,
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
    in_circ = all(circ_span[0] - 1.0e-6 <= gap <= circ_span[1] + 1.0e-6 for gap in gaps)
    in_norm = all(norm_span[0] - 1.0e-6 <= gap <= norm_span[1] + 1.0e-6 for gap in gaps)
    block["circumferential_in_range"] = in_circ
    block["normal_in_range"] = in_norm
    block["in_range"] = in_circ and in_norm
    if not block["in_range"]:
        findings.append(
            f"Backlash along the tooth is {tight:.4f} to {loose:.4f} mm, outside "
            f"{min(circ_span[0], norm_span[0]):.4f} to {max(circ_span[1], norm_span[1]):.4f} mm."
        )
    return block
