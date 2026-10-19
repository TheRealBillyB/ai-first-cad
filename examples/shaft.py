"""A shaft that should clear the motor-mount bore by 0.2 mm radially.

The mount's bore is 16 mm. This shaft is 15.6 mm in diameter and long enough
to pass through the boss. `check_clearance` between the two parts should
report a gap of about 0.2 mm and no interference.
"""

from build123d import Cylinder, Pos

part = Pos(0, 0, 6) * Cylinder(15.6 / 2, 20)
