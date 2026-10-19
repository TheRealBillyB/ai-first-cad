"""A motor-mount bracket braced by radial gussets.

This file is a build123d script: `aicad run` executes it and stores `part`.
Running it directly also renders the standard views and a section, and writes
the before/after feature report.

    python examples/motor_mount.py

The plate is 72 x 52 x 4 mm with a boss, a 16 mm bore, four motor screws, four
frame holes, and a cable slot on the +Y edge. Three gussets brace the boss to
the plate, along +X, -X, and -Y, clear of the bolt circle and of the cable
slot. They replace the earlier ribs, which were mostly buried inside the boss
(see `motor_mount_buried_ribs.py`).

`feature.add` and `feature.cut` are optional. A script that never calls them
still works; this one uses them so the contribution of each step is recorded.
Dimensions are millimeters.
"""

import math

from build123d import Box, Cylinder, Plane, Polygon, Pos, Rot, extrude

try:
    feature
except NameError:
    from aicad import FeatureLog

    feature = FeatureLog()

base_l, base_w, base_t = 72.0, 52.0, 4.0
boss_d, boss_h = 36.0, 8.0
bore_d = 16.0
bolt_circle = 24.0
motor_bolt_d = 3.2
frame_hole_d = 3.4
frame_inset = 6.0


def radial_gusset(angle_deg: float, outer_r: float):
    """A radial fin from the boss out onto the plate, with a blunt toe.

    A pure triangle that dies inside the cylindrical boss leaves a non-manifold
    seam, and a knife-edge toe is not a wall. The gusset is a short block that
    bites through the boss wall, fused to a trapezoid that stays outside the
    cylinder and ends 2.4 mm tall. The +Y side is left open for the cable slot.
    """
    thick = 2.6
    bite = Pos(17.2, 0, base_t + boss_h / 2) * Box(3.2, thick, boss_h)
    fin = extrude(
        Plane.XZ
        * Polygon(
            (17.4, base_t),
            (17.4, base_t + boss_h),
            (outer_r, base_t + 2.4),
            (outer_r, base_t),
        ),
        amount=thick / 2,
        both=True,
    )
    return Rot(0, 0, angle_deg) * (bite + fin)


bracket = feature.add("base", Pos(0, 0, base_t / 2) * Box(base_l, base_w, base_t))
bracket = feature.add("boss", Pos(0, 0, base_t + boss_h / 2) * Cylinder(boss_d / 2, boss_h))
# No gusset on +Y: that is where the cable slot leaves the plate.
for angle, outer_r in ((0, 30.0), (180, 30.0), (270, 25.0)):
    bracket = feature.add(f"gusset_{angle}", radial_gusset(angle, outer_r))

span = base_t + boss_h + 2
bracket = feature.cut("bore", Pos(0, 0, span / 2) * Cylinder(bore_d / 2, span))

for i in range(4):
    angle = math.radians(45 + 90 * i)
    x = (bolt_circle / 2) * math.cos(angle)
    y = (bolt_circle / 2) * math.sin(angle)
    bracket = feature.cut(
        f"motor_bolt_{i}", Pos(x, y, span / 2) * Cylinder(motor_bolt_d / 2, span)
    )

for index, (sx, sy) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1))):
    x = sx * (base_l / 2 - frame_inset)
    y = sy * (base_w / 2 - frame_inset)
    bracket = feature.cut(
        f"frame_hole_{index}", Pos(x, y, base_t / 2) * Cylinder(frame_hole_d / 2, base_t + 2)
    )

bracket = feature.cut("cable_slot", Pos(0, base_w / 2, base_t / 2) * Box(10, 8, base_t + 2))
part = bracket


if __name__ == "__main__":
    import json
    import shutil
    import tempfile
    from pathlib import Path

    from aicad import Document

    root = Path(__file__).resolve().parent
    renders = root / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    shaft = (root / "shaft.py").read_text(encoding="utf-8")
    buried = (root / "motor_mount_buried_ribs.py").read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as workspace:
        document = Document(workspace)
        document.create_part_from_script("motor_mount", Path(__file__).read_text(encoding="utf-8"))
        document.create_part_from_script("buried_ribs", buried)
        document.create_part_from_script("shaft", shaft)
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
        "reading": (
            "rib_neg_x and rib_pos_x are flagged: more than half of each rib was already "
            "inside the boss. The gussets are not flagged: most of each gusset is new "
            "material bracing the boss to the plate. The y=0 section of the old ribs cuts "
            "through the boss and does not show a rib, because the rib is buried there. "
            "The y=13 section shows the rib end sticking out. The new y=0 section shows "
            "the gussets along X."
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
    }
    print(json.dumps(summary, indent=2))
