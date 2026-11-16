"""The fatigue check treats 7075 as an aluminum and refuses a line material.

7075 fell through to the plastics note, and Dyneema, a tendon line, was
checked as if it were a solid part.
"""

from __future__ import annotations

import pytest

from aicad.errors import GeometryError
from aicad.modal import fatigue_check


def test_7075_uses_the_aluminum_long_life_stand_in():
    report = fatigue_check({"material": "7075", "stress_amplitude_mpa": 50, "surface": "machined", "section_mm": 8})
    assert report["ultimate_mpa"] == pytest.approx(572.0)
    text = str(report)
    assert "Aluminum has no true endurance limit" in text
    assert "Plastics and prints" not in text


def test_a_line_material_is_refused():
    with pytest.raises(GeometryError, match="line material"):
        fatigue_check({"material": "dyneema", "stress_amplitude_mpa": 50})
