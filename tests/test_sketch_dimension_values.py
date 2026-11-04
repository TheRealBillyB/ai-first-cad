"""A length that cannot exist is named instead of blamed on the solver."""

import pytest

from aicad import Document, GeometryError


def _spec():
    return {
        "plane": "xy",
        "entities": [
            {"id": "a", "type": "point", "at": [0, 0]},
            {"id": "b", "type": "point", "at": [20, 0]},
            {"id": "c", "type": "point", "at": [20, 10]},
            {"id": "edge", "type": "line", "start": "a", "end": "b"},
            {"id": "o", "type": "point", "at": [5, 5]},
            {"id": "bore", "type": "circle", "center": "o", "radius": 2},
        ],
        "constraints": [
            {"type": "fix", "entity": "a"},
            {"type": "fix", "entity": "b"},
            {"type": "fix", "entity": "o"},
            {"type": "distance", "a": "c", "b": "a", "value": 22, "name": "reach"},
            {"type": "distance", "a": "c", "b": "edge", "value": -10, "name": "inset"},
            {"type": "diameter", "entity": "bore", "value": 4, "name": "bore_d"},
        ],
    }


@pytest.mark.parametrize(("dimension", "value"), [("reach", -5), ("bore_d", 0), ("bore_d", -4)])
def test_an_impossible_length_is_named_and_nothing_changes(tmp_path, dimension, value):
    document = Document(tmp_path)
    document.create_sketch("s", _spec())
    before = document.get_sketch("s").dimensions
    with pytest.raises(GeometryError, match=dimension) as caught:
        document.set_dimension("s", dimension, value)
    assert "converge" not in str(caught.value)
    assert document.get_sketch("s").dimensions == before


def test_a_point_to_line_distance_keeps_its_sign(tmp_path):
    document = Document(tmp_path)
    document.create_sketch("s", _spec())
    assert document.set_dimension("s", "inset", -12).status != "didnt_converge"
