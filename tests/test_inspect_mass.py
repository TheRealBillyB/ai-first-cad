"""Inspect reports surface area, center of mass, and mass from a material density."""

from pathlib import Path

import pytest

from aicad import Document, GeometryError

ROOT = Path(__file__).resolve().parents[1]
BOX = "part = Box(10, 20, 30)\n"


def test_mass_is_volume_times_the_chosen_density(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("box", BOX)
    measured = document.measure("box", "steel")
    assert measured.area_mm2 == pytest.approx(2200)
    assert measured.center_of_mass_mm.z == pytest.approx(0, abs=1e-6)
    assert measured.volume_mm3 == pytest.approx(6000)
    assert measured.material == "steel"
    assert measured.density_g_cm3 == pytest.approx(7.85)
    assert measured.mass_g == pytest.approx(6000 * 7.85 / 1000)
    payload = measured.to_dict()
    assert payload["mass_g"] == measured.mass_g
    assert payload["area_mm2"] == measured.area_mm2
    assert "handbook density" in " ".join(payload["limits"])
    plain = document.measure("box")
    assert plain.mass_g is None
    assert "mass_g" not in plain.to_dict()
    with pytest.raises(GeometryError, match="No material"):
        document.measure("box", "unobtanium")


def test_a_catalog_envelope_is_not_reported_as_volume_times_density(tmp_path):
    document = Document(tmp_path)
    document.insert_standard_part("motor", "stepper_motor", "NEMA17")
    measured = document.measure("motor", "steel")
    assert measured.volume_mm3 > 0
    assert measured.mass_g is None
    assert measured.density_g_cm3 is None
    assert "not a motor mass" in (measured.mass_note or "")
    payload = measured.to_dict()
    assert "mass_g" not in payload
    assert "mass_note" in payload
    wrong = measured.volume_mm3 * 7.85 / 1000.0
    assert wrong > 1
    inspected = document.inspect("motor", material="steel")
    assert inspected.measurement.mass_g is None


def test_a_published_catalog_mass_does_not_follow_the_material(tmp_path):
    document = Document(tmp_path)
    document.insert_standard_part("drive", "gearbox", "S-MG17-G10")
    measured = document.measure("drive", "aluminum")
    assert measured.mass_g == pytest.approx(310)
    assert measured.density_g_cm3 is None
    assert measured.material is None
    payload = measured.to_dict()
    assert payload["mass_g"] == pytest.approx(310)
    assert "density_g_cm3" not in payload
    assert "catalog" in " ".join(payload["assumptions"]).lower()
    assert "310" in str(payload["mass_note"]) or "Catalog mass" in str(payload["mass_note"])


def test_inspect_panel_shows_area_center_and_mass():
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert "Surface area" in script
    assert "Center of mass" in script
    assert "Mass" in script
    assert "inspect-material" in script
    start = script.index("function inspectHtml")
    body = script[start : script.index("function featureHtml")]
    assert "area_mm2" in body
    assert "center_of_mass_mm" in body
    assert "mass_g" in body
    assert "mass_note" in body
