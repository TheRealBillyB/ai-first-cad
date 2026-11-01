"""Ordered build history for one part.

The rows are the steps that rebuild the solid: a shape operation, a sketch
and its extrude or cut, a fillet, a chamfer, and the other sources. A row
carries the error from the last rebuild when that step did not fit.
"""

from __future__ import annotations


def history_for(part) -> list[dict]:
    errors = part.step_errors or {}
    rows: list[dict] = []
    source = part.source
    if source == "ops":
        for index, op in enumerate(part.ops or []):
            kind = "add"
            name = f"op {index + 1}"
            if isinstance(op, dict):
                kind = str(op.get("op") or "add")
                name = str(op.get("name") or op.get("shape") or name)
            rows.append(_row(f"op/{index}", kind, name, errors))
    elif source == "sketch":
        rows.append(_row("sketch", "sketch", str(part.sketch_name or "sketch"), errors))
        for index, step in enumerate(part.sketch_steps or []):
            kind = "step"
            name = f"step {index + 1}"
            if isinstance(step, dict):
                kind = str(step.get("op") or "step")
                name = str(step.get("name") or step.get("op") or name)
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
        rows.append(_row(source or "part", source or "part", part.name, errors))
    for index, op in enumerate(part.edge_ops or []):
        kind = "edge"
        name = f"edge {index + 1}"
        if isinstance(op, dict):
            kind = str(op.get("op") or "edge")
            name = str(op.get("name") or op.get("op") or name)
        rows.append(_row(f"edge/{index}", kind, name, errors))
    return rows


def _row(step_id: str, kind: str, name: str, errors: dict) -> dict:
    error = errors.get(step_id)
    return {
        "id": step_id,
        "kind": kind,
        "name": name,
        "error": None if error is None else str(error),
    }
