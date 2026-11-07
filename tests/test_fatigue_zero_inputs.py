"""A zero or negative section or target is refused, not replaced by the default."""

from __future__ import annotations

import pytest

from aicad.errors import GeometryError
from aicad.modal import fatigue_check

_BASE = {"material": "steel", "stress_amplitude_mpa": 100, "surface": "machined"}


@pytest.mark.parametrize("section", [0, -2, "0"])
def test_a_section_that_is_not_positive_is_refused(section):
    with pytest.raises(GeometryError, match="section_mm"):
        fatigue_check({**_BASE, "section_mm": section, "target_safety_factor": 2})


@pytest.mark.parametrize("target", [0, -1, "0"])
def test_a_target_that_is_not_positive_is_refused(target):
    # A target of -1 used to pass every part, and 0 quietly became 2.
    with pytest.raises(GeometryError, match="target_safety_factor"):
        fatigue_check({**_BASE, "section_mm": 8, "target_safety_factor": target})


def test_a_missing_section_and_target_still_take_the_defaults():
    report = fatigue_check(dict(_BASE))
    assert report["section_mm"] == 8.0
    assert report["target_safety_factor"] == 2.0
    # Steel: Se = 0.5 * 420 * 0.8 * 0.9 = 151.2 MPa, so 100 MPa gives 1.512.
    assert report["safety_factor"] == pytest.approx(1.512)
    assert report["status"] == "fail"


def test_the_numbers_given_are_the_numbers_used():
    report = fatigue_check({**_BASE, "section_mm": 12, "target_safety_factor": 1.4})
    assert report["section_mm"] == 12.0
    assert report["size_factor"] == 0.85
    # Se = 0.5 * 420 * 0.8 * 0.85 = 142.8 MPa, so 100 MPa gives 1.428.
    assert report["target_safety_factor"] == 1.4
    assert report["status"] == "pass"


def test_a_section_that_is_not_a_number_is_named():
    with pytest.raises(GeometryError, match="section_mm must be a number"):
        fatigue_check({**_BASE, "section_mm": "thick"})
