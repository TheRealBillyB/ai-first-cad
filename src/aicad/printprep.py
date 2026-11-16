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
    "bambu_p1": {"name": "Bambu Lab P1S", "size_mm": [256, 256, 256]},
    "bambu_p1p": {"name": "Bambu Lab P1P", "size_mm": [256, 256, 256]},
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
    "bambu_p1s": "bambu_p1",
    "p1p": "bambu_p1p",
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
# A face whose normal is this close to straight up or down is flat.
_FLAT = 0.999
# A down-facing span no wider than this, held along an edge, prints as a bridge.
_DEFAULT_BRIDGE_MM = 5.0
# The layer height a floating-start check uses when the setup states none.
_FLOATING_LAYER_MM = 0.2
# Heights closer than this are one level when looking for where an island starts.
_LEVEL_MM = 1e-4
# Vertices closer than this are the same point when a mesh splits a shared edge.
_WELD_MM = 0.02
_BRIDGE_PAIR_LIMIT = 4_000_000
_NEEDS_SUPPORT = re.compile(
    r"may need supports?|"
    r"might need supports?|"
    r"consider enabling supports?|"
    r"floating (?:regions?|parts?|objects?)|"
    r"low bed adhesion|"
    r"print stability issues|"
    r"unsupported extrusions?|"
    r"floating cantilever",
    re.IGNORECASE,
)
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
    threshold = _threshold(setup.get("threshold_deg"))
    bridge_mm = _bridge_span(setup.get("bridge_mm", _DEFAULT_BRIDGE_MM))
    layer_height = _layer_height(setup.get("layer_height_mm"))
    mesh = kernel.tessellate(shape)
    layer_mm = layer_height or _FLOATING_LAYER_MM
    scores = [_score(mesh, name, threshold, bridge_mm, layer_mm=layer_mm) for name in _ORIENTATIONS]
    requested = str(setup.get("orientation") or "best").lower().replace("-", "_").replace(" ", "_")
    wanted = setup.get("printer")
    printer = lookup_printer(str(wanted)) if wanted else None
    pose = _stated_pose(setup)
    rotation = None
    if pose is not None:
        if requested not in ("best", "auto", "", "custom"):
            raise GeometryError(
                f"Orientation {requested!r} and a stated pose (up or rotation_deg) were both given. Use one."
            )
        rotation = pose["rotation"]
        chosen = _score(mesh, "custom", threshold, bridge_mm, rotation=rotation, note=pose["note"], layer_mm=layer_mm)
        chosen_name = "custom"
        automatic = False
    elif requested in ("best", "auto", ""):
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
        raise GeometryError(
            f"Orientation {requested!r} is unknown. Use {known}, or state a pose with up or rotation_deg."
        )
    if rotation is None:
        rotation = _rotation_to_up(_ORIENTATIONS[chosen_name][0])
    printers = _printer_rows(chosen, scores)
    focus = None
    if printer is not None:
        focus = next(row for row in printers if row["id"] == printer["id"])
    views = []
    if out_dir is not None and setup.get("render", True):
        views = _render(mesh, chosen_name, threshold, bridge_mm, Path(out_dir), part_name or "part", rotation=rotation)
    slicer = {"status": "not_requested"}
    if setup.get("slice"):
        oriented = _oriented_arrays(mesh, chosen_name, rotation)
        slicer = slice_mesh(
            oriented[0],
            oriented[1],
            Path(out_dir or "."),
            price_per_kg=setup.get("price_per_kg"),
            layer_height_mm=layer_height,
            printer_id=printer["id"] if printer is not None else None,
        )
    starts = _floating_rows(chosen.pop("_floating"), mesh, shape, rotation)
    for row in scores:
        row.pop("_floating", None)
    status = "pass"
    if focus and not focus["fits"]:
        status = "fail"
    elif chosen["support_fraction"] > 0.25 or starts:
        status = "review"
    report = {
        "part": part_name,
        "status": status,
        "orientation": chosen_name,
        "orientation_automatic": automatic,
        "orientation_note": chosen["note"],
        # The turn from the model to the print, with modeled points as columns: print = rotation @ model.
        "print_rotation": [[round(float(value), 9) for value in row] for row in rotation],
        **({"pose": pose["stated"]} if pose is not None else {}),
        "threshold_deg": threshold,
        "layer_height_mm": layer_height,
        "support_area_mm2": chosen["support_area_mm2"],
        "support_fraction": chosen["support_fraction"],
        "bridge_area_mm2": chosen["bridge_area_mm2"],
        "floating_starts": starts,
        "floating_layer_mm": layer_mm,
        **_slicer_floating(slicer),
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
        "note": _headline(chosen, automatic, focus, slicer) + _floating_note(starts),
    }
    return finish(
        report,
        kind=ROUGH_ESTIMATE,
        name="Print",
        what="Overhang, support area, and printer fit are rough estimates. A slicer time, when present, uses that slicer's default profile, and layer_height_mm when it is set.",
        limits=[
            "Only the six axis-up orientations are tried for best. A diagonal pose is checked only when it is "
            "stated with up or rotation_deg.",
            f"The bed face, within {_BED_MM:g} mm of the lowest point, is not counted as support.",
            (
                "A down-facing span held along an edge is a bridge, not support, when twice "
                f"its farthest distance to that edge is at most {bridge_mm:g} mm. "
                "A face with no path to the bed still counts."
            ),
            "Time and grams follow the slicer's default profile. layer_height_mm, when set, is passed as the layer height. A missing slicer skips those numbers.",
            (
                "A floating start is where an island of material begins in mid-air: a lowest point or edge of the "
                f"solid, off the bed, with no material within one layer ({layer_mm:g} mm) below it. Its area is the "
                "island's first layer, read from the mesh, and it counts as support."
            ),
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

    from aicad.paths import ensure_real_directory

    ensure_real_directory(path)


def slice_mesh(
    vertices: np.ndarray,
    triangles: np.ndarray,
    out_dir: Path,
    price_per_kg=None,
    layer_height_mm=None,
    printer_id: str | None = None,
) -> dict:
    executables = find_slicers()
    if not executables:
        return {
            "status": "skipped",
            "slicer": None,
            "note": (
                "No slicer was found. OrcaSlicer and PrusaSlicer are not on PATH, and Bambu Studio is not on PATH "
                "or in its usual install folder. Install one and the same check will report time and filament. "
                "The overhang map and the printer fit do not need a slicer."
            ),
            "assumptions": ["No slicer ran. Time and grams are not estimated."],
        }
    _real_output_dir(out_dir)
    gcode = out_dir / "print.gcode"
    # A link at this name is removed so the copy is created in this folder.
    _drop_link(gcode)
    last = ""
    last_name = Path(executables[-1]).name
    from aicad.labels import plain_line
    from aicad.paths import publish_bytes
    from aicad.tool_directory import (
        held_is_file,
        held_read_bytes,
        held_run_kwargs,
        held_subprocess_directory,
        held_unlink,
        held_write_bytes,
    )

    with held_subprocess_directory() as work:
        held_write_bytes(work, "oriented.stl", _stl_bytes(vertices, triangles))
        for executable in executables:
            last_name = Path(executable).name
            if is_bambu(executable):
                bambu = _bambu_slice_report(executable, work, printer_id, layer_height_mm, price_per_kg)
                if bambu.get("status") == "sliced":
                    return bambu
                last = bambu.get("note") or last
                continue
            for export in (True, False):
                from aicad.sandbox import program_environment, run_confined_helper

                def run_once(supports: bool) -> tuple[str, bytes]:
                    command = _slice_command(
                        executable,
                        Path("print.gcode"),
                        Path("oriented.stl"),
                        price_per_kg,
                        export,
                        layer_height_mm,
                        supports,
                    )
                    completed = run_confined_helper(
                        command,
                        env=program_environment(),
                        timeout=180,
                        **held_run_kwargs(work),
                    )
                    text = ((completed.stdout or "") + "\n" + (completed.stderr or ""))[-800:]
                    return text, held_read_bytes(work, "print.gcode", maximum=64 * 1024 * 1024)

                last, payload = run_once(False)
                supports_state = None
                if payload and _needs_supports(last):
                    if held_is_file(work, "print.gcode"):
                        held_unlink(work, "print.gcode")
                    retry_text, retry_payload = run_once(True)
                    if retry_text:
                        last = retry_text
                    if retry_payload:
                        payload = retry_payload
                        supports_state = "on"
                    else:
                        supports_state = "warned"
                if payload:
                    publish_bytes(gcode, payload)
                    gcode_text = payload.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
                    parsed = parse_gcode(gcode_text)
                    parsed["status"] = "sliced" if parsed.get("grams") is not None or parsed.get("seconds") is not None else "unparsed"
                    parsed["slicer"] = Path(executable).name
                    parsed["gcode"] = str(gcode.resolve())
                    parsed["note"] = plain_line(_slicer_note(parsed, price_per_kg))
                    parsed["supports"] = supports_state
                    if supports_state == "on":
                        parsed["note"] += " Supports were turned on because the slicer warned they were needed."
                    elif supports_state == "warned":
                        parsed["note"] += " The slicer warned that supports were needed, and the supported slice did not write gcode."
                    if parsed.get("grams") and price_per_kg is not None and not parsed.get("cost"):
                        parsed["cost"] = round(float(parsed["grams"]) * float(price_per_kg) / 1000.0, 2)
                        parsed["cost_source"] = "price_per_kg"
                    layer_note = (
                        f"Layer height is {layer_height_mm:g} mm."
                        if layer_height_mm is not None
                        else "Layer height is the profile's."
                    )
                    parsed["layer_height_mm"] = layer_height_mm
                    parsed["assumptions"] = [
                        f"Sliced by {parsed['slicer']} with its default profile. Nozzle and infill are whatever that profile says. {layer_note}",
                        "Filament density 1.24 g/cm³ (PLA) is passed so a profile with no density does not report 0 g.",
                        "Time and grams come from the gcode comments. A different profile changes both.",
                        "Cost is the slicer's filament cost when the gcode has one, otherwise grams times price_per_kg / 1000.",
                    ]
                    if supports_state == "on":
                        parsed["assumptions"].append(
                            "Supports were enabled because the slicer warned that the print needs them."
                        )
                    return parsed
                if held_is_file(work, "print.gcode"):
                    held_unlink(work, "print.gcode")
    from aicad.labels import plain_line

    return {
        "status": "failed",
        "slicer": last_name,
        "note": plain_line("The slicer ran and did not write gcode. " + last),
        "assumptions": ["The slicer is installed, but this headless command did not produce a toolpath."],
    }


def _bambu_slice_report(executable: str, work, printer_id, layer_height_mm, price_per_kg) -> dict:
    """Slice with Bambu Studio, again with supports when it warns they are needed."""
    from aicad.labels import plain_line

    parsed, log = _slice_bambu(executable, work, printer_id, layer_height_mm, supports=False)
    if parsed is None:
        return {"status": "failed", "note": plain_line("Bambu Studio did not slice the part. " + (log or ""))}
    first_warning = parsed.get("warning")
    supports_state = None
    if first_warning and _needs_supports(first_warning):
        retried, _retry_log = _slice_bambu(executable, work, printer_id, layer_height_mm, supports=True)
        if retried is not None:
            parsed = retried
            supports_state = "on"
        else:
            supports_state = "warned"
    parsed["status"] = "sliced"
    parsed["slicer"] = Path(executable).name
    parsed["slicer_warning"] = first_warning
    parsed["supports"] = supports_state
    if parsed.get("grams") and price_per_kg is not None:
        parsed["cost"] = round(float(parsed["grams"]) * float(price_per_kg) / 1000.0, 2)
        parsed["cost_source"] = "price_per_kg"
    note = _slicer_note(parsed, price_per_kg)
    if first_warning:
        note += f" Bambu Studio warned: {first_warning}"
    if supports_state == "on":
        note += " It was sliced again with supports on."
    if parsed.get("export_note"):
        note += " " + parsed["export_note"]
    parsed["note"] = plain_line(note)
    profiles = parsed["profiles"]
    parsed["layer_height_mm"] = layer_height_mm
    parsed["assumptions"] = [
        f"Sliced by Bambu Studio with its {profiles['machine']} machine, {profiles['process']} process, "
        f"and {profiles['filament']} filament profiles."
        + (
            ""
            if parsed["printer_matched"]
            else " No printer was named, so the A1 profiles were used."
            if printer_id is None
            else f" {printer_id} has no Bambu profile here, so the A1 profiles were used."
        ),
        "Time and grams come from the result.json Bambu Studio writes. Cost is grams times price_per_kg / 1000.",
        "A warning Bambu Studio gives, such as a floating cantilever, is reported as slicer_warning.",
    ]
    parsed.pop("warning", None)
    return parsed


def find_slicer() -> str | None:
    found = find_slicers()
    return found[0] if found else None


def find_slicers() -> list[str]:
    """OrcaSlicer, then PrusaSlicer, then Bambu Studio, as far as each is installed."""
    from aicad.prereqs import find_program

    found = []
    for name in (*_SLICERS, *_BAMBU_NAMES):
        path = find_program(name)
        if path and path not in found:
            found.append(path)
    for path in _bambu_installs():
        if path not in found:
            found.append(path)
    return found


# Bambu Studio's command line, after OrcaSlicer and PrusaSlicer. It is often
# not on PATH, so its usual install folders are looked in too.
_BAMBU_NAMES = ("bambu-studio", "BambuStudio", "bambu_studio")
_BAMBU_INSTALLS = (
    (r"%ProgramFiles%\Bambu Studio\bambu-studio.exe",),
    ("/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",),
)
# The Bambu machine, process, and filament profiles for each printer the app
# knows. Another printer is sliced as an A1 and the result says so.
_BAMBU_PROFILES = {
    "bambu_a1": ("Bambu Lab A1 0.4 nozzle", "0.20mm Standard @BBL A1", "Bambu PLA Basic @BBL A1"),
    "bambu_a1_mini": ("Bambu Lab A1 mini 0.4 nozzle", "0.20mm Standard @BBL A1M", "Bambu PLA Basic @BBL A1M"),
    # Bambu's X1C process and filament profiles list the P1S as compatible.
    "bambu_p1": ("Bambu Lab P1S 0.4 nozzle", "0.20mm Standard @BBL X1C", "Bambu PLA Basic @BBL X1C"),
    "bambu_p1p": ("Bambu Lab P1P 0.4 nozzle", "0.20mm Standard @BBL P1P", "Bambu PLA Basic @BBL X1C"),
    "bambu_x1": ("Bambu Lab X1 Carbon 0.4 nozzle", "0.20mm Standard @BBL X1C", "Bambu PLA Basic @BBL X1C"),
}
_BAMBU_DEFAULT = "bambu_a1"
# Bambu Studio's return code when writing an output file failed after the slice
# itself finished. Its result.json still has the slice's time and grams.
_BAMBU_EXPORT_FAILED = -13
_PROFILE_MAX_BYTES = 4 * 1024 * 1024


def is_bambu(executable: str) -> bool:
    return "bambu" in Path(executable).name.lower()


def _bambu_installs() -> list[str]:
    import os

    found = []
    for (pattern,) in _BAMBU_INSTALLS:
        path = Path(os.path.expandvars(pattern))
        if "%" in str(path):
            continue
        try:
            if path.is_file() and os.access(path, os.X_OK):
                found.append(str(path.resolve()))
        except OSError:
            continue
    return found


def _bambu_profile_root(executable: str) -> Path | None:
    """The BBL profile folder that ships beside the program."""
    program = Path(executable).resolve()
    for candidate in (
        program.parent / "resources" / "profiles" / "BBL",
        program.parent.parent / "Resources" / "profiles" / "BBL",
    ):
        if candidate.is_dir():
            return candidate
    return None


def _bambu_profile(root: Path, kind: str, name: str, extra: dict | None = None, depth: int = 0) -> dict:
    """One Bambu system profile with its parents folded in, as the command line needs it.

    The command line does not follow ``inherits`` for a file it is given, so
    each parent is read and the child laid over it.
    """
    import json

    if depth > 16:
        raise GeometryError(f"Bambu profile {name!r} inherits too deeply.")
    path = root / kind / f"{name}.json"
    with open(path, "rb") as handle:
        raw = handle.read(_PROFILE_MAX_BYTES + 1)
    if len(raw) > _PROFILE_MAX_BYTES:
        raise GeometryError(f"Bambu profile {name!r} is larger than expected.")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise GeometryError(f"Bambu profile {name!r} is not an object.")
    parent = data.get("inherits")
    if isinstance(parent, str) and parent:
        merged = _bambu_profile(root, kind, parent, depth=depth + 1)
        merged.update(data)
        data = merged
    data.pop("inherits", None)
    data["from"] = "system"
    if extra:
        data.update(extra)
    return data


def _bambu_command(executable: str, supports: bool) -> list[str]:
    return [
        executable,
        "--allow-newer-file",
        "--slice", "0",
        "--arrange", "1",
        "--orient", "0",
        "--load-settings", "machine.json;process.json",
        "--load-filaments", "filament.json",
        "--outputdir", ".",
        "oriented.stl",
    ]


def _bambu_result(data: dict) -> dict:
    """Time, grams, and any warning from the result.json Bambu Studio writes."""
    plates = data.get("sliced_plates") if isinstance(data, dict) else None
    if not isinstance(plates, list) or not plates:
        return {"seconds": None, "time": None, "grams": None, "cost": None, "cost_source": None, "warning": None}
    seconds = 0.0
    grams = 0.0
    warnings = []
    for plate in plates:
        if not isinstance(plate, dict):
            continue
        seconds += float(plate.get("total_predication") or 0.0)
        for filament in plate.get("filaments") or []:
            if isinstance(filament, dict):
                grams += float(filament.get("total_used_g") or 0.0)
        message = str(plate.get("warning_message") or "").strip()
        if message:
            warnings.append(message)
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    text = (f"{hours}h " if hours else "") + f"{minutes}m {secs}s"
    return {
        "seconds": whole,
        "time": text,
        "grams": round(grams, 2),
        "cost": None,
        "cost_source": None,
        "warning": "; ".join(warnings) or None,
    }


def _slice_bambu(executable: str, work, printer_id: str | None, layer_height_mm, supports: bool):
    """Slice the oriented STL already in ``work`` with Bambu Studio. The parsed result and the log tail."""
    import json

    from aicad.sandbox import program_environment, run_confined_helper
    from aicad.tool_directory import held_is_file, held_read_bytes, held_run_kwargs, held_unlink, held_write_bytes

    root = _bambu_profile_root(executable)
    if root is None:
        return None, "Bambu Studio's profile folder was not found beside the program."
    chosen = printer_id if printer_id in _BAMBU_PROFILES else _BAMBU_DEFAULT
    machine, process, filament = _BAMBU_PROFILES[chosen]
    overrides = {"enable_support": "1" if supports else "0"}
    if layer_height_mm is not None:
        overrides["layer_height"] = f"{float(layer_height_mm):g}"
    try:
        files = {
            "machine.json": _bambu_profile(root, "machine", machine),
            "process.json": _bambu_profile(root, "process", process, overrides),
            "filament.json": _bambu_profile(root, "filament", filament),
        }
    except (OSError, ValueError, GeometryError) as exc:
        return None, f"Bambu Studio's {chosen} profiles could not be read: {exc}"
    for name, data in files.items():
        held_write_bytes(work, name, json.dumps(data).encode("utf-8"))
    # The supported slice runs in the same folder. A result left by the first
    # run must not be read as the second run's.
    if held_is_file(work, "result.json"):
        held_unlink(work, "result.json")
    completed = run_confined_helper(
        _bambu_command(executable, supports), env=program_environment(), timeout=300, **held_run_kwargs(work)
    )
    log = ((completed.stdout or "") + "\n" + (completed.stderr or ""))[-800:]
    if not held_is_file(work, "result.json"):
        return None, log
    try:
        data = json.loads(held_read_bytes(work, "result.json", maximum=8 * 1024 * 1024).decode("utf-8"))
    except (OSError, ValueError):
        return None, log
    if not isinstance(data, dict):
        return None, log
    code = data.get("return_code")
    early = _bambu_result(data)
    # A failed file export after a finished slice still leaves its time and grams.
    # _bambu_result gives 0, not None, for a plate without them, so ask for more than 0.
    export_failed = code == _BAMBU_EXPORT_FAILED and bool(early["seconds"]) and bool(early["grams"])
    if code not in (0, None) and not export_failed:
        return None, log + f"\nBambu Studio returned {code}: {data.get('error_string') or 'no reason given'}"
    parsed = _bambu_result(data)
    if parsed["seconds"] is None and parsed["grams"] is None:
        return None, log + "\n" + str(data.get("error_string") or "")
    if export_failed:
        parsed["export_note"] = (
            f"Bambu Studio returned {code} after slicing, which is a failed file export, and wrote the time and "
            "grams of the slice. Those are used."
        )
    parsed["profiles"] = {"printer": chosen, "machine": machine, "process": process, "filament": filament}
    parsed["printer_matched"] = chosen == printer_id
    return parsed, log


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


def _layer_height(value) -> float | None:
    """Slicer layer height in millimeters, or None to keep the slicer's profile."""
    if value is None:
        return None
    try:
        height = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError("layer_height_mm is the slicer layer height in millimeters.") from exc
    if isinstance(value, bool) or not math.isfinite(height) or height <= 0:
        raise GeometryError("layer_height_mm is the slicer layer height in millimeters.")
    return height


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


def _score(
    mesh,
    name: str,
    threshold: float,
    bridge_mm: float = _DEFAULT_BRIDGE_MM,
    *,
    rotation: np.ndarray | None = None,
    note: str | None = None,
    layer_mm: float = _FLOATING_LAYER_MM,
) -> dict:
    vertices, triangles, normals = _oriented_arrays(mesh, name, rotation)
    areas, _on_bed, overhang, bridge = _classify(vertices, triangles, normals, threshold, bridge_mm)
    total = float(areas.sum()) or 1.0
    support_mask = overhang & ~bridge
    floating = floating_starts(vertices, triangles, normals, layer_mm, counted=support_mask)
    # A floating start needs support even where its faces are steep enough to print.
    support = float(areas[support_mask].sum()) + sum(start["uncounted_mm2"] for start in floating)
    size = vertices.max(axis=0) - vertices.min(axis=0) if len(vertices) else np.zeros(3)
    return {
        "orientation": name,
        "note": note if note is not None else _ORIENTATIONS[name][1],
        "floating_start_count": len(floating),
        "_floating": floating,
        "support_area_mm2": round(support, 2),
        "support_fraction": round(support / total, 4),
        "bridge_area_mm2": round(float(areas[bridge].sum()), 2),
        "bridge_mm": bridge_mm,
        "bed_band_mm": _BED_MM,
        "height_mm": round(float(size[2]), 3),
        "footprint_mm": [round(float(size[0]), 3), round(float(size[1]), 3)],
        "size_mm": [round(float(value), 3) for value in size],
    }


def floating_starts(vertices, triangles, normals, layer_mm: float, counted=None) -> list[dict]:
    """Where an island of material begins with nothing under it, in the print pose.

    A layer that has an island the layer below does not touch can only start at
    a lowest point of the solid: a vertex, edge, or flat patch whose neighbors
    are all higher and whose faces face down, so material is above it and air is
    below. One on the bed is the first layer. One with material less than a
    layer below it prints onto that. Every other one is a floating start.

    A bridge, or a shelf grown from a wall, is not one: its lowest edge meets
    material that goes on down, so it is not a lowest point. Each start gives
    its height, where it is, the first layer's area, the triangles in that
    layer, and the area of those triangles not already in ``counted``.
    """
    if not len(triangles):
        return []
    verts, faces = _weld_faces(vertices, triangles)
    z = verts[:, 2]
    bed = float(z.min()) + _BED_MM
    good = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 2] != faces[:, 0])
    kept = faces[good]
    source = np.concatenate([kept[:, 0], kept[:, 1], kept[:, 2], kept[:, 1], kept[:, 2], kept[:, 0]])
    target = np.concatenate([kept[:, 1], kept[:, 2], kept[:, 0], kept[:, 0], kept[:, 1], kept[:, 2]])
    lowest_neighbor = np.full(len(verts), np.inf)
    np.minimum.at(lowest_neighbor, source, z[target])
    # A vertex no neighbor is below. A lowest patch is made only of these.
    candidate = (lowest_neighbor >= z - _LEVEL_MM) & (z > bed)
    if not candidate.any():
        return []
    neighbors = _csr(source, target, len(verts))
    face_ids = np.flatnonzero(good)
    touching = _csr(np.concatenate([kept[:, 0], kept[:, 1], kept[:, 2]]), np.tile(face_ids, 3), len(verts))
    seen = np.zeros(len(verts), dtype=bool)
    starts = []
    # Built once: rebuilding it for each start made many starts quadratic in the mesh.
    edge_map = None
    for vertex in np.flatnonzero(candidate)[np.argsort(z[candidate], kind="stable")]:
        vertex = int(vertex)
        if seen[vertex]:
            continue
        level = z[vertex]
        # The flat patch at this height that this vertex belongs to.
        patch = [vertex]
        seen[vertex] = True
        lowest = True
        cursor = 0
        while cursor < len(patch):
            current = patch[cursor]
            cursor += 1
            if not candidate[current]:
                lowest = False
            for other in _row(neighbors, current):
                if abs(z[other] - level) <= _LEVEL_MM and not seen[other]:
                    seen[other] = True
                    patch.append(int(other))
        if not lowest:
            continue
        around = sorted({int(face) for member in patch for face in _row(touching, member)})
        # Material above and air below: some face here faces down.
        if not any(normals[face][2] < -1e-6 for face in around):
            continue
        if _held_from_below(verts, faces, patch, around, layer_mm):
            continue
        if edge_map is None:
            edge_map = _edge_map(faces)
        starts.append(_start_row(verts, faces, normals, patch, around, level, layer_mm, counted, edge_map))
    return starts


def _csr(source: np.ndarray, target: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]:
    """Each source's targets, as offsets into one sorted array."""
    order = np.argsort(source, kind="stable")
    offsets = np.zeros(count + 1, dtype=np.int64)
    np.add.at(offsets, source + 1, 1)
    return np.cumsum(offsets), target[order]


def _row(table: tuple[np.ndarray, np.ndarray], index: int) -> np.ndarray:
    offsets, values = table
    return values[offsets[index] : offsets[index + 1]]


def _held_from_below(verts, faces, patch, around, layer_mm: float) -> bool:
    """True when material lies less than one layer straight below some point of the patch."""
    others = np.ones(len(faces), dtype=bool)
    others[around] = False
    corners = verts[faces[others]]
    if not len(corners):
        return False
    xy = corners[:, :, :2]
    step = max(1, len(patch) // 32)
    for member in patch[::step]:
        point = verts[member]
        hit = _below(xy, corners, point)
        if hit is not None and point[2] - hit <= layer_mm + _BED_MM:
            return True
    return False


def _below(xy, corners, point):
    """The highest triangle height at or straight below ``point``, or None.

    A surface at the same height counts: a part set on another part rests on it.
    """
    a, b, c = xy[:, 0], xy[:, 1], xy[:, 2]
    p = point[:2]
    denominator = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    usable = np.abs(denominator) > 1e-12
    safe = np.where(usable, denominator, 1.0)
    u = ((b[:, 1] - c[:, 1]) * (p[0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (p[1] - c[:, 1])) / safe
    v = ((c[:, 1] - a[:, 1]) * (p[0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (p[1] - c[:, 1])) / safe
    w = 1.0 - u - v
    inside = usable & (u >= -1e-9) & (v >= -1e-9) & (w >= -1e-9)
    heights = u * corners[:, 0, 2] + v * corners[:, 1, 2] + w * corners[:, 2, 2]
    below = inside & (heights <= point[2] + 1e-6)
    if not below.any():
        return None
    return float(heights[below].max())


def _start_row(verts, faces, normals, patch, around, level: float, layer_mm: float, counted, edge_map) -> dict:
    """One floating start: its height, place, first-layer area, and triangles."""
    top = level + layer_mm
    band = set(around)
    stack = list(around)
    while stack:
        current = stack.pop()
        face = faces[current]
        a, b, c = int(face[0]), int(face[1]), int(face[2])
        for key in (_edge_key(a, b), _edge_key(b, c), _edge_key(c, a)):
            for other in edge_map.get(key, ()):
                if other in band or normals[other][2] >= -1e-6:
                    continue
                if float(verts[faces[other]][:, 2].min()) >= top:
                    continue
                band.add(other)
                stack.append(other)
    down = [face for face in band if normals[face][2] < -1e-6]
    area = 0.0
    uncounted = 0.0
    for face in down:
        piece = _plan_area_below(verts[faces[face]], top)
        area += piece
        if counted is None or not counted[face]:
            uncounted += piece
    points = verts[patch]
    return {
        "z_mm": float(level),
        "xy_mm": [float(points[:, 0].mean()), float(points[:, 1].mean())],
        "area_mm2": area,
        "uncounted_mm2": uncounted,
        "triangles": sorted(down),
    }


def _plan_area_below(corners, top: float) -> float:
    """The area seen from above of the part of a triangle below height ``top``."""
    polygon = []
    count = len(corners)
    for index in range(count):
        here = corners[index]
        there = corners[(index + 1) % count]
        here_in = here[2] <= top
        there_in = there[2] <= top
        if here_in:
            polygon.append(here)
        if here_in != there_in:
            t = (top - here[2]) / (there[2] - here[2])
            polygon.append(here + t * (there - here))
    if len(polygon) < 3:
        return 0.0
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return 0.5 * abs(sum(xs[i] * ys[(i + 1) % len(xs)] - xs[(i + 1) % len(xs)] * ys[i] for i in range(len(xs))))


def _floating_rows(found: list[dict], mesh, shape, rotation) -> list[dict]:
    """The floating starts of the chosen pose, with the model faces and the place in the model."""
    if not found:
        return []
    turned = mesh.vertices @ rotation.T
    offset = turned.min(axis=0)
    rows = []
    for start in found:
        faces = []
        if mesh.face_index is not None:
            faces = sorted({int(mesh.face_index[triangle]) for triangle in start["triangles"]})
        printed = np.array([start["xy_mm"][0], start["xy_mm"][1], start["z_mm"]])
        model = rotation.T @ (printed + offset)
        rows.append(
            {
                "z_mm": round(start["z_mm"], 3),
                "xy_mm": [round(value, 3) for value in start["xy_mm"]],
                "at_model_mm": [round(float(value), 3) for value in model],
                "area_mm2": round(float(start["area_mm2"]), 3),
                "faces": faces,
            }
        )
    rows.sort(key=lambda row: (row["z_mm"], row["xy_mm"]))
    return rows


def _floating_note(starts: list[dict]) -> str:
    if not starts:
        return ""
    lowest = starts[0]
    return (
        f" {len(starts)} floating start{'s' if len(starts) != 1 else ''}: material begins in mid-air with nothing "
        f"under it, the lowest at z {lowest['z_mm']:g} mm (faces {', '.join(str(face) for face in lowest['faces']) or 'unknown'}). "
        "It needs support in this pose."
    )


def _slicer_floating(slicer: dict) -> dict:
    """A slicer's own floating-region warning, when the slice gave one."""
    warning = slicer.get("slicer_warning") if isinstance(slicer, dict) else None
    if isinstance(warning, str) and re.search(r"floating|cantilever", warning, re.IGNORECASE):
        return {"slicer_floating_warning": warning}
    return {}


def _threshold(value) -> float:
    """The overhang angle in degrees from vertical, above 0 and below 90.

    At 0 a vertical wall whose normal leans down by rounding would need support.
    At 90 no face ever would, not even a ceiling.
    """
    if value is None:
        return 45.0
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError("threshold_deg must be a number of degrees from vertical, such as 45.") from exc
    if not 0 < threshold < 90:
        raise GeometryError(
            "threshold_deg is how far from vertical a face may lean before it needs support. "
            "Use a number above 0 and below 90, such as 45."
        )
    return threshold


def _classify(vertices, triangles, normals, threshold: float, bridge_mm: float):
    """Faces held from below, down-facing faces, and the down-facing faces that are bridges.

    A face is held from below when it is on the bed, or when it is flat and
    sits on a flat up-facing face of another body: a part on a slab, or one
    part stacked on another. The bed is the lowest point of everything
    checked, so before this a slab under a plate of parts made every part's
    underside an overhang.
    """
    areas = _areas(vertices, triangles)
    min_z = float(vertices[:, 2].min()) if len(vertices) else 0.0
    centroids = vertices[triangles].mean(axis=1)
    on_bed = centroids[:, 2] <= min_z + _BED_MM
    held = on_bed | _resting(vertices, triangles, normals, centroids, ~on_bed)
    # A face that leans phi from vertical has normal z of -sin(phi).
    # Straight down is 90 degrees and a vertical wall is 0.
    limit = -math.sin(math.radians(threshold))
    overhang = (normals[:, 2] < limit) & ~held
    bridge = _bridge_mask(vertices, triangles, overhang, held, bridge_mm)
    return areas, held, overhang, bridge


def _resting(vertices, triangles, normals, centroids, candidates) -> np.ndarray:
    """Flat down-facing faces that sit wholly on flat up-facing faces, within the bed band.

    One solid never has such a pair: they would be a wall of no thickness.
    So the face below belongs to another body, which holds this one up. A
    face only partly over the body below is not held: its corners, the
    middles of its edges, and its center must all be on that body.
    """
    resting = np.zeros(len(triangles), dtype=bool)
    down = np.flatnonzero(candidates & (normals[:, 2] < -_FLAT))
    up = np.flatnonzero(normals[:, 2] > _FLAT)
    if not len(down) or not len(up):
        return resting
    corners = vertices[triangles[up]]
    heights = corners[:, :, 2].mean(axis=1)
    order = np.argsort(heights, kind="stable")
    heights = heights[order]
    corners = corners[order][:, :, :2]
    for index in down:
        z = float(centroids[index, 2])
        low = int(np.searchsorted(heights, z - _BED_MM, side="left"))
        high = int(np.searchsorted(heights, z + _BED_MM, side="right"))
        if low >= high:
            continue
        below = corners[low:high]
        own = vertices[triangles[index]][:, :2]
        middle = centroids[index, :2]
        # Pulled a little toward the center, so a corner on the edge of the face below is on it.
        probes = [0.98 * point + 0.02 * middle for point in (*own, *((own + np.roll(own, -1, axis=0)) / 2))]
        if all(_inside_any(point, below) for point in [middle, *probes]):
            resting[index] = True
    return resting


def _inside_any(point, triangles) -> bool:
    """Whether the 2D point is in any of the 2D triangles, edges included."""
    a = triangles[:, 0]
    b = triangles[:, 1]
    c = triangles[:, 2]

    def side(p, q, r):
        return (q[:, 0] - p[:, 0]) * (r[1] - p[:, 1]) - (q[:, 1] - p[:, 1]) * (r[0] - p[:, 0])

    first = side(a, b, point)
    second = side(b, c, point)
    third = side(c, a, point)
    tolerance = 1e-9
    has_negative = (first < -tolerance) | (second < -tolerance) | (third < -tolerance)
    has_positive = (first > tolerance) | (second > tolerance) | (third > tolerance)
    return bool(np.any(~(has_negative & has_positive)))


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


def _oriented_arrays(mesh, name: str, rotation: np.ndarray | None = None):
    """The mesh turned into its print pose and set on the bed, with each triangle's normal.

    ``rotation`` is the turn for a stated pose. Without it, the named axis is turned up.
    """
    if rotation is None:
        rotation = _rotation_to_up(_ORIENTATIONS[name][0])
    vertices = mesh.vertices @ rotation.T
    vertices = vertices - np.array([vertices[:, 0].min(), vertices[:, 1].min(), vertices[:, 2].min()])
    corners = vertices[mesh.triangles]
    raw = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    length = np.linalg.norm(raw, axis=1, keepdims=True)
    normals = raw / np.clip(length, 1e-12, None)
    return vertices, mesh.triangles, normals


def _stated_pose(setup: dict) -> dict | None:
    """The print pose the setup states, as a rotation, or None for a named orientation.

    ``up`` is the modeled direction that points up on the bed, such as
    [0, 1, 1] for a part laid on a 45 degree face. ``rotation_deg`` is the
    same turn as a part placement's rotation_deg (kernel.place, build123d's
    Rot(rx, ry, rz)), and the result is set on the bed as it lands.
    """
    has_up = setup.get("up") is not None
    has_turn = setup.get("rotation_deg") is not None
    if has_up and has_turn:
        raise GeometryError("State the print pose with up or with rotation_deg, not both.")
    if has_up:
        up = np.array(kernel.finite_vec3(setup["up"], "up"), dtype=np.float64)
        length = float(np.linalg.norm(up))
        if length < 1e-9:
            raise GeometryError("up must be a direction, not zero.")
        up = up / length
        shown = ", ".join(f"{value:g}" for value in setup["up"])
        return {
            "rotation": _rotation_to_up((float(up[0]), float(up[1]), float(up[2]))),
            "note": f"Turn it so modeled ({shown}) points up.",
            "stated": {"up": [float(value) for value in setup["up"]]},
        }
    if has_turn:
        from build123d import Pos, Rot

        angles = kernel.finite_vec3(setup["rotation_deg"], "rotation_deg", unit="deg")
        # The turn a part's placement makes, read from build123d itself, so the
        # two can never disagree: each column is where a model axis ends up.
        turn = Rot(*angles)
        columns = [(turn * Pos(*axis)).position for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
        rotation = np.array([[float(column.X), float(column.Y), float(column.Z)] for column in columns]).T
        shown = ", ".join(f"{float(value):g}" for value in setup["rotation_deg"])
        return {
            "rotation": rotation,
            "note": f"Turned by rotation_deg ({shown}), as a part's placement turns, then set on the bed.",
            "stated": {"rotation_deg": [float(value) for value in setup["rotation_deg"]]},
        }
    return None


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


def _render(mesh, name: str, threshold: float, bridge_mm: float, out_dir: Path, part_name: str, rotation=None):
    vertices, triangles, normals = _oriented_arrays(mesh, name, rotation)
    angle = np.degrees(np.arccos(np.clip(normals[:, 2], -1.0, 1.0))) - 90.0
    angle = np.clip(angle, 0.0, 90.0)
    _areas, on_bed, _overhang, bridge = _classify(vertices, triangles, normals, threshold, bridge_mm)
    angle = angle.copy()
    angle[on_bed | bridge] = 0.0
    # Full red is the support angle. Straight down is steeper than that, and it
    # stays red. The documented default is 45 degrees from vertical.
    shown = np.minimum(angle, float(threshold))
    colored_vertices, colored_triangles, scalars = _per_face_scalars(vertices, triangles, shown)
    views = render_scalar_views(
        colored_vertices,
        colored_triangles,
        scalars,
        out_dir,
        part_name,
        "overhang",
        f"Overhang  ·  {name}  ·  support past {threshold:g}°",
        "deg",
        "degrees from vertical",
        maximum=float(threshold),
        minimum=0.0,
    )
    return [view.to_dict() for view in views]


def _per_face_scalars(vertices: np.ndarray, triangles: np.ndarray, values: np.ndarray):
    """Give each triangle its own vertices painted with that triangle's value.

    A shared vertex used to average the faces that meet there, so a wall and
    the overhang beside it both lost their angle. The picture interpolates
    vertex colors, and these copies stay on one face.
    """
    corners = vertices[np.asarray(triangles, dtype=np.int32)]
    flat_vertices = np.asarray(corners, dtype=np.float64).reshape(-1, 3)
    flat_values = np.repeat(np.asarray(values, dtype=np.float64), 3)
    flat_triangles = np.arange(len(triangles) * 3, dtype=np.int32).reshape(-1, 3)
    return flat_vertices, flat_triangles, flat_values


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


def _read_tool_output(path: Path) -> bytes:
    """Bytes written by a helper in its private folder. A missing file is empty."""
    from aicad.file_access import read_file

    try:
        return read_file(path, maximum=64 * 1024 * 1024)
    except OSError:
        return b""


def _stl_bytes(vertices: np.ndarray, triangles: np.ndarray) -> bytes:
    payload = bytearray(b"aicad oriented" + b"\0" * 66)
    payload += struct.pack("<I", len(triangles))
    for triangle in triangles:
        a, b, c = vertices[int(triangle[0])], vertices[int(triangle[1])], vertices[int(triangle[2])]
        normal = np.cross(b - a, c - a)
        length = float(np.linalg.norm(normal))
        normal = normal / length if length > 1e-12 else np.array([0.0, 0.0, 1.0])
        payload += struct.pack("<12fH", *normal, *a, *b, *c, 0)
    return bytes(payload)


def _write_stl(vertices: np.ndarray, triangles: np.ndarray, path: Path) -> None:
    from aicad.paths import publish_bytes

    publish_bytes(path, _stl_bytes(vertices, triangles))


def _parse_duration(text: str) -> int | None:
    total = 0
    found = False
    for value, unit in re.findall(r"(\d+)\s*([dhms])", text.lower()):
        found = True
        number = int(value)
        total += {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit] * number
    return total if found else None


def _needs_supports(text: str) -> bool:
    """True when the slicer says this print should be sliced again with supports."""
    return _NEEDS_SUPPORT.search(text) is not None


def _support_flags(executable: str) -> list[str]:
    """The switch that turns supports on for this slicer."""
    if "orca" in Path(executable).name.lower():
        return ["--enable-support=1"]
    return ["--support-material"]


def _slice_command(
    executable: str,
    gcode: Path,
    stl: Path,
    price_per_kg,
    export: bool,
    layer_height_mm=None,
    supports: bool = False,
) -> list[str]:
    command = [executable]
    if export:
        command.extend(["--export-gcode", "--output", str(gcode)])
    else:
        command.extend(["--slice", "0", "--export-gcode", str(gcode)])
    command.extend(["--filament-density", "1.24"])
    if price_per_kg is not None:
        command.extend(["--filament-cost", str(price_per_kg)])
    if layer_height_mm is not None:
        command.extend(["--layer-height", f"{float(layer_height_mm):g}"])
    if supports:
        command.extend(_support_flags(executable))
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
        "The slicer, when it is installed, uses its own default profile for infill. Layer height is layer_height_mm when that is set, and otherwise the profile's layer height. Filament density is passed as 1.24 g/cm³ so the gram comment is not zero.",
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
