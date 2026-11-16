"""Modeled threads: a lead screw and its nut, or a metric thread.

A design script could not import bd_warehouse, which has the thread
geometry, so the vise's two-start lead screw was written by hand: about two
hours of a session. aicad.threads builds threads with it, a script reaches
them through aicad, and a shape operation can be a thread.
"""

from __future__ import annotations

import math
import textwrap
from pathlib import Path

import pytest
from build123d import Cylinder, Pos

from aicad import kernel, threads
from aicad.errors import GeometryError
from aicad.sandbox import run_part_script, sandbox_supported


def test_a_lead_screw_has_its_crest_and_root():
    screw = threads.screw(14, 4, 30, starts=2)
    assert screw.is_valid and len(screw.solids()) == 1
    box = screw.bounding_box()
    assert box.max.X - box.min.X == pytest.approx(14, abs=0.05)
    assert box.max.Z - box.min.Z == pytest.approx(30, abs=1e-6)
    info = threads.thread_info(14, 4, starts=2)
    assert info["lead_mm"] == 8
    assert info["crest_diameter_mm"] == pytest.approx(14)
    assert info["root_diameter_mm"] == pytest.approx(10)
    assert info["helix_angle_deg"] == pytest.approx(math.degrees(math.atan(8 / (math.pi * 12))))


@pytest.mark.parametrize("starts", [1, 2])
def test_a_screw_and_a_nut_from_the_same_numbers_mesh(starts):
    screw = threads.screw(14, 4, 30, starts=starts)
    nut = Pos(0, 0, 10) * Cylinder(15, 20) - threads.nut_tool(14, 4, 20, starts=starts)
    assert (screw & nut).volume == pytest.approx(0.0, abs=0.01)
    loose_screw = threads.screw(14, 4, 30, starts=starts, clearance_mm=0.2)
    loose_nut = Pos(0, 0, 10) * Cylinder(15, 20) - threads.nut_tool(14, 4, 20, starts=starts, clearance_mm=0.2)
    assert loose_screw.distance_to(loose_nut) > 0.05


def test_a_metric_bolt_thread_fits_its_tapped_hole():
    bolt = threads.screw(8, 1.25, 15, profile="iso")
    hole = Pos(0, 0, 5) * Cylinder(8, 10) - threads.nut_tool(8, 1.25, 10, profile="iso")
    assert bolt.is_valid and hole.is_valid
    assert (bolt & hole).volume == pytest.approx(0.0, abs=0.01)


def test_a_thread_is_a_shape_operation(tmp_path):
    rod = kernel.make_from_ops([{"op": "add", "shape": "thread", "diameter": 14, "pitch": 4, "starts": 2, "length": 40, "at": [0, 0, 5]}])
    box = rod.impl.bounding_box()
    assert box.min.Z == pytest.approx(5, abs=1e-6) and box.max.Z == pytest.approx(45, abs=1e-6)
    nut = kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [30, 30, 20], "center": [0, 0, 10]},
            {"op": "cut", "shape": "thread", "diameter": 14, "pitch": 4, "starts": 2, "length": 20},
        ]
    )
    assert nut.impl.is_valid and len(nut.impl.solids()) == 1
    assert float(nut.impl.volume) < 30 * 30 * 20 - math.pi * 5**2 * 20


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"profile": "buttress"}, "profile"),
        ({"profile": "iso", "starts": 2}, "one start"),
        ({"starts": 0}, "1 to 8 starts"),
        ({"angle": 95}, "flank angle"),
        ({"hand": "up"}, "right or left"),
        ({"clearance_mm": 2}, "quarter of the pitch"),
        ({"ends": "round"}, "ends"),
    ],
)
def test_a_thread_that_cannot_be_made_is_refused(arguments, message):
    with pytest.raises(GeometryError, match=message):
        threads.screw(14, 4, 20, **arguments)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def test_a_part_script_can_make_a_lead_screw(tmp_path: Path, config_home):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    shape = run_part_script(
        textwrap.dedent(
            """
            from aicad.threads import screw
            part = screw(14, 4, 20, starts=2, clearance_mm=0.2)
            """
        ),
        tmp_path,
    )
    assert shape.impl.is_valid
    assert float(shape.impl.volume) > math.pi * 5**2 * 20


def test_a_thread_op_lists_and_changes_its_own_sizes(tmp_path):
    from aicad.workspace import Workspace

    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "create_part_from_ops",
        {"name": "rod", "ops": [{"op": "add", "shape": "thread", "diameter": 14, "pitch": 4, "starts": 2, "length": 20}]},
        source="agent",
    )
    listed = {item["id"]: item for item in workspace.execute("list_parameters", {"name": "rod"}, source="agent")["parameters"]}
    assert {"op/0/diameter", "op/0/pitch", "op/0/length", "op/0/starts", "op/0/angle", "op/0/at.x"} <= set(listed)
    assert not any(item.startswith("op/0/center") for item in listed)
    assert listed["op/0/starts"]["unit"] == ""
    workspace.execute("set_parameter", {"name": "rod", "parameter": "op/0/length", "value": 30}, source="ui")
    box = workspace.document._parts["rod"].shape.impl.bounding_box()
    assert box.max.Z - box.min.Z == pytest.approx(30, abs=1e-6)
    workspace.execute("set_parameter", {"name": "rod", "parameter": "op/0/at.x", "value": 50}, source="ui")
    box = workspace.document._parts["rod"].shape.impl.bounding_box()
    assert (box.max.X + box.min.X) / 2 == pytest.approx(50, abs=0.05)
    # The count comes back from set_parameter as 1.0, a whole number.
    workspace.execute("set_parameter", {"name": "rod", "parameter": "op/0/starts", "value": 1}, source="ui")
    with pytest.raises(GeometryError, match="No parameter 'op/0/center.x'"):
        workspace.execute("set_parameter", {"name": "rod", "parameter": "op/0/center.x", "value": 5}, source="ui")


def test_a_thread_given_a_center_is_refused():
    with pytest.raises(GeometryError, match="placed by at"):
        kernel.make_from_ops([{"op": "add", "shape": "thread", "diameter": 14, "pitch": 4, "length": 20, "center": [50, 0, 0]}])


def test_starts_given_as_a_whole_float_is_taken():
    assert threads.thread_info(14, 4, starts=2.0)["lead_mm"] == 8
    with pytest.raises(GeometryError, match="1 to 8 starts"):
        threads.screw(14, 4, 20, starts=2.5)
