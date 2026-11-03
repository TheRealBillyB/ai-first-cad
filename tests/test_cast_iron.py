"""Grey cast iron in the stress library."""

import pytest

from aicad.fea import list_materials, lookup_material
from aicad.modal import fatigue_check


def test_grey_cast_iron_is_in_the_library_with_cited_numbers():
    ids = {row["id"] for row in list_materials()["materials"]}
    assert "grey_cast_iron" in ids
    iron = lookup_material("grey_cast_iron")
    assert iron["strength_mpa"] == 207.0
    # Brittle: the strength is a tensile rupture strength, not a yield.
    assert iron["strength_kind"] == "tensile"
    assert iron["youngs_modulus_mpa"] == 100000.0
    assert iron["poisson"] == 0.26
    assert iron["density_g_cm3"] == 7.15
    assert iron["printed"] is False
    assert "ASTM A48" in iron["note"]
    assert "brittle" in iron["note"]
    assert "no yield" in iron["note"]


@pytest.mark.parametrize(
    "name",
    ["cast iron", "cast-iron", "Grey Iron", "gray iron", "gray cast iron", "ASTM A48 Class 30", "EN-GJL-200"],
)
def test_common_cast_iron_names_find_the_entry(name):
    assert lookup_material(name)["id"] == "grey_cast_iron"


def test_cast_iron_fatigue_uses_its_tensile_strength_as_sut():
    report = fatigue_check(
        {
            "material": "cast iron",
            "stress_amplitude_mpa": 20,
            "surface": "machined",
            "section_mm": 8,
        }
    )
    assert report["ultimate_mpa"] == pytest.approx(207.0)
    # 0.3 * 207 * 0.8 * 0.9 = 44.7. Amplitude 20 passes.
    assert report["endurance_mpa"] == pytest.approx(44.7, abs=0.1)
    assert report["status"] == "pass"
