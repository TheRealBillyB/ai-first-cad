"""Print orientation, overhangs, and a slicer estimate.

The build direction is +Z after the part is turned. A triangle needs support
when it faces down more steeply than the threshold, and when it is not the
face sitting on the bed. A short span held along an edge is a bridge and is
not counted. A slicer is optional: if OrcaSlicer or PrusaSlicer is not on
PATH, the report says so and the other checks still stand.
"""

from __future__ import annotations

import math
import re
import stat
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
# Faces this close to the lowest point are the bed, not an overhang.
_BED_MM = 0.05
# A down-facing span no wider than this, held along an edge, prints as a bridge.
_DEFAULT_BRIDGE_MM = 5.0
# Vertices closer than this are the same point when a mesh splits a shared edge.
_WELD_MM = 0.02
_BRIDGE_PAIR_LIMIT = 4_000_000
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
    bridge_mm = _bridge_span(setup.get("bridge_mm", _DEFAULT_BRIDGE_MM))
    mesh = kernel.tessellate(shape)
    scores = [_score(mesh, name, threshold, bridge_mm) for name in _ORIENTATIONS]
    requested = str(setup.get("orientation") or "best").lower().replace("-", "_").replace(" ", "_")
    wanted = setup.get("printer")
    printer = lookup_printer(str(wanted)) if wanted else None
    if requested in ("best", "auto", ""):
        ranked = scores
        if printer is not None:
            fitting = [row for row in scores if _fits(row["size_mm"], printer["size_mm"])]
            if fitting:
                ranked = fitting
        chosen = min(ranked, key=lambda row: (row["support_area_mm2"], row["height_mm"]))
        chosen_name = chosen["orientation"]
        automatic = True
    elif requested in _ORIENTATIONS:
        chosen_name = requested
        chosen = next(row for row in scores if row["orientation"] == requested)
        automatic = False
    else:
        known = ", ".join(("best", *_ORIENTATIONS))
        raise GeometryError(f"Orientation {requested!r} is unknown. Use {known}.")
    printers = _printer_rows(chosen, scores)
    focus = None
    if printer is not None:
        focus = next(row for row in printers if row["id"] == printer["id"])
    views = []
    if out_dir is not None and setup.get("render", True):
        views = _render(mesh, chosen_name, threshold, bridge_mm, Path(out_dir), part_name or "part")
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
        "bridge_area_mm2": chosen["bridge_area_mm2"],
        "bridge_mm": chosen["bridge_mm"],
        "bed_band_mm": chosen["bed_band_mm"],
        "height_mm": chosen["height_mm"],
        "footprint_mm": chosen["footprint_mm"],
        "size_mm": chosen["size_mm"],
        "orientations": scores,
        "printers": printers,
        "printer": focus,
        "slicer": slicer,
        "views": views,
        "assumptions": _assumptions(threshold, bridge_mm),
        "note": _headline(chosen, automatic, focus, slicer),
    }
    return finish(
        report,
        kind=ROUGH_ESTIMATE,
        name="Print",
        what="Overhang, support area, and printer fit are rough estimates. A slicer time, when present, uses that slicer's default profile.",
        limits=[
            "Only the six axis-up orientations are tried. A diagonal pose is not searched.",
            f"The bed face, within {_BED_MM:g} mm of the lowest point, is not counted as support.",
            (
                "A down-facing span held along an edge is a bridge, not support, when twice "
                f"its farthest distance to that edge is at most {bridge_mm:g} mm. "
                "A face with no path to the bed still counts."
            ),
            "Time and grams follow the slicer's default profile. A missing slicer skips those numbers.",
        ],
    )


def orientation_size(shape: kernel.Shape, orientation: str, printer: str | None = None) -> dict:
    """Oriented size without pictures. Used by the printer requirement.

    ``best`` with a printer prefers a pose that fits that bed.
    """
    name = str(orientation or "z_up").lower().replace("-", "_").replace(" ", "_")
    if name in ("best", "auto"):
        setup: dict = {"orientation": "best", "render": False, "slice": False}
        if printer:
            setup["printer"] = printer
        report = analyze_print(shape, setup)
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


def _real_output_dir(path: Path) -> None:
    """Create ``path``. Do not follow a symlink or a junction to do it."""

    import errno
    import os

    from aicad.paths import is_reparse

    raw = Path(os.path.abspath(path))
    current = Path(raw.anchor)
    for part in raw.relative_to(current).parts:
        current = current / part
        if is_reparse(current) or current.is_symlink():
            raise OSError(errno.ELOOP, f"{path} is a link and was not followed.")
        if current.exists():
            if not current.is_dir():
                raise OSError(errno.ENOTDIR, f"{path} is not a folder.")
            continue
        current.mkdir()


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
    _real_output_dir(out_dir)
    stl = out_dir / "oriented.stl"
    gcode = out_dir / "print.gcode"
    _write_stl(vertices, triangles, stl)
    # A link at this name is removed so the file is created in this folder.
    _drop_link(gcode)
    last = ""
    last_name = Path(executables[-1]).name
    for executable in executables:
        last_name = Path(executable).name
        commands = [
            _slice_command(executable, gcode, stl, price_per_kg, export=True),
            _slice_command(executable, gcode, stl, price_per_kg, export=False),
        ]
        for command in commands:
            from aicad.sandbox import program_environment

            completed = subprocess.run(
                command,
                cwd=str(Path(executable).resolve().parent),
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=180,
                check=False,
                close_fds=True,
                env=program_environment(),
            )
            last = ((completed.stdout or "") + "\n" + (completed.stderr or ""))[-800:]
            from aicad.file_access import read_text

            try:
                gcode_text = read_text(gcode, maximum=64 * 1024 * 1024, errors="replace")
            except OSError:
                gcode_text = ""
            if gcode_text:
                parsed = parse_gcode(gcode_text)
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
    from aicad.prereqs import find_program

    found = []
    for name in _SLICERS:
        path = find_program(name)
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


def _bridge_span(value) -> float:
    try:
        span = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(
            "bridge_mm is the span, in millimeters, that still counts as a bridge. Use 0 to count every overhang."
        ) from exc
    if not math.isfinite(span) or span < 0:
        raise GeometryError(
            "bridge_mm is the span, in millimeters, that still counts as a bridge. Use 0 to count every overhang."
        )
    return span


def _score(mesh, name: str, threshold: float, bridge_mm: float = _DEFAULT_BRIDGE_MM) -> dict:
    vertices, triangles, normals = _oriented_arrays(mesh, name)
    areas, _on_bed, overhang, bridge = _classify(vertices, triangles, normals, threshold, bridge_mm)
    total = float(areas.sum()) or 1.0
    support_mask = overhang & ~bridge
    support = float(areas[support_mask].sum())
    size = vertices.max(axis=0) - vertices.min(axis=0) if len(vertices) else np.zeros(3)
    return {
        "orientation": name,
        "note": _ORIENTATIONS[name][1],
        "support_area_mm2": round(support, 2),
        "support_fraction": round(support / total, 4),
        "bridge_area_mm2": round(float(areas[bridge].sum()), 2),
        "bridge_mm": bridge_mm,
        "bed_band_mm": _BED_MM,
        "height_mm": round(float(size[2]), 3),
        "footprint_mm": [round(float(size[0]), 3), round(float(size[1]), 3)],
        "size_mm": [round(float(value), 3) for value in size],
    }


def _classify(vertices, triangles, normals, threshold: float, bridge_mm: float):
    """Bed faces, down-facing faces, and the down-facing faces that are bridges."""
    areas = _areas(vertices, triangles)
    min_z = float(vertices[:, 2].min()) if len(vertices) else 0.0
    centroids = vertices[triangles].mean(axis=1)
    on_bed = centroids[:, 2] <= min_z + _BED_MM
    # A face that leans phi from vertical has normal z of -sin(phi).
    # Straight down is 90 degrees and a vertical wall is 0.
    limit = -math.sin(math.radians(threshold))
    overhang = (normals[:, 2] < limit) & ~on_bed
    bridge = _bridge_mask(vertices, triangles, overhang, on_bed, bridge_mm)
    return areas, on_bed, overhang, bridge


def _bridge_mask(vertices, triangles, overhang, on_bed, bridge_mm: float) -> np.ndarray:
    """Down-facing triangles that span at most ``bridge_mm`` from a supported edge.

    A patch counts only when it shares an edge with a face that itself reaches
    the bed without crossing a down-facing face. The span is twice the farthest
    sample from that edge, so a tongue held at one end is as long as the tongue.
    """
    count = len(triangles)
    mask = np.zeros(count, dtype=bool)
    if bridge_mm <= 0 or count == 0 or not bool(np.any(overhang)):
        return mask
    verts, faces = _weld_faces(vertices, triangles)
    edge_map = _edge_map(faces)
    grounded = _grounded(faces, edge_map, on_bed, overhang)
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    for members in edge_map.values():
        hanging = [item for item in members if overhang[item]]
        for other in hanging[1:]:
            union(hanging[0], other)
    groups: dict[int, list[int]] = {}
    for index in np.flatnonzero(overhang):
        groups.setdefault(find(int(index)), []).append(int(index))
    for members in groups.values():
        segments = _attachment_segments(verts, faces, edge_map, members, grounded)
        if not segments:
            continue
        points = _span_samples(verts, faces, members)
        if _bridge_span_mm(points, verts, segments) <= bridge_mm + 1e-6:
            mask[members] = True
    return mask


def _weld_faces(vertices, triangles):
    keys = np.round(vertices / _WELD_MM).astype(np.int64)
    mapping: dict[tuple[int, int, int], int] = {}
    remap = np.empty(len(vertices), dtype=np.int32)
    fresh: list[np.ndarray] = []
    for index, key in enumerate(keys):
        token = (int(key[0]), int(key[1]), int(key[2]))
        slot = mapping.get(token)
        if slot is None:
            slot = len(fresh)
            mapping[token] = slot
            fresh.append(vertices[index])
        remap[index] = slot
    return np.asarray(fresh, dtype=np.float64), remap[triangles]


def _edge_key(left: int, right: int) -> tuple[int, int]:
    return (left, right) if left < right else (right, left)


def _edge_map(faces) -> dict[tuple[int, int], list[int]]:
    edges: dict[tuple[int, int], list[int]] = {}
    for index, face in enumerate(faces):
        a, b, c = int(face[0]), int(face[1]), int(face[2])
        if a == b or b == c or c == a:
            continue
        for key in (_edge_key(a, b), _edge_key(b, c), _edge_key(c, a)):
            edges.setdefault(key, []).append(index)
    return edges


def _grounded(faces, edge_map, on_bed, overhang) -> np.ndarray:
    """Non-overhang triangles that touch the bed, or touch one that does."""
    grounded = np.array(on_bed, dtype=bool, copy=True)
    stack = [int(index) for index in np.flatnonzero(grounded)]
    while stack:
        current = stack.pop()
        face = faces[current]
        a, b, c = int(face[0]), int(face[1]), int(face[2])
        if a == b or b == c or c == a:
            continue
        for key in (_edge_key(a, b), _edge_key(b, c), _edge_key(c, a)):
            for other in edge_map.get(key, ()):
                if grounded[other] or overhang[other]:
                    continue
                grounded[other] = True
                stack.append(other)
    return grounded


def _attachment_segments(verts, faces, edge_map, members, grounded) -> list[tuple[int, int]]:
    """Edges where a down-facing patch meets a face that continues below that edge.

    A side wall of the same tongue shares the patch's outline but stands above
    it, so that outline is not a support.
    """
    hanging = set(members)
    found: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for tri in members:
        face = faces[tri]
        a, b, c = int(face[0]), int(face[1]), int(face[2])
        for key in (_edge_key(a, b), _edge_key(b, c), _edge_key(c, a)):
            if key in seen or key[0] == key[1]:
                continue
            midpoint = float(verts[key[0], 2] + verts[key[1], 2]) * 0.5
            for other in edge_map.get(key, ()):
                if not grounded[other] or other in hanging:
                    continue
                centroid = float(verts[faces[other], 2].mean())
                if centroid < midpoint - 1e-3:
                    seen.add(key)
                    found.append(key)
                    break
    return found


def _span_samples(verts, faces, members) -> np.ndarray:
    chunks = [verts[np.unique(faces[members].reshape(-1)), :2]]
    mids = []
    cents = []
    for tri in members:
        a, b, c = (int(value) for value in faces[tri])
        pa, pb, pc = verts[a, :2], verts[b, :2], verts[c, :2]
        mids.append((pa + pb) * 0.5)
        mids.append((pb + pc) * 0.5)
        mids.append((pc + pa) * 0.5)
        cents.append((pa + pb + pc) / 3.0)
    chunks.append(np.asarray(mids, dtype=np.float64))
    chunks.append(np.asarray(cents, dtype=np.float64))
    return np.vstack(chunks)


def _bridge_span_mm(points, verts, segments) -> float:
    origin = np.array([verts[a, :2] for a, _b in segments], dtype=np.float64)
    dest = np.array([verts[b, :2] for _a, b in segments], dtype=np.float64)
    if len(points) * len(origin) > _BRIDGE_PAIR_LIMIT:
        stride = int(math.ceil((len(points) * len(origin)) / _BRIDGE_PAIR_LIMIT))
        points = points[::stride]
    ab = dest - origin
    length2 = np.sum(ab * ab, axis=1)
    zero = length2 < 1e-18
    length2 = np.where(zero, 1.0, length2)
    diff = points[:, None, :] - origin[None, :, :]
    factor = np.clip(np.sum(diff * ab[None, :, :], axis=2) / length2, 0.0, 1.0)
    factor[:, zero] = 0.0
    closest = origin[None, :, :] + factor[:, :, None] * ab[None, :, :]
    distance = np.linalg.norm(points[:, None, :] - closest, axis=2)
    return float(2.0 * distance.min(axis=1).max())


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


def _printer_rows(chosen: dict, scores: list[dict] | None = None) -> list[dict]:
    rows = []
    others = scores or []
    for key, printer in PRINTERS.items():
        fits = _fits(chosen["size_mm"], printer["size_mm"])
        if fits:
            note = "Fits in this orientation, turning in the plane if that helps."
        else:
            alternatives = [
                row["orientation"]
                for row in others
                if row["orientation"] != chosen["orientation"] and _fits(row["size_mm"], printer["size_mm"])
            ]
            if alternatives:
                note = (
                    "Taller or wider than this bed in this orientation. "
                    + ", ".join(alternatives)
                    + " fits this bed."
                )
            elif others:
                note = "Every axis orientation is taller or wider than this bed."
            else:
                note = "Taller or wider than this bed in this orientation."
        rows.append(
            {
                "id": key,
                "name": printer["name"],
                "bed_mm": printer["size_mm"],
                "fits": fits,
                "note": note,
            }
        )
    return rows


def _fits(size_mm, bed) -> bool:
    x, y, z = (float(value) for value in size_mm)
    bx, by, bz = (float(value) for value in bed)
    if z > bz + 1e-6:
        return False
    return (x <= bx + 1e-6 and y <= by + 1e-6) or (x <= by + 1e-6 and y <= bx + 1e-6)


def _render(mesh, name: str, threshold: float, bridge_mm: float, out_dir: Path, part_name: str):
    vertices, triangles, normals = _oriented_arrays(mesh, name)
    angle = np.degrees(np.arccos(np.clip(normals[:, 2], -1.0, 1.0))) - 90.0
    angle = np.clip(angle, 0.0, 90.0)
    _areas, on_bed, _overhang, bridge = _classify(vertices, triangles, normals, threshold, bridge_mm)
    angle = angle.copy()
    angle[on_bed | bridge] = 0.0
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


def _drop_link(path: Path) -> None:
    """Remove a symlink, junction, or extra hard link so the next create stays here."""
    from aicad.paths import _unlink_reparse, is_reparse

    try:
        if is_reparse(path):
            _unlink_reparse(path)
            return
        info = path.stat()
    except OSError:
        return
    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        path.unlink()


def _write_stl(vertices: np.ndarray, triangles: np.ndarray, path: Path) -> None:
    from aicad.paths import publish_bytes

    payload = bytearray(b"aicad oriented" + b"\0" * 66)
    payload += struct.pack("<I", len(triangles))
    for triangle in triangles:
        a, b, c = vertices[int(triangle[0])], vertices[int(triangle[1])], vertices[int(triangle[2])]
        normal = np.cross(b - a, c - a)
        length = float(np.linalg.norm(normal))
        normal = normal / length if length > 1e-12 else np.array([0.0, 0.0, 1.0])
        payload += struct.pack("<12fH", *normal, *a, *b, *c, 0)
    publish_bytes(path, bytes(payload))


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


def _assumptions(threshold: float, bridge_mm: float) -> list[str]:
    return [
        f"Support is required when a triangle leans more than {threshold:g}° from vertical. Straight down is 90°. A vertical wall is 0°.",
        f"The face on the bed is not counted as support. Anything within {_BED_MM:g} mm of the lowest point is treated as sitting on the plate.",
        (
            "A connected down-facing patch is a bridge when it shares an edge with a face "
            f"that reaches the bed, and twice its farthest distance to that edge is at most {bridge_mm:g} mm. "
            "bridge_mm 0 counts every overhang as support."
        ),
        "The six orientations put one modeled axis up. They do not try a diagonal pose.",
        "Best means the smallest support area, and the shorter part when two poses tie. When a printer is named, a pose that fits that bed is preferred over a shorter pose that does not.",
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
