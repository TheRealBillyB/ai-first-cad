"""Sizes written as top-level numbers in a part script.

A size is one assignment at the top of the script whose value is a number::

    width = 40
    height = 12.5

The name is a single identifier. The value is a number, or a negative number.
``part`` is the solid, so that name is not a size. Changing a size rewrites
that number and rebuilds the script.
"""

from __future__ import annotations

import ast

from aicad.errors import GeometryError


def script_sizes(script: str) -> list[dict]:
    """Last top-level numeric assignment for each name, in source order."""
    try:
        tree = ast.parse(script or "")
    except SyntaxError:
        return []
    found: dict[str, tuple[float, ast.AST]] = {}
    order: list[str] = []
    for node in tree.body:
        parsed = _assignment(node)
        if parsed is None:
            continue
        name, value, value_node = parsed
        if name not in found:
            order.append(name)
        found[name] = (value, value_node)
    return [{"name": name, "value": found[name][0], "node": found[name][1]} for name in order]


def rewrite_script_size(script: str, name: str, value: float) -> str:
    """Replace one top-level number. The rest of the script stays as it was."""
    if not isinstance(script, str):
        raise GeometryError("The script has no sizes.")
    match = None
    for item in script_sizes(script):
        if item["name"] == name:
            match = item
    if match is None:
        known = ", ".join(item["name"] for item in script_sizes(script)) or "(none)"
        raise GeometryError(f"No size {name!r} in this script. Sizes: {known}.")
    node = match["node"]
    if node.end_lineno != node.lineno:
        raise GeometryError(f"Size {name!r} has to be one number on one line.")
    lines = script.splitlines(keepends=True)
    index = node.lineno - 1
    if index < 0 or index >= len(lines):
        raise GeometryError(f"Size {name!r} is outside the script.")
    line = lines[index]
    start = node.col_offset
    end = node.end_col_offset
    if start < 0 or end is None or end > len(line):
        raise GeometryError(f"Size {name!r} could not be rewritten.")
    lines[index] = line[:start] + _format_number(value) + line[end:]
    return "".join(lines)


def _assignment(node: ast.AST) -> tuple[str, float, ast.AST] | None:
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    target = node.targets[0]
    if not isinstance(target, ast.Name) or target.id == "part":
        return None
    if not target.id.isidentifier():
        return None
    value = _number(node.value)
    if value is None:
        return None
    return target.id, value, node.value


def _number(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _number(node.operand)
        if inner is None:
            return None
        return -inner
    return None


def _format_number(value: float) -> str:
    if float(value) == int(value) and abs(value) < 1e15:
        return str(int(value))
    return format(float(value), ".12g")
