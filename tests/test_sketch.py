"""Constraint sketches: SolveSpace status, regeneration, and the bracket."""

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest
from PIL import Image

from aicad import Document, GeometryError, Sketch, solve_sketch
from aicad.kernel import solid_from_sketch

ROOT = Path(__file__).resolve().parents[1]


def _load_example():
    path = ROOT / "examples" / "motor_mount.py"
    spec = importlib.util.spec_from_file_location("motor_mount_example", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_EXAMPLE = _load_example()
bracket_sketch = _EXAMPLE.bracket_sketch
bracket_steps = _EXAMPLE.bracket_steps
sketch_report = _EXAMPLE.sketch_report


def _png_stddev(path: Path) -> float:
    image = Image.open(path).convert("RGB")
    histogram = image.histogram()
    count = image.size[0] * image.size[1] * 3
    total = sum(index * count_at for index, count_at in enumerate(histogram))
    mean = total / count
    variance = sum(count_at * (index - mean) ** 2 for index, count_at in enumerate(histogram))
    return (variance / count) ** 0.5


def _rectangle() -> Sketch:
    return (
        Sketch()
        .point("origin", 0, 0)
        .fix("origin")
        .point("sw", -36, -26)
        .point("se", 36, -26)
        .point("ne", 36, 26)
        .point("nw", -36, 26)
        .line("bottom", "sw", "se")
        .line("right", "se", "ne")
        .line("top", "ne", "nw")
        .line("left", "nw", "sw")
        .line("diag", "sw", "ne", construction=True)
        .horizontal("bottom")
        .horizontal("top")
        .vertical("left")
        .vertical("right")
        .distance("sw", "se", 72, name="base_length")
        .distance("sw", "nw", 52, name="base_width")
        .midpoint("origin", "diag")
    )


def test_rectangle_is_fully_constrained():
    report = solve_sketch(_rectangle(), name="plate")
    assert report.status == "fully_constrained"
    assert report.dof == 0
    assert report.free == []
    assert report.conflicts == []
    assert report.geometry["points"]["se"] == {"x": 36.0, "y": -26.0}
    assert report.geometry["points"]["nw"] == {"x": -36.0, "y": 26.0}
    assert report.solver.startswith("SolveSpace")


def test_point_on_a_circle_reports_the_remaining_dof():
    sketch = (
        Sketch()
        .point("origin", 0, 0)
        .fix("origin")
        .circle("bore", "origin", 8)
        .diameter("bore", 16, name="bore")
        .point("free", 8, 0)
        .coincident("free", "bore")
    )
    report = solve_sketch(sketch)
    assert report.status == "under_constrained"
    assert report.dof == 1
    assert [item["entity"] for item in report.free] == ["free"]
    assert report.free[0]["free_dof"] == 1


def test_conflicting_distances_name_both_constraints():
    sketch = (
        Sketch()
        .point("a", 0, 0)
        .fix("a")
        .point("b", 10, 0)
        .distance("a", "b", 10, name="near")
        .distance("a", "b", 12, name="far")
    )
    report = solve_sketch(sketch)
    assert report.status == "over_constrained"
    assert set(report.conflicts) == {"near", "far"}


def test_four_bolt_pattern_lands_on_the_circle():
    sketch = (
        Sketch()
        .point("origin", 0, 0)
        .fix("origin")
        .point("b0", 8, 8)
        .point("b1", -8, 8)
        .point("b2", -8, -8)
        .point("b3", 8, -8)
        .pattern_on_circle("origin", ["b0", "b1", "b2", "b3"], 24, start_angle=45, name="bolt_circle")
    )
    report = solve_sketch(sketch)
    assert report.status == "fully_constrained"
    assert report.dof == 0
    radius = 12.0
    for index, name in enumerate(("b0", "b1", "b2", "b3")):
        angle = math.radians(45 + 90 * index)
        point = report.geometry["points"][name]
        assert point["x"] == pytest.approx(radius * math.cos(angle), abs=1e-4)
        assert point["y"] == pytest.approx(radius * math.sin(angle), abs=1e-4)


def test_tolerance_is_stored_and_labeled():
    sketch = (
        Sketch()
        .point("origin", 0, 0)
        .fix("origin")
        .circle("bore", "origin", 8)
        .diameter("bore", 16, name="bore_diameter", tolerance=0.05)
    )
    report = solve_sketch(sketch)
    assert report.status == "fully_constrained"
    dimension = report.dimensions[0]
    assert dimension["tolerance"] == {"plus": 0.05, "minus": 0.05}
    assert dimension["text"] == "Ø16 ±0.05"
    assert report.geometry["circles"]["bore"]["radius"] == pytest.approx(8)


def test_named_dimension_rebuilds_the_solid(tmp_path):
    document = Document(tmp_path)
    document.create_sketch("plate", _rectangle())
    document.create_part_from_sketch(
        "pad",
        "plate",
        [{"op": "extrude", "points": ["sw", "se", "ne", "nw"], "height": 4, "name": "base"}],
    )
    before = document.measure("pad").volume_mm3
    assert before == pytest.approx(72 * 52 * 4)
    report = document.set_dimension("plate", "base_length", 80)
    assert report.status == "fully_constrained"
    assert report.geometry["points"]["se"]["x"] == pytest.approx(40)
    after = document.measure("pad")
    assert after.volume_mm3 == pytest.approx(80 * 52 * 4)
    assert after.bounding_box.size.x == pytest.approx(80)
    reopened = Document(tmp_path)
    assert reopened.measure("pad").volume_mm3 == pytest.approx(80 * 52 * 4)
    inspected = reopened.inspect("pad")
    assert inspected.sketch["status"] == "fully_constrained"
    assert inspected.sketch["dimensions"][0]["name"] == "base_length"


def test_a_conflicting_edit_keeps_the_previous_solid(tmp_path):
    document = Document(tmp_path)
    document.create_sketch("plate", _rectangle())
    document.create_part_from_sketch(
        "pad",
        "plate",
        [{"op": "extrude", "points": ["sw", "se", "ne", "nw"], "height": 4, "name": "base"}],
    )
    conflict = _rectangle().distance("sw", "se", 10, name="extra")
    with pytest.raises(GeometryError, match="conflict"):
        document.update_sketch("plate", conflict)
    assert document.measure("pad").volume_mm3 == pytest.approx(72 * 52 * 4)
    assert document.get_sketch("plate").status == "fully_constrained"


def test_over_constrained_sketch_is_not_extruded(tmp_path):
    document = Document(tmp_path)
    sketch = (
        Sketch()
        .point("a", 0, 0)
        .fix("a")
        .point("b", 10, 0)
        .distance("a", "b", 10, name="near")
        .distance("a", "b", 12, name="far")
    )
    report = document.create_sketch("broken", sketch)
    assert report.status == "over_constrained"
    with pytest.raises(GeometryError, match="conflict"):
        document.create_part_from_sketch(
            "nope",
            "broken",
            [{"op": "extrude", "points": ["a", "b", "a"], "height": 1}],
        )
    assert document.list_parts() == []


def _ring() -> Sketch:
    return (
        Sketch()
        .point("a", 10, 0)
        .point("b", 20, 0)
        .point("c", 20, 5)
        .point("d", 10, 5)
        .line("bottom", "a", "b")
        .line("side", "b", "c")
        .line("top", "c", "d")
        .line("inner", "d", "a")
        .horizontal("bottom")
        .horizontal("top")
        .vertical("side")
        .vertical("inner")
        .fix("a")
        .distance("a", "b", 10, name="width")
        .distance("b", "c", 5, name="height")
    )


def test_revolve_builds_a_ring():
    report = solve_sketch(_ring())
    assert report.status == "fully_constrained"
    assert report.dof == 0
    shape = solid_from_sketch(
        report.geometry,
        [{"op": "revolve", "points": ["a", "b", "c", "d"], "axis": "y", "name": "ring"}],
    )
    assert shape.impl.volume == pytest.approx(math.pi * (400 - 100) * 5, rel=1e-4)


def test_under_constrained_sketch_still_builds_and_says_so(tmp_path):
    sketch = (
        Sketch()
        .point("origin", 0, 0)
        .fix("origin")
        .point("sw", -10, -5)
        .point("se", 10, -5)
        .point("ne", 10, 5)
        .point("nw", -10, 5)
        .line("bottom", "sw", "se")
        .line("right", "se", "ne")
        .line("top", "ne", "nw")
        .line("left", "nw", "sw")
        .line("diag", "sw", "ne", construction=True)
        .horizontal("bottom")
        .horizontal("top")
        .vertical("left")
        .vertical("right")
        .distance("sw", "se", 20, name="length")
        .midpoint("origin", "diag")
    )
    report = solve_sketch(sketch)
    assert report.status == "under_constrained"
    assert report.dof >= 1
    assert report.free
    document = Document(tmp_path)
    document.create_sketch("open", sketch)
    document.create_part_from_sketch(
        "pad",
        "open",
        [{"op": "extrude", "points": ["sw", "se", "ne", "nw"], "height": 2, "name": "base"}],
    )
    inspected = document.inspect("pad")
    assert inspected.sketch["status"] == "under_constrained"
    assert inspected.sketch["warning"]
    assert inspected.measurement.volume_mm3 > 0


def test_sketch_png_is_not_blank(tmp_path):
    document = Document(tmp_path)
    report = document.create_sketch("plate", _rectangle())
    path = Path(report.view.path)
    assert path.is_file()
    assert _png_stddev(path) > 8
    assert report.view.name == "sketch"


def test_bracket_sketch_is_fully_constrained_and_inset():
    assert sketch_report.status == "fully_constrained"
    assert sketch_report.dof == 0
    points = sketch_report.geometry["points"]
    assert points["hsw"] == {"x": -30.0, "y": -20.0}
    assert points["hse"] == {"x": 30.0, "y": -20.0}
    assert points["hne"] == {"x": 30.0, "y": 20.0}
    assert points["hnw"] == {"x": -30.0, "y": 20.0}
    radius = 12.0
    for index, name in enumerate(("b0", "b1", "b2", "b3")):
        angle = math.radians(45 + 90 * index)
        assert points[name]["x"] == pytest.approx(radius * math.cos(angle), abs=1e-4)
        assert points[name]["y"] == pytest.approx(radius * math.sin(angle), abs=1e-4)
    bore = next(item for item in sketch_report.dimensions if item["name"] == "bore_diameter")
    assert bore["tolerance"] == {"plus": 0.05, "minus": 0.05}
    fresh = solve_sketch(bracket_sketch())
    assert fresh.status == "fully_constrained"


def test_bracket_solid_matches_the_old_envelope(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script(
        "mount", (ROOT / "examples" / "motor_mount.py").read_text(encoding="utf-8")
    )
    measured = document.measure("mount")
    assert measured.watertight is True
    assert measured.bounding_box.size.x == pytest.approx(72)
    assert measured.bounding_box.size.y == pytest.approx(52)
    assert measured.bounding_box.size.z == pytest.approx(12)
    report = document.feature_report("mount")
    assert report.redundant_features == []
    assert any(step.name.startswith("gusset_") for step in report.steps)

    document.create_sketch("bracket_base", bracket_sketch())
    document.create_part_from_sketch("from_sketch", "bracket_base", bracket_steps())
    assert document.measure("from_sketch").volume_mm3 == pytest.approx(measured.volume_mm3, rel=1e-6)
    document.set_dimension("bracket_base", "bolt_circle", 28)
    moved = document.get_sketch("bracket_base").geometry["points"]["b0"]
    assert math.hypot(moved["x"], moved["y"]) == pytest.approx(14, abs=1e-4)
    assert document.measure("from_sketch").watertight is True


def test_cli_dimension_changes_the_part(tmp_path):
    import subprocess

    spec = tmp_path / "plate.json"
    spec.write_text(json.dumps(_rectangle().to_spec()), encoding="utf-8")
    steps = tmp_path / "steps.json"
    steps.write_text(
        json.dumps([{"op": "extrude", "points": ["sw", "se", "ne", "nw"], "height": 4, "name": "base"}]),
        encoding="utf-8",
    )
    workspace = tmp_path / "ws"
    base = [sys.executable, "-m", "aicad"]
    workspace_arg = ["--workspace", str(workspace)]
    created = subprocess.run(
        [*base, "sketch", "plate", "--file", str(spec), *workspace_arg],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout)["status"] == "fully_constrained"
    built = subprocess.run(
        [*base, "sketch-part", "pad", "plate", "--steps", str(steps), *workspace_arg],
        check=False,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    changed = subprocess.run(
        [*base, "dimension", "plate", "base_length", "80", *workspace_arg],
        check=False,
        capture_output=True,
        text=True,
    )
    assert changed.returncode == 0, changed.stderr
    payload = json.loads(changed.stdout)
    assert payload["rebuilt_parts"][0]["volume_mm3"] == pytest.approx(80 * 52 * 4)
