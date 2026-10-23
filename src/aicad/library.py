"""Standard parts: fasteners, bearings, seals, motors, and gearboxes.

This module is part of the kernel boundary. Table-driven solids are built in
``catalog_build``. Sizes that are not in those tables still come from
bd_warehouse, which returns build123d solids. Callers outside aicad only see
catalog records and the document's named parts.

A screw's shank points down (−Z). The underside of the head is on z = 0.
Nuts, washers, bearings, and seals sit on z = 0 and extend toward +Z.
A motor's mounting face is on z = 0, the shaft points toward +Z, and the body
extends toward −Z. A gearbox input face is on z = 0 and the output shaft
points toward +Z, so a fixed joint of the two faces stacks the gearbox on the
motor shaft.
"""

from __future__ import annotations

import copy

from aicad import catalog_build, catalog_data
from aicad.errors import GeometryError

_KINDS = (
    "socket_head_cap_screw",
    "hex_nut",
    "plain_washer",
    "ball_bearing",
    "tapered_roller_bearing",
    "radial_shaft_seal",
    "stepper_motor",
    "gearbox",
)

_FASTENERS = {"socket_head_cap_screw", "hex_nut", "plain_washer"}

_KIND_ALIASES = {
    "screw": "socket_head_cap_screw",
    "shcs": "socket_head_cap_screw",
    "socket_head": "socket_head_cap_screw",
    "cap_screw": "socket_head_cap_screw",
    "nut": "hex_nut",
    "washer": "plain_washer",
    "bearing": "ball_bearing",
    "ballbearing": "ball_bearing",
    "deep_groove": "ball_bearing",
    "tapered": "tapered_roller_bearing",
    "tapered_roller": "tapered_roller_bearing",
    "taper_roller_bearing": "tapered_roller_bearing",
    "trb": "tapered_roller_bearing",
    "seal": "radial_shaft_seal",
    "shaft_seal": "radial_shaft_seal",
    "oil_seal": "radial_shaft_seal",
    "radial_seal": "radial_shaft_seal",
    "motor": "stepper_motor",
    "nema": "stepper_motor",
    "stepper": "stepper_motor",
    "planetary": "gearbox",
    "planetary_gearbox": "gearbox",
}

_KIND_WORDS = {
    "socket_head_cap_screw": ("screw", "shcs", "socket", "cap", "fastener", "bolt"),
    "hex_nut": ("nut", "hex"),
    "plain_washer": ("washer",),
    "ball_bearing": ("bearing", "ball", "608", "6000", "6200", "6300"),
    "tapered_roller_bearing": ("tapered", "taper", "roller", "302", "320"),
    "radial_shaft_seal": ("seal", "din", "6194", "3760"),
    "stepper_motor": ("motor", "nema", "stepper"),
    "gearbox": ("gearbox", "gear", "planetary"),
}


def search(query: str = "") -> dict[str, object]:
    """Find catalog entries. An empty query lists the kinds, not every size."""
    text = " ".join(query.lower().split())
    if not text:
        return {
            "query": query,
            "matches": [_kind_summary(kind) for kind in _KINDS],
            "truncated": False,
            "note": (
                "Pass a designation or a word such as '6001', '30205', '20x35x7', "
                "'NEMA17', 'M5', 'screw', 'seal', 'motor', or 'gearbox'. "
                "Screws also need a length when you insert them. "
                "A joint can name a mate from the match, such as parent_mate 'bore' "
                "or child_mate 'shaft', instead of typing the anchor."
            ),
        }

    matches: list[dict[str, object]] = []
    for kind in _KINDS:
        matches.extend(_matches_for_kind(kind, text))
    truncated = len(matches) > 40
    return {
        "query": query,
        "matches": matches[:40],
        "truncated": truncated,
        "note": (
            "Designations follow the standard named on each match. "
            "Insert with insert_standard_part. A mate name on a joint uses that "
            "part's axis and anchor."
        ),
    }


def build(kind: str, size: str, length: float | None = None, fastener_type: str | None = None):
    """Return an unplaced build123d solid for a catalog entry."""
    kind = _canonical_kind(kind)
    if kind == "socket_head_cap_screw":
        if length is None or length <= 0:
            raise GeometryError(
                "socket_head_cap_screw requires length in millimeters (the shank under the head)."
            )
        row = catalog_data.cap_screw(size)
        if row is not None and _iso_fastener(kind, fastener_type):
            return catalog_build.cap_screw(row, float(length))
        return _bd_screw(size, float(length), fastener_type)
    if kind == "hex_nut":
        _reject_length(kind, length)
        row = catalog_data.hex_nut(size)
        if row is not None and _iso_fastener(kind, fastener_type):
            return catalog_build.hex_nut(row)
        return _bd_nut(size, fastener_type)
    if kind == "plain_washer":
        _reject_length(kind, length)
        row = catalog_data.plain_washer(size)
        if row is not None and _iso_fastener(kind, fastener_type):
            return catalog_build.plain_washer(row)
        return _bd_washer(size, fastener_type)
    if kind == "ball_bearing":
        _reject_length(kind, length)
        row = catalog_data.deep_groove(size)
        if row is None:
            raise GeometryError(
                f"No deep-groove bearing {size!r}. Use a designation such as 608 or 6001."
            )
        return catalog_build.deep_groove(row)
    if kind == "tapered_roller_bearing":
        _reject_length(kind, length)
        row = catalog_data.tapered(size)
        if row is None:
            raise GeometryError(
                f"No tapered roller bearing {size!r}. Use a designation such as 30205 or 32005X."
            )
        return catalog_build.tapered_roller(row)
    if kind == "radial_shaft_seal":
        _reject_length(kind, length)
        row = catalog_data.shaft_seal(size)
        if row is None:
            raise GeometryError(
                f"No radial shaft seal {size!r}. Use shaft x housing x width, such as 20x35x7."
            )
        return catalog_build.radial_shaft_seal(row)
    if kind == "stepper_motor":
        _reject_length(kind, length)
        row = catalog_data.stepper(size)
        if row is None:
            raise GeometryError(f"No stepper {size!r}. Use NEMA17 or NEMA23.")
        return catalog_build.stepper_motor(row)
    if kind == "gearbox":
        _reject_length(kind, length)
        row = catalog_data.gearbox(size)
        if row is None:
            raise GeometryError(f"No gearbox {size!r}. Use a designation such as S-MG17-G10.")
        return catalog_build.planetary_gearbox(row)
    raise GeometryError(f"Unknown part kind {kind!r}. Use one of: {', '.join(_KINDS)}.")


def metadata(kind: str, size: str, length: float | None = None) -> dict[str, object]:
    """Designation, standard, dimensions, mass, load ratings, and mates."""
    kind = _canonical_kind(kind)
    size = _canonical_size(kind, size)
    record = _record(kind, size, length)
    record["mates"] = mates(kind, size, length)
    return record


def mates(kind: str, size: str, length: float | None = None) -> list[dict[str, object]]:
    """Named frames in the part's local coordinates, for an assembly joint."""
    kind = _canonical_kind(kind)
    return _mate_list(kind, _canonical_size(kind, size), length)


def resolve_assembly_mates(spec: dict, specs_by_part: dict[str, dict | None]) -> dict:
    """Fill joint anchors from catalog mate names.

    ``parent_mate`` sets the parent anchor and the joint axis.
    ``child_mate`` sets the child anchor. An instance that is not a catalog
    part has no mates.
    """
    if not isinstance(spec, dict):
        return spec
    spec = copy.deepcopy(spec)
    instances = {}
    for instance in spec.get("instances") or []:
        if isinstance(instance, dict) and instance.get("name") and instance.get("part"):
            instances[str(instance["name"])] = str(instance["part"])
    joints = spec.get("joints") or []
    if not isinstance(joints, list):
        return spec
    for joint in joints:
        if not isinstance(joint, dict):
            continue
        for side, anchor_key in (("parent", "parent_anchor"), ("child", "child_anchor")):
            mate_name = joint.get(f"{side}_mate")
            if not mate_name:
                continue
            instance_name = str(joint.get(side) or "")
            part_name = instances.get(instance_name)
            if part_name is None:
                raise GeometryError(
                    f"Joint mate {mate_name!r} needs instance {instance_name!r} in the assembly."
                )
            library_spec = specs_by_part.get(part_name)
            if not library_spec:
                raise GeometryError(
                    f"Instance {instance_name!r} is not a catalog part, so it has no mate {mate_name!r}. "
                    "Pass an anchor, or insert a catalog part."
                )
            frame = _mate_named(
                str(library_spec.get("kind") or ""),
                str(library_spec.get("size") or ""),
                library_spec.get("length"),
                str(mate_name),
            )
            joint[anchor_key] = list(frame["anchor"])
            if side == "parent":
                joint["axis"] = list(frame["axis"])
    return spec


def _canonical_kind(kind: str) -> str:
    key = kind.strip().lower().replace("-", "_").replace(" ", "_")
    key = _KIND_ALIASES.get(key, key)
    if key not in _KINDS:
        raise GeometryError(f"Unknown part kind {kind!r}. Use one of: {', '.join(_KINDS)}.")
    return key


def _canonical_size(kind: str, size: str) -> str:
    token = size.strip()
    if kind == "ball_bearing":
        row = catalog_data.deep_groove(token)
        if row is not None:
            return row.designation
    if kind == "tapered_roller_bearing":
        row = catalog_data.tapered(token)
        if row is not None:
            return row.designation
    if kind == "radial_shaft_seal":
        row = catalog_data.shaft_seal(token)
        if row is not None:
            return row.designation
    if kind == "stepper_motor":
        row = catalog_data.stepper(token)
        if row is not None:
            return row.designation
    if kind == "gearbox":
        row = catalog_data.gearbox(token)
        if row is not None:
            return row.designation
    if kind == "socket_head_cap_screw":
        row = catalog_data.cap_screw(token)
        if row is not None:
            return f"{row.size}-{row.pitch_mm:g}"
    if kind == "hex_nut":
        row = catalog_data.hex_nut(token)
        if row is not None:
            return f"{row.size}-{row.pitch_mm:g}"
    if kind == "plain_washer":
        row = catalog_data.plain_washer(token)
        if row is not None:
            return row.size
    return token


def _record(kind: str, size: str, length: float | None) -> dict[str, object]:
    if kind == "socket_head_cap_screw":
        row = catalog_data.cap_screw(size)
        if row is None:
            return _fallback_fastener(kind, size, length, "ISO 4762")
        minor = row.diameter_mm - catalog_build._ISO724_MINOR * row.pitch_mm  # noqa: SLF001
        return _base(
            kind,
            f"{row.size}-{row.pitch_mm:g}",
            "ISO 4762",
            f"ISO 4762 socket head cap screw {row.size}-{row.pitch_mm:g}",
            {
                "diameter_mm": row.diameter_mm,
                "pitch_mm": row.pitch_mm,
                "head_diameter_mm": row.head_diameter_mm,
                "head_height_mm": row.head_height_mm,
                "socket_across_flats_mm": row.socket_across_flats_mm,
                "socket_depth_mm": row.socket_depth_mm,
                "shank_diameter_mm": round(minor, 4),
                "length_mm": length,
            },
            (
                "Head diameter and height are the ISO 4762 maximums. "
                "The shank is the ISO 724 basic minor diameter, so a clearance check is not against the major diameter."
            ),
        )
    if kind == "hex_nut":
        row = catalog_data.hex_nut(size)
        if row is None:
            return _fallback_fastener(kind, size, length, "ISO 4032")
        return _base(
            kind,
            f"{row.size}-{row.pitch_mm:g}",
            "ISO 4032",
            f"ISO 4032 hex nut {row.size}-{row.pitch_mm:g}",
            {
                "diameter_mm": row.diameter_mm,
                "pitch_mm": row.pitch_mm,
                "across_flats_mm": row.across_flats_mm,
                "height_mm": row.height_mm,
            },
            "Across flats and height are the ISO 4032 style 1 maximums.",
        )
    if kind == "plain_washer":
        row = catalog_data.plain_washer(size)
        if row is None:
            return _fallback_fastener(kind, size, length, "ISO 7089")
        return _base(
            kind,
            row.size,
            "ISO 7089",
            f"ISO 7089 plain washer {row.size}",
            {
                "inside_mm": row.inside_mm,
                "outside_mm": row.outside_mm,
                "thickness_mm": row.thickness_mm,
            },
            "Inside diameter, outside diameter, and thickness are the ISO 7089 nominals.",
        )
    if kind == "ball_bearing":
        row = catalog_data.deep_groove(size)
        if row is None:
            raise GeometryError(f"No deep-groove bearing {size!r}.")
        legacy = catalog_data.legacy_bearing_code(row)
        text = _base(
            kind,
            row.designation,
            "ISO 15",
            f"ISO 15 deep-groove ball bearing {row.designation}, {row.bore_mm:g} x {row.outside_mm:g} x {row.width_mm:g} mm",
            {
                "bore_mm": row.bore_mm,
                "outside_mm": row.outside_mm,
                "width_mm": row.width_mm,
                "chamfer_mm": row.chamfer_mm,
            },
            "Bore, outside diameter, width, and minimum chamfer are ISO 15. The ball and ring shoulders are a construction that keeps that boundary.",
        )
        text["aliases"] = [legacy] if legacy else []
        _ratings(text, row.dynamic_n, row.static_n, "SKF")
        _catalog_mass(text, row.mass_g, "SKF")
        return text
    if kind == "tapered_roller_bearing":
        row = catalog_data.tapered(size)
        if row is None:
            raise GeometryError(f"No tapered roller bearing {size!r}.")
        angle_note = (
            f"Cup angle {row.angle_deg:g}° is the midpoint of ISO 355 angle series {row.series[0]} ({row.series})."
            if row.angle_from_series
            else f"Cup angle {row.angle_deg:g}° is used because this row has no ISO 355 series code. Bore, outside diameter, width, cone width, and cup width are still the boundary dimensions."
        )
        text = _base(
            kind,
            row.designation,
            "ISO 355",
            (
                f"ISO 355 tapered roller bearing {row.designation}, "
                f"bore {row.bore_mm:g} mm, outside {row.outside_mm:g} mm, width {row.width_mm:g} mm, "
                f"cone {row.cone_width_mm:g} mm, cup {row.cup_width_mm:g} mm"
            ),
            {
                "bore_mm": row.bore_mm,
                "outside_mm": row.outside_mm,
                "width_mm": row.width_mm,
                "cone_width_mm": row.cone_width_mm,
                "cup_width_mm": row.cup_width_mm,
                "chamfer_mm": row.chamfer_mm,
                "series": row.series or None,
                "angle_deg": row.angle_deg,
            },
            angle_note,
        )
        text["aliases"] = [alias for alias, target in catalog_data._TAPERED_ALIASES.items() if target == row.designation]  # noqa: SLF001
        _ratings(text, row.dynamic_n, row.static_n, row.rating_source)
        return text
    if kind == "radial_shaft_seal":
        row = catalog_data.shaft_seal(size)
        if row is None:
            raise GeometryError(f"No radial shaft seal {size!r}.")
        return _base(
            kind,
            row.designation,
            "DIN 3760 / ISO 6194",
            (
                f"DIN 3760 / ISO 6194 radial shaft seal {row.designation}, "
                f"shaft {row.shaft_mm:g} mm, housing {row.housing_mm:g} mm, width {row.width_mm:g} mm"
            ),
            {
                "shaft_mm": row.shaft_mm,
                "housing_mm": row.housing_mm,
                "width_mm": row.width_mm,
            },
            "Nominal shaft, housing, and width from ISO 6194-1 Table 1. The solid is a type AS lip with a dust lip. The lip is at the nominal shaft diameter.",
        )
    if kind == "stepper_motor":
        row = catalog_data.stepper(size)
        if row is None:
            raise GeometryError(f"No stepper {size!r}.")
        dim = catalog_data.stepper_mm(row)
        text = _base(
            kind,
            row.designation,
            "NEMA ICS 16",
            (
                f"NEMA ICS 16 stepper {row.designation}, "
                f"shaft {dim['shaft_mm']:.3f} mm, pilot {dim['pilot_mm']:.3f} mm, "
                f"bolt circle {dim['bolt_circle_mm']:.3f} mm"
            ),
            {
                **dim,
                "hole_thread": row.hole_thread,
                "flange_number": row.flange,
            },
            (
                "Bolt circle, pilot, shaft diameter, and shaft length are NEMA ICS 16 Tables 2, 4, and 6. "
                "Pilot depth is the Table 4 maximum. "
                f"Body length {row.body_length_mm:g} mm is not in NEMA ICS 16. "
                "The square flange is the reference width, clipped to the maximum flange circle."
            ),
        )
        text["load_ratings"] = {
            "continuous_shaft_torque_nm": round(dim["shaft_torque_nm"], 4),
            "source": "NEMA ICS 16 Table 6",
        }
        text["mass_from_volume"] = False
        text["mass_note"] = (
            "No catalog mass is stored for this motor. The solid is the mounting envelope, "
            "so volume times density is not a motor mass."
        )
        return text
    if kind == "gearbox":
        row = catalog_data.gearbox(size)
        if row is None:
            raise GeometryError(f"No gearbox {size!r}.")
        text = _base(
            kind,
            row.designation,
            "NEMA ICS 16 mounting face",
            (
                f"Planetary gearbox {row.designation}, ratio {row.ratio:g}, "
                f"output shaft {row.output_diameter_mm:g} x {row.output_length_mm:g} mm, "
                f"flange {row.flange_mm:g} mm"
            ),
            {
                "ratio": row.ratio,
                "flange_mm": row.flange_mm,
                "body_length_mm": row.body_length_mm,
                "output_diameter_mm": row.output_diameter_mm,
                "output_length_mm": row.output_length_mm,
                "input_bore_mm": row.input_bore_mm,
                "input_bore_depth_mm": row.input_bore_depth_mm,
                "pilot_mm": round(row.pilot_mm, 4),
                "pilot_depth_mm": row.pilot_depth_mm,
                "bolt_circle_mm": round(row.bolt_circle_mm, 4),
                "hole_diameter_mm": row.hole_diameter_mm,
                "frame": row.frame,
            },
            row.note,
        )
        text["load_ratings"] = {"permissible_torque_nm": row.torque_nm, "source": "published drawing"}
        _catalog_mass(text, row.mass_g, "published drawing")
        if row.mass_g is None:
            text["mass_from_volume"] = False
            text["mass_note"] = (
                "No mass is on the published drawing used here. "
                "The solid is the mounting envelope, so volume times density is not a gearbox mass."
            )
        return text
    raise GeometryError(f"Unknown part kind {kind!r}.")


def _base(kind: str, size: str, standard: str, description: str, dimensions: dict, dimension_note: str) -> dict:
    return {
        "kind": kind,
        "size": size,
        "designation": size,
        "standard": standard,
        "description": description,
        "dimensions": dimensions,
        "dimension_note": dimension_note,
        "aliases": [],
        "load_ratings": None,
        "mass_estimate_kg": None,
        "mass_note": "",
        "mass_from_volume": True,
        "parameters": {"length": "required, millimeters, shank length under the head"} if kind == "socket_head_cap_screw" else {},
    }


def _ratings(record: dict, dynamic_n: int | None, static_n: int | None, source: str) -> None:
    if dynamic_n is None and static_n is None:
        return
    record["load_ratings"] = {
        "dynamic_n": dynamic_n,
        "static_n": static_n,
        "source": source,
        "method": "ISO 281",
    }


def _catalog_mass(record: dict, mass_g: float | None, source: str) -> None:
    if mass_g is None:
        return
    record["mass_estimate_kg"] = round(mass_g / 1000.0, 4)
    record["mass_from_volume"] = False
    record["mass_note"] = f"Catalog mass from {source}. It is not a weighed mass, and it does not follow the instance material."


def _fallback_fastener(kind: str, size: str, length: float | None, standard: str) -> dict:
    noun = {
        "socket_head_cap_screw": "socket head cap screw",
        "hex_nut": "hex nut",
        "plain_washer": "plain washer",
    }[kind]
    dimensions: dict[str, object] = {}
    if length is not None:
        dimensions["length_mm"] = length
    return _base(
        kind,
        size,
        standard,
        f"{standard} {noun} {size}",
        dimensions,
        "This size is not in the local table. The solid comes from bd_warehouse published dimensions.",
    )


def _mate_list(kind: str, size: str, length: float | None) -> list[dict[str, object]]:
    frames: list[tuple[str, list[float], str]] = []
    if kind == "ball_bearing":
        row = catalog_data.deep_groove(size)
        if row is None:
            return []
        frames = [
            ("bore", [0, 0, row.width_mm / 2], "Bore centerline at mid-width."),
            ("seat", [0, 0, 0], "Face on z = 0."),
        ]
    elif kind == "tapered_roller_bearing":
        row = catalog_data.tapered(size)
        if row is None:
            return []
        frames = [
            ("bore", [0, 0, row.width_mm / 2], "Bore centerline at mid-width."),
            ("cone_back", [0, 0, 0], "Cone back face."),
            ("cup_back", [0, 0, row.width_mm], "Cup back face."),
        ]
    elif kind == "radial_shaft_seal":
        row = catalog_data.shaft_seal(size)
        if row is None:
            return []
        frames = [
            ("shaft", [0, 0, row.width_mm / 2], "Shaft centerline. The lip is at the nominal shaft diameter."),
            ("housing", [0, 0, row.width_mm / 2], "Same axis. The outside diameter seats in the housing bore."),
        ]
    elif kind == "socket_head_cap_screw":
        shank = 0.0 if length is None else float(length)
        frames = [
            ("head", [0, 0, 0], "Underside of the head. The shank points toward -Z."),
            ("axis", [0, 0, -shank / 2], "Midpoint of the shank."),
        ]
    elif kind == "hex_nut":
        row = catalog_data.hex_nut(size)
        height = 0.0 if row is None else row.height_mm
        frames = [("axis", [0, 0, height / 2], "Nut axis.")]
    elif kind == "plain_washer":
        row = catalog_data.plain_washer(size)
        height = 0.0 if row is None else row.thickness_mm
        frames = [("axis", [0, 0, height / 2], "Washer axis.")]
    elif kind == "stepper_motor":
        row = catalog_data.stepper(size)
        if row is None:
            return []
        dim = catalog_data.stepper_mm(row)
        frames = [
            ("mount", [0, 0, 0], "Mounting face. The shaft points toward +Z and the body toward -Z."),
            ("shaft", [0, 0, dim["shaft_length_mm"] / 2], "Shaft centerline, midway along the NEMA ICS 16 shaft."),
            ("shaft_end", [0, 0, dim["shaft_length_mm"]], "Tip of the shaft."),
        ]
    elif kind == "gearbox":
        row = catalog_data.gearbox(size)
        if row is None:
            return []
        frames = [
            ("input", [0, 0, 0], "Input face. The body and output shaft point toward +Z."),
            ("output", [0, 0, row.body_length_mm + row.output_length_mm / 2], "Output shaft centerline."),
        ]
    return [
        {"name": name, "anchor": anchor, "axis": [0.0, 0.0, 1.0], "note": note}
        for name, anchor, note in frames
    ]


def _mate_named(kind: str, size: str, length: float | None, name: str) -> dict[str, object]:
    kind = _canonical_kind(kind)
    found = _mate_list(kind, _canonical_size(kind, size), length)
    for frame in found:
        if str(frame["name"]).lower() == name.strip().lower():
            return frame
    known = ", ".join(str(frame["name"]) for frame in found) or "(none)"
    raise GeometryError(f"No mate {name!r} on this catalog part. Mates: {known}.")


def _kind_summary(kind: str) -> dict[str, object]:
    sizes = _sizes(kind)
    return {
        "kind": kind,
        "description": _describe(kind, sizes[0] if sizes else ""),
        "size_count": len(sizes),
        "size_examples": sizes[:6],
        "parameters": {"length": "required, millimeters, shank length under the head"} if kind == "socket_head_cap_screw" else {},
    }


def _matches_for_kind(kind: str, text: str) -> list[dict[str, object]]:
    if not _kind_mentioned(kind, text) and not _size_mentioned(kind, text):
        return []
    sizes = _sizes(kind)
    wanted = [size for size in sizes if _size_matches(kind, size, text)]
    if not wanted and _kind_mentioned(kind, text):
        wanted = sizes[:8]
    matches = []
    for size in wanted:
        try:
            record = metadata(kind, size, None)
        except GeometryError:
            continue
        matches.append(
            {
                "kind": kind,
                "size": record["size"],
                "aliases": record["aliases"],
                "description": record["description"],
                "standard": record["standard"],
                "dimensions": record["dimensions"],
                "load_ratings": record["load_ratings"],
                "mass_estimate_kg": record["mass_estimate_kg"],
                "mates": [mate["name"] for mate in record["mates"]],
                "parameters": record["parameters"],
            }
        )
    return matches


def _kind_mentioned(kind: str, text: str) -> bool:
    if kind.replace("_", " ") in text or kind in text:
        return True
    return any(word in text for word in _KIND_WORDS[kind])


def _size_mentioned(kind: str, text: str) -> bool:
    return any(_size_matches(kind, size, text) for size in _sizes(kind))


def _size_matches(kind: str, size: str, text: str) -> bool:
    if kind in _FASTENERS:
        tokens = set()
        for part in text.split():
            tokens.add(part.upper())
            if "-" in part:
                tokens.add(part.split("-", 1)[0].upper())
        head = size.split("-", 1)[0].upper()
        return head in tokens or size.upper() in tokens
    folded = "".join(text.lower().split()).replace("×", "x").replace("*", "x")
    if size.lower() in folded or size.lower() in text.lower():
        return True
    if kind == "ball_bearing":
        row = catalog_data.deep_groove(size)
        if row is not None:
            legacy = catalog_data.legacy_bearing_code(row)
            if legacy and legacy.lower() in folded:
                return True
    if kind == "tapered_roller_bearing":
        for alias, target in catalog_data._TAPERED_ALIASES.items():  # noqa: SLF001
            if target == size and alias.lower() in folded:
                return True
    return False


def _sizes(kind: str) -> list[str]:
    if kind == "socket_head_cap_screw":
        return _merged_fastener_sizes(kind, catalog_build.known_fastener_sizes()[kind], _bd_sizes(kind))
    if kind == "hex_nut":
        return _merged_fastener_sizes(kind, catalog_build.known_fastener_sizes()[kind], _bd_sizes(kind))
    if kind == "plain_washer":
        return _merged_fastener_sizes(kind, catalog_build.known_fastener_sizes()[kind], _bd_sizes(kind))
    if kind == "ball_bearing":
        return [row.designation for row in catalog_data.DEEP_GROOVE]
    if kind == "tapered_roller_bearing":
        return [row.designation for row in catalog_data.TAPERED]
    if kind == "radial_shaft_seal":
        return [row.designation for row in catalog_data.SHAFT_SEALS]
    if kind == "stepper_motor":
        return [row.designation for row in catalog_data.STEPPERS]
    if kind == "gearbox":
        return [row.designation for row in catalog_data.GEARBOXES]
    return []


def _merged_fastener_sizes(kind: str, local: list[str], extra: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for size in [*local, *extra]:
        canonical = _canonical_size(kind, size)
        if canonical in seen:
            continue
        seen.add(canonical)
        ordered.append(canonical)
    return ordered


def _describe(kind: str, size: str) -> str:
    if not size:
        return kind.replace("_", " ")
    try:
        return str(metadata(kind, size)["description"])
    except GeometryError:
        return size


def _reject_length(kind: str, length: float | None) -> None:
    if length is not None:
        raise GeometryError(f"{kind} has no length. Its size comes from the standard.")


def _iso_fastener(kind: str, fastener_type: str | None) -> bool:
    if fastener_type is None:
        return True
    expected = {
        "socket_head_cap_screw": "iso4762",
        "hex_nut": "iso4032",
        "plain_washer": "iso7089",
    }[kind]
    return fastener_type.strip().lower().replace(" ", "").replace("-", "") == expected


def _load_bd_warehouse() -> None:
    """Import bd_warehouse without reading a project font config or moving the process."""
    from aicad.paths import prepare_geometry_import

    prepare_geometry_import()


def _bd_sizes(kind: str) -> list[str]:
    try:
        _load_bd_warehouse()
        if kind == "socket_head_cap_screw":
            from bd_warehouse.fastener import SocketHeadCapScrew

            return list(SocketHeadCapScrew.sizes("iso4762"))
        if kind == "hex_nut":
            from bd_warehouse.fastener import HexNut

            return list(HexNut.sizes("iso4032"))
        if kind == "plain_washer":
            from bd_warehouse.fastener import PlainWasher

            return list(PlainWasher.sizes("iso7089"))
    except Exception:
        return []
    return []


def _bd_screw(size: str, length: float, fastener_type: str | None):
    _load_bd_warehouse()
    from bd_warehouse.fastener import SocketHeadCapScrew

    try:
        return SocketHeadCapScrew(size=size, length=float(length), fastener_type=fastener_type or "iso4762", simple=True)
    except Exception as exc:
        raise GeometryError(f"No socket head cap screw {size!r}. {exc}") from exc


def _bd_nut(size: str, fastener_type: str | None):
    _load_bd_warehouse()
    from bd_warehouse.fastener import HexNut

    try:
        return HexNut(size=size, fastener_type=fastener_type or "iso4032", simple=True)
    except Exception as exc:
        raise GeometryError(f"No hex nut {size!r}. {exc}") from exc


def _bd_washer(size: str, fastener_type: str | None):
    _load_bd_warehouse()
    from bd_warehouse.fastener import PlainWasher

    token = size.split("-", 1)[0] if "-" in size and size.upper().startswith("M") else size
    try:
        return PlainWasher(size=token, fastener_type=fastener_type or "iso7089")
    except Exception as exc:
        raise GeometryError(f"No plain washer {size!r}. {exc}") from exc
