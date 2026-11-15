"""A strength solve reports how far one face moved relative to another.

How far a vise jaw opens under load had to be read from the single
max-displacement point and its direction. setup.relative names face pairs
and the solve reports their relative motion and the change in the gap.
"""

from __future__ import annotations

import pytest

from aicad import fea
from aicad.errors import GeometryError
from aicad.workspace import Workspace

_AVAILABLE, _REASON = fea.fea_available()
needs_solver = pytest.mark.skipif(not _AVAILABLE, reason=_REASON)


def _fork(workspace: Workspace) -> None:
    workspace.execute(
        "create_part_from_ops",
        {
            "name": "fork",
            "ops": [
                {"op": "add", "shape": "box", "size": [30, 10, 10], "center": [0, 0, 5]},
                {"op": "add", "shape": "box", "size": [5, 10, 30], "center": [-12.5, 0, 25]},
                {"op": "add", "shape": "box", "size": [5, 10, 30], "center": [12.5, 0, 25]},
            ],
        },
        source="agent",
    )


def _setup(**extra) -> dict:
    return {
        "material": "aluminum_6061",
        "mesh_size_mm": 2.0,
        "target_safety_factor": 1.0,
        "fixes": [{"name": "foot", "select": {"kind": "plane", "normal": [0, 0, -1]}}],
        "loads": [
            {"name": "left", "type": "force", "vector": [-50, 0, 0], "select": {"kind": "plane", "normal": [-1, 0, 0], "at": [-15, 0, 35]}},
            {"name": "right", "type": "force", "vector": [50, 0, 0], "select": {"kind": "plane", "normal": [1, 0, 0], "at": [15, 0, 35]}},
        ],
        **extra,
    }


@needs_solver
def test_the_gap_between_two_jaws_grows_when_they_are_pulled_apart(tmp_path):
    workspace = Workspace(tmp_path / "project")
    _fork(workspace)
    relative = [
        {
            "name": "jaws",
            "a": {"kind": "plane", "normal": [1, 0, 0], "at": [-10, 0, 25]},
            "b": {"kind": "plane", "normal": [-1, 0, 0], "at": [10, 0, 25]},
        }
    ]
    result = workspace.execute("solve_mechanics", {"name": "fork", "setup": _setup(relative=relative)}, source="agent")
    (jaws,) = result["relative"]
    assert jaws["name"] == "jaws"
    assert jaws["distance_between_mm"] == pytest.approx(20, abs=0.5)
    # Both prongs bend outward, so the inner faces move apart, by about twice one prong's motion.
    assert jaws["gap_change_mm"] > 0
    assert jaws["a_mean_displacement_mm"][0] < 0 < jaws["b_mean_displacement_mm"][0]
    assert jaws["gap_change_mm"] == pytest.approx(
        jaws["b_mean_displacement_mm"][0] - jaws["a_mean_displacement_mm"][0], rel=0.05
    )
    assert jaws["relative_magnitude_mm"] == pytest.approx(abs(jaws["relative_mm"][0]), rel=0.05)


@needs_solver
def test_a_tip_moves_relative_to_its_fixed_root(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "beam", "ops": [{"op": "add", "shape": "box", "size": [60, 10, 10], "center": [30, 0, 0]}]}, source="agent")
    setup = {
        "material": "steel",
        "mesh_size_mm": 2.5,
        "target_safety_factor": 1.0,
        "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        "loads": [{"name": "tip", "type": "force", "vector": [0, 0, -100], "select": {"kind": "plane", "normal": [1, 0, 0]}}],
        "relative": [{"name": "span", "a": {"kind": "plane", "normal": [-1, 0, 0]}, "b": {"kind": "plane", "normal": [1, 0, 0]}}],
    }
    result = workspace.execute("solve_mechanics", {"name": "beam", "setup": setup}, source="agent")
    (span,) = result["relative"]
    assert span["a_mean_displacement_mm"] == pytest.approx([0, 0, 0], abs=1e-9)
    assert span["relative_mm"][2] < 0
    assert abs(span["relative_mm"][2]) == pytest.approx(result["max_displacement_mm"], rel=0.1)
    # The beam bends down; its length barely changes.
    assert abs(span["gap_change_mm"]) < 0.1 * abs(span["relative_mm"][2])


def test_a_pair_needs_two_selects_that_match(tmp_path):
    catalog = [{"index": 0, "kind": "plane", "normal": [1, 0, 0], "center_mm": [0, 0, 0], "area_mm2": 1.0}]
    with pytest.raises(GeometryError, match="needs a and b"):
        fea._bind_relative([{"name": "x", "a": {"kind": "index", "index": 0}}], catalog, None)
    with pytest.raises(GeometryError, match="list of"):
        fea._bind_relative({"a": {}}, catalog, None)
    with pytest.raises(GeometryError, match="matched no face"):
        fea._bind_relative([{"a": {"kind": "index", "index": 0}, "b": {"kind": "plane", "normal": [0, 0, 1]}}], catalog, None)
