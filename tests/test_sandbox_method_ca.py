"""CalculiX input stays one card per line.

The marker is CANARY_HIT. A part name, a note, or a material name does not
add a card, and that marker stays where it was written.
"""

from __future__ import annotations

import pytest

from aicad.errors import GeometryError
from aicad.fea import solver_deck
from aicad.modal import _frequency_deck

_MARKER = "CANARY_HIT"


def test_a_solver_line_cannot_carry_a_break():
    with pytest.raises(GeometryError, match="one line"):
        solver_deck(["*HEADING", f"aicad{_MARKER}\n*INCLUDE, INPUT=secret"])
    with pytest.raises(GeometryError, match="one line"):
        solver_deck(["*HEADING", f"aicad\u2028*INCLUDE, INPUT=secret"])
    text = solver_deck(["*HEADING", "aicad linear static", "*NODE", "1, 0.000000, 0.000000, 0.000000"])
    assert "*INCLUDE" not in text
    assert "\u2028" not in text
    assert _MARKER not in text


def test_a_material_name_is_not_a_calculix_card(tmp_path):
    marker = tmp_path / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    body = {
        "offset": 0,
        "points": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        "order": 1,
        "elements": [[0, 1, 2, 3]],
        "material": {
            "name": f"steel\n*INCLUDE, INPUT={marker}",
            "note": f"\u2028*INCLUDE, INPUT={marker}",
            "youngs_modulus_mpa": 2000.0,
            "poisson": 0.3,
            "density_g_cm3": 7.8,
        },
    }
    text = _frequency_deck([body], [], [], 1)
    assert "*INCLUDE" not in text
    assert "\u2028" not in text
    assert _MARKER not in text
    assert "steel" not in text
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
