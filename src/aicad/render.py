"""Headless renders of a solid from the standard views.

The pictures are drawn with a small software rasterizer (NumPy and Pillow).
Nothing here needs a GPU, a display, or a window system, so it runs on a server.
build123d stays behind :mod:`aicad.kernel`; this module only sees triangles.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from aicad.kernel import TriangleMesh
from aicad.models import Measurement, RenderedView, SketchReport, WallReport
from aicad.paths import publish_file


def _save_png(path: Path, image: Image.Image, *, optimize: bool = False) -> None:
    """Replace ``path`` with a PNG. A link at that name is not followed."""

    def write(temporary: Path) -> None:
        if optimize:
            image.save(temporary, format="PNG", optimize=True)
        else:
            image.save(temporary, format="PNG")

    publish_file(path, write)

# Viewport of the 3D picture, before the title strip. SSAA renders larger and
# downsamples, which takes the stair-steps off silhouettes.
_VIEW_W = 600
_VIEW_H = 450
_SSAA = 2
_HEADER = 36
_FOOTER = 28

_PAPER = (231, 226, 216)
_INK = (32, 36, 40)
_MUTED = (82, 78, 70)
_EDGE = (28, 32, 36)
_METAL = np.array([176.0, 186.0, 196.0])

# Camera sits on `eye` looking toward the origin. `up` is the screen's up hint.
# Z is up. The label says which way the camera is looking so a picture is unambiguous.
_VIEWS: tuple[tuple[str, tuple[float, float, float], tuple[float, float, float], str], ...] = (
    ("front", (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), "front  ·  looking +Y"),
    ("back", (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), "back  ·  looking −Y"),
    ("left", (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), "left  ·  looking +X"),
    ("right", (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), "right  ·  looking −X"),
    ("top", (0.0, 0.0, 1.0), (0.0, 1.0, 0.0), "top  ·  looking −Z"),
    ("bottom", (0.0, 0.0, -1.0), (0.0, 1.0, 0.0), "bottom  ·  looking +Z"),
    ("iso", (1.0, -1.0, 0.85), (0.0, 0.0, 1.0), "isometric"),
)

_CONTACT_ORDER = ("front", "right", "top", "iso", "back", "left", "bottom")


def render_views(
    mesh: TriangleMesh,
    out_dir: Path,
    stem: str,
    measurement: Measurement,
    wall: WallReport | None = None,
) -> list[RenderedView]:
    """Write the standard views and a contact sheet. Returns one record per file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    bases = {name: _camera_basis(eye, up) for name, eye, up, _label in _VIEWS}
    scale, _span = _shared_scale(mesh.vertices, bases)
    panels: dict[str, Image.Image] = {}
    records: list[RenderedView] = []

    for name, _eye, _up, label in _VIEWS:
        image = _compose_view(
            mesh,
            bases[name],
            scale,
            title=label,
            footer=_footer(measurement),
        )
        path = out_dir / f"{stem}_{name}.png"
        _save_png(path, image, optimize=True)
        panels[name] = image
        records.append(
            RenderedView(name=name, path=str(path.resolve()), width=image.width, height=image.height)
        )

    contact = _contact_sheet(stem, panels, measurement, wall)
    contact_path = out_dir / f"{stem}_contact.png"
    _save_png(contact_path, contact, optimize=True)
    records.append(
        RenderedView(
            name="contact",
            path=str(contact_path.resolve()),
            width=contact.width,
            height=contact.height,
        )
    )
    return records


def _camera_basis(
    eye: tuple[float, float, float], up: tuple[float, float, float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    z_cam = _unit(np.array(eye, dtype=np.float64))
    x_cam = np.cross(np.array(up, dtype=np.float64), z_cam)
    x_len = float(np.linalg.norm(x_cam))
    if x_len < 1e-9:
        raise RuntimeError("Camera up vector is parallel to the view direction.")
    x_cam /= x_len
    y_cam = np.cross(z_cam, x_cam)
    return x_cam, y_cam, z_cam


def _shared_scale(
    vertices: np.ndarray, bases: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]
) -> tuple[float, float]:
    """One millimeters-per-pixel scale for every view, chosen so nothing is clipped."""
    span = 1e-6
    for x_cam, y_cam, _z_cam in bases.values():
        x = vertices @ x_cam
        y = vertices @ y_cam
        span = max(span, float(x.max() - x.min()), float(y.max() - y.min()))
    usable = min(_VIEW_W, _VIEW_H) * (1.0 - 0.16) * _SSAA
    return usable / span, span


def _compose_view(
    mesh: TriangleMesh,
    basis: tuple[np.ndarray, np.ndarray, np.ndarray],
    scale: float,
    title: str,
    footer: str,
) -> Image.Image:
    buffer_w = _VIEW_W * _SSAA
    buffer_h = _VIEW_H * _SSAA
    color, depth = _rasterize(mesh, basis, scale, buffer_w, buffer_h)
    view = Image.fromarray(color, mode="RGB").resize((_VIEW_W, _VIEW_H), Image.Resampling.BOX)
    canvas = Image.new("RGB", (_VIEW_W, _HEADER + _VIEW_H + _FOOTER), _PAPER)
    canvas.paste(view, (0, _HEADER))
    draw = ImageDraw.Draw(canvas)
    title_font = _font(15)
    small_font = _font(12)
    draw.text((12, 8), title, fill=_INK, font=title_font)
    draw.text((12, _HEADER + _VIEW_H + 6), footer, fill=_MUTED, font=small_font)
    _draw_triad(draw, basis, small_font)
    _draw_scale_bar(draw, scale, small_font)
    return canvas


def _rasterize(
    mesh: TriangleMesh,
    basis: tuple[np.ndarray, np.ndarray, np.ndarray],
    scale: float,
    width: int,
    height: int,
    background: tuple[int, int, int] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    x_cam, y_cam, z_cam = basis
    center = _projection_center(mesh.vertices, x_cam, y_cam)
    screen = _project(mesh.vertices, x_cam, y_cam, z_cam, scale, width, height, center)
    light_key = _unit(np.array([0.35, -0.55, 0.85]))
    light_fill = _unit(np.array([-0.7, 0.25, 0.35]))
    image = np.empty((height, width, 3), dtype=np.uint8)
    image[:] = _PAPER if background is None else background
    depth = np.full((height, width), -1e30, dtype=np.float64)

    triangles = mesh.triangles
    normals = mesh.normals
    for tri in triangles:
        i0, i1, i2 = int(tri[0]), int(tri[1]), int(tri[2])
        p0, p1, p2 = screen[i0], screen[i1], screen[i2]
        shades = (
            _shade(normals[i0], light_key, light_fill),
            _shade(normals[i1], light_key, light_fill),
            _shade(normals[i2], light_key, light_fill),
        )
        if mesh.colors is None:
            _paint_triangle(image, depth, p0, p1, p2, shades)
        else:
            colored = (
                mesh.colors[i0] * shades[0],
                mesh.colors[i1] * shades[1],
                mesh.colors[i2] * shades[2],
            )
            _paint_triangle_color(image, depth, p0, p1, p2, colored)

    # Colored assemblies used to return here, so a fabricated joint lost its edges.
    bias = 0.85 if mesh.colors is not None else 0.35
    for polyline in mesh.edges:
        projected = _project(polyline, x_cam, y_cam, z_cam, scale, width, height, center)
        for start, end in zip(projected[:-1], projected[1:]):
            _draw_visible_segment(image, depth, start, end, bias)
    return image, depth


def _projection_center(
    vertices: np.ndarray, x_cam: np.ndarray, y_cam: np.ndarray
) -> tuple[float, float]:
    x = vertices @ x_cam
    y = vertices @ y_cam
    return (float(x.min() + x.max()) / 2.0, float(y.min() + y.max()) / 2.0)


def _project(
    points: np.ndarray,
    x_cam: np.ndarray,
    y_cam: np.ndarray,
    z_cam: np.ndarray,
    scale: float,
    width: int,
    height: int,
    center: tuple[float, float],
) -> np.ndarray:
    x = points @ x_cam
    y = points @ y_cam
    z = points @ z_cam
    # `center` comes from the whole solid, so edges land on the same picture as faces.
    sx = (x - center[0]) * scale + width / 2.0
    sy = height / 2.0 - (y - center[1]) * scale
    return np.column_stack((sx, sy, z))


def _shade(normal: np.ndarray, key: np.ndarray, fill: np.ndarray) -> float:
    length = float(np.linalg.norm(normal))
    if length < 1e-9:
        return 0.55
    direction = normal / length
    lit = 0.78 * abs(float(direction @ key)) + 0.22 * abs(float(direction @ fill))
    return 0.20 + 0.80 * lit


def _paint_triangle(
    image: np.ndarray,
    depth: np.ndarray,
    p0: np.ndarray,
    p1: np.ndarray,
    p2: np.ndarray,
    shades: tuple[float, float, float],
) -> None:
    height, width = depth.shape
    min_x = max(int(math.floor(min(p0[0], p1[0], p2[0]))), 0)
    max_x = min(int(math.ceil(max(p0[0], p1[0], p2[0]))), width - 1)
    min_y = max(int(math.floor(min(p0[1], p1[1], p2[1]))), 0)
    max_y = min(int(math.ceil(max(p0[1], p1[1], p2[1]))), height - 1)
    if min_x > max_x or min_y > max_y:
        return
    denom = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
    if abs(denom) < 1e-8:
        return

    ys, xs = np.mgrid[min_y : max_y + 1, min_x : max_x + 1]
    w0 = ((p1[1] - p2[1]) * (xs - p2[0]) + (p2[0] - p1[0]) * (ys - p2[1])) / denom
    w1 = ((p2[1] - p0[1]) * (xs - p2[0]) + (p0[0] - p2[0]) * (ys - p2[1])) / denom
    w2 = 1.0 - w0 - w1
    inside = (w0 >= 0.0) & (w1 >= 0.0) & (w2 >= 0.0)
    z = w0 * p0[2] + w1 * p1[2] + w2 * p2[2]
    region = depth[min_y : max_y + 1, min_x : max_x + 1]
    update = inside & (z > region)
    if not np.any(update):
        return
    region[update] = z[update]
    shade = w0 * shades[0] + w1 * shades[1] + w2 * shades[2]
    rgb = np.clip(_METAL * shade[..., None], 0, 255).astype(np.uint8)
    pixels = image[min_y : max_y + 1, min_x : max_x + 1]
    pixels[update] = rgb[update]


def _paint_triangle_color(
    image: np.ndarray,
    depth: np.ndarray,
    p0: np.ndarray,
    p1: np.ndarray,
    p2: np.ndarray,
    colors: tuple[np.ndarray, np.ndarray, np.ndarray],
) -> None:
    height, width = depth.shape
    min_x = max(int(math.floor(min(p0[0], p1[0], p2[0]))), 0)
    max_x = min(int(math.ceil(max(p0[0], p1[0], p2[0]))), width - 1)
    min_y = max(int(math.floor(min(p0[1], p1[1], p2[1]))), 0)
    max_y = min(int(math.ceil(max(p0[1], p1[1], p2[1]))), height - 1)
    if min_x > max_x or min_y > max_y:
        return
    denom = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
    if abs(denom) < 1e-8:
        return
    ys, xs = np.mgrid[min_y : max_y + 1, min_x : max_x + 1]
    w0 = ((p1[1] - p2[1]) * (xs - p2[0]) + (p2[0] - p1[0]) * (ys - p2[1])) / denom
    w1 = ((p2[1] - p0[1]) * (xs - p2[0]) + (p0[0] - p2[0]) * (ys - p2[1])) / denom
    w2 = 1.0 - w0 - w1
    inside = (w0 >= 0.0) & (w1 >= 0.0) & (w2 >= 0.0)
    z = w0 * p0[2] + w1 * p1[2] + w2 * p2[2]
    region = depth[min_y : max_y + 1, min_x : max_x + 1]
    update = inside & (z > region)
    if not np.any(update):
        return
    region[update] = z[update]
    rgb = w0[..., None] * colors[0] + w1[..., None] * colors[1] + w2[..., None] * colors[2]
    pixels = image[min_y : max_y + 1, min_x : max_x + 1]
    pixels[update] = np.clip(rgb[update], 0, 255).astype(np.uint8)


def _draw_visible_segment(
    image: np.ndarray,
    depth: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
    bias: float,
) -> None:
    height, width = depth.shape
    steps = int(max(abs(end[0] - start[0]), abs(end[1] - start[1]))) + 1
    if steps > 4000:
        steps = 4000
    for t in np.linspace(0.0, 1.0, steps):
        x = start[0] * (1.0 - t) + end[0] * t
        y = start[1] * (1.0 - t) + end[1] * t
        z = start[2] * (1.0 - t) + end[2] * t
        ix = int(round(float(x)))
        iy = int(round(float(y)))
        if 0 <= ix < width and 0 <= iy < height and z >= depth[iy, ix] - bias:
            for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
                px = ix + dx
                py = iy + dy
                if 0 <= px < width and 0 <= py < height and z >= depth[py, px] - bias:
                    image[py, px] = _EDGE


def _contact_sheet(
    stem: str,
    panels: dict[str, Image.Image],
    measurement: Measurement,
    wall: WallReport | None,
) -> Image.Image:
    sample = next(iter(panels.values()))
    cell_w, cell_h = sample.size
    sheet = Image.new("RGB", (cell_w * 4, cell_h * 2), _PAPER)
    for index, name in enumerate(_CONTACT_ORDER):
        col, row = index % 4, index // 4
        sheet.paste(panels[name], (col * cell_w, row * cell_h))
    stats = Image.new("RGB", (cell_w, cell_h), _PAPER)
    draw = ImageDraw.Draw(stats)
    draw.rectangle((8, 8, cell_w - 9, cell_h - 9), outline=(190, 184, 172))
    font = _font(15)
    small = _font(13)
    size = measurement.bounding_box.size
    lines = [
        (stem, font, _INK),
        ("", small, _MUTED),
        (
            f"{size.x:g} × {size.y:g} × {size.z:g} mm",
            font,
            _INK,
        ),
        (f"volume   {measurement.volume_mm3:g} mm^3", small, _INK),
        (f"area     {measurement.area_mm2:g} mm^2", small, _INK),
        (
            f"valid { _yes(measurement.valid) }    watertight { _yes(measurement.watertight) }",
            small,
            _INK,
        ),
        (
            f"solids {measurement.solid_count}   faces {measurement.face_count}   "
            f"edges {measurement.edge_count}",
            small,
            _INK,
        ),
        (
            "center of mass  "
            f"({measurement.center_of_mass_mm.x:g}, "
            f"{measurement.center_of_mass_mm.y:g}, "
            f"{measurement.center_of_mass_mm.z:g})",
            small,
            _INK,
        ),
    ]
    if wall is not None and wall.estimated_min_mm is not None:
        verdict = "no threshold" if wall.passed is None else ("pass" if wall.passed else "fail")
        lines.append(
            (f"est. min wall  {wall.estimated_min_mm:g} mm  ({verdict})", small, _INK)
        )
    elif wall is not None:
        lines.append(("est. min wall  unavailable", small, _INK))
    y = 22
    for text, face, color in lines:
        draw.text((22, y), text, fill=color, font=face)
        y += 26 if face is font else 22
    sheet.paste(stats, (3 * cell_w, cell_h))
    return sheet


def _footer(measurement: Measurement) -> str:
    size = measurement.bounding_box.size
    return f"millimeters    {size.x:g} × {size.y:g} × {size.z:g} mm"


def _draw_triad(
    draw: ImageDraw.ImageDraw,
    basis: tuple[np.ndarray, np.ndarray, np.ndarray],
    font: ImageFont.ImageFont,
) -> None:
    x_cam, y_cam, _z_cam = basis
    origin = (48, _HEADER + _VIEW_H - 46)
    length = 28
    axes = (
        (np.array([1.0, 0.0, 0.0]), "X", (176, 64, 48)),
        (np.array([0.0, 1.0, 0.0]), "Y", (48, 130, 72)),
        (np.array([0.0, 0.0, 1.0]), "Z", (48, 96, 176)),
    )
    for direction, label, color in axes:
        end = (
            origin[0] + int(round(float(direction @ x_cam) * length)),
            origin[1] - int(round(float(direction @ y_cam) * length)),
        )
        draw.line((origin, end), fill=color, width=2)
        draw.text((end[0] + 2, end[1] - 8), label, fill=color, font=font)


def _draw_scale_bar(draw: ImageDraw.ImageDraw, scale: float, font: ImageFont.ImageFont) -> None:
    px_per_mm = (scale / _SSAA)
    if px_per_mm <= 0:
        return
    bar_mm = _nice_length((_VIEW_W * 0.22) / px_per_mm)
    bar_px = bar_mm * px_per_mm
    x1 = _VIEW_W - 16 - int(round(bar_px))
    y = _HEADER + _VIEW_H + 16
    x0 = x1
    draw.line((x0, y, x0 + int(round(bar_px)), y), fill=_INK, width=2)
    draw.line((x0, y - 3, x0, y + 3), fill=_INK, width=1)
    label = f"{bar_mm:g} mm"
    draw.text((x0, _HEADER + _VIEW_H + 4), label, fill=_MUTED, font=font)


def _nice_length(mm: float) -> float:
    if mm <= 0:
        return 1.0
    exponent = 10 ** math.floor(math.log10(mm))
    for multiplier in (1, 2, 5, 10):
        candidate = multiplier * exponent
        if candidate >= mm * 0.6:
            return float(candidate)
    return float(10 * exponent)


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _unit(vector: np.ndarray) -> np.ndarray:
    return vector / np.linalg.norm(vector)


def render_section(
    outers: list[list[tuple[float, float]]],
    holes: list[list[tuple[float, float]]],
    out_path: Path,
    title: str,
    subtitle: str,
    x_label: str,
    y_label: str,
) -> RenderedView:
    """Draw a planar cross-section. Coordinates are millimeters in the cutting plane."""
    canvas = Image.new("RGB", (_VIEW_W, _VIEW_H + _HEADER + _FOOTER), _PAPER)
    draw = ImageDraw.Draw(canvas)
    font = _font(16)
    small = _font(13)
    draw.rectangle((0, 0, _VIEW_W, _HEADER), fill=(232, 228, 218))
    draw.text((16, 12), title, fill=_INK, font=font)
    draw.text((16, 34), subtitle, fill=_MUTED, font=small)

    points = [point for loop in outers + holes for point in loop]
    if points:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1e-6)
        span_y = max(max_y - min_y, 1e-6)
        margin = 36
        usable_w = _VIEW_W - 2 * margin
        usable_h = _VIEW_H - 2 * margin
        scale = min(usable_w / span_x, usable_h / span_y)
        origin_x = margin + (usable_w - span_x * scale) / 2
        origin_y = _HEADER + margin + (usable_h - span_y * scale) / 2

        def to_px(point: tuple[float, float]) -> tuple[float, float]:
            return (
                origin_x + (point[0] - min_x) * scale,
                origin_y + (max_y - point[1]) * scale,
            )

        fill = (176, 148, 112)
        outline = (42, 38, 32)
        for loop in outers:
            draw.polygon([to_px(point) for point in loop], fill=fill, outline=outline)
        for loop in holes:
            draw.polygon([to_px(point) for point in loop], fill=_PAPER, outline=outline)
        for loop in outers + holes:
            pixels = [to_px(point) for point in loop]
            if len(pixels) >= 2:
                draw.line(pixels + [pixels[0]], fill=outline, width=2)
        _draw_scale_bar(draw, scale * _SSAA, small)
    else:
        draw.text((24, _HEADER + 24), "The plane misses the part.", fill=_INK, font=font)

    draw.text((16, _HEADER + _VIEW_H + 8), f"plane x  {x_label}", fill=_MUTED, font=small)
    draw.text((220, _HEADER + _VIEW_H + 8), f"plane y  {y_label}", fill=_MUTED, font=small)
    _save_png(out_path, canvas)
    return RenderedView(name="section", path=str(out_path), width=canvas.width, height=canvas.height)


def render_sketch(report: SketchReport, out_path: Path) -> RenderedView:
    """Draw a solved sketch with its driving dimensions labeled.

    Constrained geometry is ink. Entities that can still move are blue.
    An over-constrained sketch is drawn in red, and the conflicting constraint
    names are listed under the title. Construction geometry is dashed.
    """
    canvas = Image.new("RGB", (_VIEW_W, _VIEW_H + _HEADER + _FOOTER), _PAPER)
    draw = ImageDraw.Draw(canvas)
    font = _font(16)
    small = _font(13)
    draw.rectangle((0, 0, _VIEW_W, _HEADER), fill=(232, 228, 218))
    title = report.name or "sketch"
    draw.text((16, 10), title, fill=_INK, font=font)

    geometry = report.geometry if isinstance(report.geometry, dict) else {}
    points = geometry.get("points") or {}
    lines = geometry.get("lines") or {}
    circles = geometry.get("circles") or {}
    arcs = geometry.get("arcs") or {}
    free_ids = {item.get("entity") for item in report.free}
    conflict = report.status in ("over_constrained", "didnt_converge")
    labels = _sketch_labels(report, points, circles)

    coords = [(point["x"], point["y"]) for point in points.values()]
    for circle in circles.values():
        center, radius = circle["center"], float(circle["radius"])
        coords.append((center["x"] - radius, center["y"] - radius))
        coords.append((center["x"] + radius, center["y"] + radius))
    coords.extend(anchor for _text, anchor in labels)

    if coords:
        xs = [coord[0] for coord in coords]
        ys = [coord[1] for coord in coords]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)
        margin = 48
        usable_w = _VIEW_W - 2 * margin
        usable_h = _VIEW_H - 2 * margin
        scale = min(usable_w / span_x, usable_h / span_y)
        origin_x = margin + (usable_w - span_x * scale) / 2
        origin_y = _HEADER + margin + (usable_h - span_y * scale) / 2

        def to_px(x: float, y: float) -> tuple[float, float]:
            return (origin_x + (x - min_x) * scale, origin_y + (max_y - y) * scale)

        for circle_id, circle in circles.items():
            center = circle["center"]
            radius = float(circle["radius"])
            color = _sketch_color(circle_id, circle.get("construction"), free_ids, conflict)
            _draw_circle(
                draw,
                to_px(center["x"], center["y"]),
                radius * scale,
                color,
                dashed=bool(circle.get("construction")),
            )
        for arc_id, arc in arcs.items():
            color = _sketch_color(arc_id, arc.get("construction"), free_ids, conflict)
            polyline = _arc_polyline(arc)
            pixels = [to_px(x, y) for x, y in polyline]
            _draw_chain(draw, pixels, color, dashed=bool(arc.get("construction")))
        for line_id, line in lines.items():
            start, end = line["start"], line["end"]
            color = _sketch_color(line_id, line.get("construction"), free_ids, conflict)
            if not line.get("construction") and _line_touches_free(line, points, free_ids):
                color = _FREE
            _draw_chain(
                draw,
                [to_px(start["x"], start["y"]), to_px(end["x"], end["y"])],
                color,
                dashed=bool(line.get("construction")),
            )
        for point_id, point in points.items():
            color = _sketch_color(point_id, False, free_ids, conflict)
            px, py = to_px(point["x"], point["y"])
            draw.ellipse((px - 2.5, py - 2.5, px + 2.5, py + 2.5), fill=color)

        for text, anchor in labels:
            px, py = to_px(anchor[0], anchor[1])
            _draw_label(draw, text, px, py, small)
        _draw_scale_bar(draw, scale * _SSAA, small)
    else:
        draw.text((24, _HEADER + 24), "The sketch has no solved points.", fill=_INK, font=font)

    footer = _sketch_footer(report)
    draw.text((16, _HEADER + _VIEW_H + 6), footer, fill=_status_color(report.status), font=small)
    _save_png(out_path, canvas)
    return RenderedView(name="sketch", path=str(out_path.resolve()), width=canvas.width, height=canvas.height)


_FREE = (32, 78, 168)
_CONFLICT = (168, 48, 40)
_CONSTRUCTION = (120, 116, 108)
_DIM = (18, 78, 92)


def _sketch_color(entity_id, construction, free_ids, conflict) -> tuple[int, int, int]:
    if construction:
        return _CONSTRUCTION
    if conflict:
        return _CONFLICT
    if entity_id in free_ids:
        return _FREE
    return _INK


def _status_color(status: str) -> tuple[int, int, int]:
    if status == "fully_constrained":
        return (36, 96, 64)
    if status == "under_constrained":
        return _FREE
    if status in ("over_constrained", "didnt_converge"):
        return _CONFLICT
    return _MUTED


def _sketch_footer(report: SketchReport) -> str:
    status = report.status.replace("_", " ")
    if report.conflicts:
        named = ", ".join(report.conflicts[:4])
        return f"{status}  ·  {report.dof} dof  ·  conflict: {named}"
    if report.free:
        named = ", ".join(str(item.get("entity")) for item in report.free[:4])
        return f"{status}  ·  {report.dof} dof  ·  free: {named}"
    return f"{status}  ·  {report.dof} dof"


def _sketch_labels(report: SketchReport, points: dict, circles: dict) -> list[tuple[str, tuple[float, float]]]:
    labels = []
    for index, dimension in enumerate(report.dimensions):
        text = f"{dimension.get('name')}  {dimension.get('text')}"
        anchor = _dimension_anchor(dimension, points, circles, index)
        if anchor is not None:
            labels.append((text, anchor))
    return labels


def _dimension_anchor(dimension: dict, points: dict, circles: dict, index: int) -> tuple[float, float] | None:
    kind = dimension.get("kind")
    if kind in ("diameter", "radius", "pattern_on_circle"):
        circle = circles.get(dimension.get("entity"))
        if circle:
            radius = float(circle["radius"])
            center = circle["center"]
            angle = math.radians(28 + index * 22)
            reach = radius + 4.0
            return (
                float(center["x"]) + math.cos(angle) * reach,
                float(center["y"]) + math.sin(angle) * reach,
            )
    first = points.get(dimension.get("a"))
    second = points.get(dimension.get("b"))
    if kind == "distance" and first and second:
        mx = (first["x"] + second["x"]) / 2
        my = (first["y"] + second["y"]) / 2
        dx = second["x"] - first["x"]
        dy = second["y"] - first["y"]
        length = math.hypot(dx, dy) or 1.0
        return (mx - dy / length * 5.0, my + dx / length * 5.0)
    if first:
        return (float(first["x"]), float(first["y"]) + 4.0)
    entity = dimension.get("entity")
    if isinstance(entity, str) and entity in points:
        point = points[entity]
        return (float(point["x"]), float(point["y"]) + 4.0)
    return None


def _line_touches_free(line: dict, points: dict, free_ids: set) -> bool:
    # Line records store coordinates, not ids. Match a free point by position.
    for point_id, point in points.items():
        if point_id not in free_ids:
            continue
        for end in (line["start"], line["end"]):
            if abs(end["x"] - point["x"]) < 1e-6 and abs(end["y"] - point["y"]) < 1e-6:
                return True
    return False


def _arc_polyline(arc: dict, count: int = 24) -> list[tuple[float, float]]:
    center, start, end = arc["center"], arc["start"], arc["end"]
    radius = math.hypot(start["x"] - center["x"], start["y"] - center["y"])
    a0 = math.atan2(start["y"] - center["y"], start["x"] - center["x"])
    a1 = math.atan2(end["y"] - center["y"], end["x"] - center["x"])
    sweep = (a1 - a0) % (2 * math.pi)
    if sweep < 1e-6:
        sweep = 2 * math.pi
    points = []
    for step in range(count + 1):
        angle = a0 + sweep * step / count
        points.append((center["x"] + radius * math.cos(angle), center["y"] + radius * math.sin(angle)))
    return points


def _draw_circle(draw: ImageDraw.ImageDraw, center: tuple[float, float], radius_px: float, color, dashed: bool) -> None:
    if radius_px < 0.5:
        return
    if not dashed:
        box = (
            center[0] - radius_px,
            center[1] - radius_px,
            center[0] + radius_px,
            center[1] + radius_px,
        )
        draw.ellipse(box, outline=color, width=2)
        return
    steps = 48
    chain = []
    for step in range(steps + 1):
        angle = 2 * math.pi * step / steps
        chain.append((center[0] + radius_px * math.cos(angle), center[1] + radius_px * math.sin(angle)))
    _draw_chain(draw, chain, color, dashed=True)


def _draw_chain(draw: ImageDraw.ImageDraw, pixels: list[tuple[float, float]], color, dashed: bool) -> None:
    if len(pixels) < 2:
        return
    if not dashed:
        draw.line(pixels, fill=color, width=2)
        return
    for index in range(0, len(pixels) - 1, 2):
        draw.line((pixels[index], pixels[index + 1]), fill=color, width=2)


def _draw_label(draw: ImageDraw.ImageDraw, text: str, x: float, y: float, font: ImageFont.ImageFont) -> None:
    box = draw.textbbox((x, y), text, font=font)
    pad = 2
    draw.rectangle((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad), fill=_PAPER)
    draw.text((x, y), text, fill=_DIM, font=font)


_SCALAR_VIEWS = ("front", "right", "top", "iso")


def render_scalar_views(
    vertices: np.ndarray,
    triangles: np.ndarray,
    scalars: np.ndarray,
    out_dir: Path,
    stem: str,
    prefix: str,
    title: str,
    unit: str,
    legend: str,
) -> list[RenderedView]:
    """Color a surface mesh by a scalar and write front, right, top, iso, and a contact sheet.

    Blue is the low end of the scale and red is the high end. Each picture is
    scaled to this mesh, and the title carries the maximum so two pictures are
    not compared by color alone.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    normals = _vertex_normals(vertices, triangles)
    low = float(np.min(scalars)) if len(scalars) else 0.0
    high = float(np.max(scalars)) if len(scalars) else 1.0
    if high - low < 1e-12:
        high = low + 1.0
    colors = _scalar_colors(scalars, low, high)
    mesh = TriangleMesh(vertices=vertices, normals=normals, triangles=triangles, edges=[], colors=colors)
    bases = {name: _camera_basis(eye, up) for name, eye, up, _label in _VIEWS if name in _SCALAR_VIEWS}
    scale, _span = _shared_scale(vertices, bases)
    panels: dict[str, Image.Image] = {}
    records: list[RenderedView] = []
    footer = f"{legend}    {low:.4g} to {high:.4g} {unit}"
    for name in _SCALAR_VIEWS:
        _key, _eye, _up, label = next(item for item in _VIEWS if item[0] == name)
        image = _compose_view(mesh, bases[name], scale, title=f"{title}  ·  {label}", footer=footer)
        _draw_scalar_legend(image, low, high, unit)
        path = out_dir / f"{stem}_{prefix}_{name}.png"
        _save_png(path, image, optimize=True)
        panels[name] = image
        records.append(
            RenderedView(name=f"{prefix}_{name}", path=str(path.resolve()), width=image.width, height=image.height)
        )
    contact = _scalar_contact(panels)
    contact_path = out_dir / f"{stem}_{prefix}_contact.png"
    _save_png(contact_path, contact, optimize=True)
    records.append(
        RenderedView(
            name=f"{prefix}_contact",
            path=str(contact_path.resolve()),
            width=contact.width,
            height=contact.height,
        )
    )
    return records


def _vertex_normals(vertices: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    normals = np.zeros_like(vertices)
    for tri in triangles:
        a, b, c = vertices[int(tri[0])], vertices[int(tri[1])], vertices[int(tri[2])]
        normals[int(tri[0])] += np.cross(b - a, c - a)
        normals[int(tri[1])] += np.cross(b - a, c - a)
        normals[int(tri[2])] += np.cross(b - a, c - a)
    return normals


def _scalar_colors(scalars: np.ndarray, low: float, high: float) -> np.ndarray:
    span = high - low
    colors = np.empty((len(scalars), 3), dtype=np.float64)
    for index, value in enumerate(scalars):
        colors[index] = _colormap((float(value) - low) / span)
    return colors


def _colormap(t: float) -> np.ndarray:
    t = min(1.0, max(0.0, t))
    stops = (
        (0.0, (38, 55, 130)),
        (0.25, (32, 120, 190)),
        (0.5, (40, 170, 120)),
        (0.75, (230, 190, 50)),
        (1.0, (175, 40, 35)),
    )
    for (left, a), (right, b) in zip(stops, stops[1:]):
        if t <= right:
            mix = 0.0 if right == left else (t - left) / (right - left)
            return np.array([a[i] + (b[i] - a[i]) * mix for i in range(3)], dtype=np.float64)
    return np.array(stops[-1][1], dtype=np.float64)


def _draw_scalar_legend(image: Image.Image, low: float, high: float, unit: str) -> None:
    draw = ImageDraw.Draw(image)
    small = _font(12)
    width = 120
    height = 8
    x0 = image.width - width - 16
    y0 = 22
    for step in range(width):
        color = tuple(int(channel) for channel in _colormap(step / max(width - 1, 1)))
        draw.line((x0 + step, y0, x0 + step, y0 + height), fill=color)
    draw.text((x0, 6), f"{low:.3g} {unit}", fill=_INK, font=small)
    label = f"{high:.3g}"
    draw.text((x0 + width - 36, 6), label, fill=_INK, font=small)


def _scalar_contact(panels: dict[str, Image.Image]) -> Image.Image:
    sample = next(iter(panels.values()))
    cell_w, cell_h = sample.size
    sheet = Image.new("RGB", (cell_w * 2, cell_h * 2), _PAPER)
    for index, name in enumerate(_SCALAR_VIEWS):
        col, row = index % 2, index // 2
        sheet.paste(panels[name], (col * cell_w, row * cell_h))
    return sheet


def frame_scale(
    meshes: list[TriangleMesh],
    bases: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
    view_w: int,
    view_h: int,
    ssaa: int = 1,
) -> float:
    """One pixels-per-millimeter scale so every frame fits in the viewport."""
    if view_w < 1 or view_h < 1 or ssaa < 1:
        raise RuntimeError("The animation viewport is empty.")
    span = 1e-6
    for mesh, basis in zip(meshes, bases, strict=True):
        x_cam, y_cam, _z_cam = basis
        if len(mesh.vertices) == 0:
            continue
        x = mesh.vertices @ x_cam
        y = mesh.vertices @ y_cam
        span = max(span, float(x.max() - x.min()), float(y.max() - y.min()))
    usable = min(view_w, view_h) * 0.84 * ssaa
    return usable / span


def render_frame(
    mesh: TriangleMesh,
    basis: tuple[np.ndarray, np.ndarray, np.ndarray],
    scale: float,
    width: int,
    height: int,
    *,
    background: tuple[int, int, int] = _PAPER,
    overlay: str = "",
    ssaa: int = 1,
) -> Image.Image:
    """One RGB frame. ``width`` and ``height`` are the picture size."""
    header = 0
    if overlay:
        header = max(14, min(28, height // 8))
    view_h = height - header
    if view_h < 1 or width < 1:
        raise RuntimeError("The animation frame is smaller than one pixel.")
    paper = tuple(int(channel) for channel in background)
    buffer_w = width * ssaa
    buffer_h = view_h * ssaa
    color, _depth = _rasterize(mesh, basis, scale, buffer_w, buffer_h, background=paper)
    view = Image.fromarray(color, mode="RGB")
    if ssaa != 1:
        view = view.resize((width, view_h), Image.Resampling.BOX)
    if header == 0:
        return view
    canvas = Image.new("RGB", (width, height), paper)
    canvas.paste(view, (0, header))
    draw = ImageDraw.Draw(canvas)
    size = max(10, min(15, header - 2))
    draw.text((6, max(0, (header - size) // 2)), overlay, fill=_ink_for(paper), font=_font(size))
    return canvas


def _ink_for(background: tuple[int, int, int]) -> tuple[int, int, int]:
    luminance = 0.2126 * background[0] + 0.7152 * background[1] + 0.0722 * background[2]
    if luminance >= 140:
        return _INK
    return (236, 236, 232)


def render_motion_frames(
    meshes: list[TriangleMesh],
    out_dir: Path,
    stem: str,
    titles: list[str],
) -> dict:
    """Write one isometric picture per frame, a strip, and a GIF."""
    if not meshes:
        raise RuntimeError("Motion rendering needs at least one mesh.")
    out_dir.mkdir(parents=True, exist_ok=True)
    basis = _camera_basis((1.0, -1.0, 0.85), (0.0, 0.0, 1.0))
    combined = np.vstack([mesh.vertices for mesh in meshes])
    scale, _span = _shared_scale(combined, {"iso": basis})
    images: list[Image.Image] = []
    frames = []
    for index, mesh in enumerate(meshes):
        title = titles[index] if index < len(titles) else f"frame {index}"
        image = _compose_view(mesh, basis, scale, title=title, footer=stem)
        path = out_dir / f"{stem}_{index:02d}.png"
        _save_png(path, image, optimize=True)
        images.append(image)
        frames.append({"index": index, "title": title, "path": str(path.resolve()), "name": "iso"})
    strip = _strip(images)
    strip_path = out_dir / f"{stem}_strip.png"
    _save_png(strip_path, strip, optimize=True)
    gif_path = out_dir / f"{stem}.gif"

    def write_gif(temporary: Path) -> None:
        images[0].save(
            temporary,
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=280,
            loop=0,
            disposal=2,
        )

    publish_file(gif_path, write_gif)
    return {
        "frames": frames,
        "strip": str(strip_path.resolve()),
        "gif": str(gif_path.resolve()),
    }


def _strip(images: list[Image.Image]) -> Image.Image:
    cell_w, cell_h = images[0].size
    sheet = Image.new("RGB", (cell_w * len(images), cell_h), _PAPER)
    for index, image in enumerate(images):
        sheet.paste(image, (index * cell_w, 0))
    return sheet


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()
