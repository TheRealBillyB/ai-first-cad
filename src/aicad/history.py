"""Ordered build history for one part.

The rows are the steps that rebuild the solid: a shape operation, a sketch
and its extrude or cut, a fillet, a chamfer, and the other sources. A row
carries the error from the last rebuild when that step did not fit.
"""

from __future__ import annotations

from aicad.labels import plain_line, strip_hidden


def history_for(part) -> list[dict]:
    errors = part.step_errors if isinstance(part.step_errors, dict) else {}
    rows: list[dict] = []
    source = part.source
    if source == "ops":
        for index, op in enumerate(_records(part.ops)):
            kind = "add"
            name = f"op {index + 1}"
            if isinstance(op, dict):
                kind = _label(op.get("op"), "add")
                name = _label(op.get("name") or op.get("shape"), name)
            rows.append(_row(f"op/{index}", kind, name, errors))
    elif source == "sketch":
        rows.append(_row("sketch", "sketch", _label(part.sketch_name, "sketch"), errors))
        for index, step in enumerate(_records(part.sketch_steps)):
            kind = "step"
            name = f"step {index + 1}"
            if isinstance(step, dict):
                kind = _label(step.get("op"), "step")
                name = _label(step.get("name") or step.get("op"), name)
            rows.append(_row(f"step/{index}", kind, name, errors))
    elif source == "script":
        rows.append(_row("script", "script", "script", errors))
    elif source == "library":
        rows.append(_row("library", "library", "catalog", errors))
    elif source == "step":
        rows.append(_row("import", "import", "imported", errors))
    elif source == "gear":
        rows.append(_row("gear", "gear", "gear", errors))
    else:
        label = _label(source, "part")
        rows.append(_row(label, label, _label(getattr(part, "name", None), label), errors))
    for index, op in enumerate(_records(part.edge_ops)):
        kind = "edge"
        name = f"edge {index + 1}"
        if isinstance(op, dict):
            kind = _label(op.get("op"), "edge")
            name = _label(op.get("name") or op.get("op"), name)
        rows.append(_row(f"edge/{index}", kind, name, errors))
    return rows


def _records(value) -> list:
    """Step rows are a list. A number, a string, or an object is not a list of steps."""
    return value if isinstance(value, list) else []


def _label(value, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = plain_line(value)
    return text or fallback


def _row(step_id: str, kind: str, name: str, errors: dict) -> dict:
    error = errors.get(step_id)
    return {
        "id": step_id,
        "kind": kind,
        "name": name,
        "error": None if error is None else (strip_hidden(str(error)) or None),
    }
