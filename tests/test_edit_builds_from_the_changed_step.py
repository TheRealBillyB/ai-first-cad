"""An edit to a part built from shapes or a sketch builds from the step that changed.

Every add_step, set_parameter, fillet, or chamfer built the part again from
its first op. On a drilled rotor that was about 7 s of booleans for each new
hole. A list of ops makes the same solid whenever it is built, so the solid
after each op is kept and a list that starts the same way starts from it.
"""

from __future__ import annotations

import pytest

from aicad import kernel
from aicad.errors import GeometryError
from aicad.workspace import Workspace


def _hole(index: int) -> dict:
    return {"op": "cut", "shape": "cylinder", "radius": 1, "height": 20, "center": [-20 + 4 * index, 0, 0]}


_PLATE = [{"op": "add", "shape": "box", "size": [60, 20, 4]}] + [_hole(index) for index in range(8)]


@pytest.fixture
def counted(monkeypatch):
    """How many ops and sketch steps were built."""
    built = {"ops": 0, "sketch": 0}
    real_op = kernel._apply_model_op
    real_step = kernel._apply_sketch_step

    def op(*args, **kwargs):
        built["ops"] += 1
        return real_op(*args, **kwargs)

    def step(*args, **kwargs):
        built["sketch"] += 1
        return real_step(*args, **kwargs)

    monkeypatch.setattr(kernel, "_apply_model_op", op)
    monkeypatch.setattr(kernel, "_apply_sketch_step", step)
    return built


def _solid(workspace: Workspace, name: str):
    part = workspace.document._parts[name]
    return round(float(part.shape.impl.volume), 6), len(part.shape.impl.faces())


def _fresh(ops: list[dict]):
    shape = kernel.make_from_ops(ops)
    return round(float(shape.impl.volume), 6), len(shape.impl.faces())


def test_a_new_step_builds_only_that_step(tmp_path, counted):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "plate", "ops": _PLATE}, source="agent")
    assert counted["ops"] == len(_PLATE)
    counted["ops"] = 0
    workspace.execute("add_step", {"name": "plate", "step": _hole(8)}, source="agent")
    assert counted["ops"] == 1
    counted["ops"] = 0
    assert _solid(workspace, "plate") == _fresh([*_PLATE, _hole(8)])
    features = workspace.document._parts["plate"].shape.features
    assert features == kernel.make_from_ops([*_PLATE, _hole(8)]).features


def test_a_changed_size_builds_from_its_own_step(tmp_path, counted):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "plate", "ops": _PLATE}, source="agent")
    counted["ops"] = 0
    workspace.execute("set_parameter", {"name": "plate", "parameter": "op/6/radius", "value": 1.5}, source="ui")
    # Ops 6, 7, and 8 of nine.
    assert counted["ops"] == 3
    edited = [dict(op) for op in _PLATE]
    edited[6]["radius"] = 1.5
    counted["ops"] = 0
    assert _solid(workspace, "plate") == _fresh(edited)


def test_a_fillet_does_not_build_the_shapes_again(tmp_path, counted):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "plate", "ops": _PLATE}, source="agent")
    counted["ops"] = 0
    edges = workspace.execute("describe_edges", {"name": "plate"}, source="agent")["edges"]
    ring = [edge["ref"] for edge in edges if edge["kind"] == "circle"][:1]
    workspace.execute("add_fillet", {"name": "plate", "radius": 0.3, "edges": ring}, source="agent")
    workspace.execute("set_parameter", {"name": "plate", "parameter": "edge/0/radius", "value": 0.4}, source="ui")
    assert counted["ops"] == 0


def test_an_opened_project_edits_without_building_again(tmp_path, counted):
    Workspace(tmp_path / "project").execute("create_part_from_ops", {"name": "plate", "ops": _PLATE}, source="agent")
    counted["ops"] = 0
    reopened = Workspace(tmp_path / "project")
    assert counted["ops"] == len(_PLATE)
    counted["ops"] = 0
    reopened.execute("add_step", {"name": "plate", "step": _hole(8)}, source="agent")
    assert counted["ops"] == 1


def test_a_failed_step_is_not_kept(tmp_path):
    builds = kernel.OpsBuilds()
    bad = {"op": "cut", "shape": "cylinder", "radius": 50, "height": 50}
    shape, errors = kernel.prefix_from_ops([*_PLATE, bad], builds=builds)
    assert "op/9" in errors and "removed the entire part" in errors["op/9"]
    assert len(builds.start([*_PLATE, bad])) == len(_PLATE)
    with pytest.raises(GeometryError, match="removed the entire part"):
        kernel.make_from_ops([*_PLATE, bad], builds=builds)


def test_only_the_last_few_lists_are_kept():
    builds = kernel.OpsBuilds(lists=2)
    first = [{"op": "add", "shape": "box", "size": [10, 10, size]} for size in (1, 2, 3)]
    for ops in first:
        kernel.make_from_ops([ops], builds=builds)
    assert builds.start([first[0]]) == []
    assert len(builds.start([first[2]])) == 1
    # A list that grew out of a kept one takes its place.
    kernel.make_from_ops([first[2], _hole(0)], builds=builds)
    assert len(builds.start([first[2], _hole(0)])) == 2
    assert len(builds.start([first[1]])) == 1


def test_a_sketch_step_builds_only_that_step(tmp_path, monkeypatch, counted):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    corners = [[0, 0], [40, 0], [40, 20], [0, 20]]
    entities = [{"id": f"p{index}", "type": "point", "at": at} for index, at in enumerate(corners)]
    entities.append({"id": "c", "type": "point", "at": [20, 10]})
    entities.append({"id": "bore", "type": "circle", "center": "c", "radius": 3})
    workspace.execute("create_sketch", {"name": "plate", "spec": {"plane": "xy", "entities": entities, "constraints": []}})
    slab = {"op": "extrude", "points": ["p0", "p1", "p2", "p3"], "height": 5, "name": "slab"}
    workspace.execute("create_part_from_sketch", {"name": "plate", "sketch": "plate", "steps": [slab]})
    counted["sketch"] = 0
    workspace.execute(
        "add_step", {"name": "plate", "step": {"op": "cut", "circle": "bore", "height": 14, "name": "bore"}}, source="agent"
    )
    assert counted["sketch"] == 1
    assert float(workspace.document._parts["plate"].shape.impl.volume) == pytest.approx(40 * 20 * 5 - 3.14159265 * 9 * 5, rel=1e-4)
    # A new sketch size makes a new solid: nothing built from the old one is used.
    counted["sketch"] = 0
    workspace.execute(
        "update_sketch",
        {"name": "plate", "spec": {"plane": "xy", "entities": [*entities[:5], {**entities[5], "radius": 4}], "constraints": []}},
        source="agent",
    )
    assert counted["sketch"] == 2
    assert float(workspace.document._parts["plate"].shape.impl.volume) == pytest.approx(40 * 20 * 5 - 3.14159265 * 16 * 5, rel=1e-4)
