"""Speed and torque of a gear train, including a planetary set and a differential."""

import errno
import json
import os
from pathlib import Path

import pytest

from aicad.cli import main as cli_main
from aicad.document import Document
from aicad.errors import GeometryError
from aicad.gears.train import solve_gear_train
from aicad.workspace import Workspace


def _shaft(name, gears, joint=None):
    record = {"name": name, "gears": gears}
    if joint:
        record["joint"] = joint
    return record


def _gear(name, teeth):
    return {"name": name, "teeth": teeth}


def _solve(train, drive):
    return solve_gear_train({"name": "train", "gear_train": train}, drive)


def _by_name(report):
    return {row["name"]: row for row in report["shafts"]}


def _balanced(report):
    assert report["status"] == "determined"
    assert report["torque_status"] == "determined"
    assert report["power_balance_w"] == pytest.approx(0, abs=1e-6)
    assert report["torque_balance_nm"] == pytest.approx(0, abs=1e-6)


def test_two_stage_spur_train_ratio_and_torque():
    """20/50 then 20/40, both external, so the output turns at 1/5 the input."""
    report = _solve(
        {
            "shafts": [
                _shaft("input", [_gear("pinion", 20)]),
                _shaft("lay", [_gear("lay_gear", 50), _gear("lay_pinion", 20)]),
                _shaft("output", [_gear("wheel", 40)]),
            ],
            "meshes": [
                {"name": "low", "a": "pinion", "b": "lay_gear"},
                {"name": "high", "a": "lay_pinion", "b": "wheel"},
            ],
        },
        {"inputs": [{"shaft": "input", "speed_rpm": 1000, "torque_nm": 2}]},
    )
    shafts = _by_name(report)
    assert report["status"] == "determined"
    assert "determined" in report["note"]
    assert shafts["lay"]["speed_ratio_exact"] == "-2/5"
    assert shafts["output"]["speed_ratio_exact"] == "1/5"
    assert shafts["output"]["speed_rpm"] == pytest.approx(200)
    assert shafts["lay"]["role"] == "idle"
    assert shafts["lay"]["torque_nm"] == pytest.approx(0)
    assert shafts["output"]["torque_nm"] == pytest.approx(-10)
    assert shafts["input"]["torque_nm"] == pytest.approx(2)
    assert report["housing_torque_nm"] == pytest.approx(8)
    _balanced(report)


def test_an_idler_cancels_and_the_end_teeth_set_the_ratio():
    report = _solve(
        {
            "shafts": [
                _shaft("input", [_gear("pinion", 20)]),
                _shaft("idler", [_gear("idler", 30)]),
                _shaft("output", [_gear("wheel", 40)]),
            ],
            "meshes": [
                {"a": "pinion", "b": "idler"},
                {"a": "idler", "b": "wheel"},
            ],
        },
        {"inputs": [{"shaft": "input", "speed_rpm": 600, "torque_nm": 3}]},
    )
    shafts = _by_name(report)
    assert shafts["output"]["speed_ratio_exact"] == "1/2"
    assert shafts["idler"]["speed_ratio_exact"] == "-2/3"
    assert shafts["idler"]["torque_nm"] == pytest.approx(0)
    assert shafts["output"]["torque_nm"] == pytest.approx(-6)
    _balanced(report)


def test_a_single_external_mesh_reverses_direction():
    report = _solve(
        {
            "shafts": [
                _shaft("input", [_gear("pinion", 20)]),
                _shaft("output", [_gear("wheel", 40)]),
            ],
            "meshes": [{"a": "pinion", "b": "wheel"}],
        },
        {"inputs": [{"shaft": "input", "speed_rpm": 100, "torque_nm": 2}]},
    )
    shafts = _by_name(report)
    assert shafts["output"]["speed_ratio_exact"] == "-1/2"
    assert shafts["output"]["torque_nm"] == pytest.approx(4)
    assert report["housing_torque_nm"] == pytest.approx(-6)
    _balanced(report)


def test_planetary_with_a_fixed_ring():
    """Sun 24, planet 18, ring 60. The carrier turns at 24/(24+60) of the sun."""
    report = _solve(
        {
            "shafts": [
                _shaft("sun", [_gear("sun", 24)]),
                _shaft("planet", [_gear("planet", 18)]),
                _shaft("ring", [_gear("ring", 60)]),
                _shaft("carrier", []),
            ],
            "meshes": [
                {"name": "sun_planet", "a": "sun", "b": "planet", "carrier": "carrier"},
                {"name": "planet_ring", "a": "planet", "b": "ring", "kind": "internal", "carrier": "carrier"},
            ],
            "fixed": ["ring"],
        },
        {"inputs": [{"shaft": "sun", "speed_rpm": 700, "torque_nm": 2}]},
    )
    shafts = _by_name(report)
    assert report["status"] == "determined"
    assert "ring is fixed" in report["note"]
    assert shafts["carrier"]["speed_ratio_exact"] == "2/7"
    assert shafts["carrier"]["speed_rpm"] == pytest.approx(200)
    assert shafts["planet"]["speed_ratio_exact"] == "-2/3"
    assert shafts["ring"]["speed_rpm"] == pytest.approx(0)
    assert shafts["ring"]["role"] == "fixed"
    assert shafts["planet"]["torque_nm"] == pytest.approx(0)
    assert shafts["carrier"]["torque_nm"] == pytest.approx(-7)
    assert shafts["ring"]["torque_nm"] == pytest.approx(5)
    assert report["housing_torque_nm"] == pytest.approx(0)
    _balanced(report)


def _planets(count: int) -> tuple[list[dict], list[dict]]:
    shafts = [_shaft("sun", [_gear("sun", 24)]), _shaft("ring", [_gear("ring", 60)]), _shaft("carrier", [])]
    meshes = []
    for index in range(count):
        name = f"p{index + 1}"
        shafts.insert(1 + index, _shaft(name, [_gear(name, 18)]))
        meshes.append({"a": "sun", "b": name, "carrier": "carrier"})
        meshes.append({"a": name, "b": "ring", "kind": "internal", "carrier": "carrier"})
    return shafts, meshes


def test_three_planets_report_the_same_shaft_torques_as_one():
    """Sun 24, planet 18, ring 60 fixed. The tooth-force split is free. The shaft torques are not."""
    shafts, meshes = _planets(3)
    report = _solve(
        {"shafts": shafts, "meshes": meshes, "fixed": ["ring"]},
        {"inputs": [{"shaft": "sun", "speed_rpm": 700, "torque_nm": 2}]},
    )
    rows = _by_name(report)
    assert report["status"] == "determined"
    assert report["torque_status"] == "determined"
    assert "tooth forces are not unique" in report["note"]
    assert rows["carrier"]["speed_ratio_exact"] == "2/7"
    assert rows["carrier"]["speed_rpm"] == pytest.approx(200)
    assert rows["p1"]["speed_ratio_exact"] == "-2/3"
    assert rows["p2"]["speed_rpm"] == pytest.approx(rows["p1"]["speed_rpm"])
    assert rows["p3"]["speed_rpm"] == pytest.approx(rows["p1"]["speed_rpm"])
    assert rows["sun"]["torque_nm"] == pytest.approx(2)
    assert rows["ring"]["torque_nm"] == pytest.approx(5)
    assert rows["carrier"]["torque_nm"] == pytest.approx(-7)
    assert rows["p1"]["torque_nm"] == pytest.approx(0)
    assert rows["p2"]["torque_nm"] == pytest.approx(0)
    assert rows["p3"]["torque_nm"] == pytest.approx(0)
    assert report["housing_torque_nm"] == pytest.approx(0)
    _balanced(report)


def test_three_planets_with_the_carrier_fixed_drive_the_ring():
    """Carrier fixed. The ring turns at −z_sun/z_ring of the sun, and the shaft torques match one planet."""
    shafts, meshes = _planets(3)
    report = _solve(
        {"shafts": shafts, "meshes": meshes, "fixed": ["carrier"]},
        {"inputs": [{"shaft": "sun", "speed_rpm": 1000, "torque_nm": 2}]},
    )
    rows = _by_name(report)
    assert report["torque_status"] == "determined"
    assert rows["ring"]["role"] == "output"
    assert rows["ring"]["speed_ratio_exact"] == "-2/5"
    assert rows["ring"]["speed_rpm"] == pytest.approx(-400)
    assert rows["p1"]["speed_ratio_exact"] == "-4/3"
    assert rows["carrier"]["speed_rpm"] == pytest.approx(0)
    assert rows["ring"]["torque_nm"] == pytest.approx(5)
    assert rows["carrier"]["torque_nm"] == pytest.approx(-7)
    assert rows["p1"]["torque_nm"] == pytest.approx(0)
    assert report["housing_torque_nm"] == pytest.approx(0)
    _balanced(report)


def test_a_planetary_carrier_with_a_following_gear_reports_the_output_torque():
    """Sun 20, three planets of 20, ring 60 fixed, then an external 20/40 on the carrier.

    The carrier turns at 1/4 of the sun. The following mesh reverses that and halves it,
    so the output turns at -1/8. For 2 N·m at the sun the output torque is +16 N·m.
    """
    shafts = [
        _shaft("sun", [_gear("sun", 20)]),
        _shaft("ring", [_gear("ring", 60)]),
        _shaft("carrier", [_gear("pinion", 20)]),
        _shaft("output", [_gear("wheel", 40)]),
    ]
    meshes = []
    for index in range(3):
        name = f"p{index + 1}"
        shafts.insert(1, _shaft(name, [_gear(name, 20)]))
        meshes.append({"a": "sun", "b": name, "carrier": "carrier"})
        meshes.append({"a": name, "b": "ring", "kind": "internal", "carrier": "carrier"})
    meshes.append({"a": "pinion", "b": "wheel"})
    report = _solve(
        {"shafts": shafts, "meshes": meshes, "fixed": ["ring"]},
        {"inputs": [{"shaft": "sun", "speed_rpm": 1000, "torque_nm": 2}]},
    )
    rows = _by_name(report)
    assert report["torque_status"] == "determined"
    assert "tooth forces are not unique" in report["note"]
    assert rows["carrier"]["role"] == "idle"
    assert rows["carrier"]["speed_rpm"] == pytest.approx(250)
    assert rows["output"]["speed_rpm"] == pytest.approx(-125)
    assert rows["output"]["torque_nm"] == pytest.approx(16)
    assert rows["carrier"]["torque_nm"] == pytest.approx(0)
    assert rows["ring"]["torque_nm"] == pytest.approx(6)
    assert rows["p1"]["torque_nm"] == pytest.approx(0)
    _balanced(report)

    listed = _solve(
        {"shafts": shafts, "meshes": meshes, "fixed": ["ring"]},
        {"inputs": [{"shaft": "sun", "speed_rpm": 1000, "torque_nm": 2}], "outputs": ["carrier"]},
    )
    assert listed["torque_status"] == "under_constrained"
    assert _by_name(listed)["output"]["torque_nm"] is None


def test_mesh_efficiency_is_not_applied_when_the_tooth_forces_are_free():
    shafts, meshes = _planets(3)
    for mesh in meshes:
        mesh["efficiency"] = 0.9
    report = _solve(
        {"shafts": shafts, "meshes": meshes, "fixed": ["ring"]},
        {"inputs": [{"shaft": "sun", "speed_rpm": 700, "torque_nm": 2}]},
    )
    rows = _by_name(report)
    assert report["status"] == "determined"
    assert report["torque_status"] == "under_constrained"
    assert "mesh efficiency is not applied" in report["note"]
    assert rows["carrier"]["speed_rpm"] == pytest.approx(200)
    assert rows["carrier"]["torque_nm"] is None
    assert rows["ring"]["torque_nm"] is None
    assert report["housing_torque_nm"] is None
    assert report["power_balance_w"] is None


def test_an_explicitly_idle_ring_is_not_treated_as_an_output():
    shafts, meshes = _planets(3)
    report = _solve(
        {"shafts": shafts, "meshes": meshes, "fixed": ["carrier"]},
        {"inputs": [{"shaft": "sun", "speed_rpm": 1000, "torque_nm": 2}], "idle": ["ring"]},
    )
    rows = _by_name(report)
    assert rows["ring"]["role"] == "idle"
    assert report["torque_status"] == "over_constrained"
    assert rows["ring"]["torque_nm"] is None


def test_open_differential_straight_ahead_and_in_a_turn():
    train = {
        "shafts": [
            _shaft("case", []),
            _shaft("left", []),
            _shaft("right", []),
        ],
        "differentials": [{"name": "axle", "carrier": "case", "left": "left", "right": "right"}],
    }
    straight = _solve(
        train,
        {
            "inputs": [{"shaft": "case", "speed_rpm": 100, "torque_nm": 40}],
            "speeds": [{"shaft": "left", "speed_rpm": 100}],
        },
    )
    shafts = _by_name(straight)
    assert straight["status"] == "determined"
    assert shafts["left"]["speed_rpm"] == pytest.approx(100)
    assert shafts["right"]["speed_rpm"] == pytest.approx(100)
    assert shafts["left"]["torque_nm"] == pytest.approx(-20)
    assert shafts["right"]["torque_nm"] == pytest.approx(-20)
    _balanced(straight)

    turn = _solve(
        train,
        {
            "inputs": [{"shaft": "case", "speed_rpm": 100, "torque_nm": 40}],
            "speeds": [{"shaft": "left", "speed_rpm": 130}],
        },
    )
    turned = _by_name(turn)
    assert turned["left"]["speed_rpm"] == pytest.approx(130)
    assert turned["right"]["speed_rpm"] == pytest.approx(70)
    assert turned["case"]["speed_rpm"] == pytest.approx(100)
    # One side speeds up by the same amount the other slows down.
    assert turned["left"]["speed_rpm"] - 100 == pytest.approx(100 - turned["right"]["speed_rpm"])
    assert (turned["left"]["speed_rpm"] + turned["right"]["speed_rpm"]) / 2 == pytest.approx(100)
    assert turned["left"]["torque_nm"] == pytest.approx(turned["right"]["torque_nm"])
    assert turned["left"]["torque_nm"] == pytest.approx(-20)
    _balanced(turn)


def test_a_reduction_into_an_open_differential_splits_the_torque():
    """20/40 into the carrier. Straight ahead the wheels match the carrier and share its torque."""
    train = {
        "shafts": [
            _shaft("input", [_gear("pinion", 20)]),
            _shaft("carrier", [_gear("ring", 40)]),
            _shaft("left", []),
            _shaft("right", []),
        ],
        "meshes": [{"name": "final", "a": "pinion", "b": "ring"}],
        "differentials": [{"name": "axle", "carrier": "carrier", "left": "left", "right": "right"}],
    }
    straight = _solve(
        train,
        {
            "inputs": [{"shaft": "input", "speed_rpm": 1000, "torque_nm": 10}],
            "speeds": [{"shaft": "left", "speed_rpm": -500}],
        },
    )
    shafts = _by_name(straight)
    assert straight["status"] == "determined"
    assert straight["torque_status"] == "determined"
    assert shafts["carrier"]["role"] == "idle"
    assert shafts["carrier"]["speed_rpm"] == pytest.approx(-500)
    assert shafts["left"]["speed_rpm"] == pytest.approx(-500)
    assert shafts["right"]["speed_rpm"] == pytest.approx(-500)
    assert shafts["carrier"]["torque_nm"] == pytest.approx(0, abs=1e-9)
    assert shafts["left"]["torque_nm"] == pytest.approx(10)
    assert shafts["right"]["torque_nm"] == pytest.approx(10)
    assert shafts["input"]["torque_nm"] == pytest.approx(10)
    _balanced(straight)

    turn = _solve(
        train,
        {
            "inputs": [{"shaft": "input", "speed_rpm": 1000, "torque_nm": 10}],
            "speeds": [{"shaft": "left", "speed_rpm": -400}],
        },
    )
    turned = _by_name(turn)
    assert turned["carrier"]["speed_rpm"] == pytest.approx(-500)
    assert turned["left"]["speed_rpm"] == pytest.approx(-400)
    assert turned["right"]["speed_rpm"] == pytest.approx(-600)
    assert turned["left"]["torque_nm"] == pytest.approx(turned["right"]["torque_nm"])
    assert turned["left"]["torque_nm"] == pytest.approx(10)
    _balanced(turn)

    braked = _solve(
        train,
        {
            "inputs": [{"shaft": "input", "speed_rpm": 1000, "torque_nm": 10}],
            "speeds": [{"shaft": "left", "speed_rpm": -500}],
            "outputs": ["carrier"],
        },
    )
    assert braked["status"] == "determined"
    assert braked["torque_status"] == "under_constrained"


def test_open_differential_is_under_constrained_until_one_output_is_known():
    report = _solve(
        {
            "shafts": [_shaft("case", []), _shaft("left", []), _shaft("right", [])],
            "differentials": [{"carrier": "case", "left": "left", "right": "right"}],
        },
        {"inputs": [{"shaft": "case", "speed_rpm": 100, "torque_nm": 40}]},
    )
    assert report["status"] == "under_constrained"
    assert report["degrees_of_freedom"] == 1
    assert "under-constrained" in report["note"]
    assert "speeds up as much as" in report["note"]
    assert report["torque_status"] == "not_solved"
    shafts = _by_name(report)
    assert shafts["case"]["speed_rpm"] == pytest.approx(100)
    assert shafts["left"]["speed_rpm"] is None
    assert shafts["right"]["speed_rpm"] is None
    assert set(report["free_shafts"]) == {"left", "right"}


def test_open_differential_rejects_speeds_that_do_not_average():
    report = _solve(
        {
            "shafts": [_shaft("case", []), _shaft("left", []), _shaft("right", [])],
            "differentials": [{"name": "axle", "carrier": "case", "left": "left", "right": "right"}],
        },
        {
            "inputs": [{"shaft": "case", "speed_rpm": 100, "torque_nm": 40}],
            "speeds": [{"shaft": "left", "speed_rpm": 120}, {"shaft": "right", "speed_rpm": 90}],
        },
    )
    assert report["status"] == "over_constrained"
    assert "over-constrained" in report["note"]
    assert "average" in report["note"]
    assert report["torque_status"] == "not_solved"
    assert all(row["speed_rpm"] is None for row in report["shafts"])


def test_mesh_efficiency_reduces_the_delivered_torque():
    report = _solve(
        {
            "shafts": [
                _shaft("input", [_gear("pinion", 20)]),
                _shaft("output", [_gear("wheel", 40)]),
            ],
            "meshes": [{"a": "pinion", "b": "wheel", "efficiency": 0.5}],
        },
        {"inputs": [{"shaft": "input", "speed_rpm": 100, "torque_nm": 2}]},
    )
    shafts = _by_name(report)
    assert shafts["output"]["speed_ratio_exact"] == "-1/2"
    assert shafts["output"]["torque_nm"] == pytest.approx(2)
    assert report["result_kind"] == "rough_estimate"
    assert report["power_balance_w"] > 0
    assert report["torque_balance_nm"] == pytest.approx(0, abs=1e-6)


def test_a_fixed_shaft_cannot_be_the_input():
    with pytest.raises(GeometryError, match="fixed"):
        _solve(
            {
                "shafts": [_shaft("input", [_gear("pinion", 20)]), _shaft("output", [_gear("wheel", 40)])],
                "meshes": [{"a": "pinion", "b": "wheel"}],
                "fixed": ["input"],
            },
            {"inputs": [{"shaft": "input", "speed_rpm": 10, "torque_nm": 1}]},
        )


def _box(document: Document, name: str) -> None:
    document.create_part_from_ops(name, [{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 5]}])


def _spur(document: Document, name: str, teeth: int) -> None:
    document.create_gear(
        name,
        {"type": "spur", "module_mm": 1, "teeth": teeth, "pressure_angle_deg": 20, "face_width_mm": 2},
    )


def test_the_document_reads_tooth_counts_from_generated_gears(tmp_path: Path):
    document = Document(tmp_path / "reducer")
    _box(document, "housing")
    _spur(document, "pinion", 20)
    _spur(document, "lay_gear", 50)
    _spur(document, "lay_pinion", 20)
    _spur(document, "wheel", 40)
    spec = {
        "name": "reducer",
        "instances": [
            {"name": "housing", "part": "housing"},
            {"name": "pinion", "part": "pinion"},
            {"name": "lay_gear", "part": "lay_gear"},
            {"name": "lay_pinion", "part": "lay_pinion"},
            {"name": "wheel", "part": "wheel"},
        ],
        "joints": [
            {"name": "pinion_shaft", "type": "revolute", "parent": "housing", "child": "pinion", "axis": [0, 0, 1]},
            {"name": "lay_shaft", "type": "revolute", "parent": "housing", "child": "lay_gear", "axis": [0, 0, 1]},
            {"name": "lay_pin", "type": "fixed", "parent": "lay_gear", "child": "lay_pinion"},
            {"name": "wheel_shaft", "type": "revolute", "parent": "housing", "child": "wheel", "axis": [0, 0, 1]},
        ],
        "gear_train": {
            "shafts": [
                {"name": "input", "joint": "pinion_shaft", "gears": ["pinion"]},
                {"name": "lay", "joint": "lay_shaft", "gears": ["lay_gear", "lay_pinion"]},
                {"name": "output", "joint": "wheel_shaft", "gears": ["wheel"]},
            ],
            "meshes": [
                {"a": "pinion", "b": "lay_gear"},
                {"a": "lay_pinion", "b": "wheel"},
            ],
        },
    }
    document.create_assembly(spec)
    report = document.solve_gear_train(
        "reducer",
        {"inputs": [{"shaft": "input", "speed_rpm": 1000, "torque_nm": 2}]},
    )
    shafts = _by_name(report)
    assert shafts["input"]["gears"][0]["teeth"] == 20
    assert shafts["output"]["speed_ratio_exact"] == "1/5"
    assert shafts["output"]["torque_nm"] == pytest.approx(-10)
    assert report["result_kind"] == "verified_check"
    _balanced(report)

    reopened = Document(tmp_path / "reducer")
    again = reopened.solve_gear_train(
        "reducer",
        {"inputs": [{"shaft": "input", "speed_rpm": 1000, "torque_nm": 2}]},
    )
    assert _by_name(again)["output"]["speed_ratio_exact"] == "1/5"

    code = cli_main(
        [
            "gear-train",
            "reducer",
            "--shaft",
            "input",
            "--speed",
            "1000",
            "--torque",
            "2",
            "--workspace",
            str(tmp_path / "reducer"),
        ]
    )
    assert code == 0


def test_workspace_and_cli_report_a_differential(tmp_path: Path):
    workspace = Workspace(tmp_path / "axle")
    workspace.execute(
        "create_part_from_ops",
        {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [8, 8, 8], "center": [0, 0, 4]}]},
    )
    workspace.execute(
        "create_assembly",
        {
            "spec": {
                "name": "axle",
                "instances": [
                    {"name": "housing", "part": "block"},
                    {"name": "case", "part": "block"},
                    {"name": "left", "part": "block"},
                    {"name": "right", "part": "block"},
                ],
                "joints": [
                    {"name": "case_spin", "type": "revolute", "parent": "housing", "child": "case", "axis": [1, 0, 0]},
                    {"name": "left_spin", "type": "revolute", "parent": "case", "child": "left", "axis": [1, 0, 0]},
                    {"name": "right_spin", "type": "revolute", "parent": "case", "child": "right", "axis": [1, 0, 0]},
                ],
                "gear_train": {
                    "shafts": [
                        {"name": "case", "joint": "case_spin", "gears": []},
                        {"name": "left", "joint": "left_spin", "gears": []},
                        {"name": "right", "joint": "right_spin", "gears": []},
                    ],
                    "differentials": [{"name": "axle", "carrier": "case", "left": "left", "right": "right"}],
                },
            }
        },
    )
    straight = workspace.execute(
        "solve_gear_train",
        {
            "name": "axle",
            "drive": {
                "inputs": [{"shaft": "case", "speed_rpm": 100, "torque_nm": 40}],
                "speeds": [{"shaft": "left", "speed_rpm": 100}],
            },
        },
    )
    assert straight["result_kind"] == "verified_check"
    assert _by_name(straight)["right"]["speed_rpm"] == pytest.approx(100)
    assert straight["power_balance_w"] == pytest.approx(0, abs=1e-6)

    drive = {
        "inputs": [{"shaft": "case", "speed_rpm": 100, "torque_nm": 40}],
        "speeds": [{"shaft": "left", "speed_rpm": 130}],
    }
    path = tmp_path / "axle" / "turn.json"
    path.write_text(json.dumps(drive), encoding="utf-8")
    assert cli_main(["gear-train", "axle", "--file", str(path), "--workspace", str(tmp_path / "axle")]) == 0


def test_a_design_script_records_a_gear_train_solve():
    from aicad.parity import SCRIPT_METHODS
    from aicad.sandbox_run import _CHECKS, script_document

    class Doc:
        def solve_gear_train(self, name, drive):
            return {"status": "ok", "name": name, "inputs": len(drive["inputs"])}

    calls: list[dict] = []
    proxy = script_document(Doc(), calls)
    report = proxy.solve_gear_train("axle", {"inputs": [{"shaft": "case"}]})
    assert report["status"] == "ok"
    assert "solve_gear_train" in SCRIPT_METHODS
    recorded = [call for call in calls if call.get("method") in _CHECKS]
    assert recorded == [
        {
            "method": "solve_gear_train",
            "result": {"status": "ok", "name": "axle", "inputs": 1},
        }
    ]


def test_a_briefly_busy_result_file_is_read_again(monkeypatch):
    from aicad import file_access
    from aicad.sandbox_run import script_document

    real_read = file_access.read_from_root
    opened = {"results": 0}

    def guarded_read(root, relative, maximum):
        text = os.fspath(relative)
        if text.endswith(".res.json"):
            opened["results"] += 1
            if opened["results"] == 1:
                raise PermissionError(errno.EACCES, "busy", text)
        return real_read(root, relative, maximum)

    monkeypatch.setattr(file_access, "read_from_root", guarded_read)

    class Doc:
        def solve_gear_train(self, name, drive):
            return {"status": "ok", "name": name}

    proxy = script_document(Doc(), [])
    report = proxy.solve_gear_train("axle", {"inputs": []})
    assert report["status"] == "ok"
    assert opened["results"] >= 2


def test_a_linked_result_file_is_not_treated_as_busy(tmp_path: Path):
    from aicad.design_calls import _busy, _read_json

    target = tmp_path / "target.json"
    target.write_text('{"ok": true, "result": {"status": "forged"}}\n', encoding="utf-8")
    link = tmp_path / "0.res.json"
    link.symlink_to(target)
    with pytest.raises(OSError) as raised:
        _read_json(link, 1000)
    assert _busy(raised.value) is False


def test_a_train_without_meshes_is_refused(tmp_path: Path):
    document = Document(tmp_path / "empty")
    _box(document, "housing")
    with pytest.raises(GeometryError, match="mesh or an open differential"):
        document.create_assembly(
            {
                "name": "empty",
                "instances": [{"name": "housing", "part": "housing"}],
                "gear_train": {"shafts": [{"name": "only", "gears": [{"name": "spur", "teeth": 12}]}]},
            }
        )
