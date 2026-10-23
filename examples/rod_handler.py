"""A drill-rod handling arm for a diamond-core rig.

The older rod arm is a few boxes on a hinge. This one is the mechanism a
handler actually is: a bolted slew base, plate-and-strap links with lightening
holes and gussets, pins and bushings, and a hydraulic cylinder on the
shoulder, the elbow, and the jaw. The jaw closes on NQ and HQ rod (about 70
to 89 mm). A cylinder relation keeps each rod eye on its clevis. The joint
tree stays a tree; the relation is what a closed hydraulic loop would have
been.

    python examples/rod_handler.py

Pictures and the check report land in examples/renders. The session is
examples/rod_handler_workspace.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

from aicad import assembly as assembly_mod
from aicad.document import Document
from aicad.kinematics import _cylinder, _matvec, _rotation
from aicad.render import render_motion_frames

ROOT = Path(__file__).resolve().parent
RENDERS = ROOT / "renders"
WORKSPACE = ROOT / "rod_handler_workspace"
ROBOT = ROOT / "rod_handler_robot"

# Turret frame, millimetres. Z is up, X reaches forward, Y is across the rig.
SHOULDER_PIN = (48.0, 0.0, 200.0)
SHOULDER_CLEVIS = (-10.0, 0.0, 50.0)
ELBOW = 500.0
SHOULDER_MOVING = (220.0, 0.0, -70.0)
ELBOW_CLEVIS = (240.0, 0.0, 108.0)
ELBOW_MOVING = (130.0, 0.0, 72.0)
ROD_AT = (390.0, 0.0, 16.0)
# Reach is +18°, the lift is -36°. The extra degrees are the end stops.
SHOULDER_RANGE = (-40.0, 24.0)
ELBOW_RANGE = (-50.0, 38.0)
HQ_OD = 88.9
HQ_ID = 77.8
ROD_LENGTH = 900.0
STEEL = 7.85

# Distinct colors so a joint, a cylinder, and the rod separate in one view.
COLOR = {
    "base": [72, 76, 82],
    "turret": [168, 174, 182],
    "upper": [42, 96, 158],
    "forearm": [188, 104, 42],
    "jaw": [168, 56, 46],
    "rod": [28, 30, 34],
    "barrel": [212, 168, 36],
    "rod_end": [232, 208, 130],
    "pin": [42, 46, 52],
    "bush": [168, 112, 62],
    "screw": [214, 216, 220],
    "jaw_barrel": [196, 154, 40],
}


FINISH = """
def _break_edges(solid):
    try:
        picked = [edge for edge in solid.edges() if edge.length > 80][:18]
        if len(picked) < 4:
            return solid
        return chamfer(picked, length=1.0)
    except Exception:
        return solid

_broken = _break_edges(part)
if _broken is not part:
    try:
        _chips = part - _broken
        if _chips.volume > 1.0:
            part = feature.cut("edge_break", _chips)
    except Exception:
        pass
"""


def _span(anchor, moving, fixed, lo, hi):
    """Pin-to-pin length and barrel angle across a revolute range."""
    driver = {
        "parent_anchor": list(anchor),
        "child_anchor": [0.0, 0.0, 0.0],
        "axis": [0.0, 1.0, 0.0],
    }
    barrel = {"parent_anchor": list(fixed), "axis": [0.0, 1.0, 0.0]}
    relation = {
        "name": "probe",
        "driver": "arm",
        "driven": "barrel",
        "moving_anchor": list(moving),
        "retracted_mm": 1.0,
        "bore_axis": [1.0, 0.0, 0.0],
    }
    rows = []
    for step in range(9):
        angle = lo + (hi - lo) * step / 8.0
        phi, extension, offset = _cylinder(relation, {"arm": driver, "barrel": barrel}, angle)
        rows.append(
            {
                "angle": round(angle, 3),
                "length_mm": round(extension + 1.0, 3),
                "barrel_deg": round(phi, 3),
                "out_of_plane_mm": round(offset, 3),
            }
        )
    return rows


def _moment_arm(anchor, moving, fixed, angle):
    """Perpendicular distance from the arm pin to the cylinder centerline."""
    rotation = _rotation([0.0, 1.0, 0.0], math.radians(angle))
    point_b = [anchor[index] + _matvec(rotation, list(moving))[index] for index in range(3)]
    along = [point_b[index] - fixed[index] for index in range(3)]
    offset = [anchor[index] - fixed[index] for index in range(3)]
    cross = [
        offset[1] * along[2] - offset[2] * along[1],
        offset[2] * along[0] - offset[0] * along[2],
        offset[0] * along[1] - offset[1] * along[0],
    ]
    return math.sqrt(sum(component * component for component in cross)) / math.sqrt(
        sum(component * component for component in along)
    )


def _compression_direction(pin, moving, fixed, angle):
    """Unit vector of a compressing cylinder, in the moving link's frame."""
    rotation = _rotation([0.0, 1.0, 0.0], math.radians(angle))
    point_b = [pin[index] + _matvec(rotation, list(moving))[index] for index in range(3)]
    delta = [point_b[index] - fixed[index] for index in range(3)]
    local = _matvec(_rotation([0.0, 1.0, 0.0], math.radians(-angle)), delta)
    length = math.sqrt(sum(component * component for component in local))
    return [component / length for component in local]


def _worst_angle(pin, moving, fixed, lo, hi):
    return min(_samples(lo, hi), key=lambda angle: _moment_arm(pin, moving, fixed, angle))


def _cylinder_size(rows):
    """Closed length, stroke, and the tube that can actually hold that stroke.

    The eye reaches 26 mm back toward the barrel. The piston has to stay past
    the clevis tang at full retract and 18 mm short of the open end at full
    extend. If those two cannot both be true, the clevises are too close for
    the angle the joint travels.
    """
    lengths = [row["length_mm"] for row in rows]
    shortest = min(lengths)
    longest = max(lengths)
    retracted = shortest - 2.0
    travel = longest - retracted
    stroke = travel + 2.0
    tube_start = 28.0
    tang_end = 36.0
    piston_retract = tang_end + 6.0
    tube_end = piston_retract + travel + 18.0
    eye_reach = 26.0
    gap = (retracted - eye_reach) - tube_end
    shaft = (retracted - 20.0) - piston_retract
    if gap < 8.0 or shaft < 40.0:
        raise RuntimeError(
            f"Cylinder cannot cover {travel:.1f} mm of travel in a {retracted:.1f} mm closed length "
            f"(eye gap {gap:.1f} mm, shaft {shaft:.1f} mm). Move a clevis."
        )
    return {
        "retracted_mm": round(retracted, 3),
        "stroke_mm": round(stroke, 3),
        "tube_start_mm": tube_start,
        "tube_end_mm": round(tube_end, 3),
        "shaft_mm": round(shaft, 3),
        "barrel_min_deg": round(min(row["barrel_deg"] for row in rows) - 8.0, 3),
        "barrel_max_deg": round(max(row["barrel_deg"] for row in rows) + 8.0, 3),
        "rows": rows,
    }


def _eye_coupon() -> str:
    """First eye: a rib buried in the boss, and clevis lugs only 3 mm thick."""
    return """
plate = Pos(0, 0, 0) * Box(80, 8, 50)
boss = Rot(90, 0, 0) * Cylinder(19, 22)
rib = Box(10, 8, 10)
bore = Rot(90, 0, 0) * Cylinder(16, 24)
lug = Pos(0, 0, -36) * Box(32, 3, 44)
lug_bore = Pos(0, 0, -48) * Rot(90, 0, 0) * Cylinder(8, 12)
part = feature.add("cheek", plate)
part = feature.add("eye", boss)
part = feature.add("eye_rib", rib)
part = feature.cut("bore", bore)
part = feature.add("lug", lug)
part = feature.cut("lug_bore", lug_bore)
"""


def _upper() -> str:
    return f"""
left = Pos(254, 36, 4) * Box(564, 8, 76)
right = Pos(254, -36, 4) * Box(564, 8, 76)
top = Pos(260, 0, 40) * Box(380, 80, 8)
bottom_heel = Pos(80, 0, -30) * Box(40, 80, 8)
bottom_toe = Pos(400, 0, -30) * Box(120, 80, 8)
bridge = Pos(220, 0, -42) * Box(100, 88, 24)
web = Pos(250, 0, 4) * Box(8, 80, 78)
eye_l = Pos(0, 36, 0) * Rot(90, 0, 0) * Cylinder(28, 22)
eye_r = Pos(0, -36, 0) * Rot(90, 0, 0) * Cylinder(28, 22)
root_a = Pos(36, 28, 4) * Box(150, 16, 72)
root_b = Pos(36, -28, 4) * Box(150, 16, 72)
eye_l2 = Pos({ELBOW}, 36, 0) * Rot(90, 0, 0) * Cylinder(28, 18)
eye_r2 = Pos({ELBOW}, -36, 0) * Rot(90, 0, 0) * Cylinder(28, 18)
bore = Rot(90, 0, 0) * Cylinder(16, 120)
bore2 = Pos({ELBOW}, 0, 0) * Rot(90, 0, 0) * Cylinder(16, 120)
gusset_l = Pos(78, 43, -10) * Box(90, 10, 30)
gusset_r = Pos(78, -43, -10) * Box(90, 10, 30)
lug_l = Pos(220, 28, -64) * Box(88, 32, 100)
lug_r = Pos(220, -28, -64) * Box(88, 32, 100)
lug_bore = Pos(220, 0, -70) * Rot(90, 0, 0) * Cylinder(10.5, 90)
el_l = Pos(240, 28, 80) * Box(72, 32, 124)
el_r = Pos(240, -28, 80) * Box(72, 32, 124)
el_bore = Pos(240, 0, 108) * Rot(90, 0, 0) * Cylinder(10.5, 52)
part = feature.add("side_a", left)
part = feature.add("side_b", right)
part = feature.add("top_strap", top)
part = feature.add("bottom_heel", bottom_heel)
part = feature.add("bottom_toe", bottom_toe)
part = feature.add("clevis_bridge", bridge)
part = feature.add("web", web)
part = feature.add("shoulder_eye_a", eye_l)
part = feature.add("shoulder_eye_b", eye_r)
part = feature.add("elbow_eye_a", eye_l2)
part = feature.add("elbow_eye_b", eye_r2)
part = feature.add("root_doubler_a", root_a)
part = feature.add("root_doubler_b", root_b)
part = feature.cut("shoulder_bore", bore)
part = feature.cut("elbow_bore", bore2)
part = feature.add("gusset_a", gusset_l)
part = feature.add("gusset_b", gusset_r)
part = feature.add("shoulder_lug_a", lug_l)
part = feature.add("shoulder_lug_b", lug_r)
part = feature.cut("shoulder_lug_bore", lug_bore)
part = feature.add("elbow_lug_a", el_l)
part = feature.add("elbow_lug_b", el_r)
part = feature.cut("elbow_lug_bore", el_bore)
part = feature.cut("lightening_a", Pos(120, 0, 4) * Rot(90, 0, 0) * Cylinder(13, 110))
part = feature.cut("lightening_b", Pos(180, 0, 4) * Rot(90, 0, 0) * Cylinder(13, 110))
part = feature.cut("lightening_c", Pos(390, 0, 4) * Rot(90, 0, 0) * Cylinder(13, 110))
{FINISH}
"""


def _forearm() -> str:
    return f"""
left = Pos(200, 20, 2) * Box(430, 8, 64)
right = Pos(200, -20, 2) * Box(430, 8, 64)
top = Pos(180, 0, 30) * Box(250, 48, 6)
bottom = Pos(180, 0, -26) * Box(250, 48, 6)
eye_l = Pos(0, 20, 0) * Rot(90, 0, 0) * Cylinder(24, 18)
eye_r = Pos(0, -20, 0) * Rot(90, 0, 0) * Cylinder(24, 18)
bore = Rot(90, 0, 0) * Cylinder(16, 70)
gusset = Pos(70, 27, -6) * Box(70, 8, 24)
lug_l = Pos(130, 24, 72) * Box(64, 28, 108)
lug_r = Pos(130, -24, 72) * Box(64, 28, 108)
lug_bore = Pos(130, 0, 72) * Rot(90, 0, 0) * Cylinder(10.5, 52)
cradle = Pos(390, 0, -6) * Box(96, 78, 52)
seat = Pos(390, 0, 16) * Rot(90, 0, 0) * Cylinder(46, 100)
part = feature.add("side_a", left)
part = feature.add("side_b", right)
part = feature.add("top_strap", top)
part = feature.add("bottom_strap", bottom)
part = feature.add("elbow_eye_a", eye_l)
part = feature.add("elbow_eye_b", eye_r)
part = feature.cut("elbow_bore", bore)
part = feature.add("gusset", gusset)
part = feature.add("lug_a", lug_l)
part = feature.add("lug_b", lug_r)
part = feature.cut("lug_bore", lug_bore)
part = feature.cut("lightening_a", Pos(110, 0, 2) * Rot(90, 0, 0) * Cylinder(11, 70))
part = feature.cut("lightening_b", Pos(210, 0, 2) * Rot(90, 0, 0) * Cylinder(11, 70))
part = feature.add("cradle", cradle)
part = feature.cut("rod_seat", seat)
# The barrel is above the rod. The post is a plate on the cradle's inner
# end, clear of the rod (the rod reaches x=345.5), and the bridge stops
# just short of the barrel so the jaw can still slide.
post = Pos(326, 0, 80) * Box(34, 48, 150)
foot = Pos(338.5, 0, 8) * Box(9, 48, 36)
bridge = Pos(355.5, 0, 152) * Box(32, 36, 28)
part = feature.add("jaw_post", post)
part = feature.add("jaw_foot", foot)
part = feature.add("jaw_bridge", bridge)
{FINISH}
"""


def _base() -> str:
    holes = "\n".join(
        f'part = feature.cut("bolt_{index}", Pos({165 * math.cos(math.radians(index * 45)):.4f}, '
        f'{165 * math.sin(math.radians(index * 45)):.4f}, 16) * Cylinder(11, 40))'
        for index in range(8)
    )
    return f"""
plate = Pos(0, 0, 11) * Box(420, 400, 22)
boss = Pos(0, 0, 40) * Cylinder(84, 36)
pedestal = Pos(0, 0, 99) * Cylinder(70, 82)
part = feature.add("plate", plate)
part = feature.add("slew_boss", boss)
part = feature.add("pedestal", pedestal)
part = feature.cut("slew_bore", Pos(0, 0, 36) * Cylinder(46, 70))
{holes}
{FINISH}
"""


def _turret() -> str:
    return """
ring = Pos(0, 0, 8) * Cylinder(92, 16)
part = feature.add("ring", ring)
part = feature.cut("ring_bore", Pos(0, 0, 8) * Cylinder(50, 24))
part = feature.add("cheek_a", Pos(40, 54, 120) * Box(150, 16, 230))
part = feature.add("cheek_b", Pos(40, -54, 120) * Box(150, 16, 230))
part = feature.cut("shoulder_bore", Pos(48, 0, 200) * Rot(90, 0, 0) * Cylinder(13.0, 180))
part = feature.add("clevis_back", Pos(-30, 0, 50) * Box(16, 112, 70))
part = feature.add("clevis_a", Pos(-8, 16, 50) * Box(40, 8, 52))
part = feature.add("clevis_b", Pos(-8, -16, 50) * Box(40, 8, 52))
part = feature.cut("clevis_bore", Pos(-10, 0, 50) * Rot(90, 0, 0) * Cylinder(10.5, 56))
""" + FINISH


def _barrel(size, bore_radius, outer_radius, pin_radius, port_sign, port_x) -> str:
    start = size["tube_start_mm"]
    end = size["tube_end_mm"]
    mid = (start + end) / 2.0
    length = end - start
    # The boss starts inside the bore and stands 12 mm off the tube.
    port_inner = port_sign * (bore_radius - 2.0)
    port_outer = port_sign * (outer_radius + 12.0)
    port_center = (port_inner + port_outer) / 2.0
    port_length = abs(port_outer - port_inner)
    # Tang runs from behind the pin (x=-24) into the tube, which starts at 28.
    return f"""
tang = Pos(13, 0, 0) * Box(74, 16, 50)
tang_bore = Rot(90, 0, 0) * Cylinder({pin_radius + 0.5}, 24)
tube = Pos({mid}, 0, 0) * Rot(0, -90, 0) * Cylinder({outer_radius}, {length})
bore = Pos({mid}, 0, 0) * Rot(0, -90, 0) * Cylinder({bore_radius}, {length + 2})
port = Pos({port_x}, 0, {port_center}) * Cylinder(6, {port_length})
part = feature.add("tang", tang)
part = feature.cut("tang_bore", tang_bore)
part = feature.add("tube", tube)
part = feature.cut("bore", bore)
part = feature.add("port", port)
"""


def _piston_rod(size, radius, pin_radius) -> str:
    shaft = size["shaft_mm"]
    # Eye steel runs from x=-26 (toward the barrel) to x=+18. The shaft ends
    # at x=-20, clear of the pin bore and still inside the eye.
    return f"""
eye = Pos(-4, 0, 0) * Box(44, 12, 36)
eye_bore = Rot(90, 0, 0) * Cylinder({pin_radius + 0.5}, 24)
shaft = Pos({-shaft / 2 - 20}, 0, 0) * Rot(0, -90, 0) * Cylinder({radius}, {shaft})
part = feature.add("eye", eye)
part = feature.cut("eye_bore", eye_bore)
part = feature.add("shaft", shaft)
"""


def _pin(radius, length, flange_at) -> str:
    return f"""
shaft = Rot(90, 0, 0) * Cylinder({radius}, {length})
flange = Pos(0, {flange_at}, 0) * Rot(90, 0, 0) * Cylinder({radius + 5}, 5)
ring = Pos(0, {-flange_at}, 0) * Rot(90, 0, 0) * Cylinder({radius + 3.2}, 2.4)
groove = Pos(0, {-flange_at}, 0) * Rot(90, 0, 0) * Cylinder({radius - 1.3}, 1.5)
part = feature.add("shaft", shaft)
part = feature.add("flange", flange)
part = feature.add("clip_ring", ring)
part = feature.cut("clip_groove", groove)
"""


def _bushing() -> str:
    return """
body = Rot(90, 0, 0) * Cylinder(16, 10)
bore = Rot(90, 0, 0) * Cylinder(12.9, 14)
part = feature.add("bush", body)
part = feature.cut("bore", bore)
"""


def _jaw_barrel() -> str:
    return """
tube = Pos(0, 0, 5) * Cylinder(18, 60)
bore = Pos(0, 0, 5) * Cylinder(12, 66)
port = Pos(22, 0, 5) * Rot(0, -90, 0) * Cylinder(6, 16)
part = feature.add("tube", tube)
part = feature.cut("bore", bore)
part = feature.add("port", port)
"""


def _jaw() -> str:
    return """
stem = Pos(0, 0, 36) * Cylinder(10, 80)
pad = Pos(0, 0, -20) * Box(76, 64, 50)
seat = Pos(0, 0, -40) * Rot(90, 0, 0) * Cylinder(46, 90)
part = feature.add("stem", stem)
part = feature.add("pad", pad)
part = feature.cut("seat", seat)
"""


def _hq_across() -> str:
    """Visible HQ tube across the jaw. The cradle seat is along Y."""
    return f"""
tube = Rot(90, 0, 0) * Cylinder({HQ_OD / 2}, {ROD_LENGTH})
bore = Rot(90, 0, 0) * Cylinder({HQ_ID / 2}, {ROD_LENGTH + 4})
part = feature.add("tube", tube)
part = feature.cut("bore", bore)
"""


def _hq_rod(length, name_note) -> str:
    return f"""
# {name_note}
tube = Pos({length / 2}, 0, 0) * Rot(0, -90, 0) * Cylinder({HQ_OD / 2}, {length})
bore = Pos({length / 2}, 0, 0) * Rot(0, -90, 0) * Cylinder({HQ_ID / 2}, {length + 4})
part = feature.add("tube", tube)
part = feature.cut("bore", bore)
"""


def _add(document: Document, name: str, script: str):
    info = document.create_part_from_script(name, script)
    print(f"  {name}: {info.volume_mm3:.0f} mm^3  {info.bounding_box.size.x:.0f} x {info.bounding_box.size.y:.0f} x {info.bounding_box.size.z:.0f}")
    return info


def _fixed(name, parent, child, anchor, child_anchor=None):
    return {
        "name": name,
        "type": "fixed",
        "parent": parent,
        "child": child,
        "parent_anchor": list(anchor),
        "child_anchor": list(child_anchor or [0, 0, 0]),
    }


def _instance(name, part, color, material="steel", payload=0.0, payload_at=None):
    record = {"name": name, "part": part, "material": material, "color": color}
    if payload:
        record["payload_kg"] = payload
        record["payload_at"] = list(payload_at or [0, 0, 0])
    return record


def _build(document: Document) -> dict:
    print("parts")
    _add(document, "eye_coupon", _eye_coupon())
    _add(document, "upper", _upper())
    _add(document, "forearm", _forearm())
    _add(document, "base", _base())
    _add(document, "turret", _turret())
    shoulder_rows = _span(SHOULDER_PIN, SHOULDER_MOVING, SHOULDER_CLEVIS, *SHOULDER_RANGE)
    elbow_rows = _span((ELBOW, 0.0, 0.0), ELBOW_MOVING, ELBOW_CLEVIS, *ELBOW_RANGE)
    shoulder = _cylinder_size(shoulder_rows)
    elbow = _cylinder_size(elbow_rows)
    print(
        f"  shoulder cylinder {shoulder['retracted_mm']:.1f} + {shoulder['stroke_mm']:.1f} mm, "
        f"elbow {elbow['retracted_mm']:.1f} + {elbow['stroke_mm']:.1f} mm"
    )
    _add(document, "shoulder_barrel", _barrel(shoulder, 20.0, 26.0, 10.0, port_sign=-1.0, port_x=150.0))
    _add(document, "shoulder_rod", _piston_rod(shoulder, 11.0, 10.0))
    _add(document, "elbow_barrel", _barrel(elbow, 20.0, 26.0, 10.0, port_sign=1.0, port_x=_port(elbow)))
    _add(document, "elbow_rod", _piston_rod(elbow, 11.0, 10.0))
    _add(document, "jaw_barrel", _jaw_barrel())
    _add(document, "jaw", _jaw())
    _add(document, "shoulder_pin", _pin(12.5, 156, -74))
    _add(document, "elbow_pin", _pin(12.5, 108, -50))
    _add(document, "clevis_pin", _pin(9.5, 68, -32))
    _add(document, "bushing", _bushing())
    rod_info = _add(document, "hq_visible", _hq_across())
    _add(document, "hq_rod_3m", _hq_rod(3000.0, "full HQ rod for the modal check, axis X"))
    _add(document, "hq_rod_1_5m", _hq_rod(1500.0, "free length when the chuck is the second support"))
    _add(document, "hq_rod_1_2m", _hq_rod(1200.0, "shorter stick-up if 1.5 m still meets a harmonic"))
    document.insert_standard_part("m20", "socket_head_cap_screw", "M20-2.5", length=60)
    visible_kg = rod_info.volume_mm3 * STEEL / 1.0e6
    full_kg = visible_kg * (3000.0 / ROD_LENGTH)
    payload_kg = full_kg - visible_kg
    print(f"  HQ visible {visible_kg:.2f} kg, 3 m rod {full_kg:.2f} kg, payload {payload_kg:.2f} kg")

    shoulder_arm = min(_moment_arm(SHOULDER_PIN, SHOULDER_MOVING, SHOULDER_CLEVIS, angle) for angle in _samples(*SHOULDER_RANGE))
    elbow_arm = min(
        _moment_arm((ELBOW, 0.0, 0.0), ELBOW_MOVING, ELBOW_CLEVIS, angle) for angle in _samples(*ELBOW_RANGE)
    )

    bolts = []
    for index in range(8):
        angle = math.radians(index * 45.0)
        bolts.append((f"bolt_{index}", [165.0 * math.cos(angle), 165.0 * math.sin(angle), 22.0]))

    cables_tight = [
        _hose(
            "shoulder_hose",
            [
                {"instance": "turret", "at": [-36, 78, 40], "clip": True},
                {"instance": "turret", "at": [16, 84, 88], "clip": True},
                {"instance": "shoulder_barrel", "at": [_port(shoulder), 18, -8], "clip": True},
            ],
        ),
        _hose(
            "elbow_hose",
            [
                {"instance": "turret", "at": [-36, -78, 40], "clip": True},
                {"instance": "upper", "at": [90, 50, 8], "clip": True},
                {"instance": "upper", "at": [106, 50, 8], "clip": True},
                {"instance": "elbow_barrel", "at": [_port(elbow), 8, 18], "clip": True},
            ],
        ),
    ]
    cables_loop = [
        _hose(
            "shoulder_hose",
            [
                {"instance": "turret", "at": [-80, 180, 160], "clip": True},
                {"instance": "turret", "at": [30, 200, 300], "clip": True},
                {"instance": "shoulder_barrel", "at": [150.0, 150, -60], "clip": True},
                {"instance": "shoulder_barrel", "at": [150.0, 0, -50], "clip": True},
            ],
        ),
        _hose(
            "elbow_hose",
            [
                {"instance": "turret", "at": [-40, -120, 70], "clip": True},
                {"instance": "turret", "at": [24, -140, 200], "clip": True},
                {"instance": "upper", "at": [110, -82, 28], "clip": True},
                {"instance": "upper", "at": [340, -82, 40], "clip": True},
                {"instance": "elbow_barrel", "at": [_port(elbow), -80, 20], "clip": True},
                {"instance": "elbow_barrel", "at": [_port(elbow), 0, 46], "clip": True},
            ],
        ),
    ]

    spec = {
        "name": "rod_handler",
        "instances": [
            _instance("base", "base", COLOR["base"]),
            _instance("turret", "turret", COLOR["turret"]),
            _instance("upper", "upper", COLOR["upper"]),
            _instance("forearm", "forearm", COLOR["forearm"]),
            _instance("jaw_barrel", "jaw_barrel", COLOR["jaw_barrel"]),
            _instance("jaw", "jaw", COLOR["jaw"]),
            _instance("rod", "hq_visible", COLOR["rod"], payload=round(payload_kg, 3), payload_at=[0, 0, 0]),
            _instance("shoulder_barrel", "shoulder_barrel", COLOR["barrel"]),
            _instance("shoulder_rod", "shoulder_rod", COLOR["rod_end"]),
            _instance("elbow_barrel", "elbow_barrel", COLOR["barrel"]),
            _instance("elbow_rod", "elbow_rod", COLOR["rod_end"]),
            _instance("shoulder_pin", "shoulder_pin", COLOR["pin"]),
            _instance("elbow_pin", "elbow_pin", COLOR["pin"]),
            _instance("shoulder_clevis_pin", "clevis_pin", COLOR["pin"]),
            _instance("shoulder_eye_pin", "clevis_pin", COLOR["pin"]),
            _instance("elbow_clevis_pin", "clevis_pin", COLOR["pin"]),
            _instance("elbow_eye_pin", "clevis_pin", COLOR["pin"]),
            _instance("bush_shoulder_a", "bushing", COLOR["bush"]),
            _instance("bush_shoulder_b", "bushing", COLOR["bush"]),
            _instance("bush_elbow_a", "bushing", COLOR["bush"]),
            _instance("bush_elbow_b", "bushing", COLOR["bush"]),
            *[_instance(name, "m20", COLOR["screw"]) for name, _anchor in bolts],
        ],
        "joints": [
            {
                "name": "slew",
                "type": "revolute",
                "parent": "base",
                "child": "turret",
                "parent_anchor": [0, 0, 140],
                "child_anchor": [0, 0, 0],
                "axis": [0, 0, 1],
                "min": -35,
                "max": 80,
                "value": 20,
            },
            {
                "name": "shoulder",
                "type": "revolute",
                "parent": "turret",
                "child": "upper",
                "parent_anchor": list(SHOULDER_PIN),
                "child_anchor": [0, 0, 0],
                "axis": [0, 1, 0],
                "min": SHOULDER_RANGE[0],
                "max": SHOULDER_RANGE[1],
                "value": 10,
            },
            {
                "name": "elbow",
                "type": "revolute",
                "parent": "upper",
                "child": "forearm",
                "parent_anchor": [ELBOW, 0, 0],
                "child_anchor": [0, 0, 0],
                "axis": [0, 1, 0],
                "min": ELBOW_RANGE[0],
                "max": ELBOW_RANGE[1],
                "value": 15,
            },
            _fixed("shoulder_pin_seat", "turret", "shoulder_pin", SHOULDER_PIN),
            _fixed("elbow_pin_seat", "upper", "elbow_pin", [ELBOW, 0, 0]),
            _fixed("shoulder_clevis_seat", "turret", "shoulder_clevis_pin", SHOULDER_CLEVIS),
            _fixed("shoulder_eye_seat", "upper", "shoulder_eye_pin", SHOULDER_MOVING),
            _fixed("elbow_clevis_seat", "upper", "elbow_clevis_pin", ELBOW_CLEVIS),
            _fixed("elbow_eye_seat", "forearm", "elbow_eye_pin", ELBOW_MOVING),
            _fixed("bush_shoulder_a_seat", "upper", "bush_shoulder_a", [0, 36, 0]),
            _fixed("bush_shoulder_b_seat", "upper", "bush_shoulder_b", [0, -36, 0]),
            _fixed("bush_elbow_a_seat", "forearm", "bush_elbow_a", [0, 20, 0]),
            _fixed("bush_elbow_b_seat", "forearm", "bush_elbow_b", [0, -20, 0]),
            {
                "name": "shoulder_barrel",
                "type": "revolute",
                "parent": "turret",
                "child": "shoulder_barrel",
                "parent_anchor": list(SHOULDER_CLEVIS),
                "child_anchor": [0, 0, 0],
                "axis": [0, 1, 0],
                "min": shoulder["barrel_min_deg"],
                "max": shoulder["barrel_max_deg"],
            },
            {
                "name": "shoulder_stroke",
                "type": "prismatic",
                "parent": "shoulder_barrel",
                "child": "shoulder_rod",
                "parent_anchor": [shoulder["retracted_mm"], 0, 0],
                "child_anchor": [0, 0, 0],
                "axis": [1, 0, 0],
                "min": 0,
                "max": shoulder["stroke_mm"],
            },
            {
                "name": "elbow_barrel",
                "type": "revolute",
                "parent": "upper",
                "child": "elbow_barrel",
                "parent_anchor": list(ELBOW_CLEVIS),
                "child_anchor": [0, 0, 0],
                "axis": [0, 1, 0],
                "min": elbow["barrel_min_deg"],
                "max": elbow["barrel_max_deg"],
            },
            {
                "name": "elbow_stroke",
                "type": "prismatic",
                "parent": "elbow_barrel",
                "child": "elbow_rod",
                "parent_anchor": [elbow["retracted_mm"], 0, 0],
                "child_anchor": [0, 0, 0],
                "axis": [1, 0, 0],
                "min": 0,
                "max": elbow["stroke_mm"],
            },
            _fixed("jaw_barrel_seat", "forearm", "jaw_barrel", [ROD_AT[0], 0, 150]),
            {
                "name": "jaw",
                "type": "prismatic",
                "parent": "jaw_barrel",
                "child": "jaw",
                "parent_anchor": [0, 0, -30],
                "child_anchor": [0, 0, 0],
                "axis": [0, 0, -1],
                "min": 0,
                "max": 64,
                "value": 0,
            },
            _fixed("rod_seat", "forearm", "rod", ROD_AT),
            *[_fixed(f"{name}_seat", "base", name, anchor) for name, anchor in bolts],
        ],
        "relations": [
            {
                "name": "shoulder_cylinder",
                "type": "cylinder",
                "driver": "shoulder",
                "driven": "shoulder_barrel",
                "stroke": "shoulder_stroke",
                "moving_anchor": list(SHOULDER_MOVING),
                "retracted_mm": shoulder["retracted_mm"],
            },
            {
                "name": "elbow_cylinder",
                "type": "cylinder",
                "driver": "elbow",
                "driven": "elbow_barrel",
                "stroke": "elbow_stroke",
                "moving_anchor": list(ELBOW_MOVING),
                "retracted_mm": elbow["retracted_mm"],
            },
        ],
        "poses": {
            "reach": {"slew": 35, "shoulder": 18, "elbow": 28, "jaw": 0},
            "lift": {"slew": 0, "shoulder": -36, "elbow": -28, "jaw": 64},
        },
        "motions": {
            "lift": {
                "keyframes": [{"pose": "reach"}, {"pose": "lift"}],
                "samples": 4,
                "duration_s": 3.0,
            }
        },
        "cables": cables_tight,
        "actuators": [
            {
                "name": "shoulder_cylinder",
                "kind": "hydraulic_cylinder",
                "joint": "shoulder",
                "bore_mm": 40,
                "rod_mm": 22,
                "moment_arm_mm": round(shoulder_arm, 2),
                "efficiency": 0.85,
            },
            {
                "name": "elbow_cylinder",
                "kind": "hydraulic_cylinder",
                "joint": "elbow",
                "bore_mm": 40,
                "rod_mm": 22,
                "moment_arm_mm": round(elbow_arm, 2),
                "efficiency": 0.85,
            },
            {
                "name": "jaw_cylinder",
                "kind": "hydraulic_cylinder",
                "joint": "jaw",
                "bore_mm": 32,
                "rod_mm": 18,
                "efficiency": 0.85,
            },
        ],
        "ignore_collisions": [
            ["bush_shoulder_a", "upper"],
            ["bush_shoulder_b", "upper"],
            ["bush_elbow_a", "forearm"],
            ["bush_elbow_b", "forearm"],
            ["jaw", "rod"],
            *[[name, "base"] for name, _anchor in bolts],
        ],
    }
    return {
        "spec": spec,
        "cables_loop": cables_loop,
        "shoulder": shoulder,
        "elbow": elbow,
        "shoulder_arm_mm": shoulder_arm,
        "elbow_arm_mm": elbow_arm,
        "visible_kg": visible_kg,
        "full_kg": full_kg,
        "payload_kg": payload_kg,
    }


def _samples(lo, hi):
    return [lo + (hi - lo) * step / 8.0 for step in range(9)]


def _port(size) -> float:
    return (size["tube_start_mm"] + size["tube_end_mm"]) / 2.0


def _hose(name, waypoints):
    return {
        "name": name,
        "kind": "hose",
        "diameter_mm": 12,
        "min_bend_radius_mm": 48,
        "stiffness": "high",
        "max_strain": 0.02,
        "pinch_mm": 1.0,
        "waypoints": waypoints,
    }


def _feature_brief(report) -> dict:
    data = report.to_dict()
    return {
        "redundant": data["redundant_features"],
        "warning": data["warning"],
        "steps": [
            {
                "name": step["name"],
                "op": step["op"],
                "redundant": step["redundant"],
                "buried_fraction": step.get("buried_fraction"),
                "note": step["note"],
            }
            for step in data["steps"]
        ],
    }


def _fea_brief(report) -> dict:
    data = report.to_dict()
    return {
        "status": data["status"],
        "max_von_mises_mpa": data["max_von_mises_mpa"],
        "max_von_mises_at_mm": data["max_von_mises_at_mm"],
        "max_displacement_mm": data["max_displacement_mm"],
        "min_safety_factor": data["min_safety_factor"],
        "warnings": data["warnings"],
        "note": data["note"],
        "views": [view["path"] for view in data["views"]],
    }


def _modal_brief(report) -> dict:
    return {
        "status": report.get("resonance", {}).get("status"),
        "modes": [
            {"hz": mode["frequency_hz"], "rigid": mode["rigid_body"]}
            for mode in report.get("modes", [])
        ],
        "flags": report.get("resonance", {}).get("flags"),
        "suggestions": report.get("resonance", {}).get("suggestions"),
        "warnings": report.get("warnings"),
        "note": report.get("note"),
    }


def _cable_brief(report) -> dict:
    return {
        "status": report["status"],
        "routes": [
            {
                "name": cable["name"],
                "status": cable["status"],
                "length_mm": cable["length_mm"],
                "max_strain": cable["max_strain"],
                "min_bend_radius_mm": cable["min_bend_radius_mm"],
                "bend_limit_mm": cable["bend_limit_mm"],
                "pinch_count": cable["pinch_count"],
            }
            for cable in report["cables"]
        ],
    }


def _power_brief(report) -> dict:
    return {
        "actuators": [
            {
                "name": item["name"],
                "joint": item["joint"],
                "peak_torque_nm": item.get("peak_torque_nm"),
                "peak_force_n": item.get("peak_force_n"),
                "peak_pressure_mpa": item.get("peak_pressure_mpa"),
                "peak_power_w": item.get("peak_power_w"),
            }
            for item in report["actuators"]
        ],
        "pump": report.get("pump"),
        "assumptions": report.get("assumptions"),
    }


def _bolt_brief(report) -> dict:
    if "bolts" in report:
        return {
            "worst": report["worst"],
            "bolts": [
                {
                    "grade": row["grade"],
                    "status": row["status"],
                    "slip_margin": row["slip_margin"],
                    "yield_margin": row["yield_margin"],
                    "preload_n": row["preload_n"],
                    "tightening_torque_nm": row["tightening_torque_nm"],
                    "loosening": row["loosening"]["rating"],
                    "note": row["note"],
                }
                for row in report["bolts"]
            ],
        }
    return {
        "grade": report["grade"],
        "status": report["status"],
        "slip_margin": report["slip_margin"],
        "yield_margin": report["yield_margin"],
        "loosening": report["loosening"]["rating"],
        "note": report["note"],
    }


def _copy_picture(source, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _closeup(document: Document, names: set[str], stem: str, title: str) -> str:
    packed = document.get_assembly("rod_handler")
    spec = packed["assembly"]
    shapes = {instance["part"]: document._parts[instance["part"]].base for instance in spec["instances"]}
    solved, _placed = assembly_mod.posed_shapes(spec, shapes)
    solved = dict(solved)
    solved["instances"] = [instance for instance in solved["instances"] if instance["name"] in names]
    mesh = assembly_mod._colored_mesh(shapes, solved)
    pictures = render_motion_frames([mesh], RENDERS, stem, [title])
    return pictures["frames"][0]["path"]


def _eye_error(document: Document) -> dict:
    from aicad.kinematics import transform_point

    packed = document.get_assembly("rod_handler")
    solved = packed["solved"]
    frames = {instance["name"]: instance for instance in solved["instances"]}
    relations = {row["name"]: row for row in solved["relations"]}
    checks = {}
    for name, parent, moving in (
        ("shoulder_cylinder", "upper", SHOULDER_MOVING),
        ("elbow_cylinder", "forearm", ELBOW_MOVING),
    ):
        target = transform_point(frames[parent]["matrix"], list(moving))
        rod_name = "shoulder_rod" if name.startswith("shoulder") else "elbow_rod"
        origin = frames[rod_name]["origin_mm"]
        miss = math.dist(target, origin)
        checks[name] = {
            "clevis_mm": [round(value, 3) for value in target],
            "eye_mm": [round(value, 3) for value in origin],
            "miss_mm": round(miss, 3),
            "stroke_mm": relations[name]["stroke_mm"],
            "barrel_deg": relations[name]["driven_value"],
        }
    return checks


def main() -> None:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    RENDERS.mkdir(parents=True, exist_ok=True)
    document = Document(WORKSPACE)
    built = _build(document)
    spec = built["spec"]
    print("assembly")
    document.create_assembly(spec)
    eyes = _eye_error(document)
    print("  eye miss", {name: row["miss_mm"] for name, row in eyes.items()})

    print("features")
    coupon_features = _feature_brief(document.feature_report("eye_coupon"))
    upper_features = _feature_brief(document.feature_report("upper"))
    print("  coupon redundant", coupon_features["redundant"])
    print("  upper redundant", upper_features["redundant"])

    print("motion")
    motion = document.run_motion("rod_handler", sequence="lift", render=True)
    print("  collisions", motion["status"], motion["colliding_frames"], "count", motion["collision_count"])
    collision_rows = []
    for frame in motion["frames"]:
        for hit in frame["collisions"]:
            row = {
                "frame": frame["index"],
                "a": hit["a"],
                "b": hit["b"],
                "mm3": round(hit["interference_volume_mm3"], 1),
                "at_mm": None if hit["at_mm"] is None else [round(value, 1) for value in hit["at_mm"]],
            }
            collision_rows.append(row)
            print("   ", row)

    print("hoses, first route")
    hoses_first = _cable_brief(document.check_cables("rod_handler", sequence="lift"))
    print(" ", hoses_first["status"], [(row["name"], row["status"], row["min_bend_radius_mm"], row["pinch_count"]) for row in hoses_first["routes"]])
    spec["cables"] = built["cables_loop"]
    document.update_assembly(spec)
    hoses_loop = _cable_brief(document.check_cables("rod_handler", sequence="lift"))
    print("  loop", hoses_loop["status"], [(row["name"], row["status"], row["min_bend_radius_mm"], row["max_strain"], row["pinch_count"]) for row in hoses_loop["routes"]])
    if hoses_loop["status"] != "pass":
        longest = {}
        raw = document.check_cables("rod_handler", sequence="lift")
        for cable in raw["cables"]:
            longest[cable["name"]] = max(cable["length_mm"]) * 1.02
        for cable in spec["cables"]:
            cable["rest_length_mm"] = longest[cable["name"]]
        document.update_assembly(spec)
        hoses_loop = _cable_brief(document.check_cables("rod_handler", sequence="lift"))
        print("  loop with cut length", hoses_loop["status"])

    print("power")
    power = document.estimate_power("rod_handler", sequence="lift")
    power_brief = _power_brief(power)
    print(" ", power_brief["actuators"])

    print("bolts")
    # A stuck rod: 8 kN sideways at the jaw, about 1.15 m from the slew center.
    # Shear per bolt is M * r / sum(r^2) = M / (n * r).
    snatch_n = 8000.0
    moment_nmm = snatch_n * 1150.0
    shear_m12 = moment_nmm / (8.0 * 130.0)
    shear_m20 = moment_nmm / (8.0 * 165.0)
    print(f"  snatch shear M12 {shear_m12:.0f} N, M20 {shear_m20:.0f} N")
    bolts_88 = _bolt_brief(
        document.check_bolts(
            {
                "size": "M12",
                "grade": "8.8",
                "condition": "dry",
                "grip_mm": 22,
                "shear_n": shear_m12,
                "axial_n": snatch_n / 8.0,
                "vibration": "intense",
            }
        )
    )
    bolts_109 = _bolt_brief(
        document.check_bolts(
            {
                "size": "M20",
                "grade": "10.9",
                "condition": "dry",
                "grip_mm": 22,
                "shear_n": shear_m20,
                "axial_n": snatch_n / 8.0,
                "vibration": "intense",
                "preload_fraction": 0.75,
                "locking": ["wedge-locking washers"],
            }
        )
    )
    print("  M12-8.8", bolts_88["status"], bolts_88["loosening"], "slip", bolts_88["slip_margin"])
    print("  M20-10.9", bolts_109["status"], bolts_109["loosening"], "slip", bolts_109["slip_margin"])

    print("fea coupon")
    coupon = document.solve_mechanics(
        "eye_coupon",
        {
            "material": "steel",
            "mesh_size_mm": 2.5,
            "fixes": [{"name": "eye", "select": {"kind": "cylinder", "radius": 16, "axis": [0, 1, 0], "at": [0, 0, 0]}}],
            "loads": [
                {
                    "name": "cylinder",
                    "type": "force",
                    "vector": [0, 0, -6000],
                    "select": {"kind": "cylinder", "radius": 8, "axis": [0, 1, 0], "at": [0, 0, -48]},
                }
            ],
        },
    )
    coupon_brief = _fea_brief(coupon)
    print(" ", coupon_brief["status"], "sf", coupon_brief["min_safety_factor"], "MPa", coupon_brief["max_von_mises_mpa"])

    shoulder_peak = next(item for item in power["actuators"] if item["joint"] == "shoulder")
    elbow_peak = next(item for item in power["actuators"] if item["joint"] == "elbow")
    shoulder_force = float(shoulder_peak["peak_torque_nm"]) * 1000.0 / built["shoulder_arm_mm"]
    elbow_force = float(elbow_peak["peak_torque_nm"]) * 1000.0 / built["elbow_arm_mm"]
    shoulder_dir = _compression_direction(
        SHOULDER_PIN,
        SHOULDER_MOVING,
        SHOULDER_CLEVIS,
        _worst_angle(SHOULDER_PIN, SHOULDER_MOVING, SHOULDER_CLEVIS, *SHOULDER_RANGE),
    )
    elbow_dir = _compression_direction(
        (ELBOW, 0.0, 0.0),
        ELBOW_MOVING,
        ELBOW_CLEVIS,
        _worst_angle((ELBOW, 0.0, 0.0), ELBOW_MOVING, ELBOW_CLEVIS, *ELBOW_RANGE),
    )
    print(
        "fea upper, shoulder",
        round(shoulder_force, 1),
        "elbow",
        round(elbow_force, 1),
    )
    upper = document.solve_mechanics(
        "upper",
        {
            "material": "steel",
            "mesh_size_mm": 8,
            "fixes": [
                {
                    "name": "shoulder_pin",
                    "select": {"kind": "cylinder", "radius": 16, "axis": [0, 1, 0], "at": [0, 0, 0]},
                }
            ],
            "loads": [
                {
                    "name": "rod_at_elbow",
                    "type": "force",
                    "vector": [0, 0, -500],
                    "select": {"kind": "cylinder", "radius": 16, "axis": [0, 1, 0], "at": [ELBOW, 0, 0]},
                },
                {
                    "name": "shoulder_cylinder",
                    "type": "force",
                    "vector": [shoulder_force * component for component in shoulder_dir],
                    "select": {"kind": "cylinder", "radius": 10.5, "axis": [0, 1, 0], "at": [220, 0, -70]},
                },
                {
                    "name": "elbow_cylinder",
                    "type": "force",
                    "vector": [elbow_force * component for component in elbow_dir],
                    "select": {"kind": "cylinder", "radius": 10.5, "axis": [0, 1, 0], "at": list(ELBOW_CLEVIS)},
                },
            ],
        },
    )
    upper_brief = _fea_brief(upper)
    print(" ", upper_brief["status"], "sf", upper_brief["min_safety_factor"], "MPa", upper_brief["max_von_mises_mpa"])

    print("modal rod")
    rod_modal = document.solve_modal(
        "hq_rod_3m",
        {
            "material": "steel",
            "mesh_size_mm": 22,
            "modes": 6,
            "order": 2,
            "fixes": [{"name": "handler", "select": {"kind": "plane", "normal": [-1, 0, 0], "at": [0, 0, 0]}}],
            "excitations": [
                {"name": "HQ drill 400 rpm", "kind": "drill", "rpm": 400},
                {"name": "HQ drill 600 rpm", "kind": "drill", "rpm": 600},
                {"name": "HQ drill 700 rpm", "kind": "drill", "rpm": 700},
            ],
        },
    )
    rod_modal_brief = _modal_brief(rod_modal)
    print(" ", rod_modal_brief["modes"][:4], rod_modal_brief["status"])

    def _rod_modal(part_name, mesh):
        print("modal", part_name)
        report = document.solve_modal(
            part_name,
            {
                "material": "steel",
                "mesh_size_mm": mesh,
                "modes": 4,
                "order": 2,
                "fixes": [{"name": "handler", "select": {"kind": "plane", "normal": [-1, 0, 0], "at": [0, 0, 0]}}],
                "excitations": [
                    {"name": "HQ drill 400 rpm", "kind": "drill", "rpm": 400},
                    {"name": "HQ drill 600 rpm", "kind": "drill", "rpm": 600},
                    {"name": "HQ drill 700 rpm", "kind": "drill", "rpm": 700},
                ],
            },
        )
        brief = _modal_brief(report)
        print(" ", brief["modes"][:3], brief["status"])
        return brief

    rod_1_5 = _rod_modal("hq_rod_1_5m", 16)
    rod_1_2 = _rod_modal("hq_rod_1_2m", 14)

    print("modal upper")
    upper_modal = document.solve_modal(
        "upper",
        {
            "material": "steel",
            "mesh_size_mm": 12,
            "modes": 4,
            "order": 2,
            "fixes": [{"name": "shoulder_pin", "select": {"kind": "cylinder", "radius": 16, "axis": [0, 1, 0], "at": [0, 0, 0]}}],
            "excitations": [{"name": "HQ drill 600 rpm", "kind": "drill", "rpm": 600}],
        },
    )
    upper_modal_brief = _modal_brief(upper_modal)
    print(" ", upper_modal_brief["modes"][:3], upper_modal_brief["status"])

    print("robot")
    if ROBOT.exists():
        shutil.rmtree(ROBOT)
    robot = document.export_robot("rod_handler", ROBOT)
    robot_brief = {
        "validation": robot["validation"],
        "note": robot["note"],
        "joints": [
            {"name": joint["name"], "type": joint["type"], "limit_assumed": joint["limit_assumed"]}
            for joint in robot["joints"]
            if joint["type"] != "fixed"
        ],
        "link_count": len(robot["links"]),
    }
    validation = robot_brief["validation"]
    simulator = validation.get("mujoco", {}).get("status")
    if simulator != "loaded":
        simulator = validation.get("pybullet", {}).get("status")
    print(" ", simulator, robot_brief["note"][:180])

    print("pictures")
    preview = document.render_assembly("rod_handler")
    overall = preview["pictures"]["frames"][0]["path"]
    joint = _closeup(
        document,
        {"turret", "upper", "shoulder_pin", "bush_shoulder_a", "bush_shoulder_b", "shoulder_barrel", "shoulder_rod", "shoulder_clevis_pin", "shoulder_eye_pin"},
        "rod_handler_joint",
        "Shoulder pin, bushings, and cylinder",
    )
    cylinder = _closeup(
        document,
        {"upper", "shoulder_barrel", "shoulder_rod", "shoulder_eye_pin"},
        "rod_handler_cylinder",
        "Shoulder cylinder on the boom clevis",
    )
    _copy_picture(overall, RENDERS / "rod_handler_overall.png")
    _copy_picture(motion["pictures"]["strip"], RENDERS / "rod_handler_lift_strip.png")
    if coupon_brief["views"]:
        _copy_picture(coupon_brief["views"][0], RENDERS / "rod_handler_coupon_fea.png")
    if upper_brief["views"]:
        _copy_picture(upper_brief["views"][0], RENDERS / "rod_handler_upper_fea.png")
    if rod_modal.get("views"):
        _copy_picture(rod_modal["views"][0]["path"], RENDERS / "rod_handler_rod_modal.png")

    report = {
        "eyes": eyes,
        "mass": {
            "visible_kg": round(built["visible_kg"], 3),
            "hq_3m_kg": round(built["full_kg"], 3),
            "payload_kg": round(built["payload_kg"], 3),
        },
        "cylinders": {
            "shoulder": {key: built["shoulder"][key] for key in ("retracted_mm", "stroke_mm", "barrel_min_deg", "barrel_max_deg")},
            "elbow": {key: built["elbow"][key] for key in ("retracted_mm", "stroke_mm", "barrel_min_deg", "barrel_max_deg")},
            "moment_arm_mm": {"shoulder": round(built["shoulder_arm_mm"], 2), "elbow": round(built["elbow_arm_mm"], 2)},
        },
        "features": {"coupon": coupon_features, "upper": upper_features},
        "motion": {
            "status": motion["status"],
            "colliding_frames": motion["colliding_frames"],
            "collision_count": motion["collision_count"],
            "hits": collision_rows[:40],
            "rod_z_mm": [frame["origins_mm"]["rod"][2] for frame in motion["frames"]],
        },
        "hoses_first": hoses_first,
        "hoses": hoses_loop,
        "power": power_brief,
        "bolts": {
            "snatch_n": snatch_n,
            "first_pattern": {"size": "M12", "grade": "8.8", "count": 8, "radius_mm": 130, "shear_n": round(shear_m12, 1), **bolts_88},
            "slew_pattern": {
                "size": "M20",
                "grade": "10.9",
                "count": 8,
                "radius_mm": 165,
                "locking": ["wedge-locking washers"],
                "shear_n": round(shear_m20, 1),
                **bolts_109,
            },
        },
        "fea": {"coupon": coupon_brief, "upper": upper_brief},
        "modal": {
            "rod_3m": rod_modal_brief,
            "rod_1_5m": rod_1_5,
            "rod_1_2m": rod_1_2,
            "upper": upper_modal_brief,
        },
        "robot": robot_brief,
        "pictures": {
            "overall": str(RENDERS / "rod_handler_overall.png"),
            "joint": joint,
            "cylinder": cylinder,
            "lift_strip": str(RENDERS / "rod_handler_lift_strip.png"),
        },
    }
    (RENDERS / "rod_handler_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("wrote", RENDERS / "rod_handler_report.json")


if __name__ == "__main__":
    main()
