"""An eyelet guide: friction over the angle the line turns through it, and a lip radius."""

import math
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError


def _eyelet_assembly(document: Document, eyelet: dict, *, limit: float = 4.0) -> dict:
    document.create_part_from_ops("block", [{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 40]}])
    return document.create_assembly(
        {
            "name": "eyelet",
            "instances": [{"name": "frame", "part": "block"}],
            "joints": [],
            "cables": [
                {
                    "name": "line",
                    "kind": "tendon",
                    "diameter_mm": 1,
                    "material": "steel",
                    "min_bend_radius_mm": limit,
                    "actuator_force_n": 20,
                    "actuator_end": "end",
                    "points": [
                        {"role": "anchor", "instance": "frame", "at": [-60, 0, 0]},
                        # The line turns 90 degrees here: in along +X, out along +Y.
                        {"role": "guide", "name": "eye", "instance": "frame", "at": [0, 0, 0], **eyelet},
                        {"role": "anchor", "instance": "frame", "at": [0, 60, 0]},
                    ],
                }
            ],
        }
    )


def _frame(document: Document) -> dict:
    return document.check_cables("eyelet")["cables"][0]["frames"][0]


def test_eyelet_friction_acts_over_the_turn_angle(tmp_path: Path):
    document = Document(tmp_path)
    _eyelet_assembly(document, {"friction": 0.2, "wrap_radius_mm": 5})
    frame = _frame(document)
    ratio = math.exp(0.2 * math.pi / 2)
    assert frame["tension_ratio"] == pytest.approx(ratio, rel=1e-6)
    assert frame["tensions_n"][1] == pytest.approx(20, abs=1e-3)
    assert frame["tensions_n"][0] == pytest.approx(20 / ratio, abs=1e-3)
    bend = frame["bends"][0]
    assert bend["turn_deg"] == pytest.approx(90, abs=1e-3)
    assert bend["capstan_ratio"] == pytest.approx(ratio, rel=1e-6)
    # The line still runs through the point, so the length has no arc.
    assert frame["length_mm"] == pytest.approx(120, abs=1e-3)


def test_the_lip_radius_is_the_eyelet_bend_radius(tmp_path: Path):
    document = Document(tmp_path)
    _eyelet_assembly(document, {"wrap_radius_mm": 3}, limit=4.0)
    frame = _frame(document)
    bend = frame["bends"][0]
    assert bend["radius_mm"] == pytest.approx(3)
    assert bend["ok"] is False
    assert frame["min_bend_radius_mm"] == pytest.approx(3)


def test_an_eyelet_without_friction_passes_tension_through(tmp_path: Path):
    document = Document(tmp_path)
    _eyelet_assembly(document, {"wrap_radius_mm": 6})
    frame = _frame(document)
    assert frame["tension_ratio"] == pytest.approx(1.0)
    assert frame["bends"][0]["ok"] is True
    assert "capstan_ratio" not in frame["bends"][0]


@pytest.mark.parametrize(
    ("eyelet", "message"),
    [
        ({"wrap_radius_mm": 0}, "wrap_radius_mm"),
        ({"friction": -0.1}, "friction"),
        ({"axis": [0, 0, 1]}, "An eyelet takes friction and wrap_radius_mm instead"),
        ({"radius_mm": 5, "axis": [0, 0, 1], "wrap_radius_mm": 2}, "has radius_mm, so that is its wrap radius"),
    ],
)
def test_a_bad_eyelet_is_refused(tmp_path: Path, eyelet: dict, message: str):
    document = Document(tmp_path)
    _eyelet_assembly(document, eyelet)
    # Tendon points are read when the cables are checked.
    with pytest.raises(GeometryError, match=message):
        _frame(document)


def test_an_anchor_takes_no_wrap_radius(tmp_path: Path):
    document = Document(tmp_path)
    document.create_part_from_ops("block", [{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 40]}])
    document.create_assembly(
        {
            "name": "bad",
            "instances": [{"name": "frame", "part": "block"}],
            "joints": [],
            "cables": [
                {
                    "name": "line",
                    "kind": "tendon",
                    "diameter_mm": 1,
                    "material": "steel",
                    "points": [
                        {"role": "anchor", "instance": "frame", "at": [0, 0, 0], "wrap_radius_mm": 2},
                        {"role": "anchor", "instance": "frame", "at": [60, 0, 0]},
                    ],
                }
            ],
        }
    )
    with pytest.raises(GeometryError, match="is an anchor"):
        document.check_cables("bad")
