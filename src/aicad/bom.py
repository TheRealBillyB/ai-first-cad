"""A count of what an assembly contains.

Rows are modeled parts, catalog fasteners, other catalog parts such as
bearings, and hoses or cables. Quantities come from the instances and the
routes. There is no price and no supplier. A mass, when it is present, is
the solid's volume times the handbook density, and the row says so.
"""

from __future__ import annotations

import csv
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
    "One row is one part name, or one catalog size and length, at one material.",
    "Two instances of the same part add up. Two screws of the same size and length add up.",
    "A catalog screw, nut, or washer is a fastener. A catalog bearing is a standard part.",
    "A hose or a cable is a route on the assembly. It is not a solid, so it has no mass here.",
    "Mass is volume times the handbook density of the instance material. It is an estimate.",
]
_FASTENERS = {"socket_head_cap_screw", "hex_nut", "plain_washer"}
_STANDARDS = {
    "socket_head_cap_screw": "ISO 4762",
    "hex_nut": "ISO 4032",
    "plain_washer": "ISO 7089",
    "ball_bearing": "SKT",
}
_ORDER = {"part": 0, "fastener": 1, "standard": 2, "hose": 3, "cable": 4, "other": 5}


def build_bom(spec: dict, shapes: dict[str, kernel.Shape], records: dict[str, dict]) -> dict:
    """Count the assembly. ``records`` maps a part name to source and library spec."""
    grouped: dict[tuple, dict] = {}
    for instance in spec["instances"]:
        part = instance["part"]
        record = records.get(part) or {}
        library_spec = record.get("library")
        material = str(instance.get("material") or "steel")
        if library_spec:
            row = _catalog_row(part, library_spec, material, shapes.get(part))
        else:
            row = _part_row(part, str(record.get("source") or "part"), material, shapes.get(part))
        # Catalog rows group by what you would order. Modeled parts group by name.
        if library_spec:
            key = (
                row["category"],
                row["size"] or "",
                row["length_mm"] if row["length_mm"] is not None else "",
                row["standard"] or "",
                row["material"] or "",
            )
        else:
            key = ("part", row["part"], row["material"] or "")
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
        if row.get("mass_estimate_kg") is not None:
            row["note"] = (
                "Mass is the total for this line: each solid's volume times the handbook density. "
                "It is not a weighed mass."
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
            "One row is one part name, or one catalog size and length, at one material.",
            "Two instances of the same part add up. Two screws of the same size and length add up.",
            "A catalog screw, nut, or washer is a fastener. A catalog bearing is a standard part.",
            "A hose or a cable is a route on the assembly. It is not a solid.",
        ],
    }
    mass = {
        "result_kind": ROUGH_ESTIMATE,
        "result_label": "Rough estimate",
        "result_name": "Mass",
        "result_what": "A mass is the solid's volume times the handbook density. It is not a weighed mass.",
        "limits": [
            "A material with no handbook density leaves the mass blank.",
            "A hose or a cable is a route, so it has no mass here.",
        ],
        "assumptions": [
            "Mass is volume times the handbook density of the instance material. It is an estimate.",
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
            "A mass, when a number is shown, is volume times a handbook density. "
            "There are no prices."
        ),
        limits=list(OMITS),
        results=blocks,
    )


def write_csv(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in report["rows"]:
            written = dict(row)
            written["instances"] = ";".join(row.get("instances") or [])
            writer.writerow(written)


def render_table(report: dict, path: Path) -> dict:
    """A plain table the assistant can look at. The CSV is the file to keep."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = report["rows"]
    font = _font(15)
    small = _font(13)
    title_font = _font(18)
    line_h = 26
    width = 1100
    height = 78 + line_h * (len(rows) + 1) + 36
    image = Image.new("RGB", (width, height), (248, 246, 240))
    draw = ImageDraw.Draw(image)
    ink = (28, 32, 36)
    muted = (90, 86, 78)
    draw.text((16, 12), f"{report['assembly']}  bill of materials", fill=ink, font=title_font)
    draw.text((16, 38), str(report.get("result_what") or report.get("note") or ""), fill=muted, font=small)
    headers = ("item", "qty", "category", "name", "line kg", "description")
    starts = (16, 70, 120, 230, 420, 520)
    top = 68
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
            _clip(row["description"], 62),
        )
        for value, start in zip(values, starts):
            draw.text((start, y), value, fill=ink, font=font)
    image.save(path, format="PNG", optimize=True)
    return {"name": "bom", "path": str(path.resolve()), "width": image.width, "height": image.height}


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
    standard = _STANDARDS.get(kind)
    category = "fastener" if kind in _FASTENERS else "standard"
    noun = {
        "socket_head_cap_screw": "socket head cap screw",
        "hex_nut": "hex nut",
        "plain_washer": "plain washer",
        "ball_bearing": "deep-groove ball bearing",
    }.get(kind, kind.replace("_", " "))
    description = f"{standard + ' ' if standard else ''}{noun} {size}"
    if length_mm is not None:
        description += f" x {length_mm:g} mm"
    mass, mass_note = _mass(shape, material, 1)
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
    }


def _route_row(cable: dict, index: int) -> dict:
    if not isinstance(cable, dict):
        raise GeometryError(f"Cable {index} must be an object.")
    name = str(cable.get("name") or f"cable_{index + 1}")
    kind = str(cable.get("kind") or "cable")
    if kind not in ("cable", "hose"):
        raise GeometryError(f"{name}: kind must be cable or hose.")
    try:
        diameter = float(cable.get("diameter_mm") or cable.get("diameter") or 6.0)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{name}: diameter_mm must be a number.") from exc
    label = "Hose" if kind == "hose" else "Cable"
    return {
        "category": kind,
        "name": name,
        "description": f"{label} {name}, {diameter:g} mm diameter. A route, not a solid.",
        "part": None,
        "size": None,
        "length_mm": None,
        "standard": None,
        "material": None,
        "source": "route",
        "mass_estimate_kg": None,
        "quantity": 1,
        "instances": [],
        "note": "No mass. The route is a polyline. Cut length is the cable check, not this list.",
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


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _font(size: int):
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()
