"""A motion study through a planetary gear train keeps the tooth ratios."""

import pytest

from aicad.errors import GeometryError
from aicad.motion_study import posed_at


def _assembly(fixed, drivers, couplings=None):
    study = {
        "name": "spin",
        "duration_s": 1,
        "steps": 5,
        "drivers": drivers,
    }
    if couplings:
        study["couplings"] = couplings
    return {
        "name": "planetary",
        "instances": [
            {"name": "ground", "part": "block"},
            {"name": "sun", "part": "block"},
            {"name": "planet", "part": "block"},
            {"name": "ring", "part": "block"},
            {"name": "carrier", "part": "block"},
        ],
        "joints": [
            {"name": "sun", "type": "revolute", "parent": "ground", "child": "sun", "axis": [0, 0, 1], "value": 10},
            {"name": "carrier", "type": "revolute", "parent": "ground", "child": "carrier", "axis": [0, 0, 1], "value": 20},
            {
                "name": "planet",
                "type": "revolute",
                "parent": "carrier",
                "child": "planet",
                "axis": [0, 0, 1],
                "value": 30,
            },
            {"name": "ring", "type": "revolute", "parent": "ground", "child": "ring", "axis": [0, 0, 1], "value": 5},
        ],
        "gear_train": {
            "shafts": [
                {"name": "sun", "joint": "sun", "gears": [{"name": "sun", "teeth": 24}]},
                {"name": "planet", "joint": "planet", "gears": [{"name": "planet", "teeth": 18}]},
                {"name": "ring", "joint": "ring", "gears": [{"name": "ring", "teeth": 60}]},
                {"name": "carrier", "joint": "carrier", "gears": []},
            ],
            "meshes": [
                {"name": "sun_planet", "a": "sun", "b": "planet", "carrier": "carrier"},
                {"name": "planet_ring", "a": "planet", "b": "ring", "kind": "internal", "carrier": "carrier"},
            ],
            "fixed": fixed,
        },
        "studies": {"spin": study},
    }


def _values(spec):
    rows = posed_at(spec, spec["studies"]["spin"], 5)
    return [{joint["name"]: joint["value"] for joint in row["solved"]["joints"]} for row in rows]


def _driver(joint, expression):
    return {"joint": joint, "expression": expression}


def test_driving_the_sun_with_the_ring_fixed_turns_the_carrier_and_planet():
    """Sun 24, planet 18, ring 60. Carrier is 2/7 of the sun. Planet spin is relative to the carrier."""
    rows = _values(_assembly(["ring"], [_driver("sun", "10 + 360 * t")]))
    assert rows[0] == {"sun": 10, "carrier": 20, "planet": 30, "ring": 5}
    for index, row in enumerate(rows):
        fraction = index / 4
        delta = 360 * fraction
        assert row["sun"] == pytest.approx(10 + delta, abs=1e-3)
        assert row["carrier"] == pytest.approx(20 + delta * 2 / 7, abs=1e-3)
        assert row["ring"] == pytest.approx(5, abs=1e-3)
        assert row["planet"] == pytest.approx(30 + delta * (-20 / 21), abs=1e-3)


def test_driving_the_carrier_with_the_ring_fixed_turns_the_sun_the_same_way():
    rows = _values(_assembly(["ring"], [_driver("carrier", "20 + 84 * t")]))
    for index, row in enumerate(rows):
        delta = 84 * index / 4
        assert row["carrier"] == pytest.approx(20 + delta, abs=1e-3)
        assert row["sun"] == pytest.approx(10 + delta * 7 / 2, abs=1e-3)
        assert row["ring"] == pytest.approx(5, abs=1e-3)
        assert row["planet"] == pytest.approx(30 + delta * (-10 / 3), abs=1e-3)


def test_driving_the_ring_with_the_carrier_fixed_reverses_the_sun():
    rows = _values(_assembly(["carrier"], [_driver("ring", "5 + 60 * t")]))
    for index, row in enumerate(rows):
        delta = 60 * index / 4
        assert row["ring"] == pytest.approx(5 + delta, abs=1e-3)
        assert row["carrier"] == pytest.approx(20, abs=1e-3)
        assert row["sun"] == pytest.approx(10 + delta * (-60 / 24), abs=1e-3)
        assert row["planet"] == pytest.approx(30 + delta * (60 / 18), abs=1e-3)


def test_driving_the_sun_with_the_carrier_fixed_reverses_the_ring():
    rows = _values(_assembly(["carrier"], [_driver("sun", "10 + 360 * t")]))
    for index, row in enumerate(rows):
        delta = 360 * index / 4
        assert row["sun"] == pytest.approx(10 + delta, abs=1e-3)
        assert row["carrier"] == pytest.approx(20, abs=1e-3)
        assert row["ring"] == pytest.approx(5 + delta * (-24 / 60), abs=1e-3)
        assert row["planet"] == pytest.approx(30 + delta * (-24 / 18), abs=1e-3)


def test_a_flipped_planet_axis_keeps_the_same_body_rotation():
    spec = _assembly(["ring"], [_driver("sun", "10 + 360 * t")])
    planet = next(joint for joint in spec["joints"] if joint["name"] == "planet")
    planet["axis"] = [0, 0, -1]
    rows = _values(spec)
    delta = 360
    assert rows[-1]["planet"] == pytest.approx(30 - 360 * (-20 / 21), abs=1e-3)
    assert rows[-1]["carrier"] == pytest.approx(20 + delta * 2 / 7, abs=1e-3)


def test_a_pairwise_coupling_does_not_replace_the_fixed_ring():
    rows = _values(
        _assembly(
            ["ring"],
            [_driver("sun", "10 + 360 * t")],
            [{"name": "bad", "driver": "sun", "driven": "ring", "ratio": 1, "offset": 0}],
        )
    )
    assert rows[-1]["ring"] == pytest.approx(5, abs=1e-3)
    assert rows[-1]["sun"] == pytest.approx(370, abs=1e-3)


def test_an_undetermined_planetary_does_not_invent_a_ratio():
    rows = _values(_assembly([], [_driver("sun", "10 + 360 * t")]))
    assert rows[-1]["sun"] == pytest.approx(370, abs=1e-3)
    assert rows[-1]["carrier"] == pytest.approx(20, abs=1e-3)
    assert rows[-1]["planet"] == pytest.approx(30, abs=1e-3)
    assert rows[-1]["ring"] == pytest.approx(5, abs=1e-3)


def test_members_that_disagree_are_refused():
    spec = _assembly(["ring"], [_driver("sun", "10"), _driver("carrier", "50")])
    with pytest.raises(GeometryError, match="disagree"):
        posed_at(spec, spec["studies"]["spin"], 3)


def test_a_fixed_axis_pair_reverses_and_keeps_its_phase():
    spec = {
        "name": "pair",
        "instances": [
            {"name": "ground", "part": "block"},
            {"name": "pinion", "part": "block"},
            {"name": "wheel", "part": "block"},
        ],
        "joints": [
            {"name": "pinion", "type": "revolute", "parent": "ground", "child": "pinion", "axis": [0, 0, 1], "value": 0},
            {
                "name": "wheel",
                "type": "revolute",
                "parent": "ground",
                "child": "wheel",
                "axis": [0, 0, 1],
                "value": 4,
                "parent_anchor": [30, 0, 0],
            },
        ],
        "gear_train": {
            "shafts": [
                {"name": "pinion", "joint": "pinion", "gears": [{"name": "pinion", "teeth": 20}]},
                {"name": "wheel", "joint": "wheel", "gears": [{"name": "wheel", "teeth": 40}]},
            ],
            "meshes": [{"a": "pinion", "b": "wheel"}],
        },
        "studies": {
            "spin": {
                "name": "spin",
                "duration_s": 1,
                "steps": 3,
                "drivers": [_driver("pinion", "100 * t")],
            }
        },
    }
    rows = _values(spec)
    assert rows[0]["wheel"] == pytest.approx(4, abs=1e-3)
    assert rows[-1]["pinion"] == pytest.approx(100, abs=1e-3)
    assert rows[-1]["wheel"] == pytest.approx(4 - 50, abs=1e-3)
