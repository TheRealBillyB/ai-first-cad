"""Sizes written as top-level numbers in a part script.

A size is one assignment at the top of the script whose value is a number::

    width = 40
    height = 12.5

The name is a single identifier. The value is a number, or a negative number.
``part`` is the solid, so that name is not a size. Changing a size rewrites
that one number and leaves the rest of the script as it was.
"""

from __future__ import annotations

import ast
import math

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
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError("A size has to be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise GeometryError("A size has to be a finite number.")
    match = None
    for item in script_sizes(script):
        if item["name"] == name:
            match = item
    if match is None:
        known = ", ".join(item["name"] for item in script_sizes(script)) or "(none)"
        raise GeometryError(f"No size {name!r} in this script. Sizes: {known}.")
    node = match["node"]
    if node.end_lineno != node.lineno or node.end_col_offset is None:
        raise GeometryError(f"Size {name!r} has to be one number on one line.")
    replacement = _format_number(number)
    if _literal_number(replacement) is None:
        raise GeometryError(f"Size {name!r} could not be rewritten.")
    updated = _replace_span(script, node.lineno, node.col_offset, node.end_col_offset, replacement)
    _require_same_script(script, updated, name, replacement)
    return updated


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


def _literal_number(text: str) -> float | None:
    """The number ``text`` writes, when that text is only a numeric literal."""
    try:
        tree = ast.parse(f"x = {text}\n")
    except SyntaxError:
        return None
    if len(tree.body) != 1:
        return None
    statement = tree.body[0]
    if not isinstance(statement, ast.Assign):
        return None
    return _number(statement.value)


def _replace_span(script: str, lineno: int, start: int, end: int, replacement: str) -> str:
    """Replace the bytes ``ast`` names on one physical line.

    ``start`` and ``end`` are UTF-8 byte offsets from the start of that line.
    A physical line ends at ``\\n``, ``\\r\\n``, or ``\\r``.
    """
    try:
        data = script.encode("utf-8")
        new = replacement.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GeometryError("The size could not be rewritten.") from exc
    spans = _physical_lines(data)
    if lineno < 1 or lineno > len(spans):
        raise GeometryError("The size is outside the script.")
    line_start, line_end = spans[lineno - 1]
    content_end = _line_content_end(data, line_start, line_end)
    abs_start = line_start + start
    abs_end = line_start + end
    if abs_start < line_start or abs_end > content_end or abs_start > abs_end:
        raise GeometryError("The size could not be rewritten.")
    updated = data[:abs_start] + new + data[abs_end:]
    try:
        return updated.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GeometryError("The size could not be rewritten.") from exc


def _physical_lines(data: bytes) -> list[tuple[int, int]]:
    """Byte spans of physical lines, including the line break."""
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    length = len(data)
    while index < length:
        if data[index] == 10:
            index += 1
            spans.append((start, index))
            start = index
        elif data[index] == 13:
            index += 2 if index + 1 < length and data[index + 1] == 10 else 1
            spans.append((start, index))
            start = index
        else:
            index += 1
    if start < length or not spans:
        spans.append((start, length))
    return spans


def _line_content_end(data: bytes, start: int, end: int) -> int:
    """Byte offset where the line's content ends and its break begins."""
    if end > start and data[end - 1] == 10:
        if end - start >= 2 and data[end - 2] == 13:
            return end - 2
        return end - 1
    if end > start and data[end - 1] == 13:
        return end - 1
    return end


def _require_same_script(before: str, after: str, name: str, replacement: str) -> None:
    """The edit may change that one number and nothing else."""
    try:
        old = ast.parse(before)
        new = ast.parse(after)
    except SyntaxError as exc:
        raise GeometryError(f"Size {name!r} could not be rewritten.") from exc
    if _blank_size(old, name) is None or _blank_size(new, name) is None:
        raise GeometryError(f"Size {name!r} could not be rewritten.")
    if ast.dump(old) != ast.dump(new):
        raise GeometryError(f"Size {name!r} could not be rewritten.")
    got = None
    for item in script_sizes(after):
        if item["name"] == name:
            got = item["value"]
    expected = _literal_number(replacement)
    if got is None or expected is None or got != expected:
        raise GeometryError(f"Size {name!r} could not be rewritten.")


def _blank_size(tree: ast.AST, name: str) -> ast.Assign | None:
    """Point the last assignment of ``name`` at zero, so only that value may differ."""
    found = None
    for node in tree.body:
        parsed = _assignment(node)
        if parsed is not None and parsed[0] == name and isinstance(node, ast.Assign):
            found = node
    if found is None:
        return None
    found.value = ast.Constant(value=0)
    return found
