"""Structured requirements scored against the current design."""

import pytest

from aicad.errors import GeometryError
from aicad.fea import fea_available
from aicad.requirements import evaluate, normalize
from aicad.workspace import Workspace

_AVAILABLE, _REASON = fea_available()
needs_solver = pytest.mark.skipif(not _AVAILABLE, reason=_REASON)


def _plate(workspace: Workspace) -> None:
    workspace.execute(
        "create_part_from_ops",
        {
            "name": "plate",
            "ops": [{"op": "add", "shape": "box", "size": [40, 20, 5], "center": [0, 0, 2.5]}],
        },
    )


def test_null_requirements_do_not_clear_the_list(tmp_path):
    workspace = Workspace(tmp_path)
    _plate(workspace)
    workspace.execute(
        "set_requirements",
        {"requirements": [{"id": "light", "kind": "max_mass", "part": "plate", "material": "pla", "max_grams": 10}]},
    )
    with pytest.raises(GeometryError, match="needs requirements"):
        workspace.execute("set_requirements", {"requirements": None})
    assert [item["id"] for item in workspace.execute("get_requirements")["requirements"]] == ["light"]
    with pytest.raises(GeometryError, match="needs name"):
        workspace.execute(
            "create_part_from_ops",
            {"name": None, "ops": [{"op": "add", "shape": "box", "size": [1, 1, 1]}]},
        )
    workspace.execute("set_requirements", {"requirements": []})
    assert workspace.execute("get_requirements")["requirements"] == []


def test_mass_and_build_volume_pass_and_fail(tmp_path):
    workspace = Workspace(tmp_path)
    _plate(workspace)
    # 40×20×5 mm is 4 cm³. PLA at 1.24 g/cm³ is 4.96 g.
    # Sorted size is 5 × 20 × 40. A 10 × 30 × 50 printer fits it only after a turn.
    workspace.execute(
        "set_requirements",
        {
            "requirements": [
                {"id": "light", "kind": "max_mass", "part": "plate", "material": "pla", "max_grams": 10, "title": "Under 10 g"},
                {"id": "heavy", "kind": "max_mass", "part": "plate", "material": "steel", "max_grams": 1, "title": "Steel under 1 g"},
                {"id": "printer", "kind": "fits_build_volume", "part": "plate", "size_mm": [10, 30, 50], "title": "Printer"},
                {"id": "tight", "kind": "fits_build_volume", "part": "plate", "size_mm": [4, 30, 50], "title": "Too short"},
                {"id": "gone", "kind": "max_mass", "part": "missing", "max_grams": 1, "title": "Missing"},
            ]
        },
    )
    checks = {item["id"]: item for item in workspace.execute("check_requirements", {"run": False})["checks"]}
    assert checks["light"]["status"] == "pass"
    assert checks["light"]["measured"] == pytest.approx(4.96, abs=0.02)
    assert checks["heavy"]["status"] == "fail"
    assert checks["printer"]["status"] == "pass"
    assert checks["printer"]["measured"] == [5.0, 20.0, 40.0]
    assert checks["tight"]["status"] == "fail"
    assert checks["gone"]["status"] == "error"
    assert "missing" in checks["gone"]["detail"]


def test_wall_and_safety_factor_stay_unchecked_until_asked(tmp_path):
    workspace = Workspace(tmp_path)
    _plate(workspace)
    workspace.execute(
        "set_requirements",
        {
            "requirements": [
                {"id": "wall", "kind": "min_wall", "part": "plate", "min_mm": 1, "samples": 2},
                {"id": "sf", "kind": "min_safety_factor", "part": "plate", "minimum": 2},
            ]
        },
    )
    waiting = {item["id"]: item["status"] for item in workspace.state()["requirements"]}
    assert waiting == {"wall": "unchecked", "sf": "unchecked"}
    ran = {item["id"]: item for item in workspace.execute("check_requirements", {"run": True})["checks"]}
    assert ran["wall"]["status"] == "pass"
    assert ran["wall"]["measured"] >= 1
    assert ran["sf"]["status"] == "error"
    again = {item["id"]: item["status"] for item in workspace.state()["requirements"]}
    assert again["wall"] == "pass"
    assert again["sf"] == "unchecked"
    workspace.execute(
        "update_part_ops",
        {"name": "plate", "ops": [{"op": "add", "shape": "box", "size": [40, 20, 8], "center": [0, 0, 4]}]},
    )
    stale = {item["id"]: item["status"] for item in workspace.state()["requirements"]}
    assert stale["wall"] == "unchecked"


def test_unknown_kind_and_duplicate_ids_are_rejected():
    with pytest.raises(GeometryError, match="unknown"):
        normalize([{"kind": "pretty", "part": "plate"}])
    with pytest.raises(GeometryError, match="share the id"):
        normalize(
            [
                {"id": "same", "kind": "max_mass", "part": "plate", "max_grams": 1},
                {"id": "same", "kind": "max_mass", "part": "plate", "max_grams": 2},
            ]
        )


def test_requirements_reload_with_the_project(tmp_path):
    first = Workspace(tmp_path)
    _plate(first)
    first.execute(
        "set_requirements",
        {"requirements": [{"id": "light", "kind": "max_mass", "part": "plate", "material": "pla", "max_grams": 10}]},
    )
    second = Workspace(tmp_path)
    checks = second.execute("get_requirements")["checks"]
    assert checks[0]["status"] == "pass"
    assert checks[0]["id"] == "light"


@needs_solver
def test_safety_factor_requirement_uses_the_same_solve(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.execute(
        "create_part_from_ops",
        {"name": "beam", "ops": [{"op": "add", "shape": "box", "size": [20, 4, 4], "center": [10, 0, 0]}]},
    )
    faces = {face["ref"]: face for face in workspace.execute("describe_faces", {"name": "beam"})["faces"]}
    root = next(ref for ref in faces if ref.startswith("plane/x-"))
    tip = next(ref for ref in faces if ref.startswith("plane/x+"))
    workspace.execute(
        "set_requirements",
        {
            "requirements": [
                {
                    "id": "cantilever",
                    "title": "Tip load",
                    "kind": "min_safety_factor",
                    "part": "beam",
                    "minimum": 0.5,
                    "setup": {
                        "material": "steel",
                        "mesh_size_mm": 1.5,
                        "fixes": [{"name": "root", "select": {"ref": root}}],
                        "loads": [
                            {
                                "name": "tip",
                                "type": "force",
                                "vector": [0, 0, -100],
                                "select": {"ref": tip},
                            }
                        ],
                    },
                }
            ]
        },
    )
    check = workspace.execute("check_requirements", {"run": True})["checks"][0]
    assert check["status"] == "pass"
    assert check["measured"] > 0.5
    assert check["result_kind"] == "rough_estimate"


def test_a_simplified_mesh_cannot_pass_a_safety_requirement():
    from aicad.requirements import safety_requirement_status

    assert safety_requirement_status(3.0, 2.0, "inconclusive") == "inconclusive"
    assert safety_requirement_status(3.0, 2.0, "pass") == "pass"
    assert safety_requirement_status(1.0, 2.0, "pass") == "fail"
    assert safety_requirement_status(None, 2.0, "pass", solved=False) == "fail"
