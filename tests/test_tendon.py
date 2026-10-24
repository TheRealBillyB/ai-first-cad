"""Tendon length, pulley wrap, capstan tension, and collision."""

import json
import math
from pathlib import Path

import pytest

from aicad.cli import main as cli_main
from aicad.document import Document
from aicad.workspace import Workspace


def _box(document: Document, name: str, size: list[float], center: list[float]) -> None:
    document.create_part_from_ops(
        name,
        [{"op": "add", "shape": "box", "size": size, "center": center}],
    )


def _by_name(report: dict) -> dict:
    return {cable["name"]: cable for cable in report["cables"]}


def test_straight_tendon_length_slack_and_stretch(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "block", [10, 10, 10], [0, 0, 40])
    points = [
        {"role": "anchor", "instance": "frame", "at": [0, 0, 0]},
        {"role": "anchor", "instance": "frame", "at": [100, 0, 0]},
    ]
    document.create_assembly(
        {
            "name": "span",
            "instances": [{"name": "frame", "part": "block"}],
            "joints": [],
            "cables": [
                {
                    "name": "exact",
                    "kind": "tendon",
                    "diameter_mm": 2,
                    "material": "steel",
                    "rest_length_mm": 100,
                    "actuator_force_n": 15,
                    "points": points,
                },
                {
                    "name": "loose",
                    "kind": "tendon",
                    "diameter_mm": 2,
                    "material": "steel",
                    "rest_length_mm": 120,
                    "actuator_force_n": 15,
                    "points": points,
                },
                {
                    "name": "short",
                    "kind": "tendon",
                    "diameter_mm": 2,
                    "material": "steel",
                    "rest_length_mm": 80,
                    "actuator_force_n": 15,
                    "points": points,
                },
            ],
        }
    )
    report = document.check_cables("span")
    cables = _by_name(report)
    exact = cables["exact"]
    assert exact["status"] == "pass"
    assert exact["length_mm"] == pytest.approx([100])
    assert exact["length_change_mm"] == pytest.approx([0])
    assert exact["length_state"] == ["neutral"]
    assert exact["frames"][0]["span_length_mm"] == pytest.approx(100)
    assert exact["frames"][0]["arc_length_mm"] == pytest.approx(0)
    assert exact["frames"][0]["tensions_n"] == pytest.approx([15])
    assert exact["frames"][0]["tension_carried"] is True
    loose = cables["loose"]
    assert loose["length_state"] == ["slack"]
    assert loose["length_change_mm"] == pytest.approx([-20])
    assert loose["status"] == "pass"
    assert loose["frames"][0]["tension_n"] == pytest.approx(0)
    assert loose["frames"][0]["tension_carried"] is False
    short = cables["short"]
    assert short["length_state"] == ["stretch"]
    assert short["length_change_mm"] == pytest.approx([20])
    assert short["max_strain"] == pytest.approx(0.25)
    assert short["status"] == "fail"
    assert report["status"] == "fail"
    assert report["result_kind"] == "rough_estimate"
    bom = document.bill_of_materials("span")
    tendon = next(row for row in bom["rows"] if row["name"] == "exact")
    assert tendon["category"] == "tendon"
    assert tendon["mass_estimate_kg"] is None
    workspace = Workspace(tmp_path)
    again = workspace.execute("check_cables", {"name": "span"})
    assert _by_name(again)["exact"]["length_mm"] == pytest.approx([100])


def test_one_pulley_wraps_ninety_degrees_and_a_tight_sheave_fails(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "block", [10, 10, 10], [0, 0, 40])
    points = [
        {"role": "anchor", "instance": "frame", "at": [-50, 10, 0]},
        {
            "role": "pulley",
            "name": "sheave",
            "instance": "frame",
            "at": [0, 0, 0],
            "axis": [0, 0, 1],
            "radius_mm": 10,
            "wrap": "cw",
        },
        {"role": "anchor", "instance": "frame", "at": [10, -50, 0]},
    ]
    shared = {"kind": "tendon", "diameter_mm": 2, "material": "steel", "points": points}
    document.create_assembly(
        {
            "name": "quarter",
            "instances": [{"name": "frame", "part": "block"}],
            "joints": [],
            "cables": [
                {"name": "quarter", "min_bend_radius_mm": 4, **shared},
                {"name": "tight", "min_bend_radius_mm": 30, **shared},
            ],
        }
    )
    report = document.check_cables("quarter")
    cables = _by_name(report)
    quarter = cables["quarter"]
    assert quarter["status"] == "pass"
    frame = quarter["frames"][0]
    # Top tangent, 90° clockwise, then the +X tangent. Spans are 50 and 50.
    assert frame["wraps"][0]["wrap_deg"] == pytest.approx(90, abs=0.05)
    assert frame["wraps"][0]["radius_mm"] == pytest.approx(10)
    assert frame["wraps"][0]["bend_ok"] is True
    assert frame["arc_length_mm"] == pytest.approx(5 * math.pi, abs=0.01)
    assert frame["span_length_mm"] == pytest.approx(100, abs=0.01)
    assert frame["length_mm"] == pytest.approx(100 + 5 * math.pi, abs=0.01)
    assert frame["contacts"][1]["at_mm"] == pytest.approx([0, 10, 0], abs=0.02)
    assert frame["contacts"][2]["at_mm"] == pytest.approx([10, 0, 0], abs=0.02)
    tight = cables["tight"]
    assert tight["status"] == "fail"
    assert tight["min_bend_radius_mm"] == pytest.approx(10)
    assert tight["frames"][0]["bend_ok"] is False
    assert tight["frames"][0]["wraps"][0]["wrap_deg"] == pytest.approx(90, abs=0.05)


def test_two_pulleys_take_the_external_tangent(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "block", [8, 8, 8], [0, 0, 40])
    document.create_assembly(
        {
            "name": "pair",
            "instances": [{"name": "frame", "part": "block"}],
            "joints": [],
            "cables": [
                {
                    "name": "belt",
                    "kind": "tendon",
                    "diameter_mm": 2,
                    "material": "steel",
                    "min_bend_radius_mm": 4,
                    "points": [
                        {"role": "anchor", "instance": "frame", "at": [-50, 10, 0]},
                        {
                            "role": "pulley",
                            "name": "upper",
                            "instance": "frame",
                            "at": [0, 0, 0],
                            "axis": [0, 0, 1],
                            "radius_mm": 10,
                            "wrap": "cw",
                        },
                        {
                            "role": "pulley",
                            "name": "lower",
                            "instance": "frame",
                            "at": [0, -40, 0],
                            "axis": [0, 0, 1],
                            "radius_mm": 10,
                            "wrap": "cw",
                        },
                        {"role": "anchor", "instance": "frame", "at": [-40, -50, 0]},
                    ],
                }
            ],
        }
    )
    frame = document.check_cables("pair")["cables"][0]["frames"][0]
    assert frame["status"] == "pass"
    assert [wrap["wrap_deg"] for wrap in frame["wraps"]] == pytest.approx([90, 90], abs=0.05)
    # 50 into the top, 40 down the external tangent, 40 out along the bottom, plus two quarter arcs.
    assert frame["span_length_mm"] == pytest.approx(130, abs=0.02)
    assert frame["arc_length_mm"] == pytest.approx(10 * math.pi, abs=0.02)
    assert frame["length_mm"] == pytest.approx(130 + 10 * math.pi, abs=0.02)
    assert frame["contacts"][2]["at_mm"] == pytest.approx([10, 0, 0], abs=0.02)
    assert frame["contacts"][3]["at_mm"] == pytest.approx([10, -40, 0], abs=0.02)


def test_tendon_length_stays_constant_through_a_revolute(tmp_path: Path, capsys):
    document = Document(tmp_path)
    _box(document, "base", [8, 8, 8], [30, 0, 30])
    _box(document, "link", [8, 8, 8], [40, 0, 30])
    document.create_assembly(
        {
            "name": "hinge",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "link", "part": "link"},
            ],
            "joints": [
                {
                    "name": "hinge",
                    "type": "revolute",
                    "parent": "base",
                    "child": "link",
                    "axis": [0, 0, 1],
                    "value": 0,
                }
            ],
            "studies": {
                "swing": {
                    "duration_s": 1,
                    "steps": 5,
                    "drivers": [{"joint": "hinge", "expression": "90 * t"}],
                }
            },
            "cables": [
                {
                    "name": "crossing",
                    "kind": "tendon",
                    "diameter_mm": 2,
                    "material": "steel",
                    "min_bend_radius_mm": 5,
                    "points": [
                        {"role": "anchor", "name": "fixed", "instance": "base", "at": [30, 0, 0]},
                        {"role": "guide", "name": "pivot", "instance": "base", "at": [0, 0, 0]},
                        {"role": "anchor", "name": "moving", "instance": "link", "at": [40, 0, 0]},
                    ],
                }
            ],
        }
    )
    report = document.check_cables("hinge", sequence="swing")
    cable = report["cables"][0]
    assert report["study"] == "swing"
    assert report["steps"] == 5
    assert cable["status"] == "pass"
    assert cable["length_mm"] == pytest.approx([70, 70, 70, 70, 70], abs=0.01)
    assert cable["length_change_mm"] == pytest.approx([0, 0, 0, 0, 0], abs=0.01)
    assert cable["length_state"] == ["neutral", "neutral", "neutral", "neutral", "neutral"]
    start = cable["frames"][0]["contacts"][-1]["at_mm"]
    end = cable["frames"][-1]["contacts"][-1]["at_mm"]
    assert start == pytest.approx([40, 0, 0], abs=0.02)
    assert math.hypot(end[0], end[1]) == pytest.approx(40, abs=0.02)
    assert abs(end[1]) == pytest.approx(40, abs=0.02)
    guide = next(bend for bend in cable["frames"][-1]["bends"] if bend["role"] == "guide")
    assert guide["radius_mm"] == pytest.approx(25, abs=0.05)
    code = cli_main(["cables", "hinge", "--sequence", "swing", "--workspace", str(tmp_path)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cables"][0]["length_mm"] == pytest.approx([70, 70, 70, 70, 70], abs=0.01)


def test_capstan_tension_ratio(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "block", [10, 10, 10], [0, 0, 40])
    document.create_assembly(
        {
            "name": "capstan",
            "instances": [{"name": "frame", "part": "block"}],
            "joints": [],
            "cables": [
                {
                    "name": "line",
                    "kind": "tendon",
                    "diameter_mm": 2,
                    "material": "steel",
                    "min_bend_radius_mm": 4,
                    "actuator_force_n": 20,
                    "actuator_end": "end",
                    "points": [
                        {"role": "anchor", "instance": "frame", "at": [-60, 10, 0]},
                        {
                            "role": "pulley",
                            "name": "post",
                            "instance": "frame",
                            "at": [0, 0, 0],
                            "axis": [0, 0, 1],
                            "radius_mm": 10,
                            "wrap": "cw",
                            "friction": 0.3,
                        },
                        {"role": "anchor", "instance": "frame", "at": [-60, -10, 0]},
                    ],
                }
            ],
        }
    )
    frame = document.check_cables("capstan")["cables"][0]["frames"][0]
    ratio = math.exp(0.3 * math.pi)
    assert frame["status"] == "pass"
    assert frame["wraps"][0]["wrap_deg"] == pytest.approx(180, abs=0.05)
    assert frame["wraps"][0]["capstan_ratio"] == pytest.approx(ratio, rel=1e-6)
    assert frame["tension_ratio"] == pytest.approx(ratio, rel=1e-6)
    assert frame["tensions_n"][1] == pytest.approx(20, abs=1e-3)
    assert frame["tensions_n"][0] == pytest.approx(20 / ratio, abs=1e-3)
    assert frame["length_mm"] == pytest.approx(120 + 10 * math.pi, abs=0.02)
    area = math.pi * 1.0**2
    assert frame["elastic_elongation_mm"] == pytest.approx(20 * frame["length_mm"] / (210000 * area), rel=1e-3)


def test_tendon_through_a_solid_and_a_rub_are_collisions(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "block", [20, 20, 20], [0, 0, 0])
    document.create_assembly(
        {
            "name": "blocked",
            "instances": [{"name": "post", "part": "block"}],
            "joints": [],
            "cables": [
                {
                    "name": "through",
                    "kind": "tendon",
                    "diameter_mm": 4,
                    "material": "steel",
                    "min_bend_radius_mm": 1,
                    "pinch_mm": 0.5,
                    "points": [
                        {"role": "anchor", "instance": "post", "at": [-40, 0, 0]},
                        {"role": "anchor", "instance": "post", "at": [40, 0, 0]},
                    ],
                },
                {
                    "name": "beside",
                    "kind": "tendon",
                    "diameter_mm": 4,
                    "material": "steel",
                    "min_bend_radius_mm": 1,
                    "pinch_mm": 0.5,
                    "points": [
                        {"role": "anchor", "instance": "post", "at": [-30, 11, 0]},
                        {"role": "anchor", "instance": "post", "at": [30, 11, 0]},
                    ],
                },
            ],
        }
    )
    cables = _by_name(document.check_cables("blocked"))
    through = cables["through"]
    assert through["status"] == "fail"
    assert through["pinch_count"] >= 1
    hit = through["frames"][0]["collisions"][0]
    assert hit["kind"] == "inside"
    assert hit["inside"] is True
    assert hit["part"] == "post"
    beside = cables["beside"]
    assert beside["status"] == "fail"
    rub = beside["frames"][0]["collisions"][0]
    assert rub["kind"] == "rub"
    assert rub["inside"] is False
    assert rub["part"] == "post"


def test_a_guide_on_the_previous_anchor_is_not_a_sharp_bend(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "block", [10, 10, 10], [0, 0, 40])
    document.create_assembly(
        {
            "name": "span",
            "instances": [{"name": "frame", "part": "block"}],
            "joints": [],
            "cables": [
                {
                    "name": "repeat",
                    "kind": "tendon",
                    "diameter_mm": 2,
                    "material": "steel",
                    "min_bend_radius_mm": 20,
                    "points": [
                        {"role": "anchor", "instance": "frame", "at": [0, 0, 0]},
                        {"role": "guide", "name": "same", "instance": "frame", "at": [0, 0, 0]},
                        {"role": "anchor", "instance": "frame", "at": [100, 0, 0]},
                    ],
                }
            ],
        }
    )
    cable = document.check_cables("span")["cables"][0]
    assert cable["status"] == "pass"
    assert cable["min_bend_radius_mm"] is None
    bend = cable["frames"][0]["bends"][0]
    assert bend["ok"] is True
    assert bend["radius_mm"] is None
    assert bend["name"] == "same"
