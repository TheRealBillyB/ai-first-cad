"""Standard parts, placement, and clearance."""

import pytest

from aicad import Document


def test_search_finds_an_m3_screw_and_a_608_bearing(tmp_path):
    document = Document(tmp_path)
    screws = document.search_parts("M3 screw")
    kinds = {match["kind"] for match in screws["matches"]}
    assert "socket_head_cap_screw" in kinds
    assert any(match["size"] == "M3-0.5" for match in screws["matches"])

    bearings = document.search_parts("608")
    match = next(item for item in bearings["matches"] if item["kind"] == "ball_bearing")
    assert match["size"] == "608"
    assert "M8-22-7" in match["aliases"]
    assert match["dimensions"]["bore_mm"] == 8
    assert match["dimensions"]["outside_mm"] == 22
    assert match["dimensions"]["width_mm"] == 7

    screws_only = [item["size"] for item in screws["matches"] if item["kind"] == "socket_head_cap_screw"]
    assert screws_only == ["M3-0.5"]
    assert all(item["kind"] == "socket_head_cap_screw" for item in screws["matches"])


def test_a_longer_designation_does_not_match_a_shorter_catalog_size(tmp_path):
    document = Document(tmp_path)
    assert _bearing_sizes(document, "60008") == []
    assert _bearing_sizes(document, "62005") == []
    assert "608" not in _bearing_sizes(document, "1608")
    seals = [
        item["size"]
        for item in document.search_parts("20x35x70")["matches"]
        if item["kind"] == "radial_shaft_seal"
    ]
    assert seals == []
    assert "6008" in _bearing_sizes(document, "6008")
    assert "6205" in _bearing_sizes(document, "6205 bearing")
    assert "608" in _bearing_sizes(document, "608 bearing")
    assert "20x35x7" in {
        item["size"]
        for item in document.search_parts("20 x 35 x 7")["matches"]
        if item["kind"] == "radial_shaft_seal"
    }
    tapered = [
        item["size"]
        for item in document.search_parts("32005")["matches"]
        if item["kind"] == "tapered_roller_bearing"
    ]
    assert tapered == ["32005X"]
    assert "6000" in _bearing_sizes(document, "M10-26-8")
    assert _bearing_sizes(document, "M10-26-80") == []


def _bearing_sizes(document: Document, query: str) -> list[str]:
    return [
        item["size"]
        for item in document.search_parts(query)["matches"]
        if item["kind"] == "ball_bearing"
    ]


def test_a_size_that_is_not_in_the_catalog_is_not_replaced_by_other_sizes(tmp_path):
    document = Document(tmp_path)
    missed = document.search_parts("M3-0.8 screw")
    assert missed["matches"] == []
    washers = document.search_parts("M3-0.8")
    assert all(item["size"] != "M3" for item in washers["matches"])
    sample = document.search_parts("screw")
    assert len(sample["matches"]) == 8
    assert {item["kind"] for item in sample["matches"]} == {"socket_head_cap_screw"}


def test_screw_clears_a_hole_and_a_pin_does_not(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script(
        "plate",
        "part = Pos(0, 0, 2) * Box(20, 20, 4) - Cylinder(1.7, 12)\n",
    )
    # The shank is the ISO 724 basic minor diameter, without threads.
    # The head is held 1 mm above the plate so the reported gap is the shank-to-hole gap.
    document.insert_standard_part(
        "screw",
        "screw",
        "M3-0.5",
        length=12,
        position=(0, 0, 5),
    )
    gap = document.check_clearance("plate", "screw", min_clearance_mm=0.2)
    assert gap.intersecting is False
    assert gap.min_distance_mm == pytest.approx(0.47, abs=0.08)
    assert gap.passed is True

    document.create_part_from_script("pin", "part = Cylinder(1.9, 8)\n")
    clash = document.check_clearance("plate", "pin", min_clearance_mm=0.0)
    assert clash.intersecting is True
    assert clash.interference_volume_mm3 > 1


def test_place_moves_a_part_and_survives_reload(tmp_path):
    document = Document(tmp_path)
    document.insert_standard_part("nut", "hex_nut", "M3-0.5")
    before = document.measure("nut").center_of_mass_mm
    document.place_part("nut", (10, 0, 4), (0, 0, 90))
    moved = document.measure("nut").center_of_mass_mm
    assert moved.x == pytest.approx(before.x + 10, abs=0.2)
    assert moved.z == pytest.approx(before.z + 4, abs=0.2)

    reopened = Document(tmp_path)
    again = reopened.measure("nut").center_of_mass_mm
    assert again.x == pytest.approx(moved.x)
    assert again.z == pytest.approx(moved.z)
    assert reopened.get_source("nut")["source"] == "library"


def test_imported_step_can_be_placed(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("box", "part = Box(4, 4, 4)\n")
    step = document.export("box", tmp_path / "box.step", "step")
    document.import_step("brought", step)
    document.place_part("brought", (8, 0, 0))
    assert document.measure("brought").center_of_mass_mm.x == pytest.approx(8, abs=1e-3)
    reopened = Document(tmp_path)
    assert reopened.measure("brought").center_of_mass_mm.x == pytest.approx(8, abs=1e-3)
