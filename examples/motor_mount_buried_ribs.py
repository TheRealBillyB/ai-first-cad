"""Rejected motor-mount ribs, kept so the feature report can show why.

The ribs are 4 mm thick boxes centered at x = ±16 mm, so they span x = 14..18 mm.
The boss radius is 18 mm, so most of each rib is already inside the boss and only
the ends poke out. `feature.add` records that. The replacement is
`motor_mount.py`, whose radial gussets are mostly new material.

This file is a build123d script. `aicad run` stores `part`.
"""

import math

from build123d import Box, Cylinder, Pos

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

bracket = feature.add("base", Pos(0, 0, base_t / 2) * Box(base_l, base_w, base_t))
bracket = feature.add("boss", Pos(0, 0, base_t + boss_h / 2) * Cylinder(boss_d / 2, boss_h))

# These ribs sit inside the boss. Only the ends past the circular wall are new material.
for sign, name in ((-1, "rib_neg_x"), (1, "rib_pos_x")):
    bracket = feature.add(name, Pos(sign * 16, 0, base_t + 3) * Box(4, 28, 6))

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
