"""Solids for the standard-parts tables.

This module is on the geometry-kernel boundary. It imports build123d.
Callers outside aicad see the document part, not these solids.

Every solid is dimensioned from ``catalog_data``. A bearing is two rings and
rolling elements. A tapered bearing keeps the cone width and the cup width,
not only the assembled width. A seal is a lip, not a sleeve. A screw has a
head, a hex socket, and a shank. A motor has the NEMA face, pilot, holes, and
shaft. None of these is a bounding box.
"""

from __future__ import annotations

import math

from aicad.catalog_data import (
    CAP_SCREWS,
    HEX_NUTS,
    PLAIN_WASHERS,
    CapScrew,
    DeepGroove,
    Gearbox,
    HexNut,
    PlainWasher,
    ShaftSeal,
    StepperMotor,
    TaperedRoller,
    stepper_mm,
)
from aicad.errors import GeometryError
from aicad.paths import prepare_geometry_import

prepare_geometry_import()

# ISO 724 basic minor diameter: d3 = d - 1.226869 * P.
_ISO724_MINOR = 1.226869


def cap_screw(row: CapScrew, length: float):
    if length <= 0:
        raise GeometryError("Socket head cap screw length must be greater than zero.")
    from build123d import Cylinder, Pos

    minor = row.diameter_mm - _ISO724_MINOR * row.pitch_mm
    head = Pos(0, 0, row.head_height_mm / 2) * Cylinder(row.head_diameter_mm / 2, row.head_height_mm)
    socket = _hex_prism(row.socket_across_flats_mm, row.socket_depth_mm + 0.2)
    socket = Pos(0, 0, row.head_height_mm - row.socket_depth_mm) * socket
    head = head - socket
    # The shank crosses the head underside so the two bodies fuse.
    shank = Pos(0, 0, -length / 2 + 0.05) * Cylinder(minor / 2, length + 0.1)
    return head + shank


def hex_nut(row: HexNut):
    return _hex_prism(row.across_flats_mm, row.height_mm)


def plain_washer(row: PlainWasher):
    from build123d import Cylinder, Pos

    height = row.thickness_mm
    outer = Pos(0, 0, height / 2) * Cylinder(row.outside_mm / 2, height)
    inner = Pos(0, 0, height / 2) * Cylinder(row.inside_mm / 2, height + 0.2)
    return outer - inner


def deep_groove(row: DeepGroove):
    bore = row.bore_mm
    outside = row.outside_mm
    width = row.width_mm
    chamfer = row.chamfer_mm
    radial = (outside - bore) / 2
    wall = max(chamfer + 0.25, radial * 0.12)
    ball = min(radial - 2 * wall, width * 0.62)
    if ball < 1:
        raise GeometryError(f"Bearing {row.designation} has no room for a ball in the ISO 15 section.")
    pitch = (bore + outside) / 4
    inner_shoulder = pitch - ball * 0.2
    outer_shoulder = pitch + ball * 0.2
    edge = min(chamfer, (outside / 2 - outer_shoulder) * 0.45, (inner_shoulder - bore / 2) * 0.45, width * 0.2)
    outer = _revolve(
        [
            (outer_shoulder, 0),
            (outer_shoulder, width),
            (outside / 2 - edge, width),
            (outside / 2, width - edge),
            (outside / 2, edge),
            (outside / 2 - edge, 0),
        ]
    )
    inner = _revolve(
        [
            (bore / 2, edge),
            (bore / 2 + edge, 0),
            (inner_shoulder, 0),
            (inner_shoulder, width),
            (bore / 2 + edge, width),
            (bore / 2, width - edge),
        ]
    )
    count = _ring_count(pitch, ball / 2, 1.08, low=6, high=12)
    balls = [
        _placed_sphere(pitch, width / 2, ball / 2, index, count) for index in range(count)
    ]
    return _fuse([outer, inner, *balls])


def tapered_roller(row: TaperedRoller):
    bore = row.bore_mm
    outside = row.outside_mm
    total = row.width_mm
    cone_w = row.cone_width_mm
    cup_w = row.cup_width_mm
    if cone_w > total + 1e-6 or cup_w > total + 1e-6:
        raise GeometryError(f"{row.designation} cone or cup is wider than the assembled bearing.")
    angle = math.radians(row.angle_deg)
    cup_z0 = total - cup_w
    overlap = cone_w - cup_z0
    if overlap <= 1:
        raise GeometryError(f"{row.designation} cone and cup do not overlap.")
    radial = (outside - bore) / 2
    roller = min(radial * 0.42, overlap * 0.45, min(cone_w, cup_w) * 0.45)
    pitch = (bore + outside) / 4
    mid_z = (cup_z0 + cone_w) / 2

    def race(side: float, z: float) -> float:
        # side +1 is the cup (outside the roller), -1 is the cone.
        return pitch + side * roller / 2 + (mid_z - z) * math.tan(angle)

    cup = _revolve(
        [
            (outside / 2, cup_z0),
            (outside / 2, total),
            (max(race(1, total), bore / 2 + roller), total),
            (max(race(1, cup_z0), bore / 2 + roller), cup_z0),
        ]
    )
    cone = _revolve(
        [
            (bore / 2, 0),
            (bore / 2, cone_w),
            (min(race(-1, cone_w), outside / 2 - roller), cone_w),
            (min(race(-1, 0), outside / 2 - roller), 0),
        ]
    )
    length = overlap * 0.72
    count = _ring_count(pitch, roller / 2, 1.25, low=8, high=16)
    rollers = [
        _tilted_roller(pitch, mid_z, roller / 2, length, row.angle_deg, index, count)
        for index in range(count)
    ]
    solid = _fuse([cup, cone, *rollers])
    return solid


def radial_shaft_seal(row: ShaftSeal):
    """DIN 3760 type AS: rubber-covered outside diameter and a dust lip.

    The lip inner diameter is the nominal shaft. Real lip interference is
    smaller than this solid resolves.
    """
    outer = row.housing_mm / 2
    shaft = row.shaft_mm / 2
    width = row.width_mm
    if outer - shaft < 2:
        raise GeometryError(f"Seal {row.designation} has no radial room for a lip.")
    case = outer - min(1.6, (outer - shaft) * 0.28)
    waist = shaft + (case - shaft) * 0.55
    dust = shaft + min(0.8, (waist - shaft) * 0.45)
    return _revolve(
        [
            (outer, 0),
            (outer, width),
            (case, width),
            (shaft, width * 0.70),
            (waist, width * 0.52),
            (dust, width * 0.26),
            (waist, width * 0.10),
            (case, 0),
        ]
    )


def stepper_motor(row: StepperMotor):
    from build123d import Box, Cylinder, Pos

    dim = stepper_mm(row)
    body = dim["body_length_mm"]
    flange = dim["flange_mm"]
    limit = dim["flange_circle_max_mm"]
    block = Pos(0, 0, -body / 2) * Box(flange, flange, body)
    clip = Pos(0, 0, -body / 2) * Cylinder(limit / 2, body + 0.4)
    solid = block & clip
    pilot = Pos(0, 0, dim["pilot_depth_mm"] / 2) * Cylinder(dim["pilot_mm"] / 2, dim["pilot_depth_mm"])
    shaft = Pos(0, 0, dim["shaft_length_mm"] / 2) * Cylinder(dim["shaft_mm"] / 2, dim["shaft_length_mm"])
    solid = solid + pilot + shaft
    hole_depth = min(8.0, body * 0.4)
    radius = dim["bolt_circle_mm"] / 2
    hole_r = dim["hole_diameter_mm"] / 2
    for index in range(4):
        angle = math.radians(45 + 90 * index)
        hole = Pos(radius * math.cos(angle), radius * math.sin(angle), -hole_depth / 2 + 0.05) * Cylinder(
            hole_r, hole_depth + 0.1
        )
        solid = solid - hole
    return solid


def planetary_gearbox(row: Gearbox):
    from build123d import Box, Cylinder, Pos

    length = row.body_length_mm
    block = Pos(0, 0, length / 2) * Box(row.flange_mm, row.flange_mm, length)
    shaft = Pos(0, 0, length + row.output_length_mm / 2) * Cylinder(
        row.output_diameter_mm / 2, row.output_length_mm
    )
    solid = block + shaft
    pilot = Pos(0, 0, row.pilot_depth_mm / 2) * Cylinder(row.pilot_mm / 2, row.pilot_depth_mm + 0.1)
    bore = Pos(0, 0, row.input_bore_depth_mm / 2) * Cylinder(row.input_bore_mm / 2, row.input_bore_depth_mm + 0.1)
    solid = solid - pilot - bore
    hole_depth = min(8.0, length * 0.25)
    radius = row.bolt_circle_mm / 2
    for index in range(4):
        angle = math.radians(45 + 90 * index)
        hole = Pos(radius * math.cos(angle), radius * math.sin(angle), hole_depth / 2 - 0.05) * Cylinder(
            row.hole_diameter_mm / 2, hole_depth + 0.1
        )
        solid = solid - hole
    return solid


def _hex_prism(across_flats: float, height: float):
    from build123d import Pos, RegularPolygon, extrude

    # Vertex radius of a hexagon whose flats are `across_flats` apart.
    radius = across_flats / math.sqrt(3)
    face = RegularPolygon(radius=radius, side_count=6, major_radius=True)
    return Pos(0, 0, 0) * extrude(face, amount=height)


def _revolve(profile: list[tuple[float, float]]):
    from build123d import Axis, BuildLine, BuildSketch, Plane, Polyline, make_face, revolve

    points = [(float(radius), float(height)) for radius, height in profile]
    if points[0] != points[-1]:
        points = [*points, points[0]]
    if any(radius < -1e-9 for radius, _height in points):
        raise GeometryError("A revolved profile crossed the axis.")
    points = [(max(radius, 0.0), height) for radius, height in points]
    try:
        with BuildSketch(Plane.XZ) as sketch:
            with BuildLine():
                Polyline(*points)
            make_face()
        solid = revolve(sketch.sketch, axis=Axis.Z)
    except Exception as exc:
        raise GeometryError(f"Could not revolve a catalog profile: {exc}") from exc
    return solid


def _placed_sphere(pitch: float, z: float, radius: float, index: int, count: int):
    from build123d import Pos, Sphere

    angle = 2 * math.pi * index / count
    return Pos(pitch * math.cos(angle), pitch * math.sin(angle), z) * Sphere(radius)


def _tilted_roller(pitch: float, z: float, radius: float, length: float, angle_deg: float, index: int, count: int):
    from build123d import Cylinder, Pos, Rot

    spin = 360.0 * index / count
    # Tilt in the radial plane so the large end points toward the cone back face (z = 0).
    roller = Pos(pitch, 0, z) * Rot(0, angle_deg, 0) * Cylinder(radius, length)
    return Pos(0, 0, 0) * Rot(0, 0, spin) * roller


def _ring_count(pitch: float, radius: float, gap: float, low: int, high: int) -> int:
    if radius <= 0:
        return low
    count = int(math.pi * pitch / (radius * gap))
    return max(low, min(high, count))


def _fuse(parts: list):
    solid = parts[0]
    for part in parts[1:]:
        solid = solid + part
    return solid


def known_fastener_sizes() -> dict[str, list[str]]:
    return {
        "socket_head_cap_screw": [f"{row.size}-{_trim(row.pitch_mm)}" for row in CAP_SCREWS],
        "hex_nut": [f"{row.size}-{_trim(row.pitch_mm)}" for row in HEX_NUTS],
        "plain_washer": [row.size for row in PLAIN_WASHERS],
    }


def _trim(value: float) -> str:
    return f"{value:g}"
