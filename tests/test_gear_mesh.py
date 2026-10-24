"""Mesh and backlash check: a true pair passes, a tight centre interferes, a wide centre is out of range."""

import math
from pathlib import Path

import pytest

from aicad.cli import main as cli_main
from aicad.document import Document
from aicad.errors import GeometryError
from aicad.gears import (
    check_pair,
    gear_data,
    mesh_placement,
    pair_geometry,
    register_mesh_check,
    registered_mesh_checks,
    unregister_mesh_check,
)
from aicad.server import build_server
from aicad.workspace import Workspace

_RANGE = {"min": 0.0, "max": 0.05}


def _spur(teeth: int) -> dict:
    return {
        "type": "spur",
        "module_mm": 2,
        "teeth": teeth,
        "pressure_angle_deg": 20,
        "face_width_mm": 8,
    }


def _pair():
    return _spur(20), _spur(40)


def test_a_correct_spur_pair_passes_and_covers_one_turn():
    pinion, gear = _pair()
    nominal = pair_geometry(pinion, gear)
    report = check_pair(pinion, gear, backlash_mm=_RANGE)
    assert report["status"] == "pass"
    assert report["result_kind"] == "verified_check"
    assert report["interference"] is False
    assert report["undercut"] is False
    assert report["centre_distance_mm"] == pytest.approx(nominal["centre_distance_mm"])
    assert report["ideal_centre_distance_mm"] == pytest.approx(nominal["centre_distance_mm"])
    assert report["centre_distance_error_mm"] == pytest.approx(0, abs=1e-6)
    assert report["backlash"]["circumferential_mm"] == pytest.approx(0, abs=1e-6)
    assert report["backlash"]["normal_mm"] == pytest.approx(0, abs=1e-6)
    assert report["backlash"]["in_range"] is True
    assert report["total_contact_ratio"] == pytest.approx(nominal["total_contact_ratio"], abs=1e-6)
    assert report["total_contact_ratio"] > 1
    sweep = report["sweep"]
    assert sweep["rotation_deg"] >= 360 - 1e-6
    assert sweep["steps"] >= 16
    assert sweep["step_deg"] <= 2
    assert sweep["worst_penetration_mm"] <= 0.02
    assert report["smaller_gear"] == "pinion"
    assert report["findings"] == []


def test_a_tight_centre_reports_interference():
    pinion, gear = _pair()
    ideal = pair_geometry(pinion, gear)["centre_distance_mm"]
    report = check_pair(pinion, gear, {"centre_distance_mm": ideal - 0.3}, backlash_mm=_RANGE)
    assert report["status"] == "fail"
    assert report["interference"] is True
    assert report["centre_distance_error_mm"] == pytest.approx(-0.3, abs=1e-6)
    assert report["backlash"]["circumferential_mm"] < 0
    assert report["sweep"]["worst_penetration_mm"] > 0.02
    assert report["sweep"]["rotation_deg"] >= 360 - 1e-6
    assert any("interfer" in item.lower() or "overlap" in item.lower() for item in report["findings"])


def test_excess_clearance_reports_backlash_out_of_range():
    pinion, gear = _pair()
    ideal = pair_geometry(pinion, gear)["centre_distance_mm"]
    centre = ideal + 0.25
    report = check_pair(pinion, gear, {"centre_distance_mm": centre}, backlash_mm=_RANGE)
    alpha = math.radians(20)
    working = math.acos(math.cos(alpha) * ideal / centre)
    expected = 2 * centre * ((math.tan(working) - working) - (math.tan(alpha) - alpha))
    assert report["backlash"]["circumferential_mm"] == pytest.approx(expected, abs=1e-4)
    assert report["backlash"]["normal_mm"] == pytest.approx(expected * math.cos(working), abs=1e-4)
    assert expected > 0.05
    assert report["status"] == "fail"
    assert report["interference"] is False
    assert report["backlash"]["in_range"] is False
    assert report["sweep"]["worst_penetration_mm"] <= 0.02
    assert any("backlash" in item.lower() and "outside" in item.lower() for item in report["findings"])


def test_helical_and_bevel_pairs_pass_and_a_bevel_offset_opens_the_backlash():
    pinion = {
        "type": "helical",
        "module_mm": 2,
        "teeth": 20,
        "pressure_angle_deg": 20,
        "helix_angle_deg": 15,
        "face_width_mm": 12,
    }
    gear = {
        "type": "helical",
        "module_mm": 2,
        "teeth": 36,
        "pressure_angle_deg": 20,
        "helix_angle_deg": -15,
        "face_width_mm": 10,
    }
    nominal = pair_geometry(pinion, gear)
    report = check_pair(pinion, gear, backlash_mm=_RANGE)
    assert report["status"] == "pass"
    assert report["axis_layout"] == "parallel"
    assert report["total_contact_ratio"] == pytest.approx(nominal["total_contact_ratio"], abs=1e-6)
    assert report["sweep"]["sections"] == 3
    assert report["sweep"]["rotation_deg"] >= 360 - 1e-6
    assert report["interference"] is False

    straight = {"module_mm": 3, "pressure_angle_deg": 20, "face_width_mm": 12, "shaft_angle_deg": 90}
    bevel_pinion = {"type": "straight_bevel", "teeth": 20, "mate_teeth": 40, **straight}
    bevel_gear = {"type": "straight_bevel", "teeth": 40, "mate_teeth": 20, **straight}
    bevel = check_pair(bevel_pinion, bevel_gear, backlash_mm=_RANGE)
    bevel_nominal = pair_geometry(bevel_pinion, bevel_gear)
    assert bevel["status"] == "pass"
    assert bevel["axis_layout"] == "intersecting"
    assert bevel["centre_distance_mm"] == pytest.approx(0, abs=1e-9)
    assert bevel["ideal_centre_distance_mm"] == pytest.approx(0, abs=1e-9)
    assert bevel["total_contact_ratio"] == pytest.approx(bevel_nominal["total_contact_ratio"], abs=1e-5)
    assert bevel["sweep"]["model"] == "virtual spur"
    assert bevel["sweep"]["rotation_deg"] >= 360 - 1e-6
    assert len(bevel["sweep"]["sections"]) == 3
    assert bevel["interference"] is False

    opened = check_pair(bevel_pinion, bevel_gear, {"centre_distance_mm": 0.3}, backlash_mm=_RANGE)
    assert opened["status"] == "fail"
    assert opened["interference"] is False
    assert opened["backlash"]["circumferential_mm"] > 0.05
    assert opened["backlash"]["in_range"] is False

    spiral_pinion = {"type": "spiral_bevel", "teeth": 20, "mate_teeth": 40, "spiral_angle_deg": 35, **straight}
    spiral_gear = {"type": "spiral_bevel", "teeth": 40, "mate_teeth": 20, "spiral_angle_deg": -35, **straight}
    spiral = check_pair(spiral_pinion, spiral_gear, backlash_mm=_RANGE)
    assert spiral["status"] == "pass"
    assert spiral["interference"] is False
    assert spiral["sweep"]["rotation_deg"] >= 360 - 1e-6


def test_an_undercut_pinion_is_reported():
    pinion = _spur(12)
    gear = _spur(36)
    assert gear_data(pinion)["undercut"] is True
    report = check_pair(pinion, gear, backlash_mm={"min": -1, "max": 1})
    assert report["undercut"] is True
    assert report["status"] == "fail"
    assert any("undercut" in item.lower() for item in report["findings"])


def test_hypoid_checker_is_unregistered_until_someone_plugs_one_in():
    assert "offset" not in registered_mesh_checks()
    assert "parallel" in registered_mesh_checks()
    assert "intersecting" in registered_mesh_checks()

    def offset_checker(*_args, **_kwargs):
        return {"status": "pass", "axis_layout": "offset", "result_name": "hypoid"}

    register_mesh_check("offset", offset_checker)
    try:
        assert "offset" in registered_mesh_checks()
    finally:
        unregister_mesh_check("offset")
    assert "offset" not in registered_mesh_checks()
    with pytest.raises(GeometryError, match="parallel|intersecting|offset"):
        register_mesh_check("skew", offset_checker)


def test_assembly_pair_and_the_command_surfaces(tmp_path: Path):
    pinion, gear = _pair()
    placement = mesh_placement(pinion, gear)
    document = Document(tmp_path / "mesh")
    document.create_gear("pinion", pinion)
    document.create_gear(
        "wheel",
        gear,
        position=placement["gear"]["position_mm"],
        rotation_deg=placement["gear"]["rotation_deg"],
    )
    placed = document.check_gears({"pinion": "pinion", "gear": "wheel", "backlash_mm": _RANGE})
    assert placed["status"] == "pass"
    assert placed["result_kind"] == "verified_check"

    document.create_part_from_ops("frame", [{"op": "add", "shape": "box", "size": [4, 4, 4]}])
    spin = placement["gear"]["rotation_deg"][2]
    document.create_assembly(
        {
            "name": "drive",
            "instances": [
                {"name": "frame", "part": "frame"},
                {"name": "pinion", "part": "pinion"},
                {"name": "wheel", "part": "wheel"},
            ],
            "joints": [
                {
                    "name": "pinion_spin",
                    "type": "revolute",
                    "parent": "frame",
                    "child": "pinion",
                    "axis": [0, 0, 1],
                },
                {
                    "name": "wheel_spin",
                    "type": "revolute",
                    "parent": "frame",
                    "child": "wheel",
                    "parent_anchor": placement["gear"]["position_mm"],
                    "axis": [0, 0, 1],
                },
            ],
            "relations": [
                {
                    "name": "mesh",
                    "type": "gear",
                    "driver": "pinion_spin",
                    "driven": "wheel_spin",
                    "ratio": -20 / 40,
                    "offset": spin,
                }
            ],
        }
    )
    assembly = document.check_gears({"assembly": "drive", "backlash_mm": _RANGE})
    assert assembly["status"] == "pass"
    assert assembly["passed"] == 1
    assert assembly["pairs"][0]["relation"] == "mesh"
    assert assembly["pairs"][0]["interference"] is False

    workspace = Workspace(tmp_path / "api")
    tight = workspace.execute(
        "check_gears",
        {
            "spec": {
                "pinion": pinion,
                "gear": gear,
                "centre_distance_mm": placement["centre_distance_mm"] - 0.3,
                "backlash_mm": _RANGE,
            }
        },
    )
    assert tight["interference"] is True
    assert tight["result_kind"] == "verified_check"

    spec_path = tmp_path / "pair.json"
    spec_path.write_text(
        '{"pinion": {"type": "spur", "module_mm": 2, "teeth": 20, "pressure_angle_deg": 20, "face_width_mm": 8},'
        '"gear": {"type": "spur", "module_mm": 2, "teeth": 40, "pressure_angle_deg": 20, "face_width_mm": 8},'
        '"backlash_mm": {"min": 0, "max": 0.05}}',
        encoding="utf-8",
    )
    assert cli_main(["check-gears", "--file", str(spec_path), "--workspace", str(tmp_path / "cli")]) == 0

    import asyncio
    import json

    server = build_server(tmp_path / "mcp")
    result = asyncio.run(
        server.call_tool(
            "check_gears",
            {
                "spec": {
                    "pinion": pinion,
                    "gear": gear,
                    "centre_distance_mm": placement["centre_distance_mm"] + 0.25,
                    "backlash_mm": _RANGE,
                }
            },
        )
    )
    body = json.loads(result.content[0].text)
    assert body["status"] == "fail"
    assert body["backlash"]["in_range"] is False
    assert body["interference"] is False
