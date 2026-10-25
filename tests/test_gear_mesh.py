"""Mesh and backlash check: a true pair passes, a tight centre interferes, a wide centre is out of range."""

import math
from pathlib import Path

import pytest

from aicad import kernel
from aicad.cli import main as cli_main
from aicad.document import Document
from aicad.errors import GeometryError
from aicad.gears import (
    build_gear,
    check_pair,
    gear_data,
    mesh_placement,
    pair_geometry,
    register_mesh_check,
    registered_mesh_checks,
)
from aicad.server import build_server
from aicad.workspace import COMMANDS, Workspace

_RANGE = {"min": 0.0, "max": 0.05}


def test_check_gears_describes_the_hypoid_sweep():
    command = COMMANDS["check_gears"]
    assert "one pinion tooth" in command.description
    assert "one turn of the smaller gear" in command.description
    offset = command.parameters["properties"]["offset_mm"]["description"]
    assert "hypoid mesh check" in offset
    assert "later" not in offset
    steps = command.parameters["properties"]["steps"]["description"]
    assert "one pinion tooth" in steps


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


def _hypoid(role: str, **overrides) -> dict:
    teeth, mate = (8, 16) if role == "pinion" else (16, 8)
    spec = {
        "type": "hypoid",
        "role": role,
        "teeth": teeth,
        "mate_teeth": mate,
        "offset_mm": 12,
        "shaft_angle_deg": 90,
        "ring_diameter_mm": 80,
        "face_width_mm": 10,
        "spiral_angle_deg": 35,
        "pressure_angle_deg": 20,
    }
    spec.update(overrides)
    return spec


def test_the_hypoid_checker_is_registered_and_can_be_replaced():
    assert "offset" in registered_mesh_checks()
    assert "parallel" in registered_mesh_checks()
    assert "intersecting" in registered_mesh_checks()

    def offset_checker(*_args, **_kwargs):
        return {"status": "pass", "axis_layout": "offset", "result_name": "stub"}

    from aicad.gears.meshcheck import _check_offset

    register_mesh_check("offset", offset_checker)
    try:
        assert "offset" in registered_mesh_checks()
    finally:
        register_mesh_check("offset", _check_offset)
    assert "offset" in registered_mesh_checks()
    with pytest.raises(GeometryError, match="parallel|intersecting|offset"):
        register_mesh_check("skew", offset_checker)


def test_a_hypoid_shaft_angle_past_a_right_angle_is_not_folded():
    report = check_pair(
        _hypoid("pinion", shaft_angle_deg=95),
        _hypoid("ring", shaft_angle_deg=95),
        backlash_mm={"min": 0.0, "max": 1.0},
        steps=8,
    )
    assert report["ideal_shaft_angle_deg"] == pytest.approx(95)
    assert report["shaft_angle_deg"] == pytest.approx(95, abs=0.05)
    assert abs(report["shaft_angle_error_deg"]) < 0.05
    assert not any("shaft angle" in item.lower() for item in report["findings"])


def test_a_hypoid_pair_passes_the_mesh_check():
    # The pitch-flank gap equals the designed shift near mid-face (0.052 mm)
    # and opens toward the ends. A 0.05 cap is the solid contact bar, not this gap.
    report = check_pair(_hypoid("pinion"), _hypoid("ring"), backlash_mm={"min": 0.0, "max": 0.30})
    assert report["status"] == "pass", report["findings"]
    assert report["result_kind"] == "verified_check"
    assert report["axis_layout"] == "offset"
    assert report["interference"] is False
    assert report["contact"] is True
    assert report["ratio"] == 2
    assert report["offset_mm"] == pytest.approx(12)
    assert report["ideal_offset_mm"] == pytest.approx(12)
    assert abs(report["offset_error_mm"]) < 1e-6
    assert report["sweep"]["model"] == "face-mill solid"
    assert report["sweep"]["member"] == "pinion"
    assert report["sweep"]["steps"] == 13
    assert report["sweep"]["rotation_deg"] == pytest.approx(22.5)
    assert report["sweep"]["step_deg"] == pytest.approx(22.5 / 12)
    poses = report["sweep"]["poses"]
    assert poses[-1]["gear_angle_deg"] - poses[0]["gear_angle_deg"] == pytest.approx(22.5)
    assert report["sweep"]["worst_overlap_mm3"] < 1e-4
    assert report["sweep"]["worst_gap_mm"] < 0.05
    assert len(report["backlash"]["along_tooth_mm"]) == 3
    assert report["backlash"]["designed_flank_shift_mm"] == pytest.approx(0.052)
    assert report["backlash"]["along_tooth_min_mm"] == pytest.approx(0.052, abs=0.012)
    assert 0.05 < report["backlash"]["along_tooth_max_mm"] < 0.30
    assert report["backlash"]["in_range"] is True
    assert report["undercut"] is False
    assert report["total_contact_ratio"] is None
    assert "load rating" in " ".join(report["limits"]).lower()
    assert "not a spiral bevel" in " ".join(report["limits"]).lower() or "not a spiral bevel" in " ".join(report["assumptions"]).lower()
    assert report["findings"] == []


def test_a_hypoid_plunge_interferes_and_a_wrong_offset_is_flagged():
    report = check_pair(
        _hypoid("pinion"),
        _hypoid("ring"),
        {"offset_mm": 12.5},
        backlash_mm={"min": 0.0, "max": 1.0},
    )
    assert report["status"] == "fail"
    assert report["offset_error_mm"] == pytest.approx(0.5)
    assert report["interference"] is True
    assert report["sweep"]["worst_overlap_mm3"] > 0.3
    assert any("offset" in item.lower() for item in report["findings"])


def test_a_hypoid_step_count_covers_one_pinion_tooth():
    with pytest.raises(GeometryError, match="one pinion tooth"):
        check_pair(_hypoid("pinion"), _hypoid("ring"), steps=3)
    from aicad.gears.hypoid_mesh import _engagement_angles

    angles, step, pitch = _engagement_angles(16, 10)
    assert len(angles) == 10
    assert pitch == pytest.approx(22.5)
    assert angles[0] == pytest.approx(-0.25 * pitch)
    assert angles[-1] - angles[0] == pytest.approx(pitch)
    assert step == pytest.approx(pitch / 9)


def test_a_hypoid_sweep_catches_interference_between_the_old_poses():
    pinion_spec = _hypoid("pinion")
    ring_spec = _hypoid("ring")
    report = check_pair(
        pinion_spec,
        ring_spec,
        {"offset_mm": 11.993},
        backlash_mm={"min": 0.0, "max": 1.0},
    )
    assert report["interference"] is True
    assert report["sweep"]["worst_overlap_mm3"] > 1e-4
    # 0, 8, and 16 degrees were the whole of the old sweep. The hit sits between them.
    assert 8.0 < report["sweep"]["worst_at_deg"] < 16.0
    pinion = kernel.adopt(build_gear(pinion_spec))
    ring = kernel.adopt(build_gear(ring_spec))
    for angle in (0.0, 8.0, 16.0):
        placement = mesh_placement(pinion_spec, ring_spec, angle)
        position = list(placement["pinion"]["position_mm"])
        position[1] = 11.993
        placed_pinion = kernel.place(pinion, tuple(position), tuple(placement["pinion"]["rotation_deg"]))
        placed_ring = kernel.place(
            ring,
            tuple(placement["gear"]["position_mm"]),
            tuple(placement["gear"]["rotation_deg"]),
        )
        assert kernel.overlap_volume(placed_pinion, placed_ring) < 1e-4


def _hypoid_assembly(document: Document, name: str, pinion_at, ring_at) -> None:
    document.create_assembly(
        {
            "name": name,
            "instances": [
                {
                    "name": "pinion",
                    "part": "pinion",
                    "placement": {"position": pinion_at["position_mm"], "rotation_deg": pinion_at["rotation_deg"]},
                },
                {
                    "name": "ring",
                    "part": "ring",
                    "placement": {"position": ring_at["position_mm"], "rotation_deg": ring_at["rotation_deg"]},
                },
            ],
            "joints": [],
        }
    )


def _mounted_hypoid(tmp_path: Path):
    """One hypoid pair in a document, plus the assembly placement of that mesh.

    Each check rebuilds both solids. Four checks in one test cross the per-test
    limit on a busy macOS runner, so each situation is its own test.
    """
    pinion_spec = _hypoid("pinion")
    ring_spec = _hypoid("ring")
    placement = mesh_placement(pinion_spec, ring_spec)
    document = Document(tmp_path / "hypoid-mesh")
    document.create_gear(
        "pinion",
        pinion_spec,
        position=placement["pinion"]["position_mm"],
        rotation_deg=placement["pinion"]["rotation_deg"],
    )
    document.create_gear(
        "ring",
        ring_spec,
        position=placement["gear"]["position_mm"],
        rotation_deg=placement["gear"]["rotation_deg"],
    )
    pinion_at = {
        "position_mm": list(placement["pinion"]["position_mm"]),
        "rotation_deg": list(placement["pinion"]["rotation_deg"]),
    }
    ring_at = {
        "position_mm": list(placement["gear"]["position_mm"]),
        "rotation_deg": list(placement["gear"]["rotation_deg"]),
    }
    return document, pinion_at, ring_at


def test_a_seated_hypoid_pair_passes(tmp_path: Path):
    document, _pinion_at, _ring_at = _mounted_hypoid(tmp_path)
    seated = document.check_gears({"pinion": "pinion", "gear": "ring", "backlash_mm": {"min": 0.0, "max": 0.30}})
    assert seated["status"] == "pass", seated["findings"]
    assert abs(seated["pinion_clock_deg"]) < 1e-3
    assert abs(seated["gear_clock_deg"]) < 1e-3


def test_a_seated_hypoid_assembly_passes(tmp_path: Path):
    document, pinion_at, ring_at = _mounted_hypoid(tmp_path)
    _hypoid_assembly(document, "seated", pinion_at, ring_at)
    assembled = document.check_gears({"assembly": "seated", "backlash_mm": {"min": 0.0, "max": 0.30}})
    assert assembled["status"] == "pass", assembled["pairs"][0]["findings"]
    assert assembled["pairs"][0]["offset_error_mm"] == pytest.approx(0, abs=1e-3)


def test_a_misclocked_hypoid_assembly_is_flagged(tmp_path: Path):
    document, pinion_at, ring_at = _mounted_hypoid(tmp_path)
    clocked = {
        "position_mm": ring_at["position_mm"],
        "rotation_deg": [ring_at["rotation_deg"][0], ring_at["rotation_deg"][1], ring_at["rotation_deg"][2] + 2.0],
    }
    _hypoid_assembly(document, "clocked", pinion_at, clocked)
    clock_report = document.check_gears({"assembly": "clocked", "backlash_mm": {"min": 0.0, "max": 1.0}})
    clocked_pair = clock_report["pairs"][0]
    assert clock_report["status"] == "fail"
    assert abs(clocked_pair["gear_clock_deg"]) == pytest.approx(2.0, abs=0.05)
    assert any("clocked" in item.lower() for item in clocked_pair["findings"])


def test_a_misplaced_hypoid_assembly_is_flagged(tmp_path: Path):
    document, pinion_at, ring_at = _mounted_hypoid(tmp_path)
    shifted = {
        "position_mm": [pinion_at["position_mm"][0], pinion_at["position_mm"][1] + 0.5, pinion_at["position_mm"][2]],
        "rotation_deg": pinion_at["rotation_deg"],
    }
    _hypoid_assembly(document, "shifted", shifted, ring_at)
    shift_report = document.check_gears({"assembly": "shifted", "backlash_mm": {"min": 0.0, "max": 1.0}})
    shifted_pair = shift_report["pairs"][0]
    assert shift_report["status"] == "fail"
    assert shifted_pair["offset_error_mm"] == pytest.approx(0.5, abs=0.02)
    assert any("offset" in item.lower() for item in shifted_pair["findings"])


def test_a_mismatched_hypoid_hand_or_ratio_is_refused():
    pinion = _hypoid("pinion", hand="right")
    with pytest.raises(GeometryError, match="opposite"):
        check_pair(pinion, _hypoid("ring"))
    with pytest.raises(GeometryError, match="ring_spiral_angle_deg"):
        check_pair(_hypoid("pinion"), _hypoid("ring", spiral_angle_deg=-35))
    with pytest.raises(GeometryError, match="tooth counts"):
        check_pair(_hypoid("pinion"), _hypoid("ring", teeth=18, mate_teeth=9))


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


def test_a_hypoid_build_runs_on_a_worker_thread():
    """A hypoid mesh check on a Python thread other than the main thread.

    The workspace server, MCP tools, and in-process design-script calls run
    geometry on a thread like this one. On macOS that thread uses the stack
    set for geometry.
    """
    import sys
    import threading

    from aicad.thread_stack import (
        GEOMETRY_THREAD_STACK,
        current_thread_stack_bytes,
        prepare_geometry_thread_stack,
    )

    prepare_geometry_thread_stack()
    box: dict = {}

    def run() -> None:
        box["main"] = threading.current_thread() is threading.main_thread()
        box["stack"] = current_thread_stack_bytes()
        try:
            box["report"] = check_pair(
                _hypoid("pinion"),
                _hypoid("ring"),
                backlash_mm={"min": 0.0, "max": 0.30},
            )
        except Exception as exc:
            box["error"] = exc

    worker = threading.Thread(target=run, name="aicad-hypoid-worker")
    worker.start()
    worker.join()
    assert worker.is_alive() is False
    assert box.get("main") is False
    if sys.platform == "darwin":
        assert box.get("stack") is not None
        assert box["stack"] >= GEOMETRY_THREAD_STACK
    error = box.get("error")
    if isinstance(error, Exception):
        raise error
    report = box["report"]
    assert report["status"] == "pass", report["findings"]
    assert report["interference"] is False


def _mount(document: Document, name: str, spec: dict, position, rotation) -> None:
    document.create_gear(name, spec, position=position, rotation_deg=rotation)


def test_a_compound_reducer_checks_the_train_meshes(tmp_path: Path):
    stage1_pinion = _spur(18)
    stage1_gear = _spur(36)
    stage2_pinion = {**_spur(20), "module_mm": 1.5, "face_width_mm": 6}
    stage2_gear = {**_spur(40), "module_mm": 1.5, "face_width_mm": 6}
    place1 = mesh_placement(stage1_pinion, stage1_gear)
    place2 = mesh_placement(stage2_pinion, stage2_gear)
    document = Document(tmp_path / "reducer")
    document.create_part_from_ops("frame", [{"op": "add", "shape": "box", "size": [4, 4, 4]}])
    _mount(document, "pinion1", stage1_pinion, [0, 0, 0], [0, 0, 0])
    _mount(document, "gear1", stage1_gear, place1["gear"]["position_mm"], place1["gear"]["rotation_deg"])
    stack = 20.0
    second = [place1["centre_distance_mm"] + place2["gear"]["position_mm"][0], 0.0, stack]
    _mount(document, "pinion2", stage2_pinion, [place1["centre_distance_mm"], 0, stack], [0, 0, 0])
    _mount(document, "gear2", stage2_gear, second, place2["gear"]["rotation_deg"])
    spin1 = place1["gear"]["rotation_deg"][2]
    spin2 = place2["gear"]["rotation_deg"][2]
    document.create_assembly(
        {
            "name": "reducer",
            "instances": [
                {"name": "frame", "part": "frame"},
                {"name": "in_gear", "part": "pinion1"},
                {"name": "mid_gear", "part": "gear1"},
                {"name": "mid_pinion", "part": "pinion2"},
                {"name": "out_gear", "part": "gear2"},
            ],
            "joints": [
                {"name": "input", "type": "revolute", "parent": "frame", "child": "in_gear", "axis": [0, 0, 1]},
                {
                    "name": "mid",
                    "type": "revolute",
                    "parent": "frame",
                    "child": "mid_gear",
                    "parent_anchor": place1["gear"]["position_mm"],
                    "axis": [0, 0, 1],
                    "value": spin1,
                },
                {
                    "name": "mid_stack",
                    "type": "revolute",
                    "parent": "mid_gear",
                    "child": "mid_pinion",
                    "parent_anchor": [0, 0, stack],
                    "axis": [0, 0, 1],
                    "value": -spin1,
                },
                {
                    "name": "output",
                    "type": "revolute",
                    "parent": "frame",
                    "child": "out_gear",
                    "parent_anchor": second,
                    "axis": [0, 0, 1],
                },
            ],
            "relations": [
                {"name": "stage1", "type": "gear", "driver": "input", "driven": "mid", "ratio": -18 / 36, "offset": spin1},
                {
                    "name": "stage2",
                    "type": "gear",
                    "driver": "mid",
                    "driven": "output",
                    "ratio": -20 / 40,
                    "offset": spin2 - (-20 / 40) * spin1,
                },
            ],
            "gear_train": {
                "shafts": [
                    {"name": "input", "joint": "input", "gears": [{"name": "p1", "instance": "in_gear"}]},
                    {
                        "name": "intermediate",
                        "joint": "mid",
                        "gears": [
                            {"name": "g1", "instance": "mid_gear"},
                            {"name": "p2", "instance": "mid_pinion"},
                        ],
                    },
                    {"name": "output", "joint": "output", "gears": [{"name": "g2", "instance": "out_gear"}]},
                ],
                "meshes": [
                    {"name": "first", "a": "p1", "b": "g1", "kind": "external"},
                    {"name": "second", "a": "p2", "b": "g2", "kind": "external"},
                ],
            },
        }
    )
    report = document.check_gears({"assembly": "reducer", "backlash_mm": _RANGE, "steps": 8})
    assert [(pair["relation"], tuple(pair["instances"])) for pair in report["pairs"]] == [
        ("first", ("in_gear", "mid_gear")),
        ("second", ("mid_pinion", "out_gear")),
    ]
    assert report["status"] == "pass"
    named = document.check_gears(
        {"assembly": "reducer", "pinion": "mid_pinion", "gear": "out_gear", "backlash_mm": _RANGE, "steps": 8}
    )
    assert named["pairs"][0]["instances"] == ["mid_pinion", "out_gear"]


def test_one_incompatible_pair_stays_in_the_assembly_report(tmp_path: Path):
    document = Document(tmp_path / "mixed")
    document.create_part_from_ops("frame", [{"op": "add", "shape": "box", "size": [4, 4, 4]}])
    document.create_gear("pinion", _spur(18))
    document.create_gear("wheel", {**_spur(36), "module_mm": 1.5})
    document.create_assembly(
        {
            "name": "mixed",
            "instances": [
                {"name": "frame", "part": "frame"},
                {"name": "pinion", "part": "pinion"},
                {"name": "wheel", "part": "wheel"},
            ],
            "joints": [
                {"name": "in_spin", "type": "revolute", "parent": "frame", "child": "pinion", "axis": [0, 0, 1]},
                {
                    "name": "out_spin",
                    "type": "revolute",
                    "parent": "frame",
                    "child": "wheel",
                    "parent_anchor": [54, 0, 0],
                    "axis": [0, 0, 1],
                },
            ],
            "relations": [
                {"name": "mesh", "type": "gear", "driver": "in_spin", "driven": "out_spin", "ratio": -0.5}
            ],
        }
    )
    report = document.check_gears({"assembly": "mixed", "backlash_mm": _RANGE, "steps": 8})
    assert report["status"] == "fail"
    assert report["failed"] == 1
    assert "same normal module" in report["pairs"][0]["findings"][0]
