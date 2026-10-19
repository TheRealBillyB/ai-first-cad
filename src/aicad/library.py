"""Standard fasteners and bearings.

This module is part of the kernel boundary. It imports bd_warehouse, which
returns build123d solids. Callers outside aicad only see catalog records and
the document's named parts.
"""

from __future__ import annotations

from aicad.errors import GeometryError

# Common bearing designations for the SKT deep-groove table (bore-outer-width).
_BEARING_ALIASES = {
    "608": "M8-22-7",
    "624": "M4-13-5",
    "625": "M5-16-5",
    "626": "M6-19-6",
    "6000": "M10-26-8",
    "6001": "M12-28-8",
    "6200": "M10-30-9",
}

_KINDS = (
    "socket_head_cap_screw",
    "hex_nut",
    "plain_washer",
    "ball_bearing",
)


def search(query: str = "") -> dict[str, object]:
    """Find catalog entries. An empty query lists the kinds, not every size."""
    text = " ".join(query.lower().split())
    if not text:
        return {
            "query": query,
            "matches": [_kind_summary(kind) for kind in _KINDS],
            "truncated": False,
            "note": (
                "Pass a size or a word such as 'M3', 'screw', 'nut', 'washer', "
                "'bearing', or '608' to list matching parts. Screws also need a length "
                "when you insert them. Orientation: screws point shank-down (-Z) with the "
                "underside of the head on z=0; nuts, washers, and bearings sit on z=0 and "
                "extend toward +Z."
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
            "Sizes are bd_warehouse standard designations. Insert with "
            "insert_standard_part, then check_clearance against your part. "
            "Imported STEP files use import_step and place_part."
        ),
    }


def build(kind: str, size: str, length: float | None = None, fastener_type: str | None = None):
    """Return an unplaced build123d solid for a catalog entry."""
    kind = _canonical_kind(kind)
    size = _canonical_size(kind, size)
    if kind == "socket_head_cap_screw":
        if length is None or length <= 0:
            raise GeometryError(
                "socket_head_cap_screw requires length in millimeters (the shank under the head)."
            )
        from bd_warehouse.fastener import SocketHeadCapScrew

        standard = fastener_type or "iso4762"
        return SocketHeadCapScrew(size=size, length=float(length), fastener_type=standard, simple=True)
    if kind == "hex_nut":
        if length is not None:
            raise GeometryError("hex_nut has no length. Its height comes from the standard.")
        from bd_warehouse.fastener import HexNut

        return HexNut(size=size, fastener_type=fastener_type or "iso4032", simple=True)
    if kind == "plain_washer":
        if length is not None:
            raise GeometryError("plain_washer has no length. Its thickness comes from the standard.")
        from bd_warehouse.fastener import PlainWasher

        return PlainWasher(size=size, fastener_type=fastener_type or "iso7089")
    if kind == "ball_bearing":
        if length is not None:
            raise GeometryError("ball_bearing has no length. Width is part of the size, such as M8-22-7.")
        from bd_warehouse.bearing import SingleRowDeepGrooveBallBearing

        return SingleRowDeepGrooveBallBearing(size=size, bearing_type=fastener_type or "SKT")
    raise GeometryError(f"Unknown part kind {kind!r}. Use one of: {', '.join(_KINDS)}.")


def _canonical_kind(kind: str) -> str:
    key = kind.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "screw": "socket_head_cap_screw",
        "shcs": "socket_head_cap_screw",
        "socket_head": "socket_head_cap_screw",
        "cap_screw": "socket_head_cap_screw",
        "nut": "hex_nut",
        "washer": "plain_washer",
        "bearing": "ball_bearing",
        "ballbearing": "ball_bearing",
    }
    key = aliases.get(key, key)
    if key not in _KINDS:
        raise GeometryError(f"Unknown part kind {kind!r}. Use one of: {', '.join(_KINDS)}.")
    return key


def _canonical_size(kind: str, size: str) -> str:
    token = size.strip()
    if kind == "ball_bearing" and token in _BEARING_ALIASES:
        token = _BEARING_ALIASES[token]
    if kind == "plain_washer" and "-" in token and token.upper().startswith("M"):
        # Washers are sized "M3", not "M3-0.5".
        token = token.split("-", 1)[0]
    return token


def _kind_summary(kind: str) -> dict[str, object]:
    sizes = _sizes(kind)
    return {
        "kind": kind,
        "description": _describe(kind, sizes[0] if sizes else ""),
        "size_count": len(sizes),
        "size_examples": sizes[:6],
        "parameters": _parameters(kind),
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
        matches.append(
            {
                "kind": kind,
                "size": size,
                "aliases": _aliases_for(kind, size),
                "description": _describe(kind, size),
                "parameters": _parameters(kind),
            }
        )
    return matches


def _kind_mentioned(kind: str, text: str) -> bool:
    words = {
        "socket_head_cap_screw": ("screw", "shcs", "socket", "cap", "fastener", "bolt"),
        "hex_nut": ("nut", "hex"),
        "plain_washer": ("washer",),
        "ball_bearing": ("bearing", "ball", "608"),
    }
    if kind.replace("_", " ") in text or kind in text:
        return True
    return any(word in text for word in words[kind])


def _size_mentioned(kind: str, text: str) -> bool:
    return any(_size_matches(kind, size, text) for size in _sizes(kind))


def _size_matches(kind: str, size: str, text: str) -> bool:
    folded = text.replace(" ", "")
    if size.lower() in folded or size.lower() in text:
        return True
    for alias, target in _BEARING_ALIASES.items():
        if kind == "ball_bearing" and size == target and alias in folded:
            return True
    # "m3" matches "M3-0.5" and washer "M3", but not "M30".
    token = folded.upper()
    head = size.split("-", 1)[0].upper()
    if head and (token == head or f"{head}-" in size.upper() and head in token.split("-")):
        if token == head or token == size.upper():
            return True
    if head and token == head:
        return True
    return False


def _aliases_for(kind: str, size: str) -> list[str]:
    if kind != "ball_bearing":
        return []
    return [alias for alias, target in _BEARING_ALIASES.items() if target == size]


def _parameters(kind: str) -> dict[str, str]:
    if kind == "socket_head_cap_screw":
        return {"length": "required, millimeters, shank length under the head"}
    return {}


def _describe(kind: str, size: str) -> str:
    if kind == "socket_head_cap_screw":
        return (
            f"ISO 4762 socket head cap screw {size}. "
            "Shank points down (-Z); the underside of the head is on z=0. Pass length in mm."
        )
    if kind == "hex_nut":
        return f"ISO 4032 hex nut {size}. Sits on z=0 and extends toward +Z."
    if kind == "plain_washer":
        return f"ISO 7089 plain washer {size}. Sits on z=0 and extends toward +Z."
    aliases = ", ".join(_aliases_for(kind, size))
    extra = f" Also known as {aliases}." if aliases else ""
    return f"Deep-groove ball bearing {size} (bore-outer-width in mm).{extra} Axis is +Z, sitting on z=0."


def _sizes(kind: str) -> list[str]:
    if kind == "socket_head_cap_screw":
        from bd_warehouse.fastener import SocketHeadCapScrew

        return list(SocketHeadCapScrew.sizes("iso4762"))
    if kind == "hex_nut":
        from bd_warehouse.fastener import HexNut

        return list(HexNut.sizes("iso4032"))
    if kind == "plain_washer":
        from bd_warehouse.fastener import PlainWasher

        return list(PlainWasher.sizes("iso7089"))
    if kind == "ball_bearing":
        from bd_warehouse.bearing import SingleRowDeepGrooveBallBearing

        available = set(SingleRowDeepGrooveBallBearing.sizes("SKT"))
        return [size for size in SingleRowDeepGrooveBallBearing.sizes("SKT") if size in available]
    return []
