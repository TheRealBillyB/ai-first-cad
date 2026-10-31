"""Involute gears against published proportions and the involute equation."""

import math
from pathlib import Path

import pytest

from aicad import kernel
from aicad.cli import main as cli_main
from aicad.document import Document
from aicad.errors import GeometryError
from aicad.gears import build_gear, families, gear_data, mesh_placement, pair_geometry, profile_points
from aicad.gears import tables
from aicad.server import build_server
from aicad.workspace import Workspace


def _inv(alpha: float) -> float:
    return math.tan(alpha) - alpha


def _half_angle(radius: float, pitch: float, base: float, alpha: float, thickness: float) -> float:
    alpha_r = math.acos(min(1.0, base / radius))
    return thickness / (2.0 * pitch) + _inv(alpha) - _inv(alpha_r)


def _section_loops(shape, origin=(0.0, 0.0, 0.0), normal=(0.0, 0.0, 1.0)):
    report = kernel.section_geometry(shape, origin, normal)
    loops = []
    for loop in report.loops:
        points = []
        for u, v in loop.points_mm:
            points.append(
                (
                    report.plane_origin_mm.x + u * report.plane_x_mm.x + v * report.plane_y_mm.x,
                    report.plane_origin_mm.y + u * report.plane_x_mm.y + v * report.plane_y_mm.y,
                )
            )
        loops.append((loop.kind, points))
    return loops


def _circle_angles(points, radius: float) -> list[float]:
    hits = []
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        dx, dy = x2 - x1, y2 - y1
        quad = dx * dx + dy * dy
        if quad < 1e-16:
            continue
        linear = 2.0 * (x1 * dx + y1 * dy)
        constant = x1 * x1 + y1 * y1 - radius * radius
        disc = linear * linear - 4.0 * quad * constant
        if disc < 0:
            continue
        root = math.sqrt(disc)
        for scale in ((-linear - root) / (2.0 * quad), (-linear + root) / (2.0 * quad)):
            if -1e-6 <= scale <= 1.0 + 1e-6:
                hits.append(math.atan2(y1 + scale * dy, x1 + scale * dx))
    return hits


def _hypoid_member(role: str) -> dict:
    teeth, mate = (8, 16) if role == "pinion" else (16, 8)
    return {
        "type": "hypoid",
        "role": role,
        "teeth": teeth,
        "mate_teeth": mate,
        "offset_mm": 12,
        "shaft_angle_deg": 90,
        "ring_diameter_mm": 80,
        "face_width_mm": 10,
        "spiral_angle_deg": 35,
        "pressure_angle_deg": 20,
    }


def test_hypoid_is_registered_and_other_families_keep_zero_offset():
    assert families()["hypoid"]["axis_layout"] == "offset"
    assert "hypoid" in families()
    spur = {"type": "spur", "module_mm": 2, "teeth": 20, "face_width_mm": 8, "offset_mm": 1}
    with pytest.raises(GeometryError, match="offset_mm"):
        gear_data(spur)
    hob = _hypoid_member("pinion")
    hob["cutter"] = {"process": "face_hob"}
    with pytest.raises(GeometryError, match="face_mill"):
        gear_data(hob)


def test_hypoid_ratio_and_offset_are_not_a_slid_bevel():
    from aicad.gears.hypoid import line_distance

    pinion = gear_data(_hypoid_member("pinion"))
    ring = gear_data(_hypoid_member("ring"))
    assert pinion["ratio"] == 2
    assert ring["ratio"] == 2
    assert pinion["offset_mm"] == 12
    # A bevel with this ratio at 90 degrees would split the shaft angle between the cones.
    assert abs(pinion["pinion_pitch_angle_deg"] + pinion["ring_pitch_angle_deg"] - 90) > 0.2
    assert abs(abs(pinion["pinion_spiral_angle_deg"]) - abs(pinion["ring_spiral_angle_deg"])) > 1
    assembly = pinion["assembly"]
    distance = line_distance(
        assembly["pinion_axis_point_mm"],
        assembly["pinion_axis_direction"],
        assembly["gear_axis_point_mm"],
        assembly["gear_axis_direction"],
    )
    assert abs(distance - 12) < 1e-6
    report = pair_geometry(_hypoid_member("pinion"), _hypoid_member("ring"))
    assert report["ratio"] == 2
    assert report["offset_mm"] == 12
    assert report["axis_layout"] == "offset"


def _large_offset_member(role: str) -> dict:
    teeth, mate = (11, 41) if role == "pinion" else (41, 11)
    return {
        "type": "hypoid",
        "role": role,
        "teeth": teeth,
        "mate_teeth": mate,
        "offset_mm": 20,
        "shaft_angle_deg": 90,
        "ring_diameter_mm": 220,
        "face_width_mm": 30,
        "spiral_angle_deg": 35,
        "pressure_angle_deg": 20,
    }


def test_a_large_offset_pinion_is_a_closed_solid():
    from aicad.gears.hypoid import ring_pitch_flank_points

    pinion = kernel.adopt(build_gear(_large_offset_member("pinion")))
    ring = kernel.adopt(build_gear(_large_offset_member("ring")))
    assert kernel.measure(pinion).watertight and kernel.measure(pinion).valid
    assert kernel.measure(ring).watertight and kernel.measure(ring).valid
    for angle in (0.0, 4.0):
        placement = mesh_placement(_large_offset_member("pinion"), _large_offset_member("ring"), angle)
        placed_pinion = kernel.place(
            pinion,
            tuple(placement["pinion"]["position_mm"]),
            tuple(placement["pinion"]["rotation_deg"]),
        )
        placed_ring = kernel.place(
            ring,
            tuple(placement["gear"]["position_mm"]),
            tuple(placement["gear"]["rotation_deg"]),
        )
        assert kernel.overlap_volume(placed_pinion, placed_ring) < 15
    from build123d import Location, Pos, Rot

    placement = mesh_placement(_large_offset_member("pinion"), _large_offset_member("ring"), 0.0)
    placed_pinion = kernel.place(
        pinion,
        tuple(placement["pinion"]["position_mm"]),
        tuple(placement["pinion"]["rotation_deg"]),
    )
    location = Location(Rot(*placement["gear"]["rotation_deg"]))
    for point in ring_pitch_flank_points(_large_offset_member("ring")):
        world = location * Pos(*point)
        probed = kernel.distance_to_point(placed_pinion, tuple(world.position))
        assert probed["inside"] is False
        assert probed["distance_mm"] > 0


def test_hypoid_pair_is_a_closed_solid_and_meshes():
    pinion = kernel.adopt(build_gear(_hypoid_member("pinion")))
    ring = kernel.adopt(build_gear(_hypoid_member("ring")))
    for shape in (pinion, ring):
        measured = kernel.measure(shape)
        assert measured.watertight and measured.valid
        assert measured.volume_mm3 > 1000
    for angle in (0.0, 8.0, 16.0):
        placement = mesh_placement(_hypoid_member("pinion"), _hypoid_member("ring"), angle)
        assert placement["offset_mm"] == 12
        first = kernel.place(
            pinion,
            tuple(placement["pinion"]["position_mm"]),
            tuple(placement["pinion"]["rotation_deg"]),
        )
        second = kernel.place(
            ring,
            tuple(placement["gear"]["position_mm"]),
            tuple(placement["gear"]["rotation_deg"]),
        )
        overlap, gap, *_ = kernel.clearance_between(first, second)
        assert overlap < 1e-4
        assert gap < 0.05
    # A move along the offset, with the rotation left at the mesh pose, has to hit.
    placement = mesh_placement(_hypoid_member("pinion"), _hypoid_member("ring"), 0.0)
    position = list(placement["pinion"]["position_mm"])
    position[1] += 0.5
    plunged = kernel.place(pinion, tuple(position), tuple(placement["pinion"]["rotation_deg"]))
    seated = kernel.place(
        ring,
        tuple(placement["gear"]["position_mm"]),
        tuple(placement["gear"]["rotation_deg"]),
    )
    overlap_plunge, *_ = kernel.clearance_between(plunged, seated)
    assert overlap_plunge > 0.3


def test_spur_and_helical_match_iso_21771():
    spur = gear_data(
        {"type": "spur", "module_mm": 2, "teeth": 20, "pressure_angle_deg": 20, "face_width_mm": 10}
    )
    alpha = math.radians(20)
    assert spur["pitch_diameter_mm"] == pytest.approx(40)
    assert spur["base_diameter_mm"] == pytest.approx(40 * math.cos(alpha))
    assert spur["tip_diameter_mm"] == pytest.approx(44)
    assert spur["root_diameter_mm"] == pytest.approx(35)
    assert spur["tooth_thickness_normal_mm"] == pytest.approx(math.pi)
    assert spur["standards"] == ["ISO 21771:2007", "ISO 53:1998"]

    shifted = gear_data(
        {
            "type": "spur",
            "module_mm": 2,
            "teeth": 20,
            "pressure_angle_deg": 20,
            "face_width_mm": 8,
            "profile_shift": 0.5,
        }
    )
    assert shifted["tip_diameter_mm"] == pytest.approx(46)
    assert shifted["root_diameter_mm"] == pytest.approx(37)
    assert shifted["tooth_thickness_normal_mm"] == pytest.approx(math.pi + 2 * math.tan(alpha))

    beta = math.radians(15)
    helical = gear_data(
        {
            "type": "helical",
            "module_mm": 2,
            "teeth": 20,
            "pressure_angle_deg": 20,
            "helix_angle_deg": 15,
            "face_width_mm": 20,
        }
    )
    transverse = 2 / math.cos(beta)
    diameter = 20 * transverse
    alpha_t = math.atan(math.tan(alpha) / math.cos(beta))
    assert helical["pitch_diameter_mm"] == pytest.approx(diameter)
    assert helical["transverse_pressure_angle_deg"] == pytest.approx(math.degrees(alpha_t))
    assert helical["base_diameter_mm"] == pytest.approx(diameter * math.cos(alpha_t))
    assert helical["tip_diameter_mm"] == pytest.approx(diameter + 4)
    assert helical["root_diameter_mm"] == pytest.approx(diameter - 5)
    assert helical["overlap_ratio"] == pytest.approx(20 * math.sin(beta) / (math.pi * 2))
    assert helical["hand"] == "right"

    low = gear_data({"type": "spur", "module_mm": 2, "teeth": 12, "pressure_angle_deg": 20, "face_width_mm": 6})
    assert low["undercut"] is True


def test_same_hand_gears_are_not_placed_as_a_mesh():
    helical = {
        "type": "helical",
        "module_mm": 2,
        "teeth": 20,
        "pressure_angle_deg": 20,
        "helix_angle_deg": 15,
        "face_width_mm": 10,
    }
    with pytest.raises(GeometryError, match="opposite hands"):
        pair_geometry(helical, helical)
    opposite = pair_geometry(helical, {**helical, "helix_angle_deg": -15})
    assert opposite["axis_layout"] == "parallel"
    assert opposite["backlash_mm"] == 0.0
    assert opposite["centre_distance_mm"] == pytest.approx(40 / math.cos(math.radians(15)), abs=1e-4)

    spiral = {
        "type": "spiral_bevel",
        "module_mm": 2,
        "teeth": 20,
        "mate_teeth": 40,
        "pressure_angle_deg": 20,
        "face_width_mm": 8,
        "spiral_angle_deg": 25,
        "shaft_angle_deg": 90,
    }
    mate = {**spiral, "teeth": 40, "mate_teeth": 20, "spiral_angle_deg": 25}
    with pytest.raises(GeometryError, match="opposite hands"):
        pair_geometry(spiral, mate)
    meshed = pair_geometry(spiral, {**mate, "spiral_angle_deg": -25})
    assert meshed["axis_layout"] == "intersecting"
    assert meshed["backlash_mm"] == 0.0


def test_a_computed_gear_record_builds_from_its_spec():
    """Results that share an input name must not be applied a second time."""
    bevel = {
        "type": "straight_bevel",
        "module_mm": 2,
        "teeth": 16,
        "mate_teeth": 32,
        "face_width_mm": 8,
        "pressure_angle_deg": 20,
        "shaft_angle_deg": 90,
    }
    mate = {**bevel, "teeth": 32, "mate_teeth": 16}
    data = gear_data(bevel)
    again = gear_data(data)
    assert data["profile_shift"] != pytest.approx(0)
    assert again["profile_shift"] == pytest.approx(data["profile_shift"])
    assert again["pitch_angle_deg"] == pytest.approx(data["pitch_angle_deg"])
    assert again["addendum_mm"] == pytest.approx(data["addendum_mm"])
    assert again["tip_diameter_mm"] == pytest.approx(data["tip_diameter_mm"])
    placed = pair_geometry(data, gear_data(mate))
    direct = pair_geometry(bevel, mate)
    assert placed["transverse_contact_ratio"] == pytest.approx(direct["transverse_contact_ratio"])
    assert mesh_placement(data, gear_data(mate))["gear"]["rotation_deg"] == pytest.approx(
        mesh_placement(bevel, mate)["gear"]["rotation_deg"]
    )
    with pytest.raises(GeometryError, match="profile_shift"):
        gear_data({**bevel, "profile_shift": 0.2})
    solid = kernel.adopt(build_gear(data))
    original = kernel.adopt(build_gear(bevel))
    assert kernel.measure(solid).volume_mm3 == pytest.approx(
        kernel.measure(original).volume_mm3, rel=1e-6
    )

    spiral = {
        "type": "spiral_bevel",
        "module_mm": 2,
        "teeth": 16,
        "mate_teeth": 32,
        "face_width_mm": 8,
        "pressure_angle_deg": 20,
        "shaft_angle_deg": 90,
        "spiral_angle_deg": 25,
    }
    spiral_data = gear_data(spiral)
    spiral_again = gear_data(spiral_data)
    assert spiral_again["spiral_angle_deg"] == pytest.approx(spiral_data["spiral_angle_deg"])
    assert spiral_again["profile_shift"] == pytest.approx(spiral_data["profile_shift"])

    pinion = gear_data(_hypoid_member("pinion"))
    rebuilt = gear_data(pinion)
    assert pinion["spiral_angle_deg"] != pytest.approx(pinion["spec"]["spiral_angle_deg"])
    assert rebuilt["spiral_angle_deg"] == pytest.approx(pinion["spiral_angle_deg"])
    assert rebuilt["ring_spiral_angle_deg"] == pytest.approx(35)
    assert rebuilt["pitch_angle_deg"] == pytest.approx(pinion["pitch_angle_deg"])
    assert rebuilt["normal_module_mm"] == pytest.approx(pinion["normal_module_mm"])

    spur = {
        "type": "spur",
        "module_mm": 2,
        "teeth": 20,
        "face_width_mm": 8,
        "profile_shift": 0.25,
    }
    spur_data = gear_data(spur)
    spur_again = gear_data(spur_data)
    assert spur_again["profile_shift"] == pytest.approx(0.25)
    assert spur_again["tooth_thickness_transverse_mm"] == pytest.approx(
        spur_data["tooth_thickness_transverse_mm"]
    )


def test_spur_pair_contact_ratio_is_the_path_of_contact():
    alpha = math.radians(20)
    radius_1, radius_2 = 20.0, 40.0
    tip_1, tip_2 = 22.0, 42.0
    base_1, base_2 = radius_1 * math.cos(alpha), radius_2 * math.cos(alpha)
    centre = 60.0
    path = math.sqrt(tip_1**2 - base_1**2) + math.sqrt(tip_2**2 - base_2**2) - centre * math.sin(alpha)
    expected = path / (math.pi * 2 * math.cos(alpha))
    left = {"type": "spur", "module_mm": 2, "teeth": 20, "pressure_angle_deg": 20, "face_width_mm": 12}
    right = {"type": "spur", "module_mm": 2, "teeth": 40, "pressure_angle_deg": 20, "face_width_mm": 10}
    report = pair_geometry(left, right)
    assert report["centre_distance_mm"] == pytest.approx(centre)
    assert report["transverse_contact_ratio"] == pytest.approx(expected, abs=1e-6)
    assert report["total_contact_ratio"] == pytest.approx(expected, abs=1e-6)


def test_profile_points_lie_on_the_involute():
    spec = {"type": "spur", "module_mm": 2, "teeth": 20, "pressure_angle_deg": 20, "face_width_mm": 8, "profile_shift": 0.2}
    data = gear_data(spec)
    alpha = math.radians(data["transverse_pressure_angle_deg"])
    pitch = data["pitch_diameter_mm"] / 2
    base = data["base_diameter_mm"] / 2
    thickness = data["tooth_thickness_transverse_mm"]
    form = data["form_radius_mm"]
    tip = data["tip_diameter_mm"] / 2
    half_pitch = math.pi / data["teeth"]
    checked = 0
    for x, y in profile_points(spec):
        radius = math.hypot(x, y)
        angle = math.atan2(y, x)
        if not (0.01 < angle < half_pitch and form + 0.15 < radius < tip - 0.05):
            continue
        expect = _half_angle(radius, pitch, base, alpha, thickness)
        assert abs(angle - expect) * radius < 1e-6
        checked += 1
    assert checked > 10


def test_straight_and_spiral_bevel_match_the_worked_20_40_pair():
    # Gleason proportions for m = 3, 20/40, shaft angle 90. Outer cone distance
    # (3/2) * sqrt(20**2 + 40**2) = 67.082. Straight addendum 4.035 / 1.965.
    # Spiral addendum 3.4275 / 1.6725 at a 35 degree mean spiral.
    common = {"module_mm": 3, "pressure_angle_deg": 20, "face_width_mm": 20, "shaft_angle_deg": 90}
    pinion = gear_data({"type": "straight_bevel", "teeth": 20, "mate_teeth": 40, **common})
    gear = gear_data({"type": "straight_bevel", "teeth": 40, "mate_teeth": 20, **common})
    assert pinion["pitch_angle_deg"] == pytest.approx(26.56505, abs=1e-4)
    assert gear["pitch_angle_deg"] == pytest.approx(63.43495, abs=1e-4)
    assert pinion["pitch_diameter_mm"] == pytest.approx(60)
    assert gear["pitch_diameter_mm"] == pytest.approx(120)
    assert pinion["outer_cone_distance_mm"] == pytest.approx(67.08204, abs=1e-4)
    assert pinion["addendum_mm"] == pytest.approx(4.035, abs=1e-4)
    assert pinion["dedendum_mm"] == pytest.approx(2.529, abs=1e-4)
    assert gear["addendum_mm"] == pytest.approx(1.965, abs=1e-4)
    assert gear["dedendum_mm"] == pytest.approx(4.599, abs=1e-4)
    assert pinion["tip_diameter_mm"] == pytest.approx(67.2180, abs=1e-3)
    assert gear["tip_diameter_mm"] == pytest.approx(121.7575, abs=1e-3)
    assert "ISO 23509:2016" in pinion["standards"]
    assert "ANSI/AGMA 2005-D03" in pinion["standards"]

    spiral = gear_data(
        {"type": "spiral_bevel", "teeth": 20, "mate_teeth": 40, "spiral_angle_deg": -35, **common}
    )
    mate = gear_data(
        {"type": "spiral_bevel", "teeth": 40, "mate_teeth": 20, "spiral_angle_deg": 35, **common}
    )
    assert spiral["hand"] == "left"
    assert mate["hand"] == "right"
    assert spiral["transverse_pressure_angle_deg"] == pytest.approx(23.9568, abs=1e-3)
    assert spiral["addendum_mm"] == pytest.approx(3.4275, abs=1e-4)
    assert spiral["dedendum_mm"] == pytest.approx(2.2365, abs=1e-4)
    assert mate["addendum_mm"] == pytest.approx(1.6725, abs=1e-4)
    assert mate["dedendum_mm"] == pytest.approx(3.9915, abs=1e-4)
    assert spiral["tip_diameter_mm"] == pytest.approx(66.1313, abs=1e-3)
    assert mate["tip_diameter_mm"] == pytest.approx(121.4959, abs=1e-3)
    with pytest.raises(GeometryError, match="profile_shift"):
        gear_data({"type": "straight_bevel", "teeth": 20, "mate_teeth": 40, "profile_shift": 0.2, **common})


def _spiral_bevel(teeth: int, mate: int, face: float, spiral: float) -> dict:
    return {
        "type": "spiral_bevel",
        "module_mm": 2,
        "teeth": teeth,
        "mate_teeth": mate,
        "pressure_angle_deg": 20,
        "face_width_mm": face,
        "spiral_angle_deg": spiral,
        "shaft_angle_deg": 90,
    }


def test_bevel_contact_uses_the_face_both_gears_share():
    short = pair_geometry(_spiral_bevel(20, 40, 8, 25), _spiral_bevel(40, 20, 8, -25))
    wide_pinion = pair_geometry(_spiral_bevel(20, 40, 16, 25), _spiral_bevel(40, 20, 8, -25))
    wide_gear = pair_geometry(_spiral_bevel(20, 40, 8, 25), _spiral_bevel(40, 20, 16, -25))
    assert short["overlap_ratio"] > 0
    assert wide_pinion["overlap_ratio"] == pytest.approx(short["overlap_ratio"], abs=1e-6)
    assert wide_gear["overlap_ratio"] == pytest.approx(short["overlap_ratio"], abs=1e-6)
    assert wide_pinion["transverse_contact_ratio"] == pytest.approx(short["transverse_contact_ratio"], abs=1e-6)
    assert wide_gear["transverse_contact_ratio"] == pytest.approx(short["transverse_contact_ratio"], abs=1e-6)


def test_bevel_pitch_cones_at_a_60_degree_shaft_angle():
    sigma = math.radians(60)
    ratio = 40 / 20
    delta = math.degrees(math.atan(math.sin(sigma) / (ratio + math.cos(sigma))))
    data = gear_data(
        {
            "type": "straight_bevel",
            "module_mm": 2.5,
            "teeth": 20,
            "mate_teeth": 40,
            "pressure_angle_deg": 20,
            "face_width_mm": 10,
            "shaft_angle_deg": 60,
        }
    )
    assert data["pitch_angle_deg"] == pytest.approx(delta, abs=1e-6)
    assert data["mate_pitch_angle_deg"] == pytest.approx(60 - delta, abs=1e-6)


def test_key_and_spline_tables():
    key = tables.din6885_key(20)
    assert key["width_mm"] == 6
    assert key["hub_depth_mm"] == 2.8
    assert tables.din6885_key(22)["width_mm"] == 6
    assert tables.din6885_key(22.1)["width_mm"] == 8
    medium = tables.iso14_spline("medium", 56)
    assert medium["major_mm"] == 65
    assert medium["count"] == 8
    light = tables.iso14_spline("light", 62)
    assert light["major_mm"] == 68
    spec = {
        "type": "spur",
        "module_mm": 8,
        "teeth": 30,
        "face_width_mm": 20,
        "spline": {"standard": "din5480", "module_mm": 3, "teeth": 38, "reference_diameter_mm": 120},
    }
    spline = gear_data(spec)["spec"]["spline"]
    assert spline["profile_shift"] == pytest.approx(0.45)
    assert spline["hub_tip_diameter_mm"] == pytest.approx(114)
    assert spline["hub_root_diameter_mm"] == pytest.approx(120)
    alpha = math.radians(30)
    thickness = 3 * (math.pi / 2 + 2 * 0.45 * math.tan(alpha))
    pitch = 3 * 38 / 2
    base = pitch * math.cos(alpha)
    # Space half-angle at the reference radius equals the shaft tooth half-angle.
    half = _half_angle(pitch, pitch, base, alpha, thickness)
    assert half == pytest.approx(thickness / (2 * pitch))


def test_generated_spur_flank_matches_the_involute_and_a_pair_meshes():
    spec = {"type": "spur", "module_mm": 2, "teeth": 16, "pressure_angle_deg": 20, "face_width_mm": 4}
    data = gear_data(spec)
    shape = kernel.adopt(build_gear(spec))
    measured = kernel.measure(shape)
    assert measured.watertight and measured.valid
    assert measured.bounding_box.size.z == pytest.approx(4)
    outer = next(points for kind, points in _section_loops(shape) if kind == "outer")
    alpha = math.radians(20)
    pitch = data["pitch_diameter_mm"] / 2
    base = data["base_diameter_mm"] / 2
    thickness = data["tooth_thickness_transverse_mm"]
    for radius in (pitch, pitch + 1.2):
        angles = [angle for angle in _circle_angles(outer, radius) if 0 < angle < math.pi / 16]
        assert angles, radius
        expect = _half_angle(radius, pitch, base, alpha, thickness)
        assert abs(min(angles) - expect) * radius < 0.02

    mate = {"type": "spur", "module_mm": 2, "teeth": 24, "pressure_angle_deg": 20, "face_width_mm": 4}
    placement = mesh_placement(spec, mate)
    gear = kernel.place(
        kernel.adopt(build_gear(mate)),
        tuple(placement["gear"]["position_mm"]),
        tuple(placement["gear"]["rotation_deg"]),
    )
    overlap, gap, *_ = kernel.clearance_between(shape, gear)
    assert overlap < 1e-4
    assert 0 <= gap < 0.05
    close = list(placement["gear"]["position_mm"])
    close[0] -= 0.3
    crowded = kernel.place(kernel.adopt(build_gear(mate)), tuple(close), tuple(placement["gear"]["rotation_deg"]))
    overlap_close, *_ = kernel.clearance_between(shape, crowded)
    assert overlap_close > overlap + 0.5


def test_helical_tooth_advances_with_height():
    spec = {
        "type": "helical",
        "module_mm": 2,
        "teeth": 14,
        "pressure_angle_deg": 20,
        "helix_angle_deg": 12,
        "face_width_mm": 8,
    }
    data = gear_data(spec)
    shape = kernel.adopt(build_gear(spec))
    assert kernel.measure(shape).watertight
    pitch = data["pitch_diameter_mm"] / 2

    def flank(z):
        outer = next(points for kind, points in _section_loops(shape, (0, 0, z), (0, 0, 1)) if kind == "outer")
        angles = [angle for angle in _circle_angles(outer, pitch) if -0.2 < angle < 0.4]
        assert angles
        return min(angle for angle in angles if angle > 0)

    rise = flank(2.0) - flank(0.0)
    expected = 2.0 * math.tan(math.radians(12)) / pitch
    assert rise == pytest.approx(expected, abs=0.01)


def test_bevel_pair_meshes_and_a_plunge_interferes():
    pinion = {
        "type": "straight_bevel",
        "module_mm": 2,
        "teeth": 12,
        "mate_teeth": 18,
        "pressure_angle_deg": 20,
        "face_width_mm": 5,
    }
    gear = {
        "type": "straight_bevel",
        "module_mm": 2,
        "teeth": 18,
        "mate_teeth": 12,
        "pressure_angle_deg": 20,
        "face_width_mm": 5,
    }
    placement = mesh_placement(pinion, gear)
    first = kernel.adopt(build_gear(pinion))
    second = kernel.place(
        kernel.adopt(build_gear(gear)),
        tuple(placement["gear"]["position_mm"]),
        tuple(placement["gear"]["rotation_deg"]),
    )
    overlap, gap, *_ = kernel.clearance_between(first, second)
    assert overlap < 1e-4
    assert gap < 0.05
    plunged = kernel.place(first, (0.0, 0.0, -0.4), (0.0, 0.0, 0.0))
    overlap_plunge, *_ = kernel.clearance_between(plunged, second)
    assert overlap_plunge > 0.3


def test_spiral_bevel_solid_is_closed():
    spec = {
        "type": "spiral_bevel",
        "module_mm": 2,
        "teeth": 12,
        "mate_teeth": 24,
        "pressure_angle_deg": 20,
        "face_width_mm": 6,
        "spiral_angle_deg": 25,
    }
    shape = kernel.adopt(build_gear(spec))
    measured = kernel.measure(shape)
    assert measured.watertight and measured.valid
    assert measured.volume_mm3 > 1000


def test_bore_keyway_hub_and_splines_cut_the_blank():
    keyed = {
        "type": "spur",
        "module_mm": 2,
        "teeth": 22,
        "pressure_angle_deg": 20,
        "face_width_mm": 5,
        "bore_mm": 20,
        "keyway": {"standard": "din6885"},
        "hub": {"diameter_mm": 28, "length_mm": 4, "side": "front"},
    }
    shape = kernel.adopt(build_gear(keyed))
    measured = kernel.measure(shape)
    assert measured.watertight
    assert measured.bounding_box.max.z > 2.5 + 2
    hole = next(points for kind, points in _section_loops(shape) if kind == "hole")
    radii = [math.hypot(x, y) for x, y in hole]
    assert min(radii) == pytest.approx(10, abs=0.15)
    # The keyway bottom is 2.8 mm past the 20 mm bore, centred on +X. The slot
    # corners sit farther from the origin than that flat.
    assert max(point[0] for point in hole) == pytest.approx(12.8, abs=0.1)
    slot = [point for point in hole if point[0] > 12]
    assert slot and max(abs(point[1]) for point in slot) == pytest.approx(3, abs=0.2)

    iso = {
        "type": "spur",
        "module_mm": 3,
        "teeth": 24,
        "pressure_angle_deg": 20,
        "face_width_mm": 5,
        "spline": {"standard": "iso14", "series": "medium", "minor_mm": 23},
    }
    iso_shape = kernel.adopt(build_gear(iso))
    assert kernel.measure(iso_shape).watertight
    # Medium 6 x 23 x 28: void inside the minor radius, a groove out to the major
    # on +X, and metal between grooves.
    assert not iso_shape.impl.is_inside((0, 0, 0))
    assert not iso_shape.impl.is_inside((11.0, 0, 0))
    assert not iso_shape.impl.is_inside((13.5, 0, 0))
    assert iso_shape.impl.is_inside((14.6, 0, 0))
    land = math.radians(30)
    assert iso_shape.impl.is_inside((12.5 * math.cos(land), 12.5 * math.sin(land), 0))

    din = {
        "type": "spur",
        "module_mm": 3,
        "teeth": 24,
        "pressure_angle_deg": 20,
        "face_width_mm": 5,
        "spline": {"standard": "din5480", "module_mm": 2, "teeth": 14, "reference_diameter_mm": 30},
    }
    shift = gear_data(din)["spec"]["spline"]["profile_shift"]
    din_shape = kernel.adopt(build_gear(din))
    assert kernel.measure(din_shape).watertight
    alpha = math.radians(30)
    pitch = 2 * 14 / 2
    base = pitch * math.cos(alpha)
    thickness = 2 * (math.pi / 2 + 2 * shift * math.tan(alpha))
    expect = _half_angle(pitch, pitch, base, alpha, thickness)
    # The space is centred on +X. Just inside that involute is void; just outside it is metal.
    void = (pitch * math.cos(expect - 0.012), pitch * math.sin(expect - 0.012), 0)
    metal = (pitch * math.cos(expect + 0.012), pitch * math.sin(expect + 0.012), 0)
    assert not din_shape.impl.is_inside(void)
    assert din_shape.impl.is_inside(metal)


def test_a_gear_past_a_kilometer_is_refused():
    spur = {"type": "spur", "module_mm": 2, "teeth": 18, "pressure_angle_deg": 20, "face_width_mm": 8}
    with pytest.raises(GeometryError, match="kilometer"):
        gear_data({**spur, "module_mm": 1e7})
    with pytest.raises(GeometryError, match="kilometer"):
        gear_data({**spur, "face_width_mm": 2_000_000})
    legal = gear_data({**spur, "module_mm": 100})
    assert legal["pitch_diameter_mm"] == pytest.approx(1800)
    with pytest.raises(GeometryError, match="kilometer"):
        gear_data(
            {
                "type": "hypoid",
                "role": "pinion",
                "teeth": 9,
                "mate_teeth": 35,
                "offset_mm": 22,
                "shaft_angle_deg": 90,
                "ring_diameter_mm": 2_000_000,
                "face_width_mm": 18,
                "spiral_angle_deg": 35,
            }
        )


def test_a_stored_keyway_or_spline_builds_again(tmp_path: Path):
    keyed = {
        "type": "spur",
        "module_mm": 2,
        "teeth": 22,
        "pressure_angle_deg": 20,
        "face_width_mm": 5,
        "bore_mm": 20,
        "keyway": {"standard": "DIN 6885-1:1968"},
    }
    stored = gear_data(keyed)["spec"]
    assert stored["keyway"]["standard"] == "DIN 6885-1:1968"
    assert gear_data(stored)["spec"]["keyway"]["width_mm"] == stored["keyway"]["width_mm"]
    document = Document(tmp_path)
    created = document.create_gear("keyed", keyed)
    assert created["volume_mm3"] > 0
    updated = document.update_gear("keyed", document.get_source("keyed")["gear"])
    assert updated["volume_mm3"] == pytest.approx(created["volume_mm3"], rel=1e-6)

    din = {
        "type": "spur",
        "module_mm": 3,
        "teeth": 24,
        "pressure_angle_deg": 20,
        "face_width_mm": 5,
        "spline": {"standard": "DIN 5480-1:2006", "module_mm": 2, "teeth": 14, "reference_diameter_mm": 30},
    }
    stored_din = gear_data(din)["spec"]
    assert stored_din["spline"]["standard"] == "DIN 5480-1:2006"
    assert gear_data(stored_din)["spec"]["spline"]["teeth"] == 14
    iso = {
        "type": "spur",
        "module_mm": 3,
        "teeth": 24,
        "pressure_angle_deg": 20,
        "face_width_mm": 5,
        "spline": {"standard": "ISO 14:1982", "series": "medium", "minor_mm": 23},
    }
    stored_iso = gear_data(iso)["spec"]
    assert gear_data(stored_iso)["spec"]["spline"]["minor_mm"] == stored_iso["spline"]["minor_mm"]


def test_document_reloads_a_gear_and_the_command_layer_matches(tmp_path: Path):
    spec = {"type": "spur", "module_mm": 2, "teeth": 14, "pressure_angle_deg": 20, "face_width_mm": 4}
    document = Document(tmp_path / "project")
    created = document.create_gear("pinion", spec)
    assert created["result_kind"] == "verified_check"
    assert created["source"] == "gear"
    assert created["gear"]["pitch_diameter_mm"] == pytest.approx(28)
    volume = created["volume_mm3"]
    source = document.get_source("pinion")
    assert source["gear"]["teeth"] == 14
    assert source["gear_data"]["base_diameter_mm"] == pytest.approx(28 * math.cos(math.radians(20)))
    reopened = Document(tmp_path / "project")
    assert reopened.measure("pinion").volume_mm3 == pytest.approx(volume, rel=1e-6)
    with pytest.raises(GeometryError, match="already"):
        document.create_gear("pinion", spec)
    replaced = document.update_gear("pinion", {**spec, "teeth": 16})
    assert replaced["gear"]["teeth"] == 16
    assert abs(replaced["volume_mm3"] - volume) / volume > 0.05

    document.place_part("pinion", [10, 20, 30], [0, 0, 15])
    kept = document.update_gear("pinion", {**spec, "teeth": 18})
    assert kept["gear"]["teeth"] == 18
    assert document.get_source("pinion")["placement"] == {
        "position": [10.0, 20.0, 30.0],
        "rotation_deg": [0.0, 0.0, 15.0],
    }
    kept_box = document.measure("pinion").bounding_box
    kept_center_x = (kept_box.min.x + kept_box.max.x) / 2
    assert kept_center_x == pytest.approx(10, abs=0.5)
    moved = document.update_gear("pinion", {**spec, "teeth": 18}, position=[1, 2, 3])
    assert document.get_source("pinion")["placement"]["position"] == [1.0, 2.0, 3.0]
    assert document.get_source("pinion")["placement"]["rotation_deg"] == [0.0, 0.0, 15.0]
    _ = moved

    workspace = Workspace(tmp_path / "app")
    report = workspace.execute("create_gear", {"name": "pinion", "spec": spec, "position": [0, 0, 0]})
    assert report["result_kind"] == "verified_check"
    note = workspace.execute("list_parameters", {"name": "pinion"})
    assert note["source"] == "gear"
    assert "generated gear" in note["note"]
    again = workspace.execute("create_gear", {"name": "pinion", "spec": {**spec, "teeth": 15}})
    assert again["gear"]["teeth"] == 15

    assert cli_main(
        [
            "gear",
            "wheel",
            "--type",
            "spur",
            "--module",
            "2",
            "--teeth",
            "18",
            "--face-width",
            "4",
            "--workspace",
            str(tmp_path / "cli"),
        ]
    ) == 0
    cli_doc = Document(tmp_path / "cli")
    assert cli_doc.get_source("wheel")["source"] == "gear"
    assert cli_doc.measure("wheel").watertight


def test_mcp_create_gear(tmp_path: Path):
    import asyncio
    import json

    server = build_server(tmp_path)
    result = asyncio.run(
        server.call_tool(
            "create_gear",
            {
                "name": "pinion",
                "spec": {
                    "type": "spur",
                    "module_mm": 2,
                    "teeth": 12,
                    "pressure_angle_deg": 20,
                    "face_width_mm": 4,
                },
            },
        )
    )
    body = json.loads(result.content[0].text)
    assert body["result_kind"] == "verified_check"
    assert body["gear"]["pitch_diameter_mm"] == pytest.approx(24)
    assert Document(tmp_path).measure("pinion").watertight


def test_a_negative_shift_with_a_real_working_angle_keeps_that_centre():
    # z = 30, m = 2, x = -0.2, α = 20°. inv αwt stays positive.
    spec = {
        "type": "spur",
        "module_mm": 2,
        "teeth": 30,
        "pressure_angle_deg": 20,
        "face_width_mm": 8,
        "profile_shift": -0.2,
    }
    report = pair_geometry(spec, spec)
    alpha = math.radians(20)
    inv_w = _inv(alpha) + 2.0 * math.tan(alpha) * (-0.4) / 60.0
    assert inv_w > 0
    guess = (3.0 * inv_w) ** (1.0 / 3.0)
    for _ in range(25):
        guess -= (_inv(guess) - inv_w) / (math.tan(guess) ** 2)
    centre = 60.0 * math.cos(alpha) / math.cos(guess)
    assert report["centre_distance_mm"] == pytest.approx(centre, abs=1e-6)
    assert report["working_transverse_pressure_angle_deg"] == pytest.approx(math.degrees(guess), abs=1e-6)
    assert report["centre_distance_mm"] == pytest.approx(59.152342, abs=1e-4)


def test_profile_shifts_with_no_zero_backlash_centre_are_refused():
    # z = 18, m = 2, x = -0.8. inv αwt is negative, so the base circles
    # already leave about 1.18 mm of circumferential backlash.
    spec = {
        "type": "spur",
        "module_mm": 2,
        "teeth": 18,
        "pressure_angle_deg": 20,
        "face_width_mm": 8,
        "profile_shift": -0.8,
    }
    with pytest.raises(GeometryError, match="zero-backlash"):
        pair_geometry(spec, spec)


def _hypoid_at(role: str, shaft_angle_deg: float, offset_mm: float) -> dict:
    teeth, mate = (8, 16) if role == "pinion" else (16, 8)
    return {
        "type": "hypoid",
        "role": role,
        "teeth": teeth,
        "mate_teeth": mate,
        "offset_mm": offset_mm,
        "shaft_angle_deg": shaft_angle_deg,
        "ring_diameter_mm": 80,
        "face_width_mm": 10,
        "spiral_angle_deg": 35,
        "pressure_angle_deg": 20,
    }


def test_a_hypoid_without_a_pitch_point_is_refused_before_the_roll():
    refused = (
        (60, 3), (60, 8), (60, 12), (60, 18),
        (75, 20),
        (90, 18), (90, 20),
        (100, 18), (100, 20),
        (110, 18), (110, 20),
        (120, 12), (120, 18), (120, 20),
    )
    accepted = (
        (60, 6), (60, 20),
        (75, 8), (75, 12), (75, 18),
        (90, 8), (90, 12),
        (100, 8), (100, 12),
        (110, 8), (110, 12),
        (120, 3), (120, 8),
    )
    for shaft, offset in refused:
        with pytest.raises(GeometryError, match="offset_mm") as caught:
            gear_data(_hypoid_at("pinion", shaft, offset))
        text = str(caught.value)
        assert "shaft_angle_deg" in text
        assert "ring_diameter_mm" in text
        assert "spiral_angle_deg" in text
        assert "missed the pinion" not in text
        assert "did not leave a toothed" not in text
    for shaft, offset in accepted:
        data = gear_data(_hypoid_at("pinion", shaft, offset))
        assert data["shaft_angle_deg"] == shaft
        assert data["offset_mm"] == offset
    # The solid builder refuses the same inputs, still before the roll.
    for shaft, offset in ((60, 12), (100, 20), (120, 12)):
        with pytest.raises(GeometryError, match="Change one of those four") as caught:
            build_gear(_hypoid_at("pinion", shaft, offset))
        text = str(caught.value)
        assert "missed the pinion" not in text
        assert "did not leave a toothed" not in text


def test_a_hypoid_pitch_point_survives_when_every_length_is_scaled():
    """Cone angles are scale-free. Growing the blank must not lose the pitch point."""
    small = gear_data(_hypoid_member("ring"))
    factor = 3.0
    grown = dict(_hypoid_member("ring"))
    grown["offset_mm"] = 12.0 * factor
    grown["ring_diameter_mm"] = 80.0 * factor
    grown["face_width_mm"] = 10.0 * factor
    data = gear_data(grown)
    assert data["offset_mm"] == pytest.approx(36.0)
    assert data["ring_pitch_diameter_mm"] == pytest.approx(240.0)
    assert data["pinion_pitch_angle_deg"] == pytest.approx(small["pinion_pitch_angle_deg"], abs=0.05)
    assert data["ring_pitch_angle_deg"] == pytest.approx(small["ring_pitch_angle_deg"], abs=0.05)
    assert data["pinion_spiral_angle_deg"] == pytest.approx(small["pinion_spiral_angle_deg"], abs=0.05)
    assert data["normal_module_mm"] == pytest.approx(small["normal_module_mm"] * factor, rel=0.01)


def test_a_hypoid_tooth_that_cannot_be_cut_is_refused_before_the_roll():
    # 120 degrees and a 6 mm offset has a pitch point, and the tooth at that
    # point does not leave a valid pinion. The refusal names the inputs.
    with pytest.raises(GeometryError, match="Change one of those four") as caught:
        build_gear(_hypoid_at("pinion", 120, 6))
    text = str(caught.value)
    assert "offset_mm" in text
    assert "shaft_angle_deg" in text
    assert "ring_diameter_mm" in text
    assert "spiral_angle_deg" in text
    assert "missed the pinion" not in text
    assert "did not leave a toothed" not in text


def test_a_hypoid_pair_at_100_degrees_builds_both_solids():
    pinion = kernel.measure(kernel.adopt(build_gear(_hypoid_at("pinion", 100, 12))))
    ring = kernel.measure(kernel.adopt(build_gear(_hypoid_at("ring", 100, 12))))
    for measured in (pinion, ring):
        assert measured.watertight and measured.valid
        assert measured.volume_mm3 > 1000
    # The uncut blank is about 25 cm³. A finished pinion is the blank minus the teeth.
    assert 12000 < pinion.volume_mm3 < 21000
    assert ring.volume_mm3 > 40000


def test_a_hypoid_pinion_builds_across_shaft_angles():
    for shaft, offset in ((75, 12), (120, 8), (60, 6), (100, 8)):
        shape = kernel.adopt(build_gear(_hypoid_at("pinion", shaft, offset)))
        measured = kernel.measure(shape)
        assert measured.watertight and measured.valid, (shaft, offset)
        assert 5000 < measured.volume_mm3 < 22000, (shaft, offset, measured.volume_mm3)
    # 110 degrees still has a pitch point. The pinion is built, or the inputs are
    # refused before a pose is reported as a missed tooth.
    try:
        shape = kernel.adopt(build_gear(_hypoid_at("pinion", 110, 12)))
    except GeometryError as exc:
        text = str(exc)
        assert "offset_mm" in text
        assert "shaft_angle_deg" in text
        assert "ring_diameter_mm" in text
        assert "spiral_angle_deg" in text
        assert "missed the pinion" not in text
        assert "did not leave a toothed" not in text
        return
    measured = kernel.measure(shape)
    assert measured.watertight and measured.valid
    assert 5000 < measured.volume_mm3 < 22000


def test_bevel_mate_ratio_and_pitch_angle_must_agree():
    common = {"module_mm": 3, "pressure_angle_deg": 20, "face_width_mm": 12, "shaft_angle_deg": 90}
    with pytest.raises(GeometryError, match="pitch_angle"):
        gear_data({"type": "straight_bevel", "teeth": 20, "mate_teeth": 40, "pitch_angle_deg": 45, **common})
    with pytest.raises(GeometryError, match="ratio"):
        gear_data({"type": "straight_bevel", "teeth": 20, "mate_teeth": 40, "ratio": 3, **common})
    agreed = gear_data(
        {
            "type": "straight_bevel",
            "teeth": 20,
            "mate_teeth": 40,
            "ratio": 2,
            "pitch_angle_deg": 26.56505,
            **common,
        }
    )
    assert agreed["pitch_angle_deg"] == pytest.approx(26.56505, abs=1e-4)
    assert agreed["ratio"] == pytest.approx(2)
