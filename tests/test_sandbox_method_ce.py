"""A cable or tendon name is one line before it is stored.

The marker is CANARY_HIT. A line separator in the name is refused. A plain
name is kept, and that marker stays where it was written.
"""

from __future__ import annotations

import pytest

from aicad.errors import GeometryError
from aicad.kinematics import normalize

_MARKER = "CANARY_HIT"


def _assembly(cable: dict) -> dict:
    return {
        "name": "arm",
        "instances": [{"name": "base", "part": "base"}],
        "joints": [],
        "cables": [cable],
    }


def test_a_cable_name_with_a_line_separator_is_not_stored(tmp_path):
    marker = tmp_path / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    for name in ("hose\u2028next", "hose\u2029next", "hose\nnext"):
        with pytest.raises(GeometryError, match="one line"):
            normalize(_assembly({"name": name, "waypoints": []}))
    kept = normalize(_assembly({"name": "coolant", "kind": "hose"}))
    assert kept["cables"][0]["name"] == "coolant"
    tendon = {
        "name": "pull\u2028wire",
        "points": [{"role": "anchor", "name": "end\u2029a", "instance": "base"}],
    }
    with pytest.raises(GeometryError, match="one line"):
        normalize(_assembly(tendon))
    plain = {
        "name": "pull",
        "points": [{"role": "anchor", "name": "end_a", "instance": "base"}],
    }
    stored = normalize(_assembly(plain))
    assert stored["cables"][0]["name"] == "pull"
    assert stored["cables"][0]["points"][0]["name"] == "end_a"
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
    assert "\u2028" not in str(stored)
