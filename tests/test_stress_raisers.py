"""A strength solve flags sharp inside corners and says when its peak is a mesh number.

The bracket is a shelf bracket: the back is held and the arm's tip is pulled
down, so the inside corner where the arm meets the back is pulled open. With
no fillet that corner is flagged, the peak sits on it, and a finer local mesh
reads more there each time. With a 4 mm fillet nothing is flagged.

A printed beam bent along its length is pulled along X. Printed standing on
its end, that pull runs across the layers; printed lying down, it does not.
"""

import pytest

from aicad import kernel
from aicad.fea import fea_available, solve_part
from aicad.stress_raisers import inside_corners

_AVAILABLE, _REASON = fea_available()
needs_solver = pytest.mark.skipif(not _AVAILABLE, reason=_REASON)

_BRACKET = [
    {"op": "add", "shape": "box", "size": [6, 10, 40], "center": [3, 5, 20]},
    {"op": "add", "shape": "box", "size": [40, 10, 6], "center": [20, 5, 3]},
]
# The edge where the arm meets the back, on the inside of the L.
_INSIDE_EDGE = "edge/line/6.000,0.000,6.000"


def _bracket(fillet: float | None = None) -> kernel.Shape:
    shape = kernel.make_from_ops(_BRACKET)
    if fillet:
        shape = kernel.apply_edge_ops(shape, [{"op": "fillet", "radius": fillet, "edges": [_INSIDE_EDGE]}])
    return shape


def _shelf_load(mesh: float = 3.0) -> dict:
    return {
        "material": "pla",
        "mesh_size_mm": mesh,
        "target_safety_factor": 2.0,
        "fixes": [{"name": "wall", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        "loads": [
            {
                "name": "shelf",
                "type": "force",
                "vector": [0, 0, -40],
                "select": {"kind": "plane", "normal": [1, 0, 0], "at": [40, 5, 3]},
            }
        ],
    }


def _on_inside_edge(point) -> bool:
    return abs(point[0] - 6.0) < 0.6 and abs(point[2] - 6.0) < 0.6


def test_the_inside_corner_of_an_l_is_found_and_a_filleted_one_is_not():
    sharp = inside_corners(_bracket())
    assert [item["kind"] for item in sharp] == ["sharp"]
    assert _on_inside_edge(sharp[0]["at_mm"])
    assert sharp[0]["length_mm"] == pytest.approx(10.0)
    assert len(sharp[0]["faces"]) == 2
    assert inside_corners(_bracket(fillet=4.0)) == []


def test_a_small_fillet_is_still_a_sharp_corner_and_a_hole_is_not():
    small = inside_corners(_bracket(fillet=0.5))
    assert [item["kind"] for item in small] == ["small_radius"]
    assert small[0]["radius_mm"] == pytest.approx(0.5)
    holed = kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [30, 30, 6], "center": [0, 0, 3]},
            {"op": "cut", "shape": "cylinder", "radius": 0.4, "height": 6, "center": [0, 0, 3]},
        ]
    )
    assert inside_corners(holed) == []


@needs_solver
def test_a_sharp_notch_is_flagged_and_its_peak_is_not_converged():
    result = solve_part(_bracket(), _shelf_load()).to_dict()
    raisers = result["stress_raisers"]
    assert len(raisers) == 1
    assert raisers[0]["kind"] == "sharp"
    assert _on_inside_edge(raisers[0]["at_mm"])
    assert raisers[0]["tension_mpa"] > 0
    convergence = result["convergence"]
    track = convergence["corners"][0]
    assert _on_inside_edge(track["center_mm"])
    peaks = [step["peak_mpa"] for step in track["steps"]]
    # 3 mm, then 1.5, 0.75 and 0.375 mm in a ball at the corner.
    assert [step["local_mesh_mm"] for step in track["steps"]] == [3.0, 1.5, 0.75, 0.375]
    assert peaks[-1] > peaks[0] * 1.3
    assert not track["settled"]
    assert convergence["stopped"] is None
    assert any("not converged" in warning for warning in result["warnings"])
    assert any("Sharp inside corner" in warning or "sharp inside corner" in warning for warning in result["warnings"])
    # The headline is the highest reading, and an unconverged peak is never a pass.
    assert result["max_von_mises_mpa"] == pytest.approx(max(peaks), rel=1e-6)
    assert result["review"]["status"] != "pass"


@needs_solver
def test_the_same_bracket_with_a_fillet_is_not_flagged():
    result = solve_part(_bracket(fillet=4.0), _shelf_load()).to_dict()
    assert result["stress_raisers"] == []
    assert "convergence" not in result
    assert not any("not converged" in warning for warning in result["warnings"])
    assert not any("inside corner" in warning for warning in result["warnings"])


@needs_solver
def test_refine_peak_false_still_warns_without_solving_again():
    result = solve_part(_bracket(), {**_shelf_load(), "refine_peak": False}).to_dict()
    track = result["convergence"]["corners"][0]
    assert len(track["steps"]) == 1
    assert "refine_peak is false" in result["convergence"]["stopped"]
    assert any("not converged" in warning for warning in result["warnings"])


_BEAM = [{"op": "add", "shape": "box", "size": [60, 10, 10], "center": [30, 0, 0]}]


def _beam_load(up) -> dict:
    return {
        "material": "pla",
        "mesh_size_mm": 4.0,
        "target_safety_factor": 2.0,
        "print_up": up,
        "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        "loads": [
            {"name": "tip", "type": "force", "vector": [0, 0, -60], "select": {"kind": "plane", "normal": [1, 0, 0]}}
        ],
    }


@needs_solver
def test_tension_along_the_beam_runs_across_the_layers_only_when_it_stands_on_its_end():
    beam = kernel.make_from_ops(_BEAM)
    standing = solve_part(beam, _beam_load([1, 0, 0])).to_dict()
    lying = solve_part(beam, _beam_load([0, 0, 1])).to_dict()

    # The highest pull is at the held root, where the clamp tilts it off the beam's axis.
    across = standing["across_layers"]
    assert across["main_tension"]["runs_across_layers"] is True
    assert across["main_tension"]["angle_from_build_direction_deg"] < 45
    assert across["max_tension_across_layers_mpa"] > 0.8 * across["main_tension"]["mpa"]
    assert standing["min_safety_factor_across_layers"] == across["safety_factor_across_layers"]
    assert across["safety_factor_across_layers"] < across["safety_factor_in_layers"]
    assert any("pulls the print layers apart" in warning for warning in standing["warnings"])

    flat = lying["across_layers"]
    assert flat["main_tension"]["runs_across_layers"] is False
    assert flat["main_tension"]["angle_from_build_direction_deg"] > 45
    # Lying flat, only the held root's sideways restraint crosses the layers.
    assert flat["max_tension_across_layers_mpa"] < 0.6 * across["max_tension_across_layers_mpa"]
    assert flat["safety_factor_across_layers"] > across["safety_factor_across_layers"]
    assert not any("pulls the print layers apart" in warning for warning in lying["warnings"])
    assert flat["safety_factor_in_layers"] == pytest.approx(across["safety_factor_in_layers"])


@needs_solver
def test_print_up_must_be_a_direction():
    from aicad.errors import GeometryError

    beam = kernel.make_from_ops(_BEAM)
    with pytest.raises(GeometryError, match="print_up"):
        solve_part(beam, _beam_load([1, 0]))
    with pytest.raises(GeometryError, match="non-zero"):
        solve_part(beam, _beam_load([0, 0, 0]))


@needs_solver
def test_a_refined_solve_that_hits_the_limit_says_so(monkeypatch):
    from aicad import fea

    real = fea._execute
    calls = []

    def limited(mesh, fixed_nodes, forces, material, order):
        calls.append(mesh["node_count"])
        if len(calls) == 1:
            return real(mesh, fixed_nodes, forces, material, order)
        return False, None, None, "CalculiX " + fea.helper_limit_stop(fea._CCX_TIMEOUT_S), order

    monkeypatch.setattr(fea, "_execute", limited)
    result = solve_part(_bracket(), _shelf_load()).to_dict()
    convergence = result["convergence"]
    assert len(calls) == 2
    assert len(convergence["corners"][0]["steps"]) == 1
    assert "did not finish" in convergence["stopped"]
    assert "limit" in convergence["stopped"] or "memory" in convergence["stopped"]
    warning = next(item for item in result["warnings"] if "not converged" in item)
    assert "The refinement stopped" in warning
    assert convergence["stopped"] in warning


@needs_solver
def test_the_across_layer_pull_takes_the_refined_corner_readings():
    setup = {**_shelf_load(), "print_up": [1, 0, 0]}
    first_mesh = solve_part(_bracket(), {**setup, "refine_peak": False}).to_dict()
    result = solve_part(_bracket(), setup).to_dict()
    across = result["across_layers"]
    # The pull across the layers climbs with the corner's mesh, and the headline takes it.
    assert across["max_tension_across_layers_mpa"] > 1.3 * first_mesh["across_layers"]["max_tension_across_layers_mpa"]
    assert result["min_safety_factor_across_layers"] == across["safety_factor_across_layers"]
    assert result["min_safety_factor_across_layers"] < result["min_safety_factor"]
    assert "not converged" in across["note"]
    track = result["convergence"]["corners"][0]
    assert across["max_tension_across_layers_mpa"] == max(step["across_layers_mpa"] for step in track["steps"])
