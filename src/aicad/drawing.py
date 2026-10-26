"""A dimensioned sheet of an assembly, as PDF and DXF.

Third-angle projection: front, top, right side, and an isometric view.
The dimensions are the axis-aligned size of the posed assembly. The title
block carries the scale and says what the sheet is not. Full GD&T, tolerances,
threads, and section views are not on it.

OpenCascade's hidden-line removal supplies the curves. The sheet itself is
plain lines and text, written here, so the files do not need another library.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from aicad import explode, kernel
from aicad.blueprint import png_size
from aicad.errors import GeometryError
from aicad.honesty import VERIFIED_CHECK, finish
OMITS = [
    "GD&T and feature-control frames",
    "tolerances of any kind",
    "surface finish",
    "weld symbols",
    "thread callouts",
    "section views and detail views",
    "hole and radius callouts",
    "a bill of materials on the sheet",
    "center marks",
]
ASSUMPTIONS = [
    "The projection is third angle. The top view sits above the front view, and the right-side view sits to its right.",
    "Front looks along +Y, top looks down, and the right-side view looks along -X. Z is up.",
    "Dimensions are the axis-aligned width, depth, and height of the posed solids.",
    "The scale is a usual sheet ratio chosen so the views fit, unless you name one that fits. A very large assembly uses a smaller ratio, and the sheet says so.",
    "A size of 1 mm or more is shown to the nearest 0.1 mm, or as a whole number when it is within 0.05 mm of one. A size below 1 mm is shown to 0.001 mm. A size smaller than that is labeled <0.001.",
    "PDF and DXF coordinates are millimeters on the A3 sheet. The dimension text is the model size.",
    "Hidden lines are dashed. The isometric view leaves them out.",
    "A tangent edge, such as a fillet meeting a face, is a thin line when the kernel returns one.",
    "Catalog fasteners are the simplified catalog solids. Threads are not drawn.",
    "An exploded isometric, when you ask for one, is pulled apart for the picture. Its gaps are not dimensions.",
]

_PAGE_W = 420.0
_PAGE_H = 297.0
# Left room is for a dimension number. A number drawn on the part is hard to read.
_ORTHO_PAD = (28.0, 16.0, 10.0, 12.0)
_ISO_PAD = (8.0, 8.0, 8.0, 14.0)
_SCALES = (
    (10, 1),
    (5, 1),
    (2, 1),
    (1, 1),
    (1, 2),
    (1, 5),
    (1, 10),
    (1, 20),
    (1, 50),
    (1, 100),
    (1, 200),
    (1, 500),
    (1, 1000),
)
_VIEWS = (
    ("front", (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), "FRONT", 0, 0, True),
    ("top", (0.0, 0.0, -1.0), (0.0, 1.0, 0.0), "TOP", 0, 1, True),
    ("right", (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), "RIGHT SIDE", 1, 0, True),
    ("iso", (-1.0, 1.0, -1.0), (0.0, 0.0, 1.0), "ISOMETRIC", 1, 1, False),
)

_INK = (28, 32, 36)
_MUTED = (90, 86, 78)
_DIM = (146, 52, 40)
_HIDDEN = (110, 106, 98)
_PAPER = (248, 246, 240)
_RULE = (40, 44, 48)


@dataclass
class _Line:
    x1: float
    y1: float
    x2: float
    y2: float
    layer: str
    width: float
    color: tuple[int, int, int]
    dash: tuple[float, float] | None = None


@dataclass
class _Text:
    x: float
    y: float
    text: str
    size: float
    layer: str
    color: tuple[int, int, int]


@dataclass
class _Fill:
    points: tuple[tuple[float, float], ...]
    color: tuple[int, int, int]
    layer: str


@dataclass
class _Cell:
    x: float
    y: float
    w: float
    h: float


def make_drawing(
    spec: dict,
    solved: dict,
    shapes: dict[str, kernel.Shape],
    *,
    pdf_path: Path,
    dxf_path: Path,
    png_path: Path,
    scale: str | None = None,
    offsets: dict[str, list[float]] | None = None,
    gap_mm: float | None = None,
) -> dict:
    """Write the sheet. ``offsets`` draws the isometric view exploded."""
    assembled = kernel.compound(
        [kernel.transform_shape(shapes[item["part"]], item["matrix"]) for item in solved["instances"]]
    )
    exploded_shape = None
    if offsets is not None:
        moved = explode.apply_moves(solved, offsets)
        exploded_shape = kernel.compound(
            [kernel.transform_shape(shapes[item["part"]], item["matrix"]) for item in moved["instances"]]
        )

    size = _size(assembled)
    views = []
    for name, look, up, title, column, row, hidden in _VIEWS:
        source = exploded_shape if name == "iso" and exploded_shape is not None else assembled
        curves = kernel.project_view(source, look, up, hidden=hidden)
        flat = kernel.project_points(_corners(source), look, up)
        box = _box(flat)
        views.append(
            {
                "name": name,
                "title": "EXPLODED ISO" if name == "iso" and exploded_shape is not None else title,
                "column": column,
                "row": row,
                "curves": curves,
                "box": box,
                "hidden": hidden,
            }
        )

    cells = _cells()
    ortho = [view for view in views if view["name"] != "iso"]
    iso = next(view for view in views if view["name"] == "iso")
    chosen, scale_note = _choose_scale(scale, ortho, cells)
    ratio = chosen[0] / chosen[1]
    iso_scale, iso_note = _view_scale(iso, cells, ratio)
    lines: list[_Line] = []
    texts: list[_Text] = []
    fills: list[_Fill] = []
    _frame(lines)
    _title_block(
        lines,
        texts,
        spec.get("name") or "assembly",
        _scale_text(chosen),
        ratio,
        exploded_shape is not None,
        gap_mm,
        chosen not in _SCALES,
    )
    for view in ortho:
        _draw_view(lines, texts, fills, view, cells[(view["column"], view["row"])], ratio, None)
    _draw_view(
        lines,
        texts,
        fills,
        iso,
        cells[(1, 1)],
        iso_scale,
        None if abs(iso_scale - ratio) < 1e-9 else _scale_text(_pair(iso_scale)),
    )
    _overall_dimensions(lines, texts, fills, views, cells, ratio, size)

    _write_pdf(pdf_path, lines, texts, fills)
    _write_dxf(dxf_path, lines, texts)
    _write_png(png_path, lines, texts, fills)

    hidden_count = sum(1 for view in views for curve in view["curves"] if curve.kind == "hidden")
    visible_count = sum(1 for view in views for curve in view["curves"] if curve.kind == "visible")
    return finish(
        {
        "assembly": spec.get("name"),
        "units": "mm",
        "projection": "third angle",
        "paper": "A3 landscape",
        "scale": _scale_text(chosen),
        "scale_ratio": ratio,
        "scale_note": scale_note,
        "iso_scale": _scale_text(_pair(iso_scale)),
        "iso_note": iso_note,
        "exploded": exploded_shape is not None,
        "dimensions": [
            {
                "name": "overall_width",
                "axis": "X",
                "mm": round(size[0], 3),
                "on": "front",
                "meaning": "Axis-aligned width of the posed assembly.",
            },
            {
                "name": "overall_depth",
                "axis": "Y",
                "mm": round(size[1], 3),
                "on": "top",
                "meaning": "Axis-aligned depth of the posed assembly.",
            },
            {
                "name": "overall_height",
                "axis": "Z",
                "mm": round(size[2], 3),
                "on": "front",
                "meaning": "Axis-aligned height of the posed assembly.",
            },
        ],
        "curves": {"visible": visible_count, "hidden": hidden_count},
        "files": {
            "pdf": str(pdf_path.resolve()),
            "dxf": str(dxf_path.resolve()),
            "png": str(png_path.resolve()),
        },
        "views": [_png_view(png_path)],
        "assumptions": list(ASSUMPTIONS),
        "omits": list(OMITS),
        "note": " ".join(
            part
            for part in (
                "This is a layout drawing of the posed assembly. It is not a shop drawing and it has no GD&T.",
                scale_note,
                iso_note,
            )
            if part
        ),
        },
        kind=VERIFIED_CHECK,
        name="Drawing",
        what=(
            "The overall sizes are the axis-aligned box of the posed solids, measured by the geometry kernel. "
            "This sheet is a layout drawing, not a shop drawing, and it has no GD&T."
        ),
        limits=list(OMITS),
    )


def parse_scale(text: str) -> tuple[int, int]:
    """Read ``1:2`` or ``2:1``. The pair has to be a ratio this sheet can draw."""
    cleaned = text.strip().replace(" ", "")
    if ":" not in cleaned:
        raise GeometryError(
            "A scale is a ratio such as 1:2 or 2:1. "
            + "Choices: " + ", ".join(_scale_text(pair) for pair in _SCALES) + "."
        )
    left, right = cleaned.split(":", 1)
    try:
        pair = (int(left), int(right))
    except ValueError as exc:
        raise GeometryError("A scale is two whole numbers, such as 1:5 or 2:1.") from exc
    if pair not in _all_scales() or pair[0] <= 0 or pair[1] <= 0:
        raise GeometryError(
            f"Scale {cleaned} is not one this sheet uses. "
            + "Choices: " + ", ".join(_scale_text(item) for item in _SCALES) + "."
        )
    return pair


def _png_view(path: Path) -> dict:
    width, height = png_size(path)
    return {"name": "drawing", "path": str(path.resolve()), "width": width, "height": height}


def _size(shape: kernel.Shape) -> tuple[float, float, float]:
    box = shape.impl.bounding_box()
    return (
        float(box.max.X) - float(box.min.X),
        float(box.max.Y) - float(box.min.Y),
        float(box.max.Z) - float(box.min.Z),
    )


def _corners(shape: kernel.Shape) -> list[tuple[float, float, float]]:
    box = shape.impl.bounding_box()
    return [
        (x, y, z)
        for x in (float(box.min.X), float(box.max.X))
        for y in (float(box.min.Y), float(box.max.Y))
        for z in (float(box.min.Z), float(box.max.Z))
    ]


def _box(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _cells() -> dict[tuple[int, int], _Cell]:
    margin = 8.0
    title_top = 58.0
    gap = 8.0
    left = margin
    bottom = title_top + 4.0
    width = _PAGE_W - 2 * margin
    height = _PAGE_H - margin - bottom
    cell_w = (width - gap) / 2
    cell_h = (height - gap) / 2
    return {
        (0, 0): _Cell(left, bottom, cell_w, cell_h),
        (0, 1): _Cell(left, bottom + cell_h + gap, cell_w, cell_h),
        (1, 0): _Cell(left + cell_w + gap, bottom, cell_w, cell_h),
        (1, 1): _Cell(left + cell_w + gap, bottom + cell_h + gap, cell_w, cell_h),
    }


def _choose_scale(requested: str | None, views: list[dict], cells: dict) -> tuple[tuple[int, int], str]:
    if requested:
        pair = parse_scale(requested)
        if _ortho_fits(views, cells, pair[0] / pair[1]):
            return pair, ""
        fallback, extra = _largest_fit(views, cells)
        note = (
            f"Scale {requested} does not fit this sheet. "
            f"The sheet uses {_scale_text(fallback)} instead."
        )
        if extra:
            note = f"{note} {extra}"
        return fallback, note
    return _largest_fit(views, cells)


def _largest_fit(views: list[dict], cells: dict) -> tuple[tuple[int, int], str]:
    for pair in _all_scales():
        if _ortho_fits(views, cells, pair[0] / pair[1]):
            if pair in _SCALES:
                return pair, ""
            return pair, (
                f"The usual sheet scales stop at 1:1000. "
                f"This sheet uses {_scale_text(pair)} so the views fit."
            )
    pair = _pair_that_fits(views, cells)
    return pair, f"This sheet uses {_scale_text(pair)} so the views fit."


def _all_scales() -> tuple[tuple[int, int], ...]:
    """Usual sheet ratios, then 1:2000, 1:5000, 1:10000, and so on."""
    scales = list(_SCALES)
    seen = set(scales)
    base = 1000
    while base < 1_000_000_000:
        for factor in (2, 5, 10):
            pair = (1, base * factor)
            if pair not in seen and pair[1] > 1000:
                scales.append(pair)
                seen.add(pair)
        base *= 10
    return tuple(scales)


def _pair_that_fits(
    views: list[dict], cells: dict, pad: tuple[float, float, float, float] = _ORTHO_PAD
) -> tuple[int, int]:
    """A 1:N reduction when even 1:1000000000 is too large."""
    needed = 1.0
    for view in views:
        cell = cells[(view["column"], view["row"])]
        box = view["box"]
        width = max(box[2] - box[0], 1e-6)
        height = max(box[3] - box[1], 1e-6)
        inner_w = max(cell.w - pad[0] - pad[2], 1.0)
        inner_h = max(cell.h - pad[1] - pad[3], 1.0)
        needed = min(needed, inner_w / width, inner_h / height)
    denom = max(1, int(math.ceil(1.0 / max(needed, 1e-15))))
    return (1, denom)


def _ortho_fits(views: list[dict], cells: dict, ratio: float) -> bool:
    pad = _ORTHO_PAD
    for view in views:
        cell = cells[(view["column"], view["row"])]
        if not _fits(view["box"], ratio, cell, pad):
            return False
    return True


def _view_scale(view: dict, cells: dict, preferred: float) -> tuple[float, str]:
    cell = cells[(view["column"], view["row"])]
    pad = _ISO_PAD
    if _fits(view["box"], preferred, cell, pad):
        return preferred, ""
    for pair in _all_scales():
        ratio = pair[0] / pair[1]
        if ratio <= preferred and _fits(view["box"], ratio, cell, pad):
            return ratio, f"The isometric view is {_scale_text(pair)} so it fits its corner."
    pair = _pair_that_fits([view], cells, pad)
    return pair[0] / pair[1], f"The isometric view is {_scale_text(pair)} so it fits its corner."


def _fits(box: tuple[float, float, float, float], ratio: float, cell: _Cell, pad: tuple[float, float, float, float]) -> bool:
    width = max(box[2] - box[0], 0.0) * ratio + pad[0] + pad[2]
    height = max(box[3] - box[1], 0.0) * ratio + pad[1] + pad[3]
    return width <= cell.w + 0.1 and height <= cell.h + 0.1


def _draw_view(lines, texts, fills, view, cell: _Cell, ratio: float, scale_note: str | None) -> None:
    box = view["box"]
    pad = _ORTHO_PAD if view["name"] != "iso" else _ISO_PAD
    mapper = _mapper(cell, box, ratio, pad)
    # Hidden lines go down first. A box's back edge sits on the front edge,
    # and a later visible line has to cover that dash.
    kind_order = {"hidden": 0, "smooth": 1, "visible": 2}
    ordered = sorted(view["curves"], key=lambda curve: kind_order.get(curve.kind, 1))
    for curve in ordered:
        kind = curve.kind
        color = _HIDDEN if kind == "hidden" else _INK
        width = 0.18 if kind != "visible" else 0.35
        dash = (1.8, 1.2) if kind == "hidden" else None
        layer = "HIDDEN" if kind == "hidden" else "VISIBLE"
        mapped = [mapper(point) for point in curve.points]
        for start, end in zip(mapped, mapped[1:]):
            lines.append(_Line(start[0], start[1], end[0], end[1], layer, width, color, dash))
    title = view["title"] if not scale_note else f"{view['title']}  {scale_note}"
    texts.append(_Text(cell.x + 2.0, cell.y + cell.h - 5.0, title, 2.6, "TITLE", _MUTED))
    if not view["curves"]:
        texts.append(_Text(cell.x + 8.0, cell.y + cell.h / 2, "No edges in this view.", 3.0, "TITLE", _MUTED))


def _mapper(cell: _Cell, box, ratio: float, pad: tuple[float, float, float, float]):
    model_w = max(box[2] - box[0], 0.0) * ratio
    model_h = max(box[3] - box[1], 0.0) * ratio
    inner_w = cell.w - pad[0] - pad[2]
    inner_h = cell.h - pad[1] - pad[3]
    origin_x = cell.x + pad[0] + max(inner_w - model_w, 0.0) / 2 - box[0] * ratio
    origin_y = cell.y + pad[1] + max(inner_h - model_h, 0.0) / 2 - box[1] * ratio

    def mapper(point: tuple[float, float]) -> tuple[float, float]:
        return (origin_x + point[0] * ratio, origin_y + point[1] * ratio)

    return mapper


def _overall_dimensions(lines, texts, fills, views, cells, ratio: float, size) -> None:
    front = next(view for view in views if view["name"] == "front")
    top = next(view for view in views if view["name"] == "top")
    front_map = _mapper(cells[(0, 0)], front["box"], ratio, _ORTHO_PAD)
    top_map = _mapper(cells[(0, 1)], top["box"], ratio, _ORTHO_PAD)
    fx0, fy0 = front_map((front["box"][0], front["box"][1]))
    fx1, fy1 = front_map((front["box"][2], front["box"][3]))
    tx0, ty0 = top_map((top["box"][0], top["box"][1]))
    _tx1, ty1 = top_map((top["box"][2], top["box"][3]))
    _horizontal(lines, texts, fills, fx0, fx1, fy0 - 8.0, _format_mm(size[0]))
    _vertical(lines, texts, fills, fy0, fy1, fx0 - 9.0, _format_mm(size[2]))
    _vertical(lines, texts, fills, ty0, ty1, tx0 - 9.0, _format_mm(size[1]))
    _extension(lines, fx0, fy0, fx0, fy0 - 11.0)
    _extension(lines, fx1, fy0, fx1, fy0 - 11.0)
    _extension(lines, fx0, fy0, fx0 - 12.0, fy0)
    _extension(lines, fx0, fy1, fx0 - 12.0, fy1)
    _extension(lines, tx0, ty0, tx0 - 12.0, ty0)
    _extension(lines, tx0, ty1, tx0 - 12.0, ty1)


def _horizontal(lines, texts, fills, x1, x2, y, text) -> None:
    if x2 < x1:
        x1, x2 = x2, x1
    _place_dimension(lines, texts, fills, x1, y, x2, y, text, along_x=True)


def _vertical(lines, texts, fills, y1, y2, x, text) -> None:
    if y2 < y1:
        y1, y2 = y2, y1
    _place_dimension(lines, texts, fills, x, y1, x, y2, text, along_x=False)


def _place_dimension(lines, texts, fills, x1, y1, x2, y2, text, along_x: bool) -> None:
    span = math.hypot(x2 - x1, y2 - y1)
    gap = _text_width(text, 3.0) + 2.0
    # Arrowheads are a few millimetres long. On a foil they would hide the size.
    draw_arrows = span >= 6.0
    if draw_arrows:
        _arrow(fills, (x1, y1), (x2, y2))
        _arrow(fills, (x2, y2), (x1, y1))
    if along_x and span > gap + 4:
        mid = (x1 + x2) / 2
        lines.append(_Line(x1, y1, mid - gap / 2, y1, "DIMENSION", 0.18, _DIM))
        lines.append(_Line(mid + gap / 2, y1, x2, y2, "DIMENSION", 0.18, _DIM))
        texts.append(_Text(*_keep_on_sheet(mid - _text_width(text, 3.0) / 2, y1 - 1.1, text, 3.0), text, 3.0, "DIMENSION", _DIM))
        return
    lines.append(_Line(x1, y1, x2, y2, "DIMENSION", 0.18, _DIM))
    if along_x:
        texts.append(_Text(*_keep_on_sheet(max(x1, x2) + 1.6, y1 - 1.1, text, 3.0), text, 3.0, "DIMENSION", _DIM))
    else:
        width = _text_width(text, 3.0)
        mid_y = (y1 + y2) / 2
        left = x1 - width - 1.4
        text_y = mid_y - 1.1
        if left < 8.0:
            # Keep the number in the margin. Moving it right puts it on the part.
            left = 8.0
            if left + width > x1 - 0.6:
                text_y = y2 + 1.5
        texts.append(_Text(*_keep_on_sheet(left, text_y, text, 3.0), text, 3.0, "DIMENSION", _DIM))


def _keep_on_sheet(x: float, y: float, text: str, size: float) -> tuple[float, float]:
    """Keep a label inside the border. A number past the edge is unreadable."""
    width = _text_width(text, size)
    x = min(max(x, 8.0), _PAGE_W - 8.0 - width)
    y = min(max(y, 8.0), _PAGE_H - 8.0 - size)
    return (x, y)


def _extension(lines, x1, y1, x2, y2) -> None:
    lines.append(_Line(x1, y1, x2, y2, "DIMENSION", 0.15, _DIM))


def _arrow(fills, tip: tuple[float, float], other: tuple[float, float]) -> None:
    length = math.hypot(other[0] - tip[0], other[1] - tip[1])
    if length < 1e-6:
        return
    ux = (other[0] - tip[0]) / length
    uy = (other[1] - tip[1]) / length
    size = 2.3
    px, py = -uy, ux
    base_x = tip[0] + ux * size
    base_y = tip[1] + uy * size
    spread = 0.42
    fills.append(
        _Fill(
            (
                tip,
                (base_x + px * size * spread, base_y + py * size * spread),
                (base_x - px * size * spread, base_y - py * size * spread),
            ),
            _DIM,
            "DIMENSION",
        )
    )


def _frame(lines: list[_Line]) -> None:
    lines.append(_Line(6, 6, _PAGE_W - 6, 6, "TITLE", 0.35, _RULE))
    lines.append(_Line(_PAGE_W - 6, 6, _PAGE_W - 6, _PAGE_H - 6, "TITLE", 0.35, _RULE))
    lines.append(_Line(_PAGE_W - 6, _PAGE_H - 6, 6, _PAGE_H - 6, "TITLE", 0.35, _RULE))
    lines.append(_Line(6, _PAGE_H - 6, 6, 6, "TITLE", 0.35, _RULE))
    lines.append(_Line(8, 58, _PAGE_W - 8, 58, "TITLE", 0.3, _RULE))
    lines.append(_Line(248, 8, 248, 58, "TITLE", 0.25, _RULE))


def _title_block(lines, texts, name, scale_text, ratio, exploded: bool, gap_mm: float | None, unusual: bool) -> None:
    texts.append(_Text(12, 46, str(name), 4.4, "TITLE", _INK))
    texts.append(_Text(12, 38, "LAYOUT DRAWING. Not a shop drawing.", 2.7, "TITLE", _INK))
    texts.append(
        _Text(12, 31, "No GD&T, tolerances, threads, surface finish, welds, or sections.", 2.4, "TITLE", _MUTED)
    )
    texts.append(_Text(12, 24.5, "Overall sizes are the axis-aligned box of the posed assembly.", 2.4, "TITLE", _MUTED))
    if exploded:
        gap = "the chosen gap" if gap_mm is None else f"{gap_mm:g} mm per joint"
        texts.append(_Text(12, 18, f"Isometric view is exploded by {gap}. Those gaps are not dimensions.", 2.4, "TITLE", _MUTED))
    else:
        texts.append(_Text(12, 18, "Hidden lines are dashed. The isometric view leaves them out.", 2.4, "TITLE", _MUTED))
    texts.append(_Text(12, 11.5, "Dimensions are millimeters.", 2.4, "TITLE", _MUTED))
    scale_label = f"SCALE {scale_text}"
    texts.append(_Text(256, 46, scale_label, 3.4, "TITLE", _INK))
    if unusual:
        texts.append(
            _Text(
                256 + _text_width(scale_label, 3.4) + 3.0,
                46.6,
                "Smaller than the usual ratios.",
                2.3,
                "TITLE",
                _MUTED,
            )
        )
    bar = _nice_length(ratio)
    bar_w = bar * ratio
    lines.append(_Line(256, 34, 256 + bar_w, 34, "TITLE", 0.45, _INK))
    lines.append(_Line(256, 32.2, 256, 35.8, "TITLE", 0.3, _INK))
    lines.append(_Line(256 + bar_w, 32.2, 256 + bar_w, 35.8, "TITLE", 0.3, _INK))
    texts.append(_Text(256, 27.2, f"{_format_mm(bar)} mm", 2.4, "TITLE", _INK))
    texts.append(_Text(256, 20, "THIRD ANGLE", 2.7, "TITLE", _INK))
    texts.append(_Text(256, 13, f"{date.today().isoformat()}    SHEET 1 OF 1", 2.3, "TITLE", _MUTED))


def _nice_length(ratio: float) -> float:
    """A bar of at most 70 mm on the sheet, including ratios below 1:1000."""
    best = 1.0
    magnitude = 1.0
    safe = max(ratio, 1e-15)
    while magnitude < 1e15:
        for factor in (1, 2, 5):
            step = factor * magnitude
            if step * safe <= 70.0:
                best = step
            else:
                return best
        magnitude *= 10
    return best


def _scale_text(pair: tuple[int, int]) -> str:
    return f"{pair[0]}:{pair[1]}"


def _pair(ratio: float) -> tuple[int, int]:
    for pair in _all_scales():
        if abs(pair[0] / pair[1] - ratio) < 1e-9:
            return pair
    if ratio >= 1:
        return (max(1, int(round(ratio))), 1)
    return (1, max(1, int(round(1.0 / max(ratio, 1e-15)))))


def _format_mm(value: float) -> str:
    """Text on the sheet. Whole millimetres stay whole. A thin size is not drawn as 0."""
    number = float(value)
    magnitude = abs(number)
    if magnitude < 1e-9:
        return "0"
    if magnitude < 1:
        text = f"{number:.3f}".rstrip("0").rstrip(".")
        if text in {"", "0", "-0"}:
            return "<0.001" if number >= 0 else ">-0.001"
        return text
    if abs(number - round(number)) < 0.05:
        return str(int(round(number)))
    return f"{number:.1f}"


def _text_width(text: str, size: float) -> float:
    return len(text) * size * 0.52


def _write_pdf(path: Path, lines: list[_Line], texts: list[_Text], fills: list[_Fill]) -> None:
    scale = 72.0 / 25.4
    width = _PAGE_W * scale
    height = _PAGE_H * scale
    commands = [f"{_pdf_color(_PAPER)} rg 0 0 {width:.2f} {height:.2f} re f"]
    for fill in fills:
        commands.append(_pdf_color(fill.color) + " rg")
        head, *rest = fill.points
        commands.append(f"{head[0] * scale:.2f} {head[1] * scale:.2f} m")
        for point in rest:
            commands.append(f"{point[0] * scale:.2f} {point[1] * scale:.2f} l")
        commands.append("f")
    for line in lines:
        dash = "[] 0 d" if not line.dash else f"[{line.dash[0] * scale:.2f} {line.dash[1] * scale:.2f}] 0 d"
        commands.append(
            f"{_pdf_color(line.color)} RG {line.width * scale:.3f} w {dash} "
            f"{line.x1 * scale:.2f} {line.y1 * scale:.2f} m {line.x2 * scale:.2f} {line.y2 * scale:.2f} l S"
        )
    for text in texts:
        size = text.size * scale
        commands.append(
            f"BT {_pdf_color(text.color)} rg /F1 {size:.2f} Tf "
            f"{text.x * scale:.2f} {text.y * scale:.2f} Td ({_pdf_escape(text.text)}) Tj ET"
        )
    stream = "\n".join(commands).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.2f} {height:.2f}] "
            f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ).encode("ascii"),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii"))
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    _write_bytes(path, payload)


def _pdf_color(color: tuple[int, int, int]) -> str:
    return f"{color[0] / 255:.3f} {color[1] / 255:.3f} {color[2] / 255:.3f}"


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_dxf(path: Path, lines: list[_Line], texts: list[_Text]) -> None:
    parts = [
        "0", "SECTION", "2", "HEADER",
        "9", "$ACADVER", "1", "AC1009",
        "9", "$INSUNITS", "70", "4",
        "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LTYPE", "70", "2",
        "0", "LTYPE", "2", "CONTINUOUS", "70", "0", "3", "Solid line", "72", "65", "73", "0", "40", "0.0",
        "0", "LTYPE", "2", "DASHED", "70", "0", "3", "Dashed", "72", "65", "73", "2", "40", "6.0", "49", "3.0", "49", "-1.5",
        "0", "ENDTAB",
        "0", "TABLE", "2", "LAYER", "70", "4",
        "0", "LAYER", "2", "VISIBLE", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "LAYER", "2", "HIDDEN", "70", "0", "62", "8", "6", "DASHED",
        "0", "LAYER", "2", "DIMENSION", "70", "0", "62", "1", "6", "CONTINUOUS",
        "0", "LAYER", "2", "TITLE", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
    ]
    for line in lines:
        parts.extend(
            [
                "0", "LINE",
                "8", line.layer,
                "10", _dxf_num(line.x1),
                "20", _dxf_num(line.y1),
                "11", _dxf_num(line.x2),
                "21", _dxf_num(line.y2),
            ]
        )
    for text in texts:
        parts.extend(
            [
                "0", "TEXT",
                "8", text.layer,
                "10", _dxf_num(text.x),
                "20", _dxf_num(text.y),
                "40", _dxf_num(text.size),
                "1", _dxf_escape(text.text),
            ]
        )
    parts.extend(["0", "ENDSEC", "0", "EOF", ""])
    _write_text(path, "\n".join(parts))


def _dxf_escape(text: str) -> str:
    """One DXF group value.

    PDF text escapes a backslash and a parenthesis because those end or
    continue a PDF string. A DXF value ends at the next line, so a newline
    or a carriage return is escaped the same way: it stays in this value
    and does not start another entity.
    """

    parts: list[str] = []
    for char in text:
        if char == "\\":
            parts.append("\\\\")
        elif char in "\n\r":
            parts.append("\\n")
        elif ord(char) < 32:
            parts.append(" ")
        else:
            parts.append(char)
    return "".join(parts)


def _dxf_num(value: float) -> str:
    return f"{value:.4f}"


def _write_png(path: Path, lines: list[_Line], texts: list[_Text], fills: list[_Fill]) -> None:
    ppm = 5.0
    image = Image.new("RGB", (int(_PAGE_W * ppm), int(_PAGE_H * ppm)), _PAPER)
    draw = ImageDraw.Draw(image)

    def pixel(x: float, y: float) -> tuple[float, float]:
        return (x * ppm, (_PAGE_H - y) * ppm)

    for fill in fills:
        draw.polygon([pixel(*point) for point in fill.points], fill=fill.color)
    for line in lines:
        start = pixel(line.x1, line.y1)
        end = pixel(line.x2, line.y2)
        width = max(1, int(round(line.width * ppm)))
        if line.dash:
            _dashed(draw, start, end, line.color, width, line.dash[0] * ppm, line.dash[1] * ppm)
        else:
            draw.line((start, end), fill=line.color, width=width)
    for text in texts:
        font = _font(max(8, int(round(text.size * ppm))))
        draw.text(pixel(text.x, text.y), text.text, fill=text.color, font=font, anchor="ls")
    _save_png(path, image)


def _dashed(draw, start, end, color, width, on: float, off: float) -> None:
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if length < 1:
        draw.line((start, end), fill=color, width=width)
        return
    ux = (end[0] - start[0]) / length
    uy = (end[1] - start[1]) / length
    cursor = 0.0
    drawing = True
    while cursor < length:
        step = on if drawing else off
        nxt = min(length, cursor + step)
        if drawing:
            draw.line(
                ((start[0] + ux * cursor, start[1] + uy * cursor), (start[0] + ux * nxt, start[1] + uy * nxt)),
                fill=color,
                width=width,
            )
        cursor = nxt
        drawing = not drawing


_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def _font(size: int):
    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _prepare(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GeometryError(
            f"Could not create the folder for {path}. {exc.strerror or exc}."
        ) from exc


def _write_bytes(path: Path, payload: bytes) -> None:
    from aicad.paths import publish_bytes

    try:
        publish_bytes(path, payload)
    except OSError as exc:
        raise GeometryError(f"Could not write {path}. {exc.strerror or exc}.") from exc


def _write_text(path: Path, text: str) -> None:
    from aicad.paths import publish_text

    try:
        publish_text(path, text)
    except OSError as exc:
        raise GeometryError(f"Could not write {path}. {exc.strerror or exc}.") from exc


def _save_png(path: Path, image: Image.Image) -> None:
    from aicad.paths import publish_file

    def write(temporary: Path) -> None:
        image.save(temporary, format="PNG", optimize=True)

    try:
        publish_file(path, write)
    except OSError as exc:
        raise GeometryError(f"Could not write {path}. {exc.strerror or exc}.") from exc
