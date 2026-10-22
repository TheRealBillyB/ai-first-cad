"""Structured requirements checked against the current design.

Mass and build-volume checks are immediate. Wall thickness and a safety
factor reuse a cached result until the part changes, and run again when the
caller asks. The same records are what the workspace shows and what the
assistant reads.
"""

from __future__ import annotations

import json
from typing import Any

from aicad import fea
from aicad.errors import GeometryError
from aicad.honesty import ROUGH_ESTIMATE, VERIFIED_CHECK, finish
from aicad.printprep import fits_printer, orientation_size

_KINDS = ("max_mass", "fits_build_volume", "fits_printer", "min_wall", "min_safety_factor")


def normalize(payload: dict | list | None) -> list[dict]:
    """Accept ``{"requirements": [...]}`` or a bare list."""
    if payload is None:
        return []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("requirements", [])
    else:
        raise GeometryError("Requirements are a list, or an object with a requirements list.")
    if not isinstance(items, list):
        raise GeometryError("requirements must be a list.")
    cleaned = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise GeometryError(f"Requirement {index} must be an object.")
        kind = item.get("kind")
        if kind not in _KINDS:
            raise GeometryError(
                f"Requirement {index} kind {kind!r} is unknown. Use {', '.join(_KINDS)}."
            )
        identifier = str(item.get("id") or f"{kind}_{index + 1}")
        if identifier in seen:
            raise GeometryError(f"Two requirements share the id {identifier!r}.")
        seen.add(identifier)
        if not item.get("part"):
            raise GeometryError(f"Requirement {identifier!r} needs a part name.")
        cleaned.append({**item, "id": identifier, "kind": kind, "part": str(item["part"])})
    return cleaned


def evaluate(
    document,
    requirements: list[dict],
    *,
    cache: dict[str, dict] | None = None,
    run: bool = False,
) -> list[dict]:
    """Score each requirement. ``run`` solves FEA and wall checks that are not cached."""
    cache = cache if cache is not None else {}
    results = []
    for item in requirements:
        try:
            results.append(_one(document, item, cache, run))
        except GeometryError as exc:
            results.append(_row(item, "error", None, None, str(exc)))
    return results


def _one(document, item: dict, cache: dict[str, dict], run: bool) -> dict:
    part = item["part"]
    kind = item["kind"]
    if part not in {info.name for info in document.list_parts()}:
        return _row(item, "error", None, None, f"No part named {part!r}.")
    measured = document.measure(part)
    token = _token(measured)
    if kind == "max_mass":
        material = fea.lookup_material(str(item.get("material") or "pla"))
        density = float(material["density_g_cm3"])
        grams = measured.volume_mm3 * density / 1000.0
        limit = _number(item.get("max_grams"), "max_grams")
        status = "pass" if grams <= limit + 1e-6 else "fail"
        return _row(
            item,
            status,
            round(grams, 3),
            limit,
            (
                f"{part} in {material['name']} is {grams:.2f} g. "
                f"The limit is {limit:g} g. Density {density:g} g/cm³, volume {measured.volume_mm3:.1f} mm³."
            ),
            unit="g",
        )
    if kind == "fits_build_volume":
        size = item.get("size_mm")
        if not isinstance(size, (list, tuple)) or len(size) != 3:
            raise GeometryError(f"{item['id']}: size_mm must be [x, y, z] in millimeters.")
        envelope = sorted(float(value) for value in size)
        part_size = sorted(
            (
                measured.bounding_box.size.x,
                measured.bounding_box.size.y,
                measured.bounding_box.size.z,
            )
        )
        fits = all(part_size[index] <= envelope[index] + 1e-6 for index in range(3))
        return _row(
            item,
            "pass" if fits else "fail",
            [round(value, 3) for value in part_size],
            [round(value, 3) for value in envelope],
            (
                f"The part's sorted size is {part_size[0]:.2f} × {part_size[1]:.2f} × {part_size[2]:.2f} mm. "
                f"The build volume, also sorted, is {envelope[0]:.2f} × {envelope[1]:.2f} × {envelope[2]:.2f} mm. "
                "Sorting lets the part be turned to fit."
            ),
            unit="mm",
        )
    if kind == "fits_printer":
        printer = item.get("printer")
        if not printer:
            raise GeometryError(f"{item['id']}: fits_printer needs a printer, such as bambu_a1 or prusa_mk4.")
        orientation = str(item.get("orientation") or "best")
        sized = orientation_size(document._get(part).shape, orientation)  # noqa: SLF001
        fit = fits_printer(sized["size_mm"], str(printer))
        detail = (
            f"{part} in orientation {sized['orientation']} is "
            f"{fit['part_mm'][0]:.2f} × {fit['part_mm'][1]:.2f} × {fit['part_mm'][2]:.2f} mm. "
            f"{fit['name']} is {fit['bed_mm'][0]:.0f} × {fit['bed_mm'][1]:.0f} × {fit['bed_mm'][2]:.0f} mm. "
            "Height is the oriented Z. The footprint may swap X and Y."
        )
        return _row(
            item,
            "pass" if fit["fits"] else "fail",
            fit["part_mm"],
            fit["bed_mm"],
            detail,
            unit="mm",
        )
    key = _cache_key(item, token)
    if kind == "min_wall":
        if key not in cache:
            if not run:
                return _row(item, "unchecked", None, item.get("min_mm"), "Wall thickness has not been checked since this part changed.")
            limit = _number(item.get("min_mm"), "min_mm")
            report = document.check_wall_thickness(part, limit, int(item.get("samples") or 4))
            cache[key] = {
                "status": "pass" if report.passed else "fail",
                "measured": report.estimated_min_mm,
                "limit": limit,
                "detail": report.note,
                "unit": "mm",
            }
        return _row(item, **cache[key])
    if kind == "min_safety_factor":
        if key not in cache:
            if not run:
                return _row(
                    item,
                    "unchecked",
                    None,
                    item.get("minimum"),
                    "The safety factor has not been solved since this part or its load changed.",
                )
            setup = item.get("setup")
            if not isinstance(setup, dict):
                raise GeometryError(f"{item['id']}: min_safety_factor needs a setup object, the same one solve_mechanics uses.")
            limit = _number(item.get("minimum"), "minimum")
            merged = dict(setup)
            merged["target_safety_factor"] = limit
            report = document.solve_mechanics(part, merged)
            if report.status != "solved" or report.min_safety_factor is None:
                cache[key] = {
                    "status": "fail",
                    "measured": report.min_safety_factor,
                    "limit": limit,
                    "detail": report.note + " " + " ".join(report.warnings),
                    "unit": "safety factor",
                }
            else:
                review_status = report.review.get("status") if report.review else None
                status = safety_requirement_status(report.min_safety_factor, limit, review_status, solved=True)
                detail = report.note
                if status == "inconclusive":
                    detail += " The mesh is coarse or simplified, so this is not a pass."
                cache[key] = {
                    "status": status,
                    "measured": report.min_safety_factor,
                    "limit": limit,
                    "detail": detail,
                    "unit": "safety factor",
                    "review": review_status,
                }
        return _row(item, **cache[key])
    raise GeometryError(f"Unknown requirement kind {kind!r}.")


def _token(measured) -> str:
    size = measured.bounding_box.size
    return f"{measured.volume_mm3:.4f}:{size.x:.4f}:{size.y:.4f}:{size.z:.4f}"


def _cache_key(item: dict, token: str) -> str:
    extra = {key: value for key, value in item.items() if key != "title"}
    return item["part"] + ":" + token + ":" + json.dumps(extra, sort_keys=True, default=str)


def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a number.") from exc
    return number


def safety_requirement_status(measured, limit: float, review_status: str | None, solved: bool = True) -> str:
    """A coarse or simplified mesh cannot satisfy a safety-factor requirement."""
    if not solved or measured is None:
        return "fail"
    if review_status == "inconclusive":
        return "inconclusive"
    if review_status == "pass" and float(measured) + 1e-9 >= float(limit):
        return "pass"
    return "fail"


def _row(item: dict, status: str, measured, limit, detail: str, unit: str | None = None, **extra) -> dict:
    payload = {
        "id": item.get("id"),
        "title": item.get("title") or item.get("id"),
        "kind": item.get("kind"),
        "part": item.get("part"),
        "status": status,
        "measured": measured,
        "limit": limit,
        "unit": unit,
        "detail": detail,
    }
    payload.update(extra)
    claim = _CLAIM.get(str(item.get("kind") or ""))
    if claim is None:
        claim = (ROUGH_ESTIMATE, "This check did not finish.", ["Read the detail for what went wrong."])
    kind, what, limits = claim
    return finish(payload, kind=kind, name=str(payload["title"] or payload["kind"]), what=what, limits=limits)


_CLAIM = {
    "max_mass": (
        ROUGH_ESTIMATE,
        "Mass is the model volume times a handbook density. It is a rough estimate, not a weighed part.",
        [
            "The density is a library number. Pigment, infill, and moisture change a real part.",
            "The volume is the CAD solid, including any modeled holes and excluding a slicer's infill.",
        ],
    ),
    "fits_build_volume": (
        VERIFIED_CHECK,
        "The sorted bounding box of the model is compared with the sorted build volume.",
        [
            "Sorting lets the part be turned so its axes line up with the box.",
            "This is the model size, not the usable area of a printer after clips and a purge line.",
        ],
    ),
    "fits_printer": (
        ROUGH_ESTIMATE,
        "Printer fit uses one of six axis-up poses. The oriented size comes from the triangle mesh.",
        [
            "A diagonal pose is not tried.",
            "Height is the oriented Z. The footprint may swap X and Y.",
        ],
    ),
    "min_wall": (
        ROUGH_ESTIMATE,
        "Wall thickness is a sampled estimate. A pass can miss a thin spot between the samples.",
        [
            "Rays and a few planar slices produce the number.",
            "Anything thinner than about 0.15 mm is below the ray step.",
        ],
    ),
    "min_safety_factor": (
        ROUGH_ESTIMATE,
        "The safety factor is a linear static estimate. A coarse or simplified mesh is not a pass.",
        [
            "No plasticity, contact, buckling, or fatigue.",
            "A mesh that was simplified, or that is coarse or linear, stays inconclusive.",
        ],
    ),
}
