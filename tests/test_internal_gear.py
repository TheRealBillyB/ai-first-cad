"""Internal spur ring: a watertight solid that meshes with a sun and a planet."""

import math
import struct
import zipfile
from pathlib import Path

import pytest

from aicad import kernel
from aicad.cli import main as cli_main
from aicad.document import Document
from aicad.errors import GeometryError
from aicad.gears import build_gear, check_pair, families, gear_data, mesh_placement, pair_geometry


def _ring(**overrides) -> dict:
    spec = {
        "type": "internal",
        "module_mm": 2,
        "teeth": 58,
        "face_width_mm": 6,
        "rim_thickness_mm": 4,
    }
    spec.update(overrides)
    return spec


def _spur(teeth: int, **overrides) -> dict:
    spec = {"type": "spur", "module_mm": 2, "teeth": teeth, "face_width_mm": 6}
    spec.update(overrides)
    return spec


def test_internal_ring_is_registered_and_uses_iso_diameters():
    assert families()["internal"]["axis_layout"] == "parallel"
    data = gear_data({"type": "ring", "module_mm": 2, "teeth": 58, "face_width_mm": 6})
    assert data["type"] == "internal"
    assert data["internal"] is True
    # d = 116, shortened addendum 1.8, dedendum 2.5, default rim four modules.
    assert data["reference_diameter_mm"] == pytest.approx(116)
    assert data["tip_diameter_mm"] == pytest.approx(112.4)
    assert data["root_diameter_mm"] == pytest.approx(121)
    assert data["base_diameter_mm"] == pytest.approx(116 * math.cos(math.radians(20)))
    assert data["rim_thickness_mm"] == pytest.approx(8)
    assert data["outside_diameter_mm"] == pytest.approx(137)
    assert data["tip_diameter_mm"] < data["reference_diameter_mm"] < data["root_diameter_mm"]
    thick = gear_data(_ring(rim_thickness_mm=3))
    assert thick["outside_diameter_mm"] == pytest.approx(121 + 6)


def test_internal_pair_clears_the_planet_by_a_quarter_module():
    """Rack A, zero shift: tip-to-root clearance is 0.25 module, and both turn the same way."""
    report = pair_geometry(_spur(19), _ring())
    assert report["mesh"] == "internal"
    assert report["centre_distance_mm"] == pytest.approx(39)
    assert report["tip_clearance_mm"] == pytest.approx(0.5)
    assert report["transverse_contact_ratio"] > 1.2
    assert report["placement"]["gear"]["position_mm"][0] == pytest.approx(39)
    checked = check_pair(_spur(19), _ring(), steps=36)
    assert checked["status"] == "pass", checked["findings"]
    assert checked["interference"] is False
    assert checked["tip_clearance_mm"] == pytest.approx(0.5)
    swapped = check_pair(_ring(), _spur(19), steps=36)
    assert swapped["status"] == "pass", swapped["findings"]
    # A positive pinion shift moves the zero-backlash centre outward.
    shifted = pair_geometry(_spur(19, profile_shift=0.4), _ring())
    assert shifted["centre_distance_mm"] > report["centre_distance_mm"]


def test_full_addendum_hits_a_small_planet_fillet():
    full = _ring(addendum_coefficient=1)
    report = check_pair(_spur(19), full, steps=24)
    assert report["status"] == "fail"
    assert any("interfere" in item.lower() for item in report["findings"])


def test_internal_ring_refuses_a_bore_a_helix_and_another_ring():
    with pytest.raises(GeometryError, match="bore"):
        gear_data(_ring(bore_mm=20))
    with pytest.raises(GeometryError, match="helix_angle_deg"):
        gear_data(_ring(helix_angle_deg=15))
    with pytest.raises(GeometryError, match="Two internal"):
        pair_geometry(_ring(), _ring(teeth=70))


def test_internal_ring_is_a_watertight_solid_and_exports(tmp_path: Path):
    spec = _ring()
    solid = kernel.adopt(build_gear(spec))
    measured = kernel.measure(solid)
    assert measured.watertight and measured.valid
    assert measured.volume_mm3 > 1000
    data = gear_data(spec)
    ra = data["tip_diameter_mm"] / 2
    rf = data["root_diameter_mm"] / 2
    ro = data["outside_diameter_mm"] / 2
    mid = (ra + rf) / 2
    half = math.pi / spec["teeth"]

    def inside(point) -> bool:
        return kernel.distance_to_point(solid, point)["inside"]

    assert inside((mid, 0.0, 0.0))
    assert inside(((rf + ro) / 2, 0.0, 0.0))
    assert not inside((mid * math.cos(half), mid * math.sin(half), 0.0))
    assert not inside((ra * 0.3, 0.0, 0.0))
    assert not inside((ro + 2.0, 0.0, 0.0))
    box = measured.bounding_box
    assert box.max.x == pytest.approx(ro, abs=0.05)
    assert box.min.z == pytest.approx(-3, abs=0.05)
    assert box.max.z == pytest.approx(3, abs=0.05)

    placement = mesh_placement(_spur(19), spec)
    planet = kernel.adopt(build_gear(_spur(19)))
    placed_planet = kernel.place(
        planet,
        tuple(placement["pinion"]["position_mm"]),
        tuple(placement["pinion"]["rotation_deg"]),
    )
    placed_ring = kernel.place(
        solid,
        tuple(placement["gear"]["position_mm"]),
        tuple(placement["gear"]["rotation_deg"]),
    )
    # Facets of the fillet can leave a sliver. The outline sweep is the mesh check.
    assert kernel.overlap_volume(placed_planet, placed_ring) < 0.01

    step = tmp_path / "ring.step"
    kernel.export_step_file(solid, step)
    loaded = kernel.import_step_file(step)
    again = kernel.measure(loaded)
    assert again.watertight and again.valid
    assert again.volume_mm3 == pytest.approx(measured.volume_mm3, rel=1e-3)

    stl = tmp_path / "ring.stl"
    kernel.export_stl_file(solid, stl)
    payload = stl.read_bytes()
    count = struct.unpack_from("<I", payload, 80)[0]
    assert count > 200

    mesh = tmp_path / "ring.3mf"
    kernel.export_3mf_file([("ring", solid)], mesh)
    with zipfile.ZipFile(mesh) as archive:
        model = archive.read("3D/3dmodel.model")
    assert b"<triangle" in model
    assert model.count(b"<vertex") > 50


def test_planetary_assembly_and_motion_use_the_generated_ring(tmp_path: Path):
    """Sun 20, planet 19, ring 58. The carrier is 10/39 of the sun. The solids are the generated gears."""
    document = Document(tmp_path / "set")
    sun = _spur(20)
    planet = _spur(19)
    ring = _ring()
    centre = pair_geometry(sun, planet)["centre_distance_mm"]
    assert centre == pytest.approx(pair_geometry(planet, ring)["centre_distance_mm"])
    document.create_gear("sun", sun)
    document.create_gear("planet", planet)
    document.create_gear("ring", ring)
    document.create_part_from_ops(
        "frame",
        [{"op": "add", "shape": "box", "size": [4, 4, 4], "center": [0, 0, 20]}],
    )
    document.create_part_from_ops(
        "carrier",
        [{"op": "add", "shape": "cylinder", "radius": 6, "height": 4, "center": [0, 0, 16]}],
    )
    ring_phase = 180.0 / 58
    document.create_assembly(
        {
            "name": "planetary",
            "instances": [
                {"name": "frame", "part": "frame"},
                {"name": "sun", "part": "sun"},
                {"name": "planet", "part": "planet"},
                {"name": "ring", "part": "ring"},
                {"name": "carrier", "part": "carrier"},
            ],
            "joints": [
                {"name": "sun", "type": "revolute", "parent": "frame", "child": "sun", "axis": [0, 0, 1]},
                {"name": "carrier", "type": "revolute", "parent": "frame", "child": "carrier", "axis": [0, 0, 1]},
                {
                    "name": "planet",
                    "type": "revolute",
                    "parent": "carrier",
                    "child": "planet",
                    "parent_anchor": [centre, 0, 0],
                    "axis": [0, 0, 1],
                },
                {
                    "name": "ring",
                    "type": "revolute",
                    "parent": "frame",
                    "child": "ring",
                    "axis": [0, 0, 1],
                    "value": ring_phase,
                },
            ],
            "gear_train": {
                "shafts": [
                    {"name": "sun", "joint": "sun", "gears": [{"name": "sun", "instance": "sun"}]},
                    {"name": "planet", "joint": "planet", "gears": [{"name": "planet", "instance": "planet"}]},
                    {"name": "ring", "joint": "ring", "gears": [{"name": "ring", "instance": "ring"}]},
                    {"name": "carrier", "joint": "carrier", "gears": []},
                ],
                "meshes": [
                    {"name": "sun_planet", "a": "sun", "b": "planet", "carrier": "carrier"},
                    {"name": "planet_ring", "a": "planet", "b": "ring", "kind": "internal", "carrier": "carrier"},
                ],
                "fixed": ["ring"],
            },
            "ignore_collisions": [
                ["sun", "planet"],
                ["planet", "ring"],
                ["sun", "ring"],
                ["frame", "ring"],
            ],
            "studies": {
                "spin": {
                    "name": "spin",
                    "duration_s": 1,
                    "steps": 5,
                    "drivers": [{"joint": "sun", "expression": "360 * t"}],
                }
            },
        }
    )
    mesh = document.check_gears({"assembly": "planetary", "steps": 24})
    assert mesh["status"] == "pass", [pair.get("findings") for pair in mesh["pairs"]]
    assert mesh["passed"] == 2

    solved = document.solve_gear_train(
        "planetary",
        {"inputs": [{"shaft": "sun", "speed_rpm": 390, "torque_nm": 2}]},
    )
    by_name = {row["name"]: row for row in solved["shafts"]}
    assert by_name["ring"]["gears"][0]["teeth"] == 58
    assert by_name["carrier"]["speed_ratio_exact"] == "10/39"
    assert by_name["ring"]["speed_rpm"] == pytest.approx(0)

    study = document.run_motion_study("planetary", sequence="spin")
    assert study["status"] == "clear"

    def value(step, name):
        return next(joint["value"] for joint in step["joints"] if joint["name"] == name)

    assert value(study["steps"][0], "sun") == pytest.approx(0, abs=1e-6)
    assert value(study["steps"][0], "ring") == pytest.approx(ring_phase, abs=1e-3)
    end = study["steps"][-1]
    assert value(end, "sun") == pytest.approx(360, abs=1e-3)
    assert value(end, "carrier") == pytest.approx(360 * 10 / 39, abs=1e-3)
    assert value(end, "ring") == pytest.approx(ring_phase, abs=1e-3)
    assert value(end, "planet") == pytest.approx(360 * -(20 / 19) * (29 / 39), abs=1e-3)


def test_command_line_builds_an_internal_ring(tmp_path: Path):
    assert (
        cli_main(
            [
                "gear",
                "ring",
                "--type",
                "internal",
                "--module",
                "2",
                "--teeth",
                "58",
                "--face-width",
                "6",
                "--rim",
                "4",
                "--workspace",
                str(tmp_path / "cli"),
            ]
        )
        == 0
    )
