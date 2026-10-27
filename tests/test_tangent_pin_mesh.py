"""A pin tangent to a flat face still exports as a mesh."""

import math
import struct
import zipfile
from pathlib import Path

from build123d import (
    Box,
    BuildLine,
    BuildSketch,
    Cylinder,
    GeomType,
    Line,
    Plane,
    Pos,
    Spline,
    export_step,
    fillet,
    import_step,
    loft,
    make_face,
)

from aicad.kernel import Shape, export_3mf_file, export_stl_file


def _section(x: float, half_width: float, height: float):
    points = []
    for index in range(21):
        angle = math.pi * index / 20
        cosine = math.cos(angle)
        sine = math.sin(angle)
        wide = math.copysign(abs(cosine) ** 0.55, cosine)
        points.append((half_width * wide, height * (abs(sine) ** 0.55)))
    with BuildSketch(Plane.YZ.offset(x)) as sketch:
        with BuildLine():
            Spline(*points, tangents=((0, 1), (0, -1)), tangent_scalars=(1, 1))
            Line(points[-1], points[0])
        make_face()
    return sketch.sketch.face()


def _slotted_bar_with_tangent_pin(directory: Path) -> Shape:
    """A lofted bar, a slot through it, and a cylinder tangent to z = 0."""
    body = loft([_section(x, 5.2, 5.4) for x in (0, 6, 12, 18, 24)], ruled=True)
    hinge_x = 12.0
    slot_half = 3.2
    wall = hinge_x - 4.5
    end = hinge_x + 2.6
    slot = Pos((wall + end) / 2, 0, 10) * Box(end - wall, 2 * slot_half, 36)
    pin = Plane(origin=(hinge_x, 0, 2), z_dir=(0, 1, 0)) * Cylinder(2.0, 8.8)
    solid = body - slot + pin
    lips = []
    for edge in solid.edges():
        if edge.geom_type == GeomType.CIRCLE:
            continue
        center = edge.center()
        if center.Z < 1.0:
            continue
        on_side = abs(abs(center.Y) - slot_half) < 0.45 and wall - 0.4 < center.X < end
        on_end = abs(center.X - wall) < 0.45 and abs(center.Y) < slot_half + 0.3
        if on_side or on_end:
            lips.append(edge)
    solid = fillet(lips, radius=0.45)
    path = directory / "bar.step"
    export_step(solid, str(path), write_pcurves=False)
    loaded = import_step(str(path))
    return Shape(loaded)


def test_a_tangent_pin_on_a_slotted_bar_exports(tmp_path: Path):
    shape = _slotted_bar_with_tangent_pin(tmp_path)
    short = [edge for edge in shape.impl.edges() if edge.length < 1e-3]
    assert short, "the imported solid should still carry the hairline edges"

    stl = tmp_path / "bar.stl"
    export_stl_file(shape, stl)
    payload = stl.read_bytes()
    count = struct.unpack_from("<I", payload, 80)[0]
    assert count > 200

    mesh = tmp_path / "bar.3mf"
    export_3mf_file([("bar", shape)], mesh)
    with zipfile.ZipFile(mesh) as archive:
        model = archive.read("3D/3dmodel.model")
    assert b"<triangle" in model
    assert model.count(b"<vertex") > 50
