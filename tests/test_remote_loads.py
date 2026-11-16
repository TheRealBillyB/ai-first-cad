"""A force acting at a point off the part reaches it with its lever arm.

A load at the end of a handle or a bracket that is not modeled had to be
turned into a force and a torque by hand. A remote load takes the force and
its point, and carries both to the selected faces.
"""

from __future__ import annotations

import numpy as np
import pytest

from aicad import fea
from aicad.errors import GeometryError
from aicad.workspace import Workspace

_AVAILABLE, _REASON = fea.fea_available()
_BEAM = [{"op": "add", "shape": "box", "size": [60, 10, 10], "center": [30, 0, 0]}]


def _tip_nodes(monkeypatch, points):
    mesh = {"points": np.array(points, dtype=np.float64)}
    monkeypatch.setattr(fea, "_nodes_for", lambda _mesh, _faces: np.arange(len(points)))
    return mesh


def test_the_nodal_forces_carry_the_force_and_its_moment(monkeypatch):
    grid = [[60.0, y, z] for y in (-5, 0, 5) for z in (-5, 0, 5)]
    mesh = _tip_nodes(monkeypatch, grid)
    forces = np.zeros((len(grid), 3))
    load = {"name": "handle", "faces": [{"index": 0}], "spec": {"vector": [0, 0, -100], "at": [110, 20, 0], "moment_nm": [0.5, 0, 0]}}
    touched = fea._apply_remote(mesh, load, forces)
    assert touched == set(range(len(grid)))
    assert forces.sum(axis=0) == pytest.approx([0, 0, -100], abs=1e-9)
    moment = np.cross(mesh["points"], forces).sum(axis=0)
    expected = np.cross([110, 20, 0], [0, 0, -100]) + np.array([500.0, 0, 0])
    assert moment == pytest.approx(expected, abs=1e-6)


def test_a_twist_nodes_on_one_line_cannot_carry_is_refused(monkeypatch):
    line = [[60.0, 0.0, z] for z in (-5, 0, 5)]
    mesh = _tip_nodes(monkeypatch, line)
    load = {"name": "twist", "faces": [{"index": 0}], "spec": {"vector": [0, 0, 0], "at": [60, 0, 0], "moment_nm": [0, 0, 1]}}
    with pytest.raises(GeometryError, match="cannot carry"):
        fea._apply_remote(mesh, load, np.zeros((3, 3)))


def test_a_remote_load_needs_its_point():
    catalog = [{"index": 0, "kind": "plane", "normal": [1, 0, 0], "center_mm": [60, 0, 0], "area_mm2": 100.0}]
    with pytest.raises(GeometryError, match="needs at"):
        fea._bind_load({"type": "remote", "vector": [0, 0, -1], "select": {"kind": "index", "index": 0}}, catalog, 0)
    bound = fea._bind_load(
        {"type": "remote", "vector": [0, 0, 0], "at": [70, 0, 0], "select": {"kind": "index", "index": 0}}, catalog, 0
    )
    assert bound["zero"] is True


def _solve(workspace, load):
    setup = {
        "material": "steel",
        "mesh_size_mm": 2.5,
        "target_safety_factor": 1.0,
        "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        "loads": [{"name": "tip", "select": {"kind": "plane", "normal": [1, 0, 0]}, **load}],
    }
    return workspace.execute("solve_mechanics", {"name": "beam", "setup": setup}, source="agent")


@pytest.mark.skipif(not _AVAILABLE, reason=_REASON)
def test_a_force_beyond_the_tip_bends_the_root_by_its_lever_arm(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "beam", "ops": _BEAM}, source="agent")
    plain = _solve(workspace, {"type": "force", "vector": [0, 0, -100]})
    at_tip = _solve(workspace, {"type": "remote", "vector": [0, 0, -100], "at": [60, 0, 0]})
    beyond = _solve(workspace, {"type": "remote", "vector": [0, 0, -100], "at": [110, 0, 0]})
    # At the face's own center, a remote load is the plain force.
    assert at_tip["max_von_mises_mpa"] == pytest.approx(plain["max_von_mises_mpa"], rel=0.02)
    assert at_tip["max_displacement_mm"] == pytest.approx(plain["max_displacement_mm"], rel=0.02)
    # 50 mm further out the tip also takes a 5000 N·mm moment. A cantilever's tip
    # deflection is F L^3 / 3EI + M L^2 / 2EI, so it grows by 1 + 75 / 60 = 2.25.
    ratio = beyond["max_displacement_mm"] / plain["max_displacement_mm"]
    assert ratio == pytest.approx(2.25, rel=0.06), ratio
