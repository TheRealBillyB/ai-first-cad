"""Through-hardened and case-hardening steels in the stress library."""

import pytest

from aicad.errors import GeometryError
from aicad.fea import list_materials, lookup_material
from aicad.modal import fatigue_check


def test_gear_steels_are_in_the_library_with_cited_numbers():
    ids = {row["id"] for row in list_materials()["materials"]}
    assert {"steel_42crmo4", "steel_16mncr5"} <= ids
    through = lookup_material("42CrMo4")
    assert through["id"] == "steel_42crmo4"
    assert through["strength_mpa"] == 750.0
    assert through["strength_kind"] == "yield"
    assert through["ultimate_mpa"] == 1000.0
    assert through["youngs_modulus_mpa"] == 210000.0
    assert "EN 10083-3:2006" in through["note"]
    case = lookup_material("16MnCr5")
    assert case["id"] == "steel_16mncr5"
    assert case["strength_mpa"] == 590.0
    assert case["ultimate_mpa"] == 780.0
    assert case["endurance"] == "steel"
    assert "EN 10084:2008" in case["note"]
    assert "core" in case["note"]


def test_a_gear_steel_uses_the_steel_endurance_line():
    """Se' = 0.5 · 1000, machined 0.8, section at most 10 mm so the size factor is 0.9."""
    report = fatigue_check(
        {
            "material": "steel_42crmo4",
            "stress_amplitude_mpa": 100,
            "mean_stress_mpa": 0,
            "surface": "machined",
            "section_mm": 8,
        }
    )
    assert report["ultimate_mpa"] == pytest.approx(1000)
    assert report["endurance_mpa"] == pytest.approx(360.0, abs=0.1)
    assert report["status"] == "pass"
    case = fatigue_check(
        {
            "material": "16mncr5",
            "stress_amplitude_mpa": 400,
            "surface": "machined",
            "section_mm": 8,
        }
    )
    # 0.5 * 780 * 0.8 * 0.9 = 280.8. Amplitude 400 is above that.
    assert case["ultimate_mpa"] == pytest.approx(780)
    assert case["endurance_mpa"] == pytest.approx(280.8, abs=0.1)
    assert case["status"] == "fail"


def test_an_unknown_steel_is_still_refused():
    with pytest.raises(GeometryError, match="No material"):
        lookup_material("not-a-steel")
