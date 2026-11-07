"""The overhang angle's refusal names the range it really allows."""

from __future__ import annotations

import math

import pytest

from aicad import kernel
from aicad.errors import GeometryError
from aicad.printprep import analyze_print

_BOX = [{"op": "add", "shape": "box", "size": [20, 20, 10], "center": [0, 0, 5]}]


@pytest.mark.parametrize("angle", [0, 90, -10, 120, math.nan, "0"])
def test_an_angle_outside_the_range_is_refused_with_that_range(angle):
    # The message used to say "between 0 and 90" while refusing 0 and 90.
    with pytest.raises(GeometryError, match="Use a number above 0 and below 90") as caught:
        analyze_print(kernel.make_from_ops(_BOX), {"threshold_deg": angle})
    assert "between 0 and 90" not in str(caught.value)


def test_an_angle_that_is_not_a_number_is_named():
    with pytest.raises(GeometryError, match="threshold_deg must be a number of degrees"):
        analyze_print(kernel.make_from_ops(_BOX), {"threshold_deg": "steep"})


@pytest.mark.parametrize(("angle", "used"), [(None, 45.0), (1, 1.0), (89.5, 89.5), ("60", 60.0)])
def test_an_angle_inside_the_range_is_used(angle, used):
    report = analyze_print(kernel.make_from_ops(_BOX), {"threshold_deg": angle})
    assert report["threshold_deg"] == used
