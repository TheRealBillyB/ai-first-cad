"""Rough blueprint sheets for a plan.

These are the block diagrams a person reviews before any solid exists.
The sheet is blue paper, a grid, light linework, gold dimension lines, and a scale bar. A block is
not an edge of a solid, and nothing here writes DXF or a driving parameter.

The picture is a PNG. The plan stores the blocks and the dimensions as data
and draws this file from that data. Opening the PNG does not run code.
"""

from __future__ import annotations

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Blueprint ink: blue paper and gold dimensions.
_PAPER = (10, 52, 102)
_PANEL = (8, 44, 88)
_GRID = (36, 104, 168)
_INK = (236, 244, 252)
_DIM = (255, 214, 102)
_LABEL = (198, 230, 255)
_TITLE = (255, 255, 255)
_NOTE = (214, 228, 242)
_FRAME = (120, 176, 220)
_DARK = (16, 22, 32)
_SSAA = 2

FILLS: dict[str, tuple[int, int, int]] = {
    "shelter": (16, 36, 72),
    "yellow": (214, 176, 48),
    "door": (255, 228, 150),
    "red": (196, 54, 54),
    "green": (36, 150, 78),
    "white": (226, 230, 234),
    "blue": (64, 122, 196),
    "leg": (150, 156, 168),
}


def render_plan_sheet(
    path: Path | str,
    *,
    title: str,
    subtitle: str = "",
    revision: int,
    status: str,
    panels: list[dict],
    footer: str = "",
) -> Path:
    """Draw one blueprint PNG. ``panels`` are the cells, left to right."""
    image = _compose_sheet(
        title=title,
        subtitle=subtitle,
        revision=revision,
        status=status,
        panels=panels,
        footer=footer,
    )
    from aicad.paths import ensure_real_directory, publish_file

    out = Path(path)
    ensure_real_directory(out.parent)

    def write(temporary: Path) -> None:
        image.save(temporary, "PNG")

    publish_file(out, write)
    return out


def plan_sheet_png(
    *,
    title: str,
    subtitle: str = "",
    revision: int,
    status: str,
    panels: list[dict],
    footer: str = "",
) -> bytes:
    """The same PNG ``render_plan_sheet`` writes, without a file path."""
    image = _compose_sheet(
        title=title,
        subtitle=subtitle,
        revision=revision,
        status=status,
        panels=panels,
        footer=footer,
    )
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _compose_sheet(
    *,
    title: str,
    subtitle: str,
    revision: int,
    status: str,
    panels: list[dict],
    footer: str,
) -> Image.Image:
    if not panels:
        raise ValueError("A plan sheet needs at least one panel.")
    width, height = _page_size(len(panels))
    scale_px = _SSAA
    image = Image.new("RGB", (width * scale_px, height * scale_px), _PAPER)
    draw = ImageDraw.Draw(image)
    font_title = _font(_SANS_BOLD, 28 * scale_px)
    font_sub = _font(_SANS, 16 * scale_px)
    font_panel = _font(_SANS_BOLD, 18 * scale_px)
    font_dim = _font(_SANS, 15 * scale_px)
    font_note = _font(_SANS, 15 * scale_px)
    font_small = _font(_SANS, 13 * scale_px)

    from aicad.labels import strip_hidden

    title = strip_hidden(title)
    subtitle = strip_hidden(subtitle)
    footer = strip_hidden(footer)
    header = 72 * scale_px
    footer_lines = [line for line in footer.split("\n") if line.strip()] if footer else []
    footer_h = (22 * max(len(footer_lines), 1) + 16) * scale_px if footer_lines else 12 * scale_px
    draw.text((20 * scale_px, 12 * scale_px), title, font=font_title, fill=_TITLE)
    if subtitle:
        draw.text((20 * scale_px, 46 * scale_px), subtitle, font=font_sub, fill=_NOTE)
    stamp = f"v{revision}  ·  {_status_text(status)}"
    draw.text((width * scale_px - 20 * scale_px, 28 * scale_px), stamp, font=font_sub, fill=_DIM, anchor="rm")

    gap = 14 * scale_px
    left = 14 * scale_px
    usable_w = width * scale_px - 2 * left
    usable_h = height * scale_px - header - footer_h
    cell_w = usable_w / len(panels)
    frames: list[tuple[dict, tuple[int, int, int, int]]] = []
    for index, panel in enumerate(panels):
        x0 = int(left + index * cell_w)
        rect = (x0, int(header), int(x0 + cell_w - gap), int(header + usable_h - gap))
        frames.append((panel, rect))

    for panel, rect in frames:
        _draw_panel(draw, panel, rect, scale_px, font_panel, font_dim, font_note, font_small)

    if footer_lines:
        footer_y = height * scale_px - footer_h + 8 * scale_px
        for index, line in enumerate(footer_lines):
            draw.text((20 * scale_px, footer_y + index * 22 * scale_px), line, font=font_small, fill=_NOTE)

    return image.resize((width, height), Image.Resampling.LANCZOS)


def _page_size(panels: int) -> tuple[int, int]:
    if panels <= 1:
        return (1400, 900)
    if panels == 2:
        return (1800, 1000)
    return (2000, 1100)


def _status_text(status: str) -> str:
    if status == "in_review":
        return "in review"
    if status == "recorded":
        return "recorded"
    return status


def _draw_panel(draw, panel: dict, rect, scale_px, font_panel, font_dim, font_note, font_small) -> None:
    left, top, right, bottom = rect
    draw.rectangle(rect, fill=_PANEL, outline=_FRAME, width=max(1, scale_px))
    from aicad.labels import strip_hidden

    draw.text(
        (left + 12 * scale_px, top + 8 * scale_px),
        strip_hidden(str(panel.get("title") or "View")).strip() or "View",
        font=font_panel,
        fill=_TITLE,
    )
    notes = [strip_hidden(str(line)) for line in panel.get("notes") or [] if strip_hidden(str(line)).strip()]
    blocks = list(panel.get("blocks") or [])
    dimensions = list(panel.get("dimensions") or [])
    labels = list(panel.get("labels") or [])
    plot_top = top + 36 * scale_px
    plot_bottom = bottom - 12 * scale_px
    if notes and not blocks and not dimensions and not labels:
        y = plot_top + 8 * scale_px
        for line in notes:
            draw.text((left + 16 * scale_px, y), line, font=font_note, fill=_NOTE)
            y += 22 * scale_px
        return
    if notes:
        note_h = (18 * len(notes) + 16) * scale_px
        plot_bottom = bottom - note_h
        y = plot_bottom + 8 * scale_px
        for line in notes:
            draw.text((left + 16 * scale_px, y), line, font=font_note, fill=_NOTE)
            y += 18 * scale_px

    bounds = _bounds(panel)
    if bounds is None:
        return
    minx, miny, maxx, maxy = bounds
    scale = _fit_scale(bounds, (left, plot_top, right, plot_bottom), scale_px)
    origin_x = (left + right) / 2 - ((minx + maxx) / 2) * scale
    origin_y = (plot_top + plot_bottom) / 2 + ((miny + maxy) / 2) * scale

    def px(x, y):
        return (origin_x + x * scale, origin_y - y * scale)

    grid = float(panel.get("grid_mm") or 0)
    if grid > 0:
        _grid(draw, px, minx, miny, maxx, maxy, grid, scale_px)

    for block in blocks:
        _block(draw, block, px, font_small, scale_px)
    for dim in dimensions:
        _dimension(draw, dim, px, font_dim, scale_px)
    for label in labels:
        from aicad.labels import strip_hidden

        _text_backed(
            draw,
            px(float(label["x"]), float(label["y"])),
            strip_hidden(str(label.get("text") or "")),
            font_small,
            _LABEL,
            0,
        )

    _scale_bar(draw, scale, left, right, top, font_small, scale_px)


def _grid(draw, px, minx, miny, maxx, maxy, step, scale_px) -> None:
    x = math.floor(minx / step) * step
    while x <= maxx + 1e-6:
        draw.line([px(x, miny), px(x, maxy)], fill=_GRID, width=max(1, scale_px))
        x += step
    y = math.floor(miny / step) * step
    while y <= maxy + 1e-6:
        draw.line([px(minx, y), px(maxx, y)], fill=_GRID, width=max(1, scale_px))
        y += step


def _block(draw, block: dict, px, font, scale_px) -> None:
    x = float(block["x"])
    y = float(block["y"])
    w = float(block["w"])
    h = float(block["h"])
    corners = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
    pts = [px(cx, cy) for cx, cy in corners]
    fill = block.get("_fill")
    outline = _INK
    if fill is not None:
        draw.polygon(pts, fill=fill)
    draw.line(pts + [pts[0]], fill=outline, width=max(2, scale_px * 2))
    from aicad.labels import strip_hidden

    text = strip_hidden(str(block.get("label") or ""))
    if not text:
        return
    color = _DARK if _luminance(fill or _PANEL) > 140 else _INK
    lines = text.split("\n")
    cx = x + w / 2
    cy = y + h / 2
    line_h = 16 * scale_px
    total = (len(lines) - 1) * line_h
    anchor = px(cx, cy)
    for index, line in enumerate(lines):
        at = (anchor[0], anchor[1] - total / 2 + index * line_h)
        _text_backed(draw, at, line, font, color, 0)


def _dimension(draw, dim: dict, px, font, scale_px) -> None:
    x0, y0 = float(dim["x0"]), float(dim["y0"])
    x1, y1 = float(dim["x1"]), float(dim["y1"])
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return
    offset = float(dim.get("offset") or 0)
    lx, ly = -dy / length, dx / length
    over = max(length * 0.04, 8.0)
    sign = 1.0 if offset >= 0 else -1.0
    a = (x0 + lx * offset, y0 + ly * offset)
    b = (x1 + lx * offset, y1 + ly * offset)
    a_ext = (x0 + lx * (offset + sign * over), y0 + ly * (offset + sign * over))
    b_ext = (x1 + lx * (offset + sign * over), y1 + ly * (offset + sign * over))
    thin = max(1, scale_px)
    draw.line([px(x0, y0), px(*a_ext)], fill=_DIM, width=thin)
    draw.line([px(x1, y1), px(*b_ext)], fill=_DIM, width=thin)
    pa, pb = px(*a), px(*b)
    draw.line([pa, pb], fill=_DIM, width=max(2, scale_px * 2))
    _tick(draw, pa, lx, ly, scale_px)
    _tick(draw, pb, lx, ly, scale_px)
    mid = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
    angle = math.degrees(math.atan2(pa[1] - pb[1], pb[0] - pa[0]))
    if angle > 90:
        angle -= 180
    elif angle < -90:
        angle += 180
    side = 1 if offset >= 0 else -1
    nudge = 16 * scale_px
    text_at = (mid[0] + lx * nudge * side, mid[1] - ly * nudge * side)
    from aicad.labels import strip_hidden

    _text_backed(draw, text_at, strip_hidden(str(dim.get("text") or "")), font, _DIM, angle)


def _tick(draw, point, lx, ly, scale_px) -> None:
    length = 7 * scale_px
    px_nx, px_ny = lx * length, -ly * length
    draw.line(
        [(point[0] - px_nx, point[1] - px_ny), (point[0] + px_nx, point[1] + px_ny)],
        fill=_DIM,
        width=max(2, scale_px),
    )


def _scale_bar(draw, scale, left, right, top, font, scale_px) -> None:
    """Pick a bar that fits the panel. A shelf and a shed do not share a bar length."""
    choices = (
        (1000.0, "1 m"),
        (500.0, "500 mm"),
        (100.0, "100 mm"),
        (50.0, "50 mm"),
        (10.0, "10 mm"),
        (5.0, "5 mm"),
        (1.0, "1 mm"),
    )
    room = max(80 * scale_px, (right - left) * 0.34)
    chosen = choices[-1]
    for mm, label in choices:
        if 48 * scale_px <= mm * scale <= room:
            chosen = (mm, label)
            break
    mm, label = chosen
    bar_px = mm * scale
    bx = right - bar_px - 78 * scale_px
    by = top + 22 * scale_px
    tick = 5 * scale_px
    draw.line([(bx, by), (bx + bar_px, by)], fill=_DIM, width=max(2, scale_px * 2))
    draw.line([(bx, by - tick), (bx, by + tick)], fill=_DIM, width=max(2, scale_px))
    draw.line([(bx + bar_px, by - tick), (bx + bar_px, by + tick)], fill=_DIM, width=max(2, scale_px))
    draw.text((bx + bar_px + 8 * scale_px, by), label, font=font, fill=_DIM, anchor="lm")


def _bounds(panel: dict):
    xs: list[float] = []
    ys: list[float] = []

    def add(x: float, y: float) -> None:
        xs.append(x)
        ys.append(y)

    for block in panel.get("blocks") or []:
        x, y = float(block["x"]), float(block["y"])
        add(x, y)
        add(x + float(block["w"]), y + float(block["h"]))
    for dim in panel.get("dimensions") or []:
        x0, y0 = float(dim["x0"]), float(dim["y0"])
        x1, y1 = float(dim["x1"]), float(dim["y1"])
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy) or 1.0
        lx, ly = -dy / length, dx / length
        offset = float(dim.get("offset") or 0)
        pad = max(abs(offset) * 0.15, 12.0)
        for x, y in ((x0, y0), (x1, y1)):
            add(x + lx * (offset + pad), y + ly * (offset + pad))
            add(x + lx * (offset - pad), y + ly * (offset - pad))
    for label in panel.get("labels") or []:
        add(float(label["x"]), float(label["y"]))
    if not xs:
        return None
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    pad = 0.06 * max(maxx - minx, maxy - miny, 1.0)
    return (minx - pad, miny - pad, maxx + pad, maxy + pad)


def _fit_scale(bounds, rect, scale_px) -> float:
    minx, miny, maxx, maxy = bounds
    left, top, right, bottom = rect
    pad = 28 * scale_px
    avail_w = max(1.0, (right - left) - 2 * pad)
    avail_h = max(1.0, (bottom - top) - 2 * pad)
    span_x = max(maxx - minx, 1.0)
    span_y = max(maxy - miny, 1.0)
    return min(avail_w / span_x, avail_h / span_y)


def _text_backed(draw, at, text: str, font, fill, angle: float) -> None:
    if not text:
        return
    bbox = font.getbbox(text)
    w = bbox[2] - bbox[0] + 8
    h = bbox[3] - bbox[1] + 8
    plate = Image.new("RGBA", (max(w, 1), max(h, 1)), (0, 0, 0, 0))
    plate_draw = ImageDraw.Draw(plate)
    plate_draw.rectangle((0, 0, plate.width, plate.height), fill=_PANEL + (230,))
    plate_draw.text((4 - bbox[0], 4 - bbox[1]), text, font=font, fill=fill + (255,))
    if abs(angle) >= 1:
        plate = plate.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    x = int(at[0] - plate.width / 2)
    y = int(at[1] - plate.height / 2)
    draw._image.paste(plate, (x, y), plate)


def _luminance(color: tuple[int, int, int]) -> float:
    red, green, blue = color
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _font(path: str, size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size=size)
    except OSError:
        return ImageFont.load_default()
