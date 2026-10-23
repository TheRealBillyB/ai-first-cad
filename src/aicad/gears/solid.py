"""Solids for spur, helical, straight bevel, and spiral bevel gears.

A spur tooth is the transverse involute extruded along the axis. A helical
tooth carries that profile on a constant-lead helicoid: positive helix is
right-hand, so the profile turns counter-clockwise about +Z as z increases.
A bevel tooth is the back-cone involute wrapped on the cone and ruled between
the inner and outer cone distances. A spiral rotates each station by the
constant spiral angle. End faces close the back-cone profile; the outer face
is the back cone itself.

Bores, hubs, DIN 6885 keyways, ISO 14 splines, and DIN 5480 hub splines are
applied the same way for every family, including a later offset-axis family.
"""

from __future__ import annotations

import math

from build123d import Box, Cylinder, Face, Polyline, Pos, Rot, Solid

from aicad.errors import GeometryError
from aicad.gears import geometry as geom

# Chordal error of the flank stays near a hundredth of a millimetre at module 2.
_CYL_INVOLUTE = 28
_CYL_FILLET = 12
_BEVEL_INVOLUTE = 16
_BEVEL_FILLET = 8


def build_gear(spec: dict):
    """The solid for a normalized or raw gear spec."""
    clean, builder = geom.require_builder(spec)
    blank = builder(clean)
    return _with_hub_and_hole(blank, clean)


def build_cylindrical(spec: dict):
    """Spur extrusion or helical helicoid. No bore yet."""
    scalars = geom.cylindrical_scalars(spec)
    outline = geom.transverse_outline(spec, _CYL_INVOLUTE, _CYL_FILLET)
    face = _face(outline)
    width = float(spec["face_width_mm"])
    twist = float(scalars["twist_deg"])
    if abs(twist) < 1e-9:
        solid = Solid.extrude(face, (0.0, 0.0, width))
        return Pos(0.0, 0.0, -width / 2.0) * solid
    bottom = Pos(0.0, 0.0, -width / 2.0) * Rot(0.0, 0.0, -twist / 2.0) * face
    try:
        return Solid.extrude_linear_with_rotation(
            bottom,
            center=(0.0, 0.0, -width / 2.0),
            normal=(0.0, 0.0, width),
            angle=twist,
        )
    except Exception as exc:
        raise GeometryError(f"The helical tooth could not be built: {exc}") from exc


def build_bevel(spec: dict):
    """Back-cone tooth ruled along the face. A spiral uses several stations."""
    cone = geom.bevel_cones(spec)
    outer = cone["outer_cone_distance_mm"]
    inner = cone["inner_cone_distance_mm"]
    beta = cone["beta_m"]
    travel = abs(geom.spiral_offset(cone, outer, beta) - geom.spiral_offset(cone, inner, beta))
    if travel < 1e-6:
        stations = 2
    else:
        stations = max(4, int(math.ceil(math.degrees(travel) / 3.0)) + 1)
        stations = min(stations, 16)
    cone, rings = geom.bevel_profile_rings(spec, stations, _BEVEL_INVOLUTE, _BEVEL_FILLET)
    return _bevel_shell(cone, rings)


def _face(points: list[tuple[float, float]]) -> Face:
    points = _dedupe_2d(points)
    if len(points) < 3:
        raise GeometryError("The tooth outline does not have enough points to close.")
    try:
        wire = Polyline(*[(x, y) for x, y in points], close=True)
        face = Face(wire)
    except Exception as exc:
        raise GeometryError(f"The tooth outline is not a single closed face: {exc}") from exc
    if face.area < 1e-6:
        raise GeometryError("The tooth outline has no area.")
    return face


def _dedupe_2d(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    cleaned = [points[0]]
    for point in points[1:]:
        if math.hypot(point[0] - cleaned[-1][0], point[1] - cleaned[-1][1]) > 1e-7:
            cleaned.append(point)
    if math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) < 1e-6:
        cleaned.pop()
    return cleaned


def _bevel_shell(cone: dict, rings: list[list[tuple[float, float, float]]]):
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_Sewing,
    )
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.ShapeFix import ShapeFix_Solid
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt

    delta = cone["pitch_angle_rad"]
    faces = []

    def add(a, b, c) -> None:
        area = _triangle_area(a, b, c)
        if area < 1e-12:
            return
        polygon = BRepBuilderAPI_MakePolygon(gp_Pnt(*a), gp_Pnt(*b), gp_Pnt(*c), True)
        made = BRepBuilderAPI_MakeFace(polygon.Wire())
        if made.IsDone():
            faces.append(made.Face())

    outer_ring = rings[-1]
    inner_ring = rings[0]
    outer_apex = (0.0, 0.0, cone["outer_cone_distance_mm"] / math.cos(delta))
    count = len(outer_ring)
    for index in range(count):
        nxt = (index + 1) % count
        add(*_wind(outer_apex, outer_ring[index], outer_ring[nxt], sign_z=1.0))
        add(*_wind(_toe_center(inner_ring), inner_ring[index], inner_ring[nxt], sign_z=-1.0))

    for band in range(len(rings) - 1):
        inner = rings[band]
        outer = rings[band + 1]
        for index in range(count):
            nxt = (index + 1) % count
            a = outer[index]
            b = outer[nxt]
            outward = (b[1] - a[1], -(b[0] - a[0]))
            add(*_wind_xy(a, b, inner[nxt], outward))
            add(*_wind_xy(a, inner[nxt], inner[index], outward))

    if len(faces) < 4:
        raise GeometryError("The bevel tooth did not produce a closed surface.")
    sewing = BRepBuilderAPI_Sewing(1e-4)
    for face in faces:
        sewing.Add(face)
    sewing.Perform()
    free = sewing.NbFreeEdges()
    if free:
        raise GeometryError(
            f"The bevel tooth shell has {free} open edges, so it is not a closed solid."
        )
    sewed = sewing.SewedShape()
    explorer = TopExp_Explorer(sewed, TopAbs_SHELL)
    if not explorer.More():
        raise GeometryError("The bevel tooth shell did not sew into a shell.")
    shell = TopoDS.Shell(explorer.Current())
    fixer = ShapeFix_Solid()
    solid_shape = fixer.SolidFromShell(shell)
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(solid_shape, props)
    if props.Mass() < 0:
        solid_shape.Reverse()
    wrapped = Solid(solid_shape)
    if wrapped.volume <= 0 or not BRepCheck_Analyzer(wrapped.wrapped).IsValid():
        raise GeometryError("The bevel solid is not a valid closed body.")
    return wrapped


def _toe_center(ring: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """A point on the toe side of the inner profile, used only to close that end."""
    count = float(len(ring))
    cx = sum(point[0] for point in ring) / count
    cy = sum(point[1] for point in ring) / count
    cz = sum(point[2] for point in ring) / count
    # Pull toward the pitch apex so the cap sits outside the face, on the toe.
    return (cx * 0.95, cy * 0.95, cz * 0.95)


def _triangle_area(a, b, c) -> float:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def _normal(a, b, c) -> tuple[float, float, float]:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    return (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)


def _wind(a, b, c, sign_z: float):
    if _normal(a, b, c)[2] * sign_z < 0:
        return a, c, b
    return a, b, c


def _wind_xy(a, b, c, outward: tuple[float, float]):
    normal = _normal(a, b, c)
    if normal[0] * outward[0] + normal[1] * outward[1] < 0:
        return a, c, b
    return a, b, c


def _with_hub_and_hole(blank, spec: dict):
    root = _root_radius(spec)
    hub = spec.get("hub")
    if hub is not None:
        _check_radius("Hub diameter", hub["diameter_mm"] / 2.0, root, "the root")
        blank = _fuse(blank, _hub_solid(blank, hub))
    hole = _hole_radius(spec)
    if hole is not None:
        _check_radius("The hole", hole, root, "the root")
        if hub is not None and hole >= hub["diameter_mm"] / 2.0 - 1e-9:
            raise GeometryError("The hole must be smaller than the hub diameter.")
        tool = _hole_tool(blank, spec, hole)
        keyway = spec.get("keyway")
        if keyway is not None:
            bottom = hole + keyway["depth_mm"]
            _check_radius("The keyway", bottom, root, "the root")
            tool = _fuse(tool, _keyway_solid(blank, hole, keyway))
        blank = _cut(blank, tool)
    return _one(blank)


def _root_radius(spec: dict) -> float:
    if spec["type"] in geom.CYLINDRICAL:
        return geom.cylindrical_scalars(spec)["rf"]
    cone = geom.bevel_cones(spec)
    scale = cone["inner_cone_distance_mm"] / cone["outer_cone_distance_mm"]
    return cone["scalars"]["rf"] * scale * math.cos(cone["pitch_angle_rad"])


def _hole_radius(spec: dict) -> float | None:
    spline = spec.get("spline")
    bore = spec.get("bore_mm")
    if spline is None:
        return None if bore is None else bore / 2.0
    if spline["standard"].startswith("ISO 14"):
        radius = float(spline["minor_mm"]) / 2.0
    else:
        radius = float(spline["hub_tip_diameter_mm"]) / 2.0
    if bore is not None and abs(bore - 2.0 * radius) > 0.05:
        raise GeometryError(
            "bore_mm does not match the spline hole. "
            "An ISO 14 hole is the minor diameter. A DIN 5480 hub hole is the hub tip diameter."
        )
    return radius


def _check_radius(label: str, radius: float, limit: float, what: str) -> None:
    if radius >= limit - 0.05:
        raise GeometryError(
            f"{label} ({radius * 2.0:.3f} mm) must stay at least 0.05 mm inside {what} "
            f"({limit * 2.0:.3f} mm)."
        )


def _span(solid) -> tuple[float, float]:
    box = solid.bounding_box()
    return float(box.min.Z) - 1.0, float(box.max.Z) + 1.0


def _hub_solid(blank, hub: dict):
    radius = hub["diameter_mm"] / 2.0
    length = hub["length_mm"]
    side = hub["side"]
    box = blank.bounding_box()
    low = float(box.min.Z)
    high = float(box.max.Z)
    overlap = min(0.4, length * 0.5)
    parts = []
    if side in {"front", "heel", "both"}:
        parts.append(Pos(0.0, 0.0, high + length / 2.0 - overlap) * Cylinder(radius, length + overlap))
    if side in {"back", "toe", "both"}:
        parts.append(Pos(0.0, 0.0, low - length / 2.0 + overlap) * Cylinder(radius, length + overlap))
    if not parts:
        raise GeometryError("hub.side must be front, back, both, heel, or toe.")
    solid = parts[0]
    for extra in parts[1:]:
        solid = _fuse(solid, extra)
    return solid


def _keyway_solid(blank, hole_radius: float, keyway: dict):
    low, high = _span(blank)
    height = high - low
    inner = max(0.0, hole_radius - 0.2)
    outer = hole_radius + keyway["depth_mm"]
    length = outer - inner
    center = inner + length / 2.0
    return Pos(center, 0.0, (low + high) / 2.0) * Box(length, keyway["width_mm"], height)


def _hole_tool(blank, spec: dict, radius: float):
    spline = spec.get("spline")
    low, high = _span(blank)
    height = high - low
    center_z = (low + high) / 2.0
    if spline is None:
        return Pos(0.0, 0.0, center_z) * Cylinder(radius, height)
    if spline["standard"].startswith("ISO 14"):
        outline = _iso14_outline(spline)
    else:
        outline = _din5480_outline(spline)
    face = _face(outline)
    solid = Solid.extrude(face, (0.0, 0.0, height))
    return Pos(0.0, 0.0, low) * solid


def _iso14_outline(spline: dict) -> list[tuple[float, float]]:
    minor = float(spline["minor_mm"]) / 2.0
    major = float(spline["major_mm"]) / 2.0
    half_width = float(spline["width_mm"]) / 2.0
    count = int(spline["count"])
    if half_width >= minor:
        raise GeometryError("The ISO 14 groove is wider than the minor diameter.")
    points: list[tuple[float, float]] = []
    x_in = math.sqrt(minor * minor - half_width * half_width)
    x_out = math.sqrt(max(major * major - half_width * half_width, 0.0))
    inner_offset = math.atan2(half_width, x_in)
    outer_offset = math.atan2(half_width, x_out)
    # Angles stay unwrapped so the closing arc does not take the long way around.
    first_entry = -inner_offset
    previous_exit = 0.0
    for index in range(count):
        center = 2.0 * math.pi * index / count
        entry = center - inner_offset
        exit_angle = center + inner_offset
        if index == 0:
            first_entry = entry
        else:
            points.extend(_arc(minor, previous_exit, entry, 4)[1:])
        points.append(_rotate_angle(minor, entry))
        points.append(_rotate_angle(major, center - outer_offset))
        points.extend(_arc(major, center - outer_offset, center + outer_offset, 4)[1:])
        points.append(_rotate_angle(minor, exit_angle))
        previous_exit = exit_angle
    points.extend(_arc(minor, previous_exit, first_entry + 2.0 * math.pi, 4)[1:])
    return points


def _din5480_outline(spline: dict) -> list[tuple[float, float]]:
    """Hub tooth space. The flank is the external involute of tooth thickness s1."""
    module = float(spline["module_mm"])
    teeth = int(spline["teeth"])
    shift = float(spline["profile_shift"])
    alpha = math.radians(30.0)
    pitch = module * teeth / 2.0
    base = pitch * math.cos(alpha)
    thickness = module * (math.pi / 2.0 + 2.0 * shift * math.tan(alpha))
    inner = float(spline["hub_tip_diameter_mm"]) / 2.0
    outer = float(spline["hub_root_diameter_mm"]) / 2.0
    if inner < base * 1.001:
        raise GeometryError("The DIN 5480 hub tip is inside the base circle.")
    if outer <= inner:
        raise GeometryError("The DIN 5480 hub root must be outside the hub tip.")

    def phi(radius: float) -> float:
        return geom._half_angle(radius, pitch, base, alpha, thickness)

    if phi(inner) <= 0 or phi(outer) <= 0:
        raise GeometryError("The DIN 5480 tooth space closes. Check the reference diameter.")
    if 2.0 * phi(inner) >= 2.0 * math.pi / teeth:
        raise GeometryError("DIN 5480 spaces overlap at the hub tip.")
    samples = 24
    points: list[tuple[float, float]] = []
    step = 2.0 * math.pi / teeth
    first = 0.0
    previous = 0.0
    for tooth in range(teeth):
        center = tooth * step
        radii = [inner + (outer - inner) * index / (samples - 1) for index in range(samples)]
        left = [(radius, center - phi(radius)) for radius in radii]
        right = [(radius, center + phi(radius)) for radius in radii]
        if tooth == 0:
            first = left[0][1]
        else:
            points.extend(_arc(inner, previous, left[0][1], 3)[1:])
        points.extend(_rotate_angle(radius, angle) for radius, angle in left)
        points.extend(_arc(outer, left[-1][1], right[-1][1], 4)[1:])
        for radius, angle in reversed(right[:-1]):
            points.append(_rotate_angle(radius, angle))
        previous = right[0][1]
    points.extend(_arc(inner, previous, first + 2.0 * math.pi, 3)[1:])
    return points


def _rotate_angle(radius: float, angle: float) -> tuple[float, float]:
    return (radius * math.cos(angle), radius * math.sin(angle))


def _arc(radius: float, start: float, end: float, count: int) -> list[tuple[float, float]]:
    # Walk counter-clockwise. If the end is behind the start, add a full turn.
    while end < start - 1e-12:
        end += 2.0 * math.pi
    count = max(count, 2)
    return [
        (radius * math.cos(start + (end - start) * index / (count - 1)),
         radius * math.sin(start + (end - start) * index / (count - 1)))
        for index in range(count)
    ]


def _fuse(left, right):
    try:
        return left + right
    except Exception as exc:
        raise GeometryError(f"Could not join the gear and its hub or hole: {exc}") from exc


def _cut(blank, tool):
    try:
        return blank - tool
    except Exception as exc:
        raise GeometryError(f"Could not cut the bore, keyway, or spline: {exc}") from exc


def _one(shape):
    solids = list(shape.solids())
    if len(solids) != 1 or solids[0].volume <= 0:
        raise GeometryError("The gear did not come out as one closed solid.")
    return solids[0]
