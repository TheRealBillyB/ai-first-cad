"""Print orientation, overhangs, and a slicer estimate.

The build direction is +Z after the part is turned. A triangle needs support
when it faces down more steeply than the threshold, and when it is not the
face sitting on the bed. A slicer is optional: if OrcaSlicer or PrusaSlicer
is not on PATH, the report says so and the other checks still stand.
"""

from __future__ import annotations

import math
import re
import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np

from aicad import kernel
from aicad.errors import GeometryError
from aicad.honesty import ROUGH_ESTIMATE, finish
from aicad.render import render_scalar_views

# Build volumes in millimeters, X Y Z, from the maker's published numbers.
PRINTERS = {
    "bambu_a1": {"name": "Bambu Lab A1", "size_mm": [256, 256, 256]},
    "bambu_a1_mini": {"name": "Bambu Lab A1 mini", "size_mm": [180, 180, 180]},
    "bambu_p1": {"name": "Bambu Lab P1P / P1S", "size_mm": [256, 256, 256]},
    "bambu_x1": {"name": "Bambu Lab X1 / X1C", "size_mm": [256, 256, 256]},
    "prusa_mk4": {"name": "Prusa MK4", "size_mm": [250, 210, 220]},
    "ender_3": {"name": "Creality Ender 3", "size_mm": [220, 220, 250]},
}
_PRINTER_ALIASES = {
    "a1": "bambu_a1",
    "bambu a1": "bambu_a1",
    "bambu_lab_a1": "bambu_a1",
    "a1_mini": "bambu_a1_mini",
    "a1 mini": "bambu_a1_mini",
    "p1": "bambu_p1",
    "p1s": "bambu_p1",
    "p1p": "bambu_p1",
    "x1": "bambu_x1",
    "x1c": "bambu_x1",
    "mk4": "prusa_mk4",
    "prusa": "prusa_mk4",
    "ender": "ender_3",
    "ender3": "ender_3",
    "ender 3": "ender_3",
}
# Local axis that should point up (+Z) after the turn.
_ORIENTATIONS = {
    "z_up": ((0.0, 0.0, 1.0), "Modeled +Z stays up."),
    "z_down": ((0.0, 0.0, -1.0), "Flip it so modeled −Z is up."),
    "x_up": ((1.0, 0.0, 0.0), "Turn it so modeled +X is up."),
    "x_down": ((-1.0, 0.0, 0.0), "Turn it so modeled −X is up."),
    "y_up": ((0.0, 1.0, 0.0), "Turn it so modeled +Y is up."),
    "y_down": ((0.0, -1.0, 0.0), "Turn it so modeled −Y is up."),
}
_SLICERS = ("orca-slicer", "orcaslicer", "OrcaSlicer", "prusa-slicer", "prusaslicer", "PrusaSlicer")
_TIME = re.compile(r"estimated printing time[^=]*=\s*([^\n]+)", re.IGNORECASE)
_GRAMS = re.compile(r"filament used \[g\]\s*=\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_COST = re.compile(r"(?:total )?filament cost\s*=\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)


def list_printers() -> dict:
    return {
        "printers": [
            {"id": key, "name": value["name"], "size_mm": value["size_mm"]}
            for key, value in PRINTERS.items()
        ],
        "orientations": [
            {"id": key, "note": note} for key, (_axis, note) in _ORIENTATIONS.items()
        ],
        "note": (
            "Sizes are the published build volumes in millimeters. "
            "A part may be turned in the plane of the bed. Height is the oriented Z."
        ),
    }


def lookup_printer(name: str) -> dict:
    if not isinstance(name, str) or not name.strip():
        known = ", ".join(PRINTERS)
        raise GeometryError(f"Name a printer. Known printers: {known}.")
    key = name.strip().lower().replace("-", "_")
    key = _PRINTER_ALIASES.get(key, key)
    if key not in PRINTERS:
        known = ", ".join(PRINTERS)
        raise GeometryError(f"No printer {name!r}. Known printers: {known}.")
    return {"id": key, **PRINTERS[key]}


def analyze_print(
    shape: kernel.Shape,
    setup: dict | None,
    out_dir: Path | None = None,
    part_name: str | None = None,
) -> dict:
    """Overhang map, a suggested orientation, printer fit, and an optional slice."""
    setup = setup or {}
    if not isinstance(setup, dict):
        raise GeometryError("A print check is an object with an orientation and a printer.")
    threshold = float(setup.get("threshold_deg") if setup.get("threshold_deg") is not None else 45.0)
    if not 0 < threshold < 90:
        raise GeometryError("threshold_deg is how far from vertical a face may lean before it needs support, between 0 and 90.")
    mesh = kernel.tessellate(shape)
    scores = [_score(mesh, name, threshold) for name in _ORIENTATIONS]
    requested = str(setup.get("orientation") or "best").lower().replace("-", "_").replace(" ", "_")
    if requested in ("best", "auto", ""):
        chosen = min(scores, key=lambda row: (row["support_area_mm2"], row["height_mm"]))
        chosen_name = chosen["orientation"]
        automatic = True
    elif requested in _ORIENTATIONS:
        chosen_name = requested
        chosen = next(row for row in scores if row["orientation"] == requested)
        automatic = False
    else:
        known = ", ".join(("best", *_ORIENTATIONS))
        raise GeometryError(f"Orientation {requested!r} is unknown. Use {known}.")
    printers = _printer_rows(chosen)
    wanted = setup.get("printer")
    focus = None
    if wanted:
        printer = lookup_printer(str(wanted))
        focus = next(row for row in printers if row["id"] == printer["id"])
    views = []
    if out_dir is not None and setup.get("render", True):
        views = _render(mesh, chosen_name, threshold, Path(out_dir), part_name or "part")
    slicer = {"status": "not_requested"}
    if setup.get("slice"):
        slicer = slice_mesh(
            _oriented_arrays(mesh, chosen_name)[0],
            _oriented_arrays(mesh, chosen_name)[1],
            Path(out_dir or "."),
            price_per_kg=setup.get("price_per_kg"),
        )
    status = "pass"
    if focus and not focus["fits"]:
        status = "fail"
    elif chosen["support_fraction"] > 0.25:
        status = "review"
    report = {
        "part": part_name,
        "status": status,
        "orientation": chosen_name,
        "orientation_automatic": automatic,
        "orientation_note": _ORIENTATIONS[chosen_name][1],
        "threshold_deg": threshold,
        "support_area_mm2": chosen["support_area_mm2"],
        "support_fraction": chosen["support_fraction"],
        "height_mm": chosen["height_mm"],
        "footprint_mm": chosen["footprint_mm"],
        "size_mm": chosen["size_mm"],
        "orientations": scores,
        "printers": printers,
        "printer": focus,
        "slicer": slicer,
        "views": views,
        "assumptions": _assumptions(threshold),
        "note": _headline(chosen, automatic, focus, slicer),
    }
    return finish(
        report,
        kind=ROUGH_ESTIMATE,
        name="Print",
        what="Overhang, support area, and printer fit are rough estimates. A slicer time, when present, uses that slicer's default profile.",
        limits=[
            "Only the six axis-up orientations are tried. A diagonal pose is not searched.",
            "The bed face, within 0.4 mm of the lowest point, is not counted as support.",
            "Time and grams follow the slicer's default profile. A missing slicer skips those numbers.",
        ],
    )


def orientation_size(shape: kernel.Shape, orientation: str) -> dict:
    """Oriented size without pictures. Used by the printer requirement."""
    name = str(orientation or "z_up").lower()
    if name in ("best", "auto"):
        report = analyze_print(shape, {"orientation": "best", "render": False, "slice": False})
        return {"orientation": report["orientation"], "size_mm": report["size_mm"], "height_mm": report["height_mm"]}
    if name not in _ORIENTATIONS:
        known = ", ".join(_ORIENTATIONS)
        raise GeometryError(f"Orientation {orientation!r} is unknown. Use {known}, or best.")
    mesh = kernel.tessellate(shape)
    scored = _score(mesh, name, 45.0)
    return {"orientation": name, "size_mm": scored["size_mm"], "height_mm": scored["height_mm"]}


def fits_printer(size_mm: list[float], printer_name: str) -> dict:
    printer = lookup_printer(printer_name)
    bed = printer["size_mm"]
    fits = _fits(size_mm, bed)
    return {
        "id": printer["id"],
        "name": printer["name"],
        "bed_mm": bed,
        "part_mm": [round(float(value), 3) for value in size_mm],
        "fits": fits,
    }


def slice_mesh(vertices: np.ndarray, triangles: np.ndarray, out_dir: Path, price_per_kg=None) -> dict:
    executables = find_slicers()
    if not executables:
        return {
            "status": "skipped",
            "slicer": None,
            "note": (
                "OrcaSlicer and PrusaSlicer are not on PATH. "
                "Install one and the same check will report time and filament. "
                "The overhang map and the printer fit do not need a slicer."
            ),
            "assumptions": ["No slicer ran. Time and grams are not estimated."],
        }
    out_dir.mkdir(parents=True, exist_ok=True)
    stl = out_dir / "oriented.stl"
    gcode = out_dir / "print.gcode"
    _write_stl(vertices, triangles, stl)
    last = ""
    last_name = Path(executables[-1]).name
    for executable in executables:
        last_name = Path(executable).name
        commands = [
            _slice_command(executable, gcode, stl, price_per_kg, export=True),
            _slice_command(executable, gcode, stl, price_per_kg, export=False),
        ]
        for command in commands:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
            last = ((completed.stdout or "") + "\n" + (completed.stderr or ""))[-800:]
            if gcode.is_file() and gcode.stat().st_size > 0:
                parsed = parse_gcode(gcode.read_text(encoding="utf-8", errors="replace"))
                parsed["status"] = "sliced" if parsed.get("grams") is not None or parsed.get("seconds") is not None else "unparsed"
                parsed["slicer"] = Path(executable).name
                parsed["gcode"] = str(gcode.resolve())
                parsed["note"] = _slicer_note(parsed, price_per_kg)
                if parsed.get("grams") and price_per_kg is not None and not parsed.get("cost"):
                    parsed["cost"] = round(float(parsed["grams"]) * float(price_per_kg) / 1000.0, 2)
                    parsed["cost_source"] = "price_per_kg"
                parsed["assumptions"] = [
                    f"Sliced by {parsed['slicer']} with its default profile. Nozzle, layer height, and infill are whatever that profile says.",
                    "Filament density 1.24 g/cm³ (PLA) is passed so a profile with no density does not report 0 g.",
                    "Time and grams come from the gcode comments. A different profile changes both.",
                    "Cost is the slicer's filament cost when the gcode has one, otherwise grams times price_per_kg / 1000.",
                ]
                return parsed
            if gcode.is_file():
                gcode.unlink()
    return {
        "status": "failed",
        "slicer": last_name,
        "note": "The slicer ran and did not write gcode. " + " ".join(last.split()),
        "assumptions": ["The slicer is installed, but this headless command did not produce a toolpath."],
    }


def find_slicer() -> str | None:
    found = find_slicers()
    return found[0] if found else None


def find_slicers() -> list[str]:
    found = []
    for name in _SLICERS:
        path = shutil.which(name)
        if path and path not in found:
            found.append(path)
    return found


def parse_gcode(text: str) -> dict:
    """Read the time, grams, and cost comments PrusaSlicer and OrcaSlicer write.

    Those comments are sometimes in the header and sometimes at the end of the file.
    """
    time_match = _TIME.search(text)
    gram_match = _GRAMS.search(text)
    cost_match = _COST.search(text)
    seconds = _parse_duration(time_match.group(1)) if time_match else None
    grams = float(gram_match.group(1)) if gram_match else None
    cost = float(cost_match.group(1)) if cost_match else None
    return {
        "seconds": seconds,
        "time": None if time_match is None else time_match.group(1).strip(),
        "grams": grams,
        "cost": cost,
        "cost_source": None if cost is None else "slicer",
    }


def _score(mesh, name: str, threshold: float) -> dict:
    vertices, triangles, normals = _oriented_arrays(mesh, name)
    areas = _areas(vertices, triangles)
    centroids = vertices[triangles].mean(axis=1)
    min_z = float(vertices[:, 2].min()) if len(vertices) else 0.0
    on_bed = centroids[:, 2] <= min_z + 0.4
    limit = -math.cos(math.radians(threshold))
    overhang = (normals[:, 2] < limit) & ~on_bed
    total = float(areas.sum()) or 1.0
    support = float(areas[overhang].sum())
    size = vertices.max(axis=0) - vertices.min(axis=0) if len(vertices) else np.zeros(3)
    return {
        "orientation": name,
        "note": _ORIENTATIONS[name][1],
        "support_area_mm2": round(support, 2),
        "support_fraction": round(support / total, 4),
        "height_mm": round(float(size[2]), 3),
        "footprint_mm": [round(float(size[0]), 3), round(float(size[1]), 3)],
        "size_mm": [round(float(value), 3) for value in size],
    }


def _oriented_arrays(mesh, name: str):
    rotation = _rotation_to_up(_ORIENTATIONS[name][0])
    vertices = mesh.vertices @ rotation.T
    vertices = vertices - np.array([vertices[:, 0].min(), vertices[:, 1].min(), vertices[:, 2].min()])
    corners = vertices[mesh.triangles]
    raw = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    length = np.linalg.norm(raw, axis=1, keepdims=True)
    normals = raw / np.clip(length, 1e-12, None)
    return vertices, mesh.triangles, normals


def _rotation_to_up(axis: tuple[float, float, float]) -> np.ndarray:
    source = np.array(axis, dtype=np.float64)
    target = np.array([0.0, 0.0, 1.0])
    cross = np.cross(source, target)
    dot = float(np.clip(np.dot(source, target), -1.0, 1.0))
    if np.linalg.norm(cross) < 1e-9:
        if dot > 0:
            return np.eye(3)
        return np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]])
    skew = np.array(
        [[0.0, -cross[2], cross[1]], [cross[2], 0.0, -cross[0]], [-cross[1], cross[0], 0.0]]
    )
    return np.eye(3) + skew + skew @ skew * (1.0 / (1.0 + dot))


def _areas(vertices: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    corners = vertices[triangles]
    return 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1)


def _printer_rows(chosen: dict) -> list[dict]:
    rows = []
    for key, printer in PRINTERS.items():
        fits = _fits(chosen["size_mm"], printer["size_mm"])
        rows.append(
            {
                "id": key,
                "name": printer["name"],
                "bed_mm": printer["size_mm"],
                "fits": fits,
                "note": "Fits in this orientation, turning in the plane if that helps." if fits else "Taller or wider than this bed in this orientation.",
            }
        )
    return rows


def _fits(size_mm, bed) -> bool:
    x, y, z = (float(value) for value in size_mm)
    bx, by, bz = (float(value) for value in bed)
    if z > bz + 1e-6:
        return False
    return (x <= bx + 1e-6 and y <= by + 1e-6) or (x <= by + 1e-6 and y <= bx + 1e-6)


def _render(mesh, name: str, threshold: float, out_dir: Path, part_name: str):
    vertices, triangles, normals = _oriented_arrays(mesh, name)
    angle = np.degrees(np.arccos(np.clip(normals[:, 2], -1.0, 1.0))) - 90.0
    angle = np.clip(angle, 0.0, 90.0)
    centroids = vertices[triangles].mean(axis=1)
    on_bed = centroids[:, 2] <= float(vertices[:, 2].min()) + 0.4
    angle = angle.copy()
    angle[on_bed] = 0.0
    # Vertex color is the mean overhang of the triangles that meet there.
    scalars = np.zeros(len(vertices))
    counts = np.zeros(len(vertices))
    for triangle, value in zip(triangles, angle):
        for node in triangle:
            scalars[int(node)] += float(value)
            counts[int(node)] += 1
    scalars = scalars / np.clip(counts, 1.0, None)
    views = render_scalar_views(
        vertices,
        triangles,
        scalars,
        out_dir,
        part_name,
        "overhang",
        f"Overhang  ·  {name}  ·  support past {threshold:g}°",
        "deg",
        "degrees from vertical",
    )
    return [view.to_dict() for view in views]


def _write_stl(vertices: np.ndarray, triangles: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(b"aicad oriented" + b"\0" * 66)
        handle.write(struct.pack("<I", len(triangles)))
        for triangle in triangles:
            a, b, c = vertices[int(triangle[0])], vertices[int(triangle[1])], vertices[int(triangle[2])]
            normal = np.cross(b - a, c - a)
            length = float(np.linalg.norm(normal))
            normal = normal / length if length > 1e-12 else np.array([0.0, 0.0, 1.0])
            handle.write(struct.pack("<12fH", *normal, *a, *b, *c, 0))


def _parse_duration(text: str) -> int | None:
    total = 0
    found = False
    for value, unit in re.findall(r"(\d+)\s*([dhms])", text.lower()):
        found = True
        number = int(value)
        total += {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit] * number
    return total if found else None


def _slice_command(executable: str, gcode: Path, stl: Path, price_per_kg, export: bool) -> list[str]:
    command = [executable]
    if export:
        command.extend(["--export-gcode", "--output", str(gcode)])
    else:
        command.extend(["--slice", "0", "--export-gcode", str(gcode)])
    command.extend(["--filament-density", "1.24"])
    if price_per_kg is not None:
        command.extend(["--filament-cost", str(price_per_kg)])
    command.append(str(stl))
    return command


def _slicer_note(parsed: dict, price_per_kg) -> str:
    bits = [f"Sliced with {parsed.get('slicer')}."]
    if parsed.get("time"):
        bits.append(f"Estimated time {parsed['time']}.")
    if parsed.get("grams") is not None:
        bits.append(f"Filament {parsed['grams']:.2f} g.")
    if parsed.get("cost") is not None:
        bits.append(f"Cost {parsed['cost']}.")
    elif price_per_kg is None:
        bits.append("No filament price was in the gcode. Pass price_per_kg to estimate a cost.")
    return " ".join(bits)


def _assumptions(threshold: float) -> list[str]:
    return [
        f"Support is required when a triangle leans more than {threshold:g}° from vertical. Straight down is 90°. A vertical wall is 0°.",
        "The face on the bed is not counted as support. Anything within 0.4 mm of the lowest point is treated as sitting on the plate.",
        "The six orientations put one modeled axis up. They do not try a diagonal pose.",
        "Best means the smallest support area, and the shorter part when two poses tie.",
        "A printer fits when the oriented height is inside the bed height and the footprint fits, swapping X and Y if that helps.",
        "Printer sizes are the published volumes: A1 and P1 and X1 are 256 mm cubes, A1 mini is 180, MK4 is 250×210×220, Ender 3 is 220×220×250.",
        "The slicer, when it is installed, uses its own default profile for layer height and infill. Filament density is passed as 1.24 g/cm³ so the gram comment is not zero.",
    ]


def _headline(chosen: dict, automatic: bool, focus: dict | None, slicer: dict) -> str:
    lead = (
        f"Suggested orientation {chosen['orientation']}"
        if automatic
        else f"Orientation {chosen['orientation']}"
    )
    text = (
        f"{lead}. Support area {chosen['support_area_mm2']:.0f} mm² "
        f"({chosen['support_fraction']:.0%} of the surface). Height {chosen['height_mm']:.1f} mm."
    )
    if focus:
        text += f" {focus['name']} {'fits' if focus['fits'] else 'does not fit'}."
    if slicer.get("status") == "skipped":
        text += " No slicer is installed, so time and grams were not estimated."
    elif slicer.get("status") == "sliced":
        text += f" Slicer: {slicer.get('time') or 'time unread'}, {slicer.get('grams')} g."
    return text
