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

_KINDS = ("max_mass", "fits_build_volume", "min_wall", "min_safety_factor")


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
                cache[key] = {
                    "status": "pass" if report.min_safety_factor + 1e-9 >= limit and report.review and report.review.get("status") != "fail" else "fail",
                    "measured": report.min_safety_factor,
                    "limit": limit,
                    "detail": report.note,
                    "unit": "safety factor",
                    "review": report.review.get("status") if report.review else None,
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
    return payload
