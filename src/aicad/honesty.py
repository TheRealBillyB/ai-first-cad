"""Label every check as a verified check or a rough estimate.

A verified check is a property of the CAD model that the geometry kernel
computes directly: a volume, a gap, a collision at a sampled pose, the
degrees of freedom of a sketch. A rough estimate samples the solid, meshes
it, or multiplies by a handbook number. Neither one is a certified test of
a physical part.

The same fields travel in the CLI JSON, the MCP report, and the workspace.
:func:`attach_sheet` also writes a blueprint sheet so the label is on paper.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from aicad.errors import GeometryError

ROUGH_ESTIMATE = "rough_estimate"
VERIFIED_CHECK = "verified_check"

LABELS = {
    ROUGH_ESTIMATE: "Rough estimate",
    VERIFIED_CHECK: "Verified check",
}

_PAPER = (10, 48, 96)
_INK = (236, 244, 252)
_GOLD = (255, 214, 120)
_PALE = (186, 214, 236)
_FRAME = (120, 176, 220)
_SANS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)
_SANS_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    *_SANS,
)


def finish(
    payload: dict,
    *,
    kind: str,
    what: str,
    limits: list[str],
    name: str | None = None,
    results: list[dict] | None = None,
) -> dict:
    """Put the label first. Existing keys, including assumptions, stay."""
    if kind not in LABELS:
        raise ValueError(f"Unknown result kind {kind!r}.")
    header: dict[str, object] = {
        "result_kind": kind,
        "result_label": LABELS[kind],
        "result_what": what,
        "limits": list(limits),
    }
    if name:
        header["result_name"] = name
    if results is not None:
        header["results"] = results
    skip = set(header)
    body = {key: value for key, value in payload.items() if key not in skip}
    return {**header, **body}


def slim(payload: dict, name: str) -> dict:
    """The label block of a nested result, without the numbers."""
    return {
        "result_name": name,
        "result_kind": payload.get("result_kind"),
        "result_label": payload.get("result_label"),
        "result_what": payload.get("result_what"),
        "limits": list(payload.get("limits") or []),
        "assumptions": list(payload.get("assumptions") or []),
    }


def weakest(kinds: list[str]) -> str:
    """A combined report is only as strong as its roughest number."""
    if ROUGH_ESTIMATE in kinds or not kinds:
        return ROUGH_ESTIMATE
    return VERIFIED_CHECK


def sheet_lines(payload: dict) -> list[str]:
    """Plain lines drawn on the blueprint sheet. Tests read these."""
    lines: list[str] = []
    intro = payload.get("result_what")
    if intro and payload.get("results"):
        lines.append(str(intro))
    for block in _blocks(payload):
        title = block.get("result_name") or block.get("result_label") or "Result"
        lines.append(str(title))
        if block.get("result_label"):
            lines.append(str(block["result_label"]))
        if block.get("result_what"):
            lines.append(str(block["result_what"]))
        for limit in block.get("limits") or []:
            lines.append(f"Limit: {limit}")
        for assumption in block.get("assumptions") or []:
            lines.append(f"Assumption: {assumption}")
    if not lines and payload.get("result_label"):
        lines.append(str(payload["result_label"]))
        if payload.get("result_what"):
            lines.append(str(payload["result_what"]))
    return lines


def write_result_sheet(path: Path | str, payload: dict, *, title: str) -> dict:
    """Draw one blueprint sheet. Light text on a blue field, like a shop print."""
    destination = Path(path)
    blocks = _blocks(payload) or [payload]
    width = 1400
    font = _font(_SANS, 22)
    font_bold = _font(_SANS_BOLD, 28)
    font_small = _font(_SANS, 18)
    font_title = _font(_SANS_BOLD, 36)
    margin = 36
    text_width = width - margin * 2
    wrapped: list[tuple[object, str]] = []
    wrapped.append((font_title, title))
    subtitle = payload.get("result_label")
    if subtitle and not payload.get("results") and not payload.get("checks"):
        wrapped.append((font_bold, str(subtitle)))
    if payload.get("result_what") and (payload.get("results") or payload.get("checks")):
        wrapped.extend((font, line) for line in _wrap(str(payload["result_what"]), font, text_width))
    for block in blocks:
        name = str(block.get("result_name") or "Result")
        label = str(block.get("result_label") or "")
        wrapped.append((font_bold, f"{name}  ·  {label}" if label else name))
        if block.get("result_what"):
            wrapped.extend((font, line) for line in _wrap(str(block["result_what"]), font, text_width))
        for limit in block.get("limits") or []:
            wrapped.extend((font_small, line) for line in _wrap(f"Limit: {limit}", font_small, text_width))
        for assumption in block.get("assumptions") or []:
            wrapped.extend((font_small, line) for line in _wrap(f"Assumption: {assumption}", font_small, text_width))
        wrapped.append((font_small, ""))
    wrapped.append((font_small, "A verified check describes the CAD model. A rough estimate is not a certified test of a physical part."))
    line_height = 28
    height = margin * 2 + 24 + line_height * len(wrapped)
    image = Image.new("RGB", (width, max(height, 320)), _PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 12, width - 12, image.size[1] - 12), outline=_FRAME, width=3)
    y = margin
    for face, line in wrapped:
        fill = _GOLD if face is font_bold or face is font_title else _INK if face is font else _PALE
        if face is font_title:
            fill = _INK
        draw.text((margin, y), line, font=face, fill=fill)
        y += line_height
    from aicad.paths import publish_file

    def _save(temporary: Path) -> None:
        image.save(temporary, format="PNG")

    try:
        publish_file(destination, _save)
    except OSError as exc:
        raise GeometryError(f"Could not write {destination}. {exc.strerror or exc}.") from exc
    return {
        "name": "result_sheet",
        "path": str(destination),
        "width": image.size[0],
        "height": image.size[1],
    }


def attach_sheet(payload: dict, directory: Path | str, stem: str, root: Path | str | None = None) -> dict:
    """Write a blueprint sheet next to the other project files, once."""
    if not isinstance(payload, dict) or payload.get("sheet") or not _has_claim(payload):
        return payload
    if root is not None:
        from aicad.paths import output_dir

        try:
            folder = output_dir(Path(root), directory, "Result sheet", GeometryError)
        except OSError as exc:
            raise GeometryError(
                f"Could not create the folder for {directory}. {exc.strerror or exc}."
            ) from exc
    else:
        folder = Path(directory)
        try:
            from aicad.paths import ensure_real_directory

            ensure_real_directory(folder)
        except OSError as exc:
            raise GeometryError(
                f"Could not create the folder for {folder}. {exc.strerror or exc}."
            ) from exc
    filename = _safe_stem(stem, payload) + ".png"
    path = folder / filename
    title = _sheet_title(payload, stem)
    view = write_result_sheet(path, payload, title=title)
    view["path"] = _public_path(path, Path(root) if root is not None else None)
    stamped = dict(payload)
    stamped["sheet"] = view
    return stamped


def _has_claim(payload: dict) -> bool:
    if payload.get("result_kind"):
        return True
    if payload.get("results"):
        return True
    checks = payload.get("checks")
    return isinstance(checks, list) and any(isinstance(item, dict) and item.get("result_kind") for item in checks)


def _blocks(payload: dict) -> list[dict]:
    checks = payload.get("checks")
    if isinstance(checks, list):
        found = [item for item in checks if isinstance(item, dict) and item.get("result_label")]
        if found:
            return found
    results = payload.get("results")
    if isinstance(results, list):
        found = [item for item in results if isinstance(item, dict) and item.get("result_label")]
        if found:
            return found
    if payload.get("result_label"):
        return [payload]
    return []


def _sheet_title(payload: dict, stem: str) -> str:
    name = payload.get("result_name") or payload.get("part") or stem.replace("-", " ")
    if isinstance(name, dict):
        name = name.get("name") or stem
    label = payload.get("result_label") or "Result"
    return f"{label}: {name}"


def _safe_stem(stem: str, payload: dict) -> str:
    part = payload.get("part") or payload.get("part_a") or ""
    if isinstance(part, dict):
        part = part.get("name") or ""
    raw = f"{stem}_{part}" if part else stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(raw)).strip("_") or "result"
    from aicad.paths import is_reserved_device_name

    # con.png is a device on Windows, whatever the extension is.
    if is_reserved_device_name(cleaned):
        raise GeometryError(f"{cleaned} is reserved on Windows. Pick another name.")
    return cleaned[:80]


def _public_path(path: Path, root: Path | None) -> str:
    resolved = path.resolve()
    if root is not None:
        try:
            return str(resolved.relative_to(root.resolve()))
        except ValueError:
            return str(resolved)
    return str(resolved)


def _wrap(text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = []
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


def _length(font: ImageFont.ImageFont, text: str) -> float:
    if hasattr(font, "getlength"):
        return float(font.getlength(text))
    return float(font.getsize(text)[0])  # type: ignore[attr-defined]


def _font(candidates: tuple[str, ...], size: int) -> ImageFont.ImageFont:
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()
