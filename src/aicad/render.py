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
from aicad.models import Measurement, RenderedView, WallReport

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
        image.save(path, format="PNG", optimize=True)
        panels[name] = image
        records.append(
            RenderedView(name=name, path=str(path.resolve()), width=image.width, height=image.height)
        )

    contact = _contact_sheet(stem, panels, measurement, wall)
    contact_path = out_dir / f"{stem}_contact.png"
    contact.save(contact_path, format="PNG", optimize=True)
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
) -> tuple[np.ndarray, np.ndarray]:
    x_cam, y_cam, z_cam = basis
    center = _projection_center(mesh.vertices, x_cam, y_cam)
    screen = _project(mesh.vertices, x_cam, y_cam, z_cam, scale, width, height, center)
    light_key = _unit(np.array([0.35, -0.55, 0.85]))
    light_fill = _unit(np.array([-0.7, 0.25, 0.35]))
    image = np.empty((height, width, 3), dtype=np.uint8)
    image[:] = _PAPER
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
        _paint_triangle(image, depth, p0, p1, p2, shades)

    bias = 0.35
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
    lit = 0.72 * abs(float(direction @ key)) + 0.28 * abs(float(direction @ fill))
    return 0.34 + 0.66 * lit


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


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()
