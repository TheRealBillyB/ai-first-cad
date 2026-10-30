"""Solids for spur, helical, straight bevel, spiral bevel, and hypoid gears.

A spur tooth is the transverse involute extruded along the axis. A helical
tooth carries that profile on a constant-lead helicoid: positive helix is
right-hand, so the profile turns counter-clockwise about +Z as z increases.
A bevel tooth is the back-cone involute wrapped on the cone and ruled between
the inner and outer cone distances. A spiral rotates each station by the
constant spiral angle. End faces close the back-cone profile; the outer face
is the back cone itself. A hypoid ring tooth is the face-mill blade, lofted between stations along
the face. The pinion is the tip-cone blank left after that tooth is rolled
about the offset shafts at the tooth ratio and cut out.

Bores, hubs, DIN 6885 keyways, ISO 14 splines, and DIN 5480 hub splines are
applied the same way for every family, including a later offset-axis family.
"""

from __future__ import annotations

import math

from aicad.paths import prepare_geometry_import

prepare_geometry_import()
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


def build_hypoid(spec: dict):
    """Ring from the face-mill tooth, or the pinion left after that tooth is rolled."""
    if spec["role"] == "pinion":
        return _generated_pinion(spec)
    return _generated_ring(spec)


def _generated_ring(spec: dict):
    """Root cone plus one lofted tooth copied around the ring."""
    from aicad.gears.hypoid import pitch_design, ring_core_rings, ring_tooth_rings

    design = pitch_design(spec)
    tooth = _lofted_tooth(ring_tooth_rings(design))
    box = tooth.bounding_box()
    core = _lofted_shell(ring_core_rings(design, float(box.min.Z) - 0.4, float(box.max.Z) + 0.4))
    step = 2.0 * math.pi / design["z2"]
    solid = core
    for index in range(design["z2"]):
        piece = tooth if index == 0 else _moved(tooth, _z_rotation(index * step))
        solid = _fuse(solid, piece)
    return _one(solid)


def _generated_pinion(spec: dict):
    """Pinion blank with the face-mill tooth rolled out, then copied around.

    One ring tooth is swept through the angles where it meets the pinion.
    That cuts every space that tooth forms. Rotating the removed metal by
    one pinion pitch repeats the space for the other teeth. Cutting the
    poses one at a time stays cheap because each tool is small.
    """
    from aicad.gears.hypoid import pinion_blank_rings, pinion_trsf, pitch_design, roll_angles, tool_tooth_rings

    design = pitch_design(spec)
    blank = _lofted_shell(pinion_blank_rings(design))
    tooth = _lofted_tooth(tool_tooth_rings(design))
    # The index pose is the pitch point. If it cannot be cut, the roll would
    # only fail later, so refuse before cutting the rest of the tooth.
    indexed = _rolled_pose(blank, _moved(tooth, pinion_trsf(design["phi_star"], design)))
    if blank.volume - indexed.volume < 1.0:
        raise GeometryError(
            "This offset_mm, shaft_angle_deg, ring_diameter_mm, and spiral_angle_deg "
            "do not leave a pinion tooth. Change one of those four."
        )
    solid = blank
    for phi in roll_angles(design):
        # A pose past a right angle can leave a bad edge. Heal that pose, or
        # leave it out, and keep going. The pitch point was already accepted.
        solid = _rolled_pose(solid, _moved(tooth, pinion_trsf(phi, design)))
    chip = _groove(blank, solid)
    removed = blank.volume - solid.volume
    if chip.volume < 1.0 or abs(chip.volume - removed) > max(5.0, 0.35 * max(removed, 1.0)):
        raise GeometryError(
            "This offset_mm, shaft_angle_deg, ring_diameter_mm, and spiral_angle_deg "
            "do not leave a pinion tooth. Change one of those four."
        )
    step = 2.0 * math.pi / design["z1"]
    chips = chip
    for index in range(1, design["z1"]):
        chips = _fuse(chips, _moved(chip, _z_rotation(index * step)))
    solid = _cut(blank, chips)
    if solid.volume < blank.volume * 0.2 or solid.volume > blank.volume * 0.98:
        raise GeometryError(
            "The face-mill roll did not leave a toothed pinion. Use more pinion teeth or a shorter face."
        )
    return _one(solid)


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


def _lofted_shell(rings: list[list[tuple[float, float, float]]]):
    """Closed solid from coaxial outlines. The first station is one end of the face."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    count = len(rings[0])
    if count < 3 or any(len(ring) != count for ring in rings):
        raise GeometryError("The hypoid stations do not share an outline.")

    def mean_z(ring) -> float:
        return sum(point[2] for point in ring) / len(ring)

    z0, z1 = mean_z(rings[0]), mean_z(rings[-1])
    span = max(abs(z1 - z0), 0.5)
    # Outward along Z is away from the other end.
    sign0 = -1.0 if z1 >= z0 else 1.0
    cap0 = (0.0, 0.0, z0 + sign0 * 0.04 * span)
    cap1 = (0.0, 0.0, z1 - sign0 * 0.04 * span)
    faces = []

    def add(a, b, c) -> None:
        if _triangle_area(a, b, c) < 1e-12:
            return
        polygon = BRepBuilderAPI_MakePolygon(gp_Pnt(*a), gp_Pnt(*b), gp_Pnt(*c), True)
        made = BRepBuilderAPI_MakeFace(polygon.Wire())
        if made.IsDone():
            faces.append(made.Face())

    for index in range(count):
        nxt = (index + 1) % count
        add(*_wind(cap0, rings[0][index], rings[0][nxt], sign_z=sign0))
        add(*_wind(cap1, rings[-1][index], rings[-1][nxt], sign_z=-sign0))
    for band in range(len(rings) - 1):
        inner = rings[band]
        outer = rings[band + 1]
        for index in range(count):
            nxt = (index + 1) % count
            edge = outer[index]
            nxt_edge = outer[nxt]
            outward = (nxt_edge[1] - edge[1], -(nxt_edge[0] - edge[0]))
            add(*_wind_xy(edge, nxt_edge, inner[nxt], outward))
            add(*_wind_xy(edge, inner[nxt], inner[index], outward))
    return _sew_faces(faces, "hypoid tooth")


def _lofted_tooth(rings: list[list[tuple[float, float, float]]]):
    """One closed tooth. The ends are the outlines themselves, not a cap on the axis."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    count = len(rings[0])
    if count < 3 or any(len(ring) != count for ring in rings):
        raise GeometryError("The hypoid tooth stations do not share an outline.")

    def centroid(ring):
        scale = 1.0 / len(ring)
        return (
            sum(point[0] for point in ring) * scale,
            sum(point[1] for point in ring) * scale,
            sum(point[2] for point in ring) * scale,
        )

    start, end = centroid(rings[0]), centroid(rings[-1])
    body = ((start[0] + end[0]) * 0.5, (start[1] + end[1]) * 0.5, (start[2] + end[2]) * 0.5)
    faces = []

    def add(a, b, c) -> None:
        if _triangle_area(a, b, c) < 1e-12:
            return
        polygon = BRepBuilderAPI_MakePolygon(gp_Pnt(*a), gp_Pnt(*b), gp_Pnt(*c), True)
        made = BRepBuilderAPI_MakeFace(polygon.Wire())
        if made.IsDone():
            faces.append(made.Face())

    def add_out(a, b, c, inside) -> None:
        normal = _normal(a, b, c)
        middle = ((a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0, (a[2] + b[2] + c[2]) / 3.0)
        outward = normal[0] * (middle[0] - inside[0]) + normal[1] * (middle[1] - inside[1]) + normal[2] * (middle[2] - inside[2])
        if outward < 0.0:
            add(a, c, b)
        else:
            add(a, b, c)

    for index in range(count):
        nxt = (index + 1) % count
        add_out(start, rings[0][index], rings[0][nxt], end)
        add_out(end, rings[-1][index], rings[-1][nxt], start)
    for band in range(len(rings) - 1):
        inner = rings[band]
        outer = rings[band + 1]
        for index in range(count):
            nxt = (index + 1) % count
            add_out(outer[index], outer[nxt], inner[nxt], body)
            add_out(outer[index], inner[nxt], inner[index], body)
    return _sew_faces(faces, "hypoid cutting tooth")


def _moved(solid, transform):
    from build123d import Location

    return solid.moved(Location(transform))


def _z_rotation(angle: float):
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    transform = gp_Trsf()
    transform.SetRotation(gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)), angle)
    return transform


def _sew_faces(faces, label: str):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.ShapeFix import ShapeFix_Solid
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if len(faces) < 4:
        raise GeometryError(f"The {label} did not produce a closed surface.")
    sewing = BRepBuilderAPI_Sewing(1e-4)
    for face in faces:
        sewing.Add(face)
    sewing.Perform()
    free = sewing.NbFreeEdges()
    if free:
        raise GeometryError(f"The {label} shell has {free} open edges, so it is not a closed solid.")
    sewed = sewing.SewedShape()
    explorer = TopExp_Explorer(sewed, TopAbs_SHELL)
    if not explorer.More():
        raise GeometryError(f"The {label} shell did not sew into a shell.")
    shell = TopoDS.Shell(explorer.Current())
    fixer = ShapeFix_Solid()
    solid_shape = fixer.SolidFromShell(shell)
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(solid_shape, props)
    if props.Mass() < 0:
        solid_shape.Reverse()
    wrapped = Solid(solid_shape)
    if wrapped.volume <= 0 or not BRepCheck_Analyzer(wrapped.wrapped).IsValid():
        raise GeometryError(f"The {label} solid is not a valid closed body.")
    return wrapped


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
    if spec["type"] == "hypoid":
        from aicad.gears.hypoid import root_radius

        return root_radius(spec)
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


# A boolean sometimes returns an extra solid with no volume. It is not a second
# body, and dropping the pose because of it leaves a ridge in the tooth.
_DUST_MM3 = 1.0e-4


def _real_solids(shape):
    """Solids with volume. A sliver next to the main body is a boolean leftover."""
    pieces = [piece for piece in shape.solids() if piece.volume > _DUST_MM3]
    if len(pieces) <= 1:
        return pieces
    largest = max(piece.volume for piece in pieces)
    floor = max(_DUST_MM3, largest * 1.0e-6)
    return [piece for piece in pieces if piece.volume > floor]


def _rolled_pose(current, tool):
    """One face-mill pose cut out of the pinion blank.

    The exact cut is kept when it is one valid solid. Past a right angle the
    same pose can leave a bad edge. A small fuzzy tolerance is then used only
    when it keeps that pocket. A pose that still will not cut is left out, so
    one graze cannot spoil the rest of the roll.
    """
    base = current.volume
    exact = None
    try:
        exact = current - tool
    except Exception:
        exact = None
    if exact is not None and _roll_ok(exact, base):
        return _real_solids(exact)[0]
    exact_volume = _solid_volume(exact)
    for fuzzy in (0.002, 0.005, 0.01, 0.02):
        healed = _fuzzy_cut(current, tool, fuzzy)
        if not _keeps_pocket(healed, base, exact_volume):
            continue
        return healed
    return current


def _keeps_pocket(healed, base: float, exact_volume: float | None) -> bool:
    """A healed pose is kept only when it matches the exact pocket."""
    if healed is None or not _roll_ok(healed, base):
        return False
    if exact_volume is None:
        return base - healed.volume >= 0.5
    return abs(healed.volume - exact_volume) <= max(1.5, 0.25 * abs(base - exact_volume))


def _groove(blank, rolled):
    """The metal the roll took out of the blank.

    The difference is that groove. When the rolled solid sits a little off the
    blank, the same difference also returns the remaining blank, and the groove
    is the smaller piece.
    """
    difference = _cut(blank, rolled)
    pieces = [piece for piece in difference.solids() if piece.volume >= 1.0]
    if len(pieces) <= 1:
        return pieces[0] if pieces else difference
    expected = blank.volume - rolled.volume
    closest = min(pieces, key=lambda piece: abs(piece.volume - expected))
    if abs(closest.volume - expected) > max(5.0, 0.35 * max(expected, 1.0)):
        return difference
    return closest


def _roll_ok(result, previous_volume: float) -> bool:
    """One solid that lost metal, or none, and did not collapse."""
    solids = _real_solids(result)
    if len(solids) != 1:
        return False
    volume = solids[0].volume
    # The allowance scales with the blank. A fixed tenth of a cubic millimetre
    # rejects a valid cut once the pinion is large.
    allowance = max(0.2, previous_volume * 1.0e-6)
    if volume > previous_volume + allowance or volume < previous_volume * 0.55:
        return False
    if not solids[0].is_manifold:
        return False
    from OCP.BRepCheck import BRepCheck_Analyzer

    return BRepCheck_Analyzer(solids[0].wrapped).IsValid()


def _solid_volume(result) -> float | None:
    if result is None:
        return None
    solids = _real_solids(result)
    if len(solids) != 1:
        return None
    return solids[0].volume


def _fuzzy_cut(current, tool, fuzzy: float):
    """Boolean cut with a tolerance, or None when it is not one solid."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.collections import List_TopoDS_Shape

    arguments = List_TopoDS_Shape()
    arguments.Append(current.wrapped)
    tools = List_TopoDS_Shape()
    tools.Append(tool.wrapped)
    operation = BRepAlgoAPI_Cut()
    operation.SetArguments(arguments)
    operation.SetTools(tools)
    operation.SetFuzzyValue(fuzzy)
    operation.Build()
    if not operation.IsDone():
        return None
    found = []
    explorer = TopExp_Explorer(operation.Shape(), TopAbs_SOLID)
    while explorer.More():
        found.append(Solid(TopoDS.Solid(explorer.Current())))
        explorer.Next()
    found = [piece for piece in found if piece.volume > _DUST_MM3]
    if not found:
        return None
    largest = max(piece.volume for piece in found)
    floor = max(_DUST_MM3, largest * 1.0e-6)
    found = [piece for piece in found if piece.volume > floor]
    if len(found) != 1:
        return None
    return found[0]


def _one(shape):
    solids = _real_solids(shape)
    if len(solids) != 1 or solids[0].volume <= 0:
        raise GeometryError("The gear did not come out as one closed solid.")
    solid = solids[0]
    if not solid.is_manifold:
        solid = _heal_solid(solid)
    if solid.volume <= 0 or not solid.is_manifold:
        raise GeometryError("The gear did not come out as one closed solid.")
    return solid


def _heal_solid(solid):
    """Close a single solid whose shell did not sew, when the kernel can."""
    from OCP.ShapeFix import ShapeFix_Solid

    fixer = ShapeFix_Solid(solid.wrapped)
    fixer.Perform()
    healed = Solid(fixer.Solid())
    if healed.volume > 0 and healed.is_manifold:
        return healed
    return solid
