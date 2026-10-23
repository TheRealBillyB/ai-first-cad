"""Catalog dimensions, solids, bill of materials, and joint mates.

Reference numbers below are written out, not read back from the tables.
Deep-groove 6001 is ISO 15: 12 × 28 × 8 mm. Tapered 30205 is ISO 355:
25 mm bore, 52 mm outside, 16.25 mm overall, 15 mm cone, 13 mm cup.
32005X is 25 × 47 × 15 mm, cone 15 mm, cup 11.5 mm. The 20 × 35 × 7 seal
is ISO 6194-1 Table 1. NEMA 17 face numbers are ICS 16 inches times 25.4.
ISO 4762 M5 head is 8.5 × 5 mm. ISO 4032 M5 is 8 mm across flats and 4.7 mm
high. ISO 7089 M5 is 5.3 × 10 × 1 mm.
"""

import math

import pytest

from aicad import library
from aicad.errors import GeometryError
from aicad.kinematics import transform_point
from aicad import Document

INCH = 25.4


def _match(document: Document, query: str, kind: str) -> dict:
    found = document.search_parts(query)
    return next(item for item in found["matches"] if item["kind"] == kind)


def _pin(document: Document, name: str, radius: float, length: float, at: tuple[float, float, float]) -> None:
    document.create_part_from_script(name, f"part = Cylinder({radius}, {length})\n")
    document.place_part(name, at)


def test_search_uses_published_designations(tmp_path):
    document = Document(tmp_path)
    kinds = {item["kind"] for item in document.search_parts("")["matches"]}
    assert {
        "ball_bearing",
        "tapered_roller_bearing",
        "radial_shaft_seal",
        "stepper_motor",
        "gearbox",
    } <= kinds

    bearing = _match(document, "6001", "ball_bearing")
    assert bearing["dimensions"]["bore_mm"] == 12
    assert bearing["dimensions"]["outside_mm"] == 28
    assert bearing["dimensions"]["width_mm"] == 8
    assert bearing["load_ratings"]["dynamic_n"] == 5070
    assert bearing["load_ratings"]["static_n"] == 2360
    assert bearing["mass_estimate_kg"] == pytest.approx(0.022)
    assert bearing["standard"] == "ISO 15"

    tapered = _match(document, "30205", "tapered_roller_bearing")
    assert tapered["dimensions"]["bore_mm"] == 25
    assert tapered["dimensions"]["outside_mm"] == 52
    assert tapered["dimensions"]["width_mm"] == 16.25
    assert tapered["dimensions"]["cone_width_mm"] == 15
    assert tapered["dimensions"]["cup_width_mm"] == 13
    assert tapered["load_ratings"]["dynamic_n"] == 38100
    assert tapered["load_ratings"]["static_n"] == 33500
    assert tapered["standard"] == "ISO 355"

    narrow = _match(document, "32005", "tapered_roller_bearing")
    assert narrow["size"] == "32005X"
    assert narrow["dimensions"]["bore_mm"] == 25
    assert narrow["dimensions"]["outside_mm"] == 47
    assert narrow["dimensions"]["width_mm"] == 15
    assert narrow["dimensions"]["cone_width_mm"] == 15
    assert narrow["dimensions"]["cup_width_mm"] == 11.5

    seal = _match(document, "20×35×7", "radial_shaft_seal")
    assert seal["size"] == "20x35x7"
    assert seal["dimensions"] == {"shaft_mm": 20, "housing_mm": 35, "width_mm": 7}
    assert seal["standard"] == "DIN 3760 / ISO 6194"
    assert _match(document, "10x22x7", "radial_shaft_seal")["dimensions"]["housing_mm"] == 22
    assert _match(document, "25 x 40 x 7", "radial_shaft_seal")["dimensions"]["shaft_mm"] == 25
    assert _match(document, "50x72x8", "radial_shaft_seal")["dimensions"]["width_mm"] == 8

    motor = _match(document, "NEMA17", "stepper_motor")
    assert motor["dimensions"]["bolt_circle_mm"] == pytest.approx(1.725 * INCH)
    assert motor["dimensions"]["pilot_mm"] == pytest.approx(0.8661 * INCH)
    assert motor["dimensions"]["shaft_mm"] == pytest.approx(0.1969 * INCH)
    assert motor["dimensions"]["shaft_length_mm"] == pytest.approx(0.812 * INCH)
    assert motor["dimensions"]["flange_mm"] == pytest.approx(1.7 * INCH)
    assert motor["load_ratings"]["continuous_shaft_torque_nm"] == pytest.approx(1.0 * 0.112984829, abs=5e-4)
    assert motor["load_ratings"]["source"] == "NEMA ICS 16 Table 6"
    assert motor["mass_estimate_kg"] is None

    box = _match(document, "S-MG17-G10", "gearbox")
    assert box["dimensions"]["ratio"] == 10
    assert box["dimensions"]["flange_mm"] == 42
    assert box["dimensions"]["input_bore_depth_mm"] == 10.5
    assert box["mass_estimate_kg"] == pytest.approx(0.310)
    assert box["load_ratings"]["permissible_torque_nm"] == 5
    other = _match(document, "S-MG23-G10-D8", "gearbox")
    assert other["dimensions"]["input_bore_mm"] == 8
    assert other["mass_estimate_kg"] is None

    screw = _match(document, "M5 screw", "socket_head_cap_screw")
    assert screw["size"] == "M5-0.8"
    assert screw["dimensions"]["head_diameter_mm"] == 8.5
    assert screw["dimensions"]["head_height_mm"] == 5
    nut = _match(document, "M5 nut", "hex_nut")
    assert nut["dimensions"]["across_flats_mm"] == 8
    assert nut["dimensions"]["height_mm"] == 4.7
    washer = _match(document, "M5 washer", "plain_washer")
    assert washer["size"] == "M5"
    assert washer["dimensions"]["inside_mm"] == 5.3
    assert washer["dimensions"]["outside_mm"] == 10
    assert washer["dimensions"]["thickness_mm"] == 1


def test_solids_match_the_boundary_and_are_not_boxes(tmp_path):
    document = Document(tmp_path)
    document.insert_standard_part("race", "ball_bearing", "6001")
    race = document.measure("race")
    assert race.valid is True
    assert race.volume_mm3 > 1000
    assert race.bounding_box.size.x == pytest.approx(28, abs=0.05)
    assert race.bounding_box.size.z == pytest.approx(8, abs=0.05)
    assert race.bounding_box.min.z == pytest.approx(0, abs=0.05)

    document.insert_standard_part("taper", "tapered", "30205")
    taper = document.measure("taper")
    assert taper.valid is True
    assert taper.bounding_box.size.x == pytest.approx(52, abs=0.15)
    assert taper.bounding_box.min.z == pytest.approx(0, abs=0.05)
    assert taper.bounding_box.max.z == pytest.approx(16.25, abs=0.15)
    _pin(document, "bore_clear", 12.4, 30, (0, 0, 8))
    assert document.check_clearance("taper", "bore_clear").intersecting is False
    _pin(document, "bore_hit", 12.7, 30, (0, 0, 8))
    assert document.check_clearance("taper", "bore_hit").intersecting is True
    # Cup starts at T - C = 3.25. A probe near the outside at the cone end misses it.
    _pin(document, "cone_end", 0.4, 0.6, (25.5, 0, 0.4))
    assert document.check_clearance("taper", "cone_end").intersecting is False
    _pin(document, "cup_end", 0.4, 0.6, (25.5, 0, 15.85))
    assert document.check_clearance("taper", "cup_end").intersecting is True
    # Cone width is 15 mm, so just short of the cup back face the bore is open.
    _pin(document, "past_cone", 0.3, 0.4, (13.5, 0, 15.9))
    assert document.check_clearance("taper", "past_cone").intersecting is False

    document.insert_standard_part("seal", "seal", "20x35x7")
    seal = document.measure("seal")
    assert seal.bounding_box.size.x == pytest.approx(35, abs=0.05)
    assert seal.bounding_box.max.z == pytest.approx(7, abs=0.05)
    _pin(document, "shaft_clear", 9.85, 20, (0, 0, 3.5))
    assert document.check_clearance("seal", "shaft_clear").intersecting is False
    _pin(document, "shaft_hit", 10.2, 20, (0, 0, 3.5))
    assert document.check_clearance("seal", "shaft_hit").intersecting is True

    document.insert_standard_part("motor", "motor", "NEMA17")
    motor = document.measure("motor")
    assert motor.bounding_box.size.x == pytest.approx(1.7 * INCH, abs=0.05)
    assert motor.bounding_box.max.z == pytest.approx(0.812 * INCH, abs=0.05)
    assert motor.bounding_box.min.z == pytest.approx(-48, abs=0.05)
    radius = 1.725 * INCH / 2
    hole = radius / math.sqrt(2)
    _pin(document, "in_hole", 0.6, 6, (hole, hole, -2))
    assert document.check_clearance("motor", "in_hole").intersecting is False
    _pin(document, "in_flange", 0.6, 6, (radius * math.cos(math.radians(20)), radius * math.sin(math.radians(20)), -2))
    assert document.check_clearance("motor", "in_flange").intersecting is True

    document.insert_standard_part("drive", "gearbox", "S-MG17-G10")
    drive = document.measure("drive")
    assert drive.bounding_box.size.x == pytest.approx(42, abs=0.05)
    assert drive.bounding_box.max.z == pytest.approx(40 + 21.5, abs=0.05)
    assert drive.bounding_box.min.z == pytest.approx(0, abs=0.05)
    # The default NEMA 17 shaft is longer than this published input bore.
    assert document.check_clearance("motor", "drive").intersecting is True

    document.insert_standard_part("screw", "socket_head_cap_screw", "M5-0.8", length=16)
    screw = document.measure("screw")
    assert screw.bounding_box.size.x == pytest.approx(8.5, abs=0.05)
    assert screw.bounding_box.min.z == pytest.approx(-16, abs=0.05)
    assert screw.bounding_box.max.z == pytest.approx(5, abs=0.05)

    document.insert_standard_part("nut", "hex_nut", "M5")
    nut = document.measure("nut")
    flats = sorted((nut.bounding_box.size.x, nut.bounding_box.size.y))
    assert flats[0] == pytest.approx(8, abs=0.05)
    assert flats[1] == pytest.approx(8 * 2 / math.sqrt(3), abs=0.08)
    assert nut.bounding_box.size.z == pytest.approx(4.7, abs=0.05)

    document.insert_standard_part("washer", "washer", "M5")
    washer = document.measure("washer")
    assert washer.bounding_box.size.x == pytest.approx(10, abs=0.05)
    assert washer.bounding_box.size.z == pytest.approx(1, abs=0.05)
    _pin(document, "washer_clear", 2.4, 4, (0, 0, 0.5))
    assert document.check_clearance("washer", "washer_clear").intersecting is False
    _pin(document, "washer_hit", 2.8, 4, (0, 0, 0.5))
    assert document.check_clearance("washer", "washer_hit").intersecting is True


def test_a_size_outside_the_local_table_still_builds(tmp_path):
    note = library.metadata("socket_head_cap_screw", "M30-3.5", 40)["dimension_note"]
    assert "bd_warehouse" in note
    document = Document(tmp_path)
    document.insert_standard_part("fine", "socket_head_cap_screw", "M30-3.5", length=40)
    assert document.measure("fine").valid is True
    with pytest.raises(GeometryError, match="No deep-groove"):
        document.insert_standard_part("missing", "ball_bearing", "nope")


def test_bill_of_materials_uses_catalog_mass_and_ratings(tmp_path):
    document = Document(tmp_path)
    document.insert_standard_part("bearing", "ball_bearing", "608")
    document.insert_standard_part("seal", "radial_shaft_seal", "20x35x7")
    document.insert_standard_part("motor", "stepper", "NEMA17")
    document.insert_standard_part("box", "gearbox", "S-MG17-G10")
    document.create_assembly(
        {
            "name": "kit",
            "instances": [
                {"name": "bearing", "part": "bearing"},
                {"name": "seal", "part": "seal"},
                {"name": "motor", "part": "motor"},
                {"name": "box", "part": "box"},
            ],
            "joints": [
                {
                    "name": "face",
                    "type": "fixed",
                    "parent": "motor",
                    "child": "box",
                    "parent_mate": "mount",
                    "child_mate": "input",
                }
            ],
        }
    )
    rows = {row["part"]: row for row in document.bill_of_materials("kit")["rows"]}
    assert rows["bearing"]["category"] == "standard"
    assert rows["bearing"]["size"] == "608"
    assert rows["bearing"]["standard"] == "ISO 15"
    assert rows["bearing"]["mass_estimate_kg"] == pytest.approx(0.012)
    assert "Catalog mass" in rows["bearing"]["note"]
    assert "3450" in rows["bearing"]["note"]
    assert rows["seal"]["standard"] == "DIN 3760 / ISO 6194"
    assert rows["seal"]["size"] == "20x35x7"
    assert "volume" in rows["seal"]["note"]
    assert rows["motor"]["mass_estimate_kg"] is None
    assert rows["motor"]["standard"] == "NEMA ICS 16"
    assert "0.113" in rows["motor"]["note"]
    assert rows["box"]["mass_estimate_kg"] == pytest.approx(0.310)
    assert "does not follow the instance material" in rows["box"]["note"]


def test_mates_drive_the_joint_and_survive_reload(tmp_path):
    document = Document(tmp_path)
    document.insert_standard_part("motor", "motor", "NEMA17")
    document.insert_standard_part("bearing", "bearing", "608")
    document.create_part_from_script("block", "part = Box(20, 20, 10)\n")
    stored = document.create_assembly(
        {
            "name": "spin",
            "instances": [
                {"name": "motor", "part": "motor"},
                {"name": "bearing", "part": "bearing"},
            ],
            "joints": [
                {
                    "name": "spin",
                    "type": "revolute",
                    "parent": "motor",
                    "child": "bearing",
                    "parent_mate": "shaft",
                    "child_mate": "bore",
                    "value": 90,
                }
            ],
        }
    )
    joint = stored["assembly"]["joints"][0]
    assert joint["parent_mate"] == "shaft"
    assert joint["child_mate"] == "bore"
    assert joint["parent_anchor"][2] == pytest.approx(0.812 * INCH / 2, abs=1e-4)
    assert joint["child_anchor"][2] == pytest.approx(3.5, abs=1e-6)
    bearing = next(item for item in stored["solved"]["instances"] if item["name"] == "bearing")
    world = transform_point(bearing["matrix"], [4, 0, 3.5])
    assert math.hypot(world[0], world[1]) == pytest.approx(4, abs=0.05)
    assert abs(world[0]) == pytest.approx(0, abs=0.05)
    assert world[2] == pytest.approx(0.812 * INCH / 2, abs=0.05)

    reopened = Document(tmp_path)
    again = reopened.get_assembly("spin")["assembly"]["joints"][0]
    assert again["parent_mate"] == "shaft"
    assert again["parent_anchor"][2] == pytest.approx(0.812 * INCH / 2, abs=1e-4)

    with pytest.raises(GeometryError, match="not a catalog part"):
        document.create_assembly(
            {
                "name": "bad",
                "instances": [
                    {"name": "block", "part": "block"},
                    {"name": "bearing", "part": "bearing"},
                ],
                "joints": [
                    {
                        "name": "seat",
                        "type": "fixed",
                        "parent": "block",
                        "child": "bearing",
                        "parent_mate": "bore",
                        "child_mate": "bore",
                    }
                ],
            }
        )
