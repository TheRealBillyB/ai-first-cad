"""check_print finds material that begins in mid-air with nothing under it."""

import pytest
from build123d import Box, Plane, Polygon, Pos, extrude

from aicad import kernel
from aicad.printprep import analyze_print


def _wedge(apex_z: float, x: float = 25.0) -> object:
    """A V-shaped prism, apex down, with 45 degree flanks: 10 mm tall, 20 mm wide at the top, 10 mm deep in Y."""
    profile = Polygon((x, apex_z), (x + 10, apex_z + 10), (x - 10, apex_z + 10), align=None)
    return extrude(Plane.XZ * profile, amount=-10)


def _check(solid, **setup) -> dict:
    # A stated pose of no turn keeps the part as modeled, +Z up.
    return analyze_print(kernel.Shape(solid), {"rotation_deg": [0, 0, 0], "render": False, **setup})


def test_a_45_degree_face_that_grows_from_the_bed_has_no_floating_start():
    report = _check(_wedge(0.0))
    assert report["floating_starts"] == []


def test_the_same_45_degree_feature_starting_mid_height_is_a_floating_start():
    # A post at x 0 holds a plate at z 30 to 34. The wedge hangs under the plate
    # with its apex edge at z 20, and nothing is under that edge.
    post = Pos(2.5, 5, 17) * Box(5, 10, 34)
    plate = Pos(20, 5, 32) * Box(40, 10, 4)
    solid = post + plate + _wedge(20.0, x=25.0)
    report = _check(solid, layer_height_mm=0.2)
    starts = report["floating_starts"]
    assert len(starts) == 1
    start = starts[0]
    assert start["z_mm"] == pytest.approx(20.0, abs=1e-3)
    assert start["xy_mm"][0] == pytest.approx(25.0, abs=1e-3)
    # The first layer is a strip 10 mm long and 2 x 0.2 mm wide across the apex.
    assert start["area_mm2"] == pytest.approx(10 * 0.4, rel=0.05)
    assert start["faces"], "the start names the model faces it belongs to"
    assert start["at_model_mm"][2] == pytest.approx(20.0, abs=1e-3)
    assert report["status"] == "review"
    assert "floating start" in report["note"]


def test_a_floating_start_counts_as_support_in_the_orientation_scores():
    post = Pos(2.5, 5, 17) * Box(5, 10, 34)
    plate = Pos(20, 5, 32) * Box(40, 10, 4)
    with_wedge = _check(post + plate + _wedge(20.0, x=25.0), threshold_deg=50)
    without = _check(post + plate, threshold_deg=50)
    # The 45 degree flanks are steep enough to print at a 50 degree limit. The
    # wedge covers 20 x 10 mm of the plate's underside, and its floating start
    # adds its first layer.
    floating = with_wedge["floating_starts"][0]["area_mm2"]
    assert floating > 0
    assert with_wedge["support_area_mm2"] == pytest.approx(without["support_area_mm2"] - 200 + floating, abs=0.05)


def test_a_bridge_between_two_posts_is_not_a_floating_start():
    left = Pos(2.5, 5, 10) * Box(5, 10, 20)
    right = Pos(37.5, 5, 10) * Box(5, 10, 20)
    beam = Pos(20, 5, 22) * Box(40, 10, 4)
    report = _check(left + right + beam)
    assert report["floating_starts"] == []


def test_a_feature_that_starts_less_than_a_layer_above_material_prints_onto_it():
    # A block on the bed, and the wedge's apex 0.1 mm above the block's top,
    # held up by a post and plate of its own.
    post = Pos(2.5, 5, 17) * Box(5, 10, 34)
    plate = Pos(20, 5, 32) * Box(40, 10, 4)
    block = Pos(25, 5, 9.95) * Box(10, 10, 19.9)
    report = _check(post + plate + block + _wedge(20.0, x=25.0), layer_height_mm=0.2)
    assert report["floating_starts"] == []
