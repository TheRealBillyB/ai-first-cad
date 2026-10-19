"""A motor-mount bracket whose base and bolt pattern are a constrained sketch.

The plate outline, the bore, the boss, four motor screws on a bolt circle, and
four frame holes are SolveSpace constraints, not coordinates typed by hand.
Gussets and the cable slot stay 3D recipes. Changing a named dimension and
re-solving rebuilds a part made from this sketch.

This file is still a build123d script: `aicad run` executes it and stores `part`.
Running it directly also renders the views, the section, and the solved sketch.

    python examples/motor_mount.py

Dimensions are millimeters.
"""

from aicad.errors import GeometryError
from aicad.kernel import solid_from_sketch
from aicad.sketch import Sketch, solve_sketch

try:
    feature
except NameError:
    from aicad import FeatureLog

    feature = FeatureLog()


def bracket_sketch() -> Sketch:
    """72 x 52 plate, Ø16 bore, Ø36 boss, four M3-ish holes on a Ø24 circle.

    The rectangle is drawn counterclockwise, so an inset is a negative
    point-to-line distance (positive would be outside the edge). The bolt
    holes are a pattern: evenly spaced on a circle, starting at 45 degrees.
    """
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
        .circle("bore", "origin", 8)
        .diameter("bore", 16, name="bore_diameter", tolerance=0.05)
        .circle("boss", "origin", 18)
        .diameter("boss", 36, name="boss_diameter")
        .point("b0", 8, 8)
        .point("b1", -8, 8)
        .point("b2", -8, -8)
        .point("b3", 8, -8)
        .pattern_on_circle("origin", ["b0", "b1", "b2", "b3"], 24, start_angle=45, name="bolt_circle")
        .circle("c0", "b0", 1.6)
        .circle("c1", "b1", 1.6)
        .circle("c2", "b2", 1.6)
        .circle("c3", "b3", 1.6)
        .diameter("c0", 3.2, name="bolt_hole")
        .equal("c0", "c1")
        .equal("c0", "c2")
        .equal("c0", "c3")
        .point("hsw", -30, -20)
        .point("hse", 30, -20)
        .point("hne", 30, 20)
        .point("hnw", -30, 20)
        .distance("hsw", "left", -6, name="frame_inset")
        .distance("hsw", "bottom", -6, name="frame_inset")
        .distance("hse", "right", -6, name="frame_inset")
        .distance("hse", "bottom", -6, name="frame_inset")
        .distance("hne", "right", -6, name="frame_inset")
        .distance("hne", "top", -6, name="frame_inset")
        .distance("hnw", "left", -6, name="frame_inset")
        .distance("hnw", "top", -6, name="frame_inset")
        .circle("f0", "hsw", 1.7)
        .circle("f1", "hse", 1.7)
        .circle("f2", "hne", 1.7)
        .circle("f3", "hnw", 1.7)
        .diameter("f0", 3.4, name="frame_hole")
        .equal("f0", "f1")
        .equal("f0", "f2")
        .equal("f0", "f3")
    )


def bracket_steps() -> list[dict]:
    """Extrude the solved profile. Gussets and the cable slot are 3D recipes."""
    plate_t = 4.0
    boss_h = 8.0
    through = plate_t + boss_h + 2.0
    return [
        {"op": "extrude", "points": ["sw", "se", "ne", "nw"], "height": plate_t, "name": "base"},
        {"op": "extrude", "circle": "boss", "height": boss_h, "z": plate_t, "name": "boss"},
        {
            "op": "gusset",
            "angle": 0,
            "outer": 30,
            "plate_thickness": plate_t,
            "boss_height": boss_h,
            "name": "gusset_0",
        },
        {
            "op": "gusset",
            "angle": 180,
            "outer": 30,
            "plate_thickness": plate_t,
            "boss_height": boss_h,
            "name": "gusset_180",
        },
        {
            "op": "gusset",
            "angle": 270,
            "outer": 25,
            "plate_thickness": plate_t,
            "boss_height": boss_h,
            "name": "gusset_270",
        },
        {"op": "cut", "circle": "bore", "height": through, "name": "bore"},
        {"op": "cut", "circle": "c0", "height": through, "name": "motor_bolt_0"},
        {"op": "cut", "circle": "c1", "height": through, "name": "motor_bolt_1"},
        {"op": "cut", "circle": "c2", "height": through, "name": "motor_bolt_2"},
        {"op": "cut", "circle": "c3", "height": through, "name": "motor_bolt_3"},
        {"op": "cut", "circle": "f0", "height": plate_t + 2, "z": -1, "name": "frame_hole_0"},
        {"op": "cut", "circle": "f1", "height": plate_t + 2, "z": -1, "name": "frame_hole_1"},
        {"op": "cut", "circle": "f2", "height": plate_t + 2, "z": -1, "name": "frame_hole_2"},
        {"op": "cut", "circle": "f3", "height": plate_t + 2, "z": -1, "name": "frame_hole_3"},
        {"op": "cut_box", "center": [0, 26, plate_t / 2], "size": [10, 8, plate_t + 2], "name": "cable_slot"},
    ]


sketch_report = solve_sketch(bracket_sketch(), name="bracket_base")
if sketch_report.status != "fully_constrained":
    raise GeometryError(sketch_report.note)
part = solid_from_sketch(sketch_report.geometry, bracket_steps(), feature=feature).impl


if __name__ == "__main__":
    import json
    import shutil
    import tempfile
    from pathlib import Path

    from aicad import Document
    from aicad.render import render_sketch

    root = Path(__file__).resolve().parent
    renders = root / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    shaft = (root / "shaft.py").read_text(encoding="utf-8")
    buried = (root / "motor_mount_buried_ribs.py").read_text(encoding="utf-8")
    sketch_view = render_sketch(sketch_report, renders / "motor_mount_sketch.png")

    with tempfile.TemporaryDirectory() as workspace:
        document = Document(workspace)
        document.create_part_from_script("motor_mount", Path(__file__).read_text(encoding="utf-8"))
        document.create_part_from_script("buried_ribs", buried)
        document.create_part_from_script("shaft", shaft)
        document.create_sketch("bracket_base", bracket_sketch())
        document.create_part_from_sketch("parametric", "bracket_base", bracket_steps())
        length_72 = document.measure("parametric").volume_mm3
        document.set_dimension("bracket_base", "base_length", 90)
        length_90 = document.measure("parametric").volume_mm3
        document.set_dimension("bracket_base", "bolt_circle", 28)
        moved = document.get_sketch("bracket_base")
        report = document.inspect("motor_mount", min_wall_mm=1.2)
        clearance = document.check_clearance("motor_mount", "shaft", min_clearance_mm=0.1)
        section = document.section("motor_mount", {"axis": "y", "offset": 0})
        buried_center = document.section("buried_ribs", {"axis": "y", "offset": 0})
        buried_end = document.section("buried_ribs", {"axis": "y", "offset": 13})
        after = document.feature_report("motor_mount")
        before = document.feature_report("buried_ribs")
        for view in report.views:
            shutil.copy(view.path, renders / Path(view.path).name)
        shutil.copy(section.view.path, renders / "motor_mount_section_y0.png")
        shutil.copy(buried_center.view.path, renders / "buried_ribs_section_y0.png")
        shutil.copy(buried_end.view.path, renders / "buried_ribs_section_y13.png")

    def _view(path: Path) -> str:
        return str(path.relative_to(root.parent))

    payload = report.to_dict()
    payload["views"] = [
        {**view, "path": _view(renders / Path(view["path"]).name)} for view in payload["views"]
    ]
    payload["sketch"] = {
        "status": sketch_report.status,
        "dof": sketch_report.dof,
        "dimensions": sketch_report.dimensions,
        "path": _view(renders / "motor_mount_sketch.png"),
        "note": sketch_report.note,
    }
    payload["parametric"] = {
        "volume_at_base_length_72": length_72,
        "volume_at_base_length_90": length_90,
        "bolt_circle_28_points": {
            name: point
            for name, point in moved.geometry["points"].items()
            if name.startswith("b")
        },
    }
    payload["clearance_to_shaft"] = clearance.to_dict()
    payload["section_y0"] = {
        "area_mm2": section.area_mm2,
        "min_thickness_mm": section.min_thickness_mm,
        "width_mm": section.width_mm,
        "height_mm": section.height_mm,
        "path": _view(renders / "motor_mount_section_y0.png"),
        "note": section.note,
    }
    comparison = {
        "before_buried_ribs": before.to_dict(),
        "after_radial_gussets": after.to_dict(),
        "sections": {
            "new_gussets_y0": _view(renders / "motor_mount_section_y0.png"),
            "old_ribs_through_boss_y0": _view(renders / "buried_ribs_section_y0.png"),
            "old_ribs_at_the_exposed_end_y13": _view(renders / "buried_ribs_section_y13.png"),
        },
        "sketch": _view(renders / "motor_mount_sketch.png"),
        "reading": (
            "rib_neg_x and rib_pos_x are flagged: more than half of each rib was already "
            "inside the boss. The gussets are not flagged: most of each gusset is new "
            "material bracing the boss to the plate. The y=0 section of the old ribs cuts "
            "through the boss and does not show a rib, because the rib is buried there. "
            "The y=13 section shows the rib end sticking out. The new y=0 section shows "
            "the gussets along X. The plate, bore, boss, and bolt pattern come from a "
            "fully constrained sketch."
        ),
    }
    (root / "feature_report.json").write_text(
        json.dumps(comparison, indent=2) + "\n", encoding="utf-8"
    )
    payload["feature_comparison"] = {
        "path": "examples/feature_report.json",
        "before_redundant": before.redundant_features,
        "after_redundant": after.redundant_features,
    }
    (root / "motor_mount_report.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "measurement": payload["measurement"],
        "wall": {
            "estimated_min_mm": payload["wall"]["estimated_min_mm"],
            "method": payload["wall"]["method"],
            "passed": payload["wall"]["passed"],
        },
        "clearance_to_shaft": payload["clearance_to_shaft"]["min_distance_mm"],
        "before_redundant": before.redundant_features,
        "after_redundant": after.redundant_features,
        "gusset_buried": {
            step["name"]: step["buried_fraction"]
            for step in after.to_dict()["steps"]
            if step["name"].startswith("gusset")
        },
        "rib_buried": {
            step["name"]: step["buried_fraction"]
            for step in before.to_dict()["steps"]
            if step["name"].startswith("rib")
        },
        "section_area_mm2": section.area_mm2,
        "sketch_status": sketch_report.status,
        "sketch_dof": sketch_report.dof,
        "volume_length_72": length_72,
        "volume_length_90": length_90,
        "sketch_png": str(sketch_view.path),
    }
    print(json.dumps(summary, indent=2))
