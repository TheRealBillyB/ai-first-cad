"""7075, titanium, magnesium, and Dyneema are in the library, and PLA and PETG have an across-layer strength.

The forearm and servo designs worked their masses and safety factors by hand
because these materials were missing, and every printed part's safety factor
was an in-plane number with a note that across layers it is weaker.
"""

from __future__ import annotations

import pytest

from aicad import fea
from aicad.errors import GeometryError
from aicad.workspace import Workspace

_AVAILABLE, _REASON = fea.fea_available()
_BEAM = [{"op": "add", "shape": "box", "size": [60, 10, 10], "center": [30, 0, 0]}]


@pytest.mark.parametrize(
    ("name", "key", "strength", "density"),
    [
        ("7075", "aluminum_7075", 503.0, 2.81),
        ("7075-T6", "aluminum_7075", 503.0, 2.81),
        ("titanium", "titanium_6al4v", 880.0, 4.43),
        ("Ti-6Al-4V", "titanium_6al4v", 880.0, 4.43),
        ("magnesium", "magnesium_az31b", 220.0, 1.77),
        ("AZ31B", "magnesium_az31b", 220.0, 1.77),
        ("dyneema", "dyneema", 3400.0, 0.975),
        ("UHMWPE", "dyneema", 3400.0, 0.975),
    ],
)
def test_the_new_materials_are_found_by_their_usual_names(name, key, strength, density):
    material = fea.lookup_material(name)
    assert material["id"] == key
    assert material["strength_mpa"] == strength
    assert material["density_g_cm3"] == density
    assert material["note"]
    listed = {item["id"] for item in fea.list_materials()["materials"]}
    assert key in listed


def test_pla_and_petg_have_a_weaker_strength_across_layers():
    for name in ("pla", "petg"):
        material = fea.lookup_material(name)
        assert material["printed"] is True
        assert 0 < material["across_layer_strength_mpa"] < material["strength_mpa"]


def test_dyneema_is_a_line_not_a_solid(tmp_path):
    with pytest.raises(GeometryError, match="line material"):
        fea.solid_material("dyneema")
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "beam", "ops": _BEAM}, source="agent")
    setup = {
        "material": "dyneema",
        "fixes": [{"select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        "loads": [{"type": "force", "vector": [0, 0, -10], "select": {"kind": "plane", "normal": [1, 0, 0]}}],
    }
    with pytest.raises(GeometryError, match="line material"):
        workspace.execute("solve_mechanics", {"name": "beam", "setup": setup}, source="agent")


def test_a_tendon_can_be_dyneema():
    from aicad.tendon import _line_material

    material = _line_material({"material": "dyneema"}, "flexor")
    assert material["youngs_modulus_mpa"] == 109000.0


def test_a_titanium_part_weighs_what_titanium_weighs(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "beam", "ops": _BEAM}, source="agent")
    result = workspace.execute("measure_part", {"name": "beam", "material": "titanium"}, source="agent")
    grams = next(value for key, value in result.items() if "mass" in key and isinstance(value, (int, float)))
    assert grams == pytest.approx(60 * 10 * 10 / 1000 * 4.43, rel=1e-3)


@pytest.mark.skipif(not _AVAILABLE, reason=_REASON)
def test_a_pla_part_gets_a_safety_factor_across_its_layers(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "beam", "ops": _BEAM}, source="agent")
    setup = {
        "material": "pla",
        "mesh_size_mm": 4.0,
        "target_safety_factor": 2.0,
        "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        "loads": [{"name": "tip", "type": "force", "vector": [0, 0, -60], "select": {"kind": "plane", "normal": [1, 0, 0]}}],
    }
    result = workspace.execute("solve_mechanics", {"name": "beam", "setup": setup}, source="agent")
    in_plane = result["min_safety_factor"]
    across = result["min_safety_factor_across_layers"]
    assert across == pytest.approx(in_plane * 28.0 / 50.0, rel=1e-3)
    assert result["material"]["across_layer_strength_mpa"] == 28.0
    if across < 2.0:
        assert any("Across the print layers" in warning for warning in result["warnings"])
    steel = workspace.execute("solve_mechanics", {"name": "beam", "setup": {**setup, "material": "steel"}}, source="agent")
    assert "min_safety_factor_across_layers" not in steel
