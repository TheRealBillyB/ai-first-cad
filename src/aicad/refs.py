"""Stable face and edge references shared by the viewport and the assistant.

A reference names geometry, not a session-local index. The same string is what
a click copies and what a load selector accepts as ``ref``.
"""

from __future__ import annotations

from aicad.errors import GeometryError


def face_ref(face: dict) -> str:
    """A selector string for one face in a :func:`aicad.kernel.describe_faces` record."""
    at = _fmt_point(face["center_mm"])
    if face.get("kind") == "plane" and face.get("normal"):
        return f"plane/{_axis_token(face['normal'])}/{at}"
    if face.get("kind") == "cylinder" and face.get("radius_mm") is not None and face.get("axis_direction"):
        radius = f"{float(face['radius_mm']):.3f}".rstrip("0").rstrip(".")
        return f"cylinder/r{radius}/{_axis_token(face['axis_direction'])}/{at}"
    return f"face/{int(face['index'])}/{at}"


def edge_ref(kind: str, midpoint: list[float]) -> str:
    safe = kind if kind in ("line", "circle", "curve") else "curve"
    return f"edge/{safe}/{_fmt_point(midpoint)}"


def ref_to_selector(ref: str) -> dict:
    """Turn a reference string into a face or edge selector."""
    if not isinstance(ref, str) or ref.count("/") < 2:
        raise GeometryError(
            f"Reference {ref!r} should look like plane/z+/0,0,4 or cylinder/r1.7/z+/0,0,2 "
            "or edge/line/5,0,2."
        )
    kind, rest = ref.split("/", 1)
    if kind == "plane":
        token, at = _split_token(rest, ref)
        return {"kind": "plane", "normal": _normal_from_token(token), "at": _point(at)}
    if kind == "cylinder":
        radius_token, tail = _split_token(rest, ref)
        axis_token, at = _split_token(tail, ref)
        if not radius_token.startswith("r"):
            raise GeometryError(f"Cylinder reference {ref!r} is missing its radius.")
        try:
            radius = float(radius_token[1:])
        except ValueError as exc:
            raise GeometryError(f"Cylinder reference {ref!r} has a bad radius.") from exc
        return {
            "kind": "cylinder",
            "radius": radius,
            "axis": _normal_from_token(axis_token),
            "at": _point(at),
        }
    if kind == "face":
        index_token, at = _split_token(rest, ref)
        try:
            index = int(index_token)
        except ValueError as exc:
            raise GeometryError(f"Face reference {ref!r} has a bad index.") from exc
        return {"kind": "index", "index": index, "at": _point(at)}
    if kind == "edge":
        edge_kind, at = _split_token(rest, ref)
        return {"kind": "edge", "edge_kind": edge_kind, "at": _point(at)}
    raise GeometryError(
        f"Unknown reference {ref!r}. Use plane, cylinder, face, or edge."
    )


def resolve_edge(edges: list[dict], ref: str) -> dict:
    """Pick the edge a reference points at."""
    selector = ref_to_selector(ref)
    if selector.get("kind") != "edge":
        raise GeometryError(f"{ref!r} is a face reference, not an edge.")
    if not edges:
        raise GeometryError("This part has no edges to select.")
    pool = [edge for edge in edges if edge.get("kind") == selector["edge_kind"]] or edges
    at = selector["at"]
    return min(pool, key=lambda edge: _distance(edge["midpoint_mm"], at))


def _split_token(text: str, ref: str) -> tuple[str, str]:
    if "/" not in text:
        raise GeometryError(f"Reference {ref!r} is incomplete.")
    token, rest = text.split("/", 1)
    return token, rest


def _axis_token(direction: list[float]) -> str:
    index = max(range(3), key=lambda item: abs(float(direction[item])))
    sign = "+" if float(direction[index]) >= 0 else "-"
    return "xyz"[index] + sign


def _normal_from_token(token: str) -> list[float]:
    if len(token) != 2 or token[0] not in "xyz" or token[1] not in "+-":
        raise GeometryError(f"Axis token {token!r} should look like z+ or x-.")
    normal = [0.0, 0.0, 0.0]
    normal["xyz".index(token[0])] = 1.0 if token[1] == "+" else -1.0
    return normal


def _point(text: str) -> list[float]:
    pieces = text.split(",")
    if len(pieces) != 3:
        raise GeometryError(f"Point {text!r} needs three comma-separated numbers.")
    try:
        return [float(piece) for piece in pieces]
    except ValueError as exc:
        raise GeometryError(f"Point {text!r} needs three numbers.") from exc


def _fmt_point(point) -> str:
    return ",".join(f"{float(component):.3f}" for component in point)


def _distance(a, b) -> float:
    return sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)) ** 0.5
