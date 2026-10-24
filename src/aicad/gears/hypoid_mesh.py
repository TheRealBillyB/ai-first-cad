"""Mesh check for a hypoid ring and pinion.

The solids are the face-mill ring tooth and the pinion blank that tooth leaves.
The check places those solids on the offset shafts and rolls one pinion tooth.
It reads a placement when the gears are mounted, and it is not a spiral bevel.
"""

from __future__ import annotations

import math

import numpy as np

from aicad.errors import GeometryError
from aicad.gears.hypoid import line_distance, pair_hypoid, ring_pitch_flank_points
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
    "through one pinion tooth."
)

_LIMITS = [
    "This is not a tolerance grade or a load rating.",
    "The sweep rolls one pinion tooth, including both ends, from before the mounted mesh until the tooth has left it. A requested step count is the number of poses. The default step is 2 degrees or finer. It is not one full turn.",
    "The gap at each step is the distance between the face triangulations. The solid boolean runs at the closest steps and is the interference result.",
    "Backlash along the tooth is the gap from the ring pitch flank to the pinion solid at the heel, the middle, and the toe. It is smallest near the middle and larger toward the ends. The closest solid gap can be smaller than that pitch-flank gap.",
    "The edge radius is recorded and is not cut, so the root is not a trochoid.",
    "This is not a spiral bevel. The cone angles do not add up to the shaft angle.",
    "Undercut and an involute contact ratio are not evaluated.",
]

# Boolean only the closest steps. The triangulation gap ranks every step.
_BOOLEAN_POSES = 3


def check_offset_pair(pinion_spec, gear_spec, left, right, mounting, ranges, steps) -> dict:
    """Check one hypoid pair. Hands or tooth counts that disagree are refused."""
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
    design_shaft = float(nominal["shaft_angle_deg"])
    ring_teeth = int(left["ring_teeth"])
    pinion_teeth = int(left["pinion_teeth"])
    angles, step, pitch = _engagement_angles(ring_teeth, steps)

    pinion_user = pinion_spec if left["role"] == "pinion" else gear_spec
    ring_user = gear_spec if right["role"] == "ring" else pinion_spec
    pinion_solid = kernel.adopt(build_gear(pinion_user))
    ring_solid = kernel.adopt(build_gear(ring_user))
    findings: list[str] = []
    for shape, label in ((pinion_solid, "pinion"), (ring_solid, "ring")):
        measured = kernel.measure(shape)
        if not (measured.watertight and measured.valid and measured.volume_mm3 > 0):
            findings.append(f"The generated {label} is not a closed solid.")

    base_pinion, base_ring, placed_from_mount = _mounted_bases(
        pinion_solid, ring_solid, left, nominal, mounting, design_offset
    )
    placed_offset, placed_shaft, pinion_clock, ring_clock = _mount_measurements(
        base_pinion, base_ring, pinion_solid, ring_solid, nominal
    )
    if not math.isfinite(placed_offset) or not math.isfinite(placed_shaft):
        raise GeometryError("The hypoid offset and shaft angle have to be finite.")

    pinion_mesh = _tessellate(pinion_solid)
    ring_mesh = _tessellate(ring_solid)
    turns = float(nominal["motion"]["pinion_turns_per_gear_turn"])
    pinion_axis = _frame(base_pinion)
    ring_axis = _frame(base_ring)
    posed = []
    for angle in angles:
        pinion_at = _spin(base_pinion, pinion_axis[3], pinion_axis[2], turns * angle)
        ring_at = _spin(base_ring, ring_axis[3], ring_axis[2], angle)
        gap = _mesh_gap(
            _world_vertices(pinion_mesh[0], pinion_at),
            pinion_mesh[1],
            _world_vertices(ring_mesh[0], ring_at),
            ring_mesh[1],
        )
        posed.append({"gear_angle_deg": angle, "gap_mm": gap, "overlap_mm3": None, "_shapes": (pinion_at, ring_at)})

    order = sorted(range(len(posed)), key=lambda index: posed[index]["gap_mm"])
    worst_overlap = 0.0
    worst_at = posed[order[0]]["gear_angle_deg"]
    for index in order[:_BOOLEAN_POSES]:
        overlap = kernel.overlap_volume(*posed[index]["_shapes"])
        posed[index]["overlap_mm3"] = overlap
        if overlap > worst_overlap:
            worst_overlap = overlap
            worst_at = posed[index]["gear_angle_deg"]
        if overlap > _OVERLAP_MM3:
            posed[index]["gap_mm"] = 0.0
    worst_gap = min(item["gap_mm"] for item in posed)
    poses = [
        {"gear_angle_deg": item["gear_angle_deg"], "gap_mm": item["gap_mm"], "overlap_mm3": item["overlap_mm3"]}
        for item in posed
    ]

    along = _along_tooth(ring_user, base_pinion, base_ring)
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
    if abs(pinion_clock) > _SHAFT_TOLERANCE_DEG:
        findings.append(f"The pinion is clocked {pinion_clock:.3f} degrees off the generated mesh.")
    if abs(ring_clock) > _SHAFT_TOLERANCE_DEG:
        findings.append(f"The ring is clocked {ring_clock:.3f} degrees off the generated mesh.")
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
        "pinion_clock_deg": pinion_clock,
        "gear_clock_deg": ring_clock,
        "contact": contact,
        "interference": interfere,
        "undercut": False,
        "total_contact_ratio": None,
        "transverse_contact_ratio": None,
        "overlap_ratio": None,
        "smaller_gear": "pinion" if pinion_teeth < ring_teeth else "gear",
        "backlash": backlash,
        "sweep": {
            "member": "pinion",
            "model": "face-mill solid",
            "rotation_deg": pitch,
            "step_deg": step,
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
            "The sweep rolls one pinion tooth, including both ends. It starts a quarter of a ring pitch before the mounted mesh and runs until the tooth has left mesh. A requested step count is the number of poses across that tooth. The default step is 2 degrees or finer, with at least 8 poses.",
            "The gap at every step is the distance between the face triangulations. A solid boolean is run at the closest steps, and that volume is the interference result.",
            "Backlash along the tooth is the gap from the ring pitch flank to the rolled pinion at the heel, the middle, and the toe. A backlash range is applied at all three.",
            "The designed flank shift keeps a flat facet out of the mate. The closest solid gap can be smaller than the pitch-flank gap, and the pitch-flank gap opens toward the ends of the face.",
            "Hands are opposite. The ratio is ring teeth divided by pinion teeth.",
            (
                "The sweep starts from the placement and clocking of the two gears."
                if placed_from_mount
                else "No placement was given, so the sweep starts from the generated mesh phase."
            ),
            "This is not a tolerance grade or a load rating, and not a spiral bevel.",
        ],
        "standards": [],
    }


def _engagement_angles(ring_teeth: int, steps) -> tuple[list[float], float, float]:
    """One pinion tooth of ring rotation, sampled coming into mesh and leaving it."""
    pitch = 360.0 / float(ring_teeth)
    if steps is None:
        # Both ends of one tooth, at 2 degrees or finer, and at least 8 poses.
        count = max(8, int(math.ceil(pitch / 2.0 - 1.0e-9)) + 1)
    elif isinstance(steps, bool) or not isinstance(steps, int) or steps < 8 or steps > 1440:
        raise GeometryError(
            "steps must be an integer from 8 to 1440. They divide one pinion tooth, not a full turn."
        )
    else:
        count = steps
    step = pitch / (count - 1)
    # A quarter pitch before the generated mesh, then one full tooth, including the far end.
    start = -0.25 * pitch
    return [start + index * step for index in range(count)], step, pitch


def _placed_offset(mounting: dict, design_offset: float) -> float:
    if mounting.get("offset_mm") is not None:
        return float(mounting["offset_mm"])
    if mounting.get("centre_distance_mm") is not None:
        return float(mounting["centre_distance_mm"])
    return design_offset


def _with_offset(position, design_offset: float, placed_offset: float) -> list[float]:
    moved = [float(component) for component in position]
    if abs(design_offset) < 1.0e-9:
        raise GeometryError("A hypoid needs a nonzero offset.")
    moved[1] = moved[1] / design_offset * placed_offset
    return moved


def _mounted_bases(pinion_solid, ring_solid, left, nominal, mounting, design_offset):
    """Place both solids. A matrix or a part rotation wins over the generated phase."""
    generated = nominal["placement"]
    first_is_pinion = left["role"] == "pinion"
    pinion_matrix, ring_matrix = _role_values(
        first_is_pinion, mounting.get("pinion_matrix"), mounting.get("gear_matrix")
    )
    pinion_position, ring_position = _role_values(
        first_is_pinion, mounting.get("pinion_position_mm"), mounting.get("gear_position_mm")
    )
    pinion_rotation, ring_rotation = _role_values(
        first_is_pinion, mounting.get("pinion_rotation_deg"), mounting.get("gear_rotation_deg")
    )
    placed_from_mount = False
    if pinion_matrix is not None and ring_matrix is not None:
        pinion = kernel.transform_shape(pinion_solid, pinion_matrix)
        ring = kernel.transform_shape(ring_solid, ring_matrix)
        placed_from_mount = True
    elif None not in (pinion_position, pinion_rotation, ring_position, ring_rotation):
        pinion = kernel.place(pinion_solid, tuple(pinion_position), tuple(pinion_rotation))
        ring = kernel.place(ring_solid, tuple(ring_position), tuple(ring_rotation))
        placed_from_mount = True
    else:
        requested = _placed_offset(mounting, design_offset)
        pinion_at = _with_offset(generated["pinion"]["position_mm"], design_offset, requested)
        pinion = kernel.place(pinion_solid, tuple(pinion_at), tuple(generated["pinion"]["rotation_deg"]))
        ring = kernel.place(
            ring_solid,
            tuple(generated["gear"]["position_mm"]),
            tuple(generated["gear"]["rotation_deg"]),
        )
    pinion = _apply_clock(pinion, mounting.get("pinion_clock_deg"))
    ring = _apply_clock(ring, mounting.get("gear_clock_deg"))
    if mounting.get("pinion_clock_deg") not in (None, 0, 0.0) or mounting.get("gear_clock_deg") not in (None, 0, 0.0):
        placed_from_mount = True
    return pinion, ring, placed_from_mount


def _role_values(first_is_pinion, first, second):
    if first_is_pinion:
        return first, second
    return second, first


def _apply_clock(shape, degrees):
    if degrees in (None, 0, 0.0):
        return shape
    _x, _y, axis, origin = _frame(shape)
    return _spin(shape, origin, axis, float(degrees))


def _mount_measurements(base_pinion, base_ring, pinion_solid, ring_solid, nominal):
    generated = nominal["placement"]
    reference_pinion = kernel.place(
        pinion_solid,
        tuple(generated["pinion"]["position_mm"]),
        tuple(generated["pinion"]["rotation_deg"]),
    )
    reference_ring = kernel.place(
        ring_solid,
        tuple(generated["gear"]["position_mm"]),
        tuple(generated["gear"]["rotation_deg"]),
    )
    pinion_x, _pinion_y, pinion_axis, pinion_origin = _frame(base_pinion)
    ring_x, _ring_y, ring_axis, ring_origin = _frame(base_ring)
    ref_pinion_x, _, _ref_pinion_axis, _ = _frame(reference_pinion)
    ref_ring_x, _, _ref_ring_axis, _ = _frame(reference_ring)
    placed_offset = _signed_offset(pinion_origin, pinion_axis, ring_origin, ring_axis)
    placed_shaft = _shaft_angle(pinion_axis, ring_axis)
    pinion_clock = _twist(pinion_axis, ref_pinion_x, pinion_x)
    ring_clock = _twist(ring_axis, ref_ring_x, ring_x)
    if abs(pinion_clock) < 1.0e-4:
        pinion_clock = 0.0
    if abs(ring_clock) < 1.0e-4:
        ring_clock = 0.0
    return placed_offset, placed_shaft, pinion_clock, ring_clock


def _signed_offset(pinion_origin, pinion_axis, ring_origin, ring_axis) -> float:
    normal = np.cross(ring_axis, pinion_axis)
    length = float(np.linalg.norm(normal))
    if length < 1.0e-9:
        return line_distance(ring_origin, ring_axis, pinion_origin, pinion_axis)
    return float(np.dot(pinion_origin - ring_origin, normal / length))


def _shaft_angle(pinion_axis, ring_axis) -> float:
    cosine = float(np.clip(np.dot(pinion_axis, ring_axis), -1.0, 1.0))
    return math.degrees(math.acos(abs(cosine)))


def _twist(axis, before, after) -> float:
    start = before - axis * float(np.dot(before, axis))
    stop = after - axis * float(np.dot(after, axis))
    if float(np.linalg.norm(start)) < 1.0e-9 or float(np.linalg.norm(stop)) < 1.0e-9:
        return 0.0
    return math.degrees(math.atan2(float(np.dot(axis, np.cross(start, stop))), float(np.dot(start, stop))))


def _frame(shape):
    transform = shape.impl.wrapped.Location().Transformation()
    columns = [
        np.array([transform.Value(1, column), transform.Value(2, column), transform.Value(3, column)], float)
        for column in range(1, 4)
    ]
    translation = transform.TranslationPart()
    origin = np.array([translation.X(), translation.Y(), translation.Z()], float)
    return columns[0], columns[1], columns[2], origin


def _spin(shape, origin, axis, degrees):
    if abs(degrees) < 1.0e-9:
        return shape
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf
    from build123d import Location

    direction = np.asarray(axis, float)
    direction = direction / np.linalg.norm(direction)
    point = np.asarray(origin, float)
    spin = gp_Trsf()
    spin.SetRotation(
        gp_Ax1(gp_Pnt(float(point[0]), float(point[1]), float(point[2])), gp_Dir(float(direction[0]), float(direction[1]), float(direction[2]))),
        math.radians(float(degrees)),
    )
    base = shape.impl.wrapped.Location().Transformation()
    return kernel.adopt(shape.impl.located(Location(spin.Multiplied(base))))


def _tessellate(shape):
    vertices, triangles = shape.impl.tessellate(0.05)
    points = np.array([(vertex.X, vertex.Y, vertex.Z) for vertex in vertices], float)
    faces = np.array(triangles, np.int32)
    return points, faces


def _world_vertices(points, shape):
    _x, _y, _z, origin = _frame(shape)
    transform = shape.impl.wrapped.Location().Transformation()
    rotation = np.array([[transform.Value(row, column) for column in range(1, 4)] for row in range(1, 4)])
    return points @ rotation.T + origin


def _mesh_gap(first_vertices, first_triangles, second_vertices, second_triangles) -> float:
    radius = 2.0
    while radius <= 64.0:
        gap, found = _triangle_gap(first_vertices, first_triangles, second_vertices, second_triangles, radius)
        if found:
            return gap
        radius *= 2.0
    return radius


def _triangle_gap(first_vertices, first_triangles, second_vertices, second_triangles, radius: float):
    first_centres = first_vertices[first_triangles].mean(axis=1)
    second_centres = second_vertices[second_triangles].mean(axis=1)
    buckets: dict[tuple[int, int, int], list[int]] = {}
    keys = np.floor(first_centres / radius).astype(np.int32)
    for index, key in enumerate(keys.tolist()):
        buckets.setdefault((int(key[0]), int(key[1]), int(key[2])), []).append(index)
    left = []
    right = []
    other_keys = np.floor(second_centres / radius).astype(np.int32)
    for index, key in enumerate(other_keys.tolist()):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    hits = buckets.get((int(key[0]) + dx, int(key[1]) + dy, int(key[2]) + dz))
                    if hits:
                        left.extend(hits)
                        right.extend([index] * len(hits))
    if not left:
        return 0.0, False
    left_index = np.asarray(left, np.int32)
    right_index = np.asarray(right, np.int32)
    first = first_vertices[first_triangles[left_index]]
    second = second_vertices[second_triangles[right_index]]
    near = np.linalg.norm(first.mean(axis=1) - second.mean(axis=1), axis=1) <= radius
    if not np.any(near):
        return 0.0, False
    return float(_pair_distance(first[near], second[near]).min()), True


def _pair_distance(first, second):
    best = np.full(len(first), np.inf)
    for corner in range(3):
        best = np.minimum(best, _point_triangle(first[:, corner], second))
        best = np.minimum(best, _point_triangle(second[:, corner], first))
    for start, stop in ((0, 1), (1, 2), (2, 0)):
        for other_start, other_stop in ((0, 1), (1, 2), (2, 0)):
            best = np.minimum(
                best,
                _segment_distance(
                    first[:, start], first[:, stop], second[:, other_start], second[:, other_stop]
                ),
            )
    return best


def _point_triangle(points, triangles):
    corner_a = triangles[:, 0]
    corner_b = triangles[:, 1]
    corner_c = triangles[:, 2]
    edge_ab = corner_b - corner_a
    edge_ac = corner_c - corner_a
    normal = np.cross(edge_ab, edge_ac)
    length = np.maximum(np.linalg.norm(normal, axis=1), 1.0e-12)
    unit = normal / length[:, None]
    signed = np.sum((points - corner_a) * unit, axis=1)
    projected = points - signed[:, None] * unit
    d00 = np.sum(edge_ab * edge_ab, axis=1)
    d01 = np.sum(edge_ab * edge_ac, axis=1)
    d11 = np.sum(edge_ac * edge_ac, axis=1)
    relative = projected - corner_a
    d20 = np.sum(relative * edge_ab, axis=1)
    d21 = np.sum(relative * edge_ac, axis=1)
    denom = d00 * d11 - d01 * d01
    denom = np.where(np.abs(denom) < 1.0e-18, 1.0, denom)
    beta = (d11 * d20 - d01 * d21) / denom
    gamma = (d00 * d21 - d01 * d20) / denom
    inside = (1.0 - beta - gamma >= -1.0e-8) & (beta >= -1.0e-8) & (gamma >= -1.0e-8)
    edges = np.minimum(
        np.minimum(_segment_distance(points, points, corner_a, corner_b), _segment_distance(points, points, corner_b, corner_c)),
        _segment_distance(points, points, corner_c, corner_a),
    )
    return np.where(inside, np.abs(signed), edges)


def _segment_distance(start, stop, other_start, other_stop):
    direction = stop - start
    other = other_stop - other_start
    between = start - other_start
    aa = np.sum(direction * direction, axis=1)
    bb = np.sum(direction * other, axis=1)
    cc = np.sum(other * other, axis=1)
    dd = np.sum(direction * between, axis=1)
    ee = np.sum(other * between, axis=1)
    denom = aa * cc - bb * bb
    degenerate = denom < 1.0e-12
    first = np.where(degenerate, 0.0, np.clip((bb * ee - cc * dd) / np.where(degenerate, 1.0, denom), 0.0, 1.0))
    second = np.clip((bb * first + ee) / np.maximum(cc, 1.0e-18), 0.0, 1.0)
    first = np.clip((bb * second - dd) / np.maximum(aa, 1.0e-18), 0.0, 1.0)
    second = np.clip((bb * first + ee) / np.maximum(cc, 1.0e-18), 0.0, 1.0)
    return np.linalg.norm((start + first[:, None] * direction) - (other_start + second[:, None] * other), axis=1)


def _along_tooth(ring_spec, pinion_shape, ring_shape) -> list[dict]:
    raw = ring_pitch_flank_points(ring_spec)
    stations = ("heel", "heel", "mid", "mid", "toe", "toe")
    grouped: dict[str, dict] = {}
    for point, station in zip(raw, stations):
        world = _world_vertices(np.array([point], float), ring_shape)[0]
        probed = kernel.distance_to_point(pinion_shape, (float(world[0]), float(world[1]), float(world[2])))
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
