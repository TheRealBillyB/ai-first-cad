"""A count of what an assembly contains.

Rows are modeled parts, catalog fasteners, other catalog parts such as
bearings, seals, motors, and gearboxes, and hoses or cables. Quantities come
from the instances and the routes. There is no price and no supplier. A mass
is a published catalog mass when the table has one. Otherwise it is the
solid's volume times the handbook density, and the row says which.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from PIL import Image, ImageDraw

from aicad import kernel, library
from aicad.errors import GeometryError
from aicad.fea import lookup_material
from aicad.honesty import ROUGH_ESTIMATE, VERIFIED_CHECK, finish, weakest
OMITS = [
    "prices and suppliers",
    "a weighed mass",
    "finishes, heat treatment, and substitutes",
    "actuator records, because a cylinder that is modeled is already a part",
    "payload_kg, which is a load on a part and not another item",
]
ASSUMPTIONS = [
    "One row is one part name, or one catalog size and length, at one material. A different spelling or a handbook alias of that material stays on the same row.",
    "Two instances of the same part add up. Two screws of the same size and length add up.",
    "A catalog screw, nut, or washer is a fastener. A bearing, seal, motor, or gearbox is a standard part.",
    "A hose, a cable, or a tendon is a route on the assembly. It is not a solid, so it has no mass here.",
    "A published catalog mass is used when the table has one. It does not follow the instance material.",
    "Without a published mass, mass is volume times the handbook density. A motor or gearbox envelope is left blank.",
]
_FASTENERS = {"socket_head_cap_screw", "hex_nut", "plain_washer"}
_ORDER = {"part": 0, "fastener": 1, "standard": 2, "hose": 3, "cable": 4, "tendon": 5, "other": 6}


def build_bom(spec: dict, shapes: dict[str, kernel.Shape], records: dict[str, dict]) -> dict:
    """Count the assembly. ``records`` maps a part name to source and library spec."""
    grouped: dict[tuple, dict] = {}
    for instance in spec["instances"]:
        part = instance["part"]
        record = records.get(part) or {}
        library_spec = record.get("library")
        material = str(instance.get("material") or "steel")
        group_key, material_label = _material_identity(material)
        if library_spec:
            row = _catalog_row(part, library_spec, material_label, shapes.get(part))
        else:
            row = _part_row(part, str(record.get("source") or "part"), material_label, shapes.get(part))
        # Catalog rows group by what you would order. Modeled parts group by name.
        # Handbook aliases and spelling share one material key.
        if library_spec:
            key = (
                row["category"],
                row["size"] or "",
                row["length_mm"] if row["length_mm"] is not None else "",
                row["standard"] or "",
                group_key,
            )
        else:
            key = ("part", row["part"], group_key)
        current = grouped.get(key)
        if current is None:
            row["instances"] = [instance["name"]]
            row["quantity"] = 1
            grouped[key] = row
        else:
            current["instances"].append(instance["name"])
            current["quantity"] += 1
            if row.get("mass_estimate_kg") is not None:
                current["mass_estimate_kg"] = round(
                    float(current.get("mass_estimate_kg") or 0.0) + float(row["mass_estimate_kg"]),
                    4,
                )

    rows = list(grouped.values())
    for row in rows:
        basis = row.pop("mass_basis", None)
        if row.get("mass_estimate_kg") is None or basis is None:
            continue
        if basis == "volume":
            row["note"] = (
                "Mass is the total for this line: each solid's volume times the handbook density. "
                "It is not a weighed mass."
            )
        elif basis == "catalog" and "total for this line" not in str(row.get("note") or ""):
            row["note"] = (
                str(row.get("note") or "Catalog mass.").rstrip()
                + " The number is the total for this line."
            )
    for cable in spec.get("cables") or []:
        rows.append(_route_row(cable, len(rows)))
    rows.sort(key=lambda row: (_ORDER.get(row["category"], 9), row["name"]))
    for number, row in enumerate(rows, start=1):
        row["item"] = number
        row["instances"] = list(row.get("instances") or [])

    categories = sorted({row["category"] for row in rows})
    quantities = {
        "result_kind": VERIFIED_CHECK,
        "result_label": "Verified check",
        "result_name": "Quantities",
        "result_what": "Each quantity is a count of the instances and routes stored on this assembly.",
        "limits": [
            "There are no prices and no suppliers.",
            "An actuator record is not an extra line. A modeled cylinder is already a part.",
            "payload_kg is a load on a part, not another item.",
        ],
        "assumptions": [
            "One row is one part name, or one catalog size and length, at one material. A different spelling or a handbook alias of that material stays on the same row.",
            "Two instances of the same part add up. Two screws of the same size and length add up.",
            "A catalog screw, nut, or washer is a fastener. A bearing, seal, motor, or gearbox is a standard part.",
            "A hose, a cable, or a tendon is a route on the assembly. It is not a solid.",
        ],
    }
    mass = {
        "result_kind": ROUGH_ESTIMATE,
        "result_label": "Rough estimate",
        "result_name": "Mass",
        "result_what": (
            "A mass is a published catalog mass when the part has one. "
            "Otherwise it is volume times the handbook density. It is not a weighed mass."
        ),
        "limits": [
            "A material with no handbook density leaves a volume-based mass blank.",
            "A motor or gearbox with no published mass is left blank. The envelope is not that mass.",
            "A hose, a cable, or a tendon is a route, so it has no mass here.",
        ],
        "assumptions": [
            "A published catalog mass does not follow the instance material.",
            "A part with no published mass uses volume times the handbook density of the instance material.",
        ],
    }
    blocks = [quantities, mass]
    return finish(
        {
            "assembly": spec.get("name"),
            "units": "mm",
            "rows": rows,
            "line_count": len(rows),
            "quantity_total": sum(int(row["quantity"]) for row in rows),
            "categories": categories,
            "assumptions": list(ASSUMPTIONS),
            "omits": list(OMITS),
            "note": (
                "This is a count of the parts, catalog fasteners, and other items in the assembly. "
                "It is not a purchase order and it has no prices."
            ),
        },
        kind=weakest([block["result_kind"] for block in blocks]),
        name="Bill of materials",
        what=(
            "The quantities are a count of this assembly. "
            "A mass is a published catalog mass when the part has one, and otherwise volume times a handbook density. "
            "There are no prices."
        ),
        limits=list(OMITS),
        results=blocks,
    )


def write_csv(report: dict, path: Path) -> None:
    _prepare(path)
    fields = [
        "item",
        "quantity",
        "category",
        "name",
        "description",
        "part",
        "size",
        "length_mm",
        "standard",
        "material",
        "mass_estimate_kg",
        "instances",
        "note",
    ]
    # utf-8-sig so Excel on Windows reads a name such as 供水 as itself.
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in report["rows"]:
        written = {key: _csv_cell(value) for key, value in row.items()}
        written["instances"] = _csv_cell(";".join(str(item) for item in (row.get("instances") or [])))
        writer.writerow(written)
    from aicad.paths import publish_bytes

    try:
        publish_bytes(Path(path), buffer.getvalue().encode("utf-8-sig"))
    except OSError as exc:
        raise GeometryError(f"Could not write {path}. {exc.strerror or exc}.") from exc


def render_table(report: dict, path: Path) -> dict:
    """A plain table the assistant can look at. The CSV is the file to keep."""
    rows = report["rows"]
    preview = " ".join(
        str(row.get(key) or "")
        for row in rows
        for key in ("name", "description", "category", "material")
    )
    preview += " " + str(report.get("result_what") or "") + " " + str(report.get("assembly") or "")
    font = _font_for(15, preview)
    small = _font_for(13, preview)
    title_font = _font_for(18, str(report.get("assembly") or ""))
    line_h = 26
    width = 1100
    ink = (28, 32, 36)
    muted = (90, 86, 78)
    caption = _wrap(str(report.get("result_what") or report.get("note") or ""), small, width - 32)
    top = 36 + 18 * max(len(caption), 1) + 8
    height = top + 28 + line_h * (len(rows) + 1) + 24
    image = Image.new("RGB", (width, max(height, 160)), (248, 246, 240))
    draw = ImageDraw.Draw(image)
    from aicad.labels import plain_line

    draw.text((16, 12), plain_line(f"{report.get('assembly') or ''}  bill of materials"), fill=ink, font=title_font)
    for index, line in enumerate(caption):
        draw.text((16, 36 + index * 18), line, fill=muted, font=small)
    headers = ("item", "qty", "category", "name", "line kg", "description")
    starts = (16, 70, 120, 230, 420, 520)
    limits = (48, 44, 100, 180, 90, 540)
    for header, start in zip(headers, starts):
        draw.text((start, top), header, fill=ink, font=font)
    draw.line((12, top + 22, width - 12, top + 22), fill=ink, width=1)
    for index, row in enumerate(rows):
        y = top + 28 + index * line_h
        if index % 2 == 0:
            draw.rectangle((12, y - 4, width - 12, y + line_h - 6), fill=(238, 234, 224))
        mass = row.get("mass_estimate_kg")
        values = (
            str(row["item"]),
            str(row["quantity"]),
            row["category"],
            row["name"],
            "—" if mass is None else f"{mass:.3f}",
            row["description"],
        )
        for value, start, limit in zip(values, starts, limits):
            draw.text((start, y), _fit(plain_line(str(value)), font, limit), fill=ink, font=font)
    _save_png(path, image)
    return {"name": "bom", "path": str(path.resolve()), "width": image.width, "height": image.height}


def _material_identity(material: str) -> tuple[str, str]:
    """Return ``(group key, label)`` for one instance material.

    A handbook name uses the handbook id, so ``Steel`` and ``mild steel``
    share ``steel``. An unknown name keeps the first spelling and groups by
    case-folded text, so ``Oak`` and ``oak`` share a row.
    """
    text = str(material or "").strip()
    try:
        found = lookup_material(text)
    except GeometryError:
        return text.casefold(), text
    canonical = str(found["id"])
    return canonical, canonical


def _part_row(part: str, source: str, material: str, shape: kernel.Shape | None) -> dict:
    mass, mass_note = _mass(shape, material, 1)
    return {
        "category": "part",
        "name": part,
        "description": f"Modeled part {part}.",
        "part": part,
        "size": None,
        "length_mm": None,
        "standard": None,
        "material": material,
        "source": source,
        "mass_estimate_kg": mass,
        "note": mass_note,
    }


def _catalog_row(part: str, spec: dict, material: str, shape: kernel.Shape | None) -> dict:
    kind = library._canonical_kind(str(spec.get("kind") or ""))  # noqa: SLF001
    size = library._canonical_size(kind, str(spec.get("size") or ""))  # noqa: SLF001
    length = spec.get("length")
    length_mm = None if length is None else float(length)
    meta = library.metadata(kind, size, length_mm)
    standard = str(meta.get("standard") or "") or None
    category = "fastener" if kind in _FASTENERS else "standard"
    description = str(meta.get("description") or size)
    if length_mm is not None and kind == "socket_head_cap_screw":
        description += f" x {length_mm:g} mm"
    if meta.get("mass_estimate_kg") is not None:
        mass = round(float(meta["mass_estimate_kg"]), 4)
        mass_note = str(meta.get("mass_note") or "Catalog mass.")
        basis = "catalog"
    elif meta.get("mass_from_volume", True):
        mass, mass_note = _mass(shape, material, 1)
        basis = "volume"
    else:
        mass = None
        mass_note = str(meta.get("mass_note") or "No published mass. The envelope is not used as a mass.")
        basis = "none"
    rating = _rating_note(meta.get("load_ratings"))
    if rating:
        mass_note = f"{mass_note} {rating}".strip()
    name = size if length_mm is None else f"{size} x {length_mm:g}"
    return {
        "category": category,
        "name": name,
        "description": description,
        "part": part,
        "size": size,
        "length_mm": length_mm,
        "standard": standard,
        "material": material,
        "source": "library",
        "mass_estimate_kg": mass,
        "note": mass_note,
        "mass_basis": basis,
    }


def _rating_note(ratings: object) -> str:
    if not isinstance(ratings, dict):
        return ""
    bits = []
    if ratings.get("dynamic_n") is not None:
        bits.append(f"dynamic {ratings['dynamic_n']} N")
    if ratings.get("static_n") is not None:
        bits.append(f"static {ratings['static_n']} N")
    if ratings.get("continuous_shaft_torque_nm") is not None:
        bits.append(f"continuous shaft torque {ratings['continuous_shaft_torque_nm']} N·m")
    if ratings.get("permissible_torque_nm") is not None:
        bits.append(f"permissible torque {ratings['permissible_torque_nm']} N·m")
    if not bits:
        return ""
    source = str(ratings.get("source") or "").strip()
    listed = ", ".join(bits)
    return f"Load rating: {listed}" + (f" ({source})." if source else ".")


def _route_row(cable: dict, index: int) -> dict:
    if not isinstance(cable, dict):
        raise GeometryError(f"Cable {index} must be an object.")
    name = str(cable.get("name") or f"cable_{index + 1}")
    kind = str(cable.get("kind") or ("tendon" if cable.get("points") else "cable"))
    if kind not in ("cable", "hose", "tendon"):
        raise GeometryError(f"{name}: kind must be cable, hose, or tendon.")
    try:
        diameter = float(cable.get("diameter_mm") or cable.get("diameter") or 6.0)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{name}: diameter_mm must be a number.") from exc
    if kind == "hose":
        label = "Hose"
    elif kind == "tendon" or cable.get("points"):
        label = "Tendon"
        kind = "tendon"
    else:
        label = "Cable"
    note = (
        "No mass. The route is anchors, pulleys, and guides. Cut length is the cable check, not this list."
        if cable.get("points")
        else "No mass. The route is a polyline. Cut length is the cable check, not this list."
    )
    material = None
    if isinstance(cable.get("material"), str) and cable.get("material").strip():
        material = cable["material"].strip()
    return {
        "category": kind,
        "name": name,
        "description": f"{label} {name}, {diameter:g} mm diameter. A route, not a solid.",
        "part": None,
        "size": None,
        "length_mm": None,
        "standard": None,
        "material": material,
        "source": "route",
        "mass_estimate_kg": None,
        "quantity": 1,
        "instances": [],
        "note": note,
    }


def _mass(shape: kernel.Shape | None, material: str, quantity: int) -> tuple[float | None, str]:
    if shape is None:
        return None, "No solid, so there is no mass estimate."
    try:
        density = float(lookup_material(material)["density_g_cm3"])
    except GeometryError:
        return None, f"No handbook density for material {material!r}, so mass is left blank."
    volume = kernel.measure(shape).volume_mm3
    kilograms = volume * density / 1_000_000.0 * quantity
    return round(kilograms, 4), "Mass is volume times the handbook density. It is not a weighed mass."


def _fit(text: str, font, width: float) -> str:
    if _length(font, text) <= width:
        return text
    trimmed = text
    while trimmed and _length(font, trimmed + "…") > width:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else "…"


def _wrap(text: str, font, width: float) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines = []
    current = words[0]
    for word in words[1:]:
        trial = current + " " + word
        if _length(font, trial) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _length(font, text: str) -> float:
    if hasattr(font, "getlength"):
        return float(font.getlength(text))
    return float(len(text) * 8)


# Latin faces first, so an English list keeps one of those. A name such as 供水
# is drawn with a later face that actually contains the character, when the
# machine has one. This does not add a font package.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyh.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)


def _font_for(size: int, text: str = ""):
    """A face that can draw ``text``. Latin text stays on the first face found."""
    from PIL import ImageFont

    chosen = None
    for path in _FONT_CANDIDATES:
        if not Path(path).is_file():
            continue
        try:
            font = ImageFont.truetype(path, size=size)
        except OSError:
            continue
        if chosen is None:
            chosen = font
        if _covers(font, text):
            return font
    if chosen is not None:
        return chosen
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _covers(font, text: str) -> bool:
    """False when a character would be drawn as the font's missing-glyph box."""
    if not text:
        return True
    try:
        missing_box = font.getbbox("\ue000")
        missing_len = float(font.getlength("\ue000"))
    except (OSError, AttributeError, ValueError):
        return True
    for char in set(text):
        if ord(char) < 128 or char.isspace():
            continue
        try:
            same_box = font.getbbox(char) == missing_box
            same_len = abs(float(font.getlength(char)) - missing_len) < 0.1
        except (OSError, AttributeError, ValueError):
            return False
        if same_box and same_len:
            return False
    return True


def _csv_cell(value: object) -> object:
    """A text cell stays text. A number stays a number.

    A line break, a line separator, or a paragraph separator becomes a space
    so the cell stays on one row. A cell that then looks like a formula is
    stored as text.
    """
    if not isinstance(value, str) or value == "":
        return value
    import unicodedata

    flat = []
    for char in value:
        if char in "\r\n" or unicodedata.category(char) in {"Zl", "Zp"}:
            flat.append(" ")
        elif unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char != "\t":
            continue
        else:
            flat.append(char)
    text = "".join(flat)
    if text.lstrip(" \t")[:1] in "=+-@":
        return "'" + text
    return text


def _prepare(path: Path) -> None:
    from aicad.paths import ensure_real_directory

    try:
        ensure_real_directory(path.parent)
    except OSError as exc:
        raise GeometryError(f"Could not create the folder for {path}. {exc.strerror or exc}.") from exc


def _save_png(path: Path, image: Image.Image) -> None:
    from aicad.paths import publish_file

    def write(temporary: Path) -> None:
        image.save(temporary, format="PNG", optimize=True)

    try:
        publish_file(path, write)
    except OSError as exc:
        raise GeometryError(f"Could not write {path}. {exc.strerror or exc}.") from exc
