"""A small motor-mount bracket.

This file is a build123d script: `aicad run` executes it and stores `part`.
Running it directly also renders the standard views into `examples/renders`
and writes `examples/motor_mount_report.json`.

    python examples/motor_mount.py

The bracket is a 72 x 52 x 4 mm plate with a boss, a 16 mm bore, four motor
screws, four frame holes, a cable slot on the +Y edge, and two ribs.
Dimensions are millimeters.
"""

import math

from build123d import Box, Cylinder, Pos

base_l, base_w, base_t = 72.0, 52.0, 4.0
boss_d, boss_h = 36.0, 8.0
bore_d = 16.0
bolt_circle = 24.0
motor_bolt_d = 3.2
frame_hole_d = 3.4
frame_inset = 6.0

base = Pos(0, 0, base_t / 2) * Box(base_l, base_w, base_t)
boss = Pos(0, 0, base_t + boss_h / 2) * Cylinder(boss_d / 2, boss_h)
bracket = base + boss

span = base_t + boss_h + 2
bracket -= Pos(0, 0, span / 2) * Cylinder(bore_d / 2, span)

for i in range(4):
    angle = math.radians(45 + 90 * i)
    x = (bolt_circle / 2) * math.cos(angle)
    y = (bolt_circle / 2) * math.sin(angle)
    bracket -= Pos(x, y, span / 2) * Cylinder(motor_bolt_d / 2, span)

for sx in (-1, 1):
    for sy in (-1, 1):
        x = sx * (base_l / 2 - frame_inset)
        y = sy * (base_w / 2 - frame_inset)
        bracket -= Pos(x, y, base_t / 2) * Cylinder(frame_hole_d / 2, base_t + 2)

# Cable slot opening through the +Y edge of the base.
bracket -= Pos(0, base_w / 2, base_t / 2) * Box(10, 8, base_t + 2)

# Ribs on the base, under the boss, clear of the bolt holes.
for sx in (-1, 1):
    bracket += Pos(sx * 16, 0, base_t + 3) * Box(4, 28, 6)

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

    with tempfile.TemporaryDirectory() as workspace:
        document = Document(workspace)
        document.create_part_from_script("motor_mount", Path(__file__).read_text(encoding="utf-8"))
        document.create_part_from_script("shaft", shaft)
        report = document.inspect("motor_mount", min_wall_mm=1.2)
        clearance = document.check_clearance("motor_mount", "shaft", min_clearance_mm=0.1)
        for view in report.views:
            shutil.copy(view.path, renders / Path(view.path).name)

    payload = report.to_dict()
    payload["views"] = [
        {
            **view,
            "path": str((renders / Path(view["path"]).name).relative_to(root.parent)),
        }
        for view in payload["views"]
    ]
    payload["clearance_to_shaft"] = clearance.to_dict()
    (root / "motor_mount_report.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "measurement": payload["measurement"],
        "wall": payload["wall"],
        "clearance_to_shaft": payload["clearance_to_shaft"],
    }
    print(json.dumps(summary, indent=2))
