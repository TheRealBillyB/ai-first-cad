"""Dimension tables for standard parts.

Add a catalog entry by adding a row. Geometry, search, the bill of materials,
and joint mates all read these tables. Do not bury a size in the builder.

Boundary dimensions are the values the standards fix. Load ratings are
published catalog figures for that designation, calculated by the ISO 281
method. They are not part of the boundary-dimension standard, and a current
maker's line can differ by a few percent. Mass, when a number is stored, is a
published catalog mass. A blank mass is estimated from the solid later.

Sources for the numbers in the rows:

- Deep-groove bore, outside diameter, width, and minimum chamfer follow
  ISO 15. The basic load ratings are the SKF general-catalogue generation
  for that boundary size. NSK, for example, lists 6000 as 5.05 kN dynamic
  and 1.97 kN static, against 4.62 kN and 1.96 kN here.
- Tapered bore, outside diameter, overall width, cone width, and cup width
  follow ISO 355. The dimension-series code is the ISO 355 code (the leading
  character is the angle series). Load ratings marked SKF are that catalogue's
  basic ratings; those marked NTN are NTN's.
- Radial shaft seals are the nominal shaft, housing, and width rows of
  ISO 6194-1:2007 Table 1. DIN 3760 type A uses the same nominal sizes.
  Width may be increased for a more complex lip, which the standard allows.
- Socket-head screws are ISO 4762 maximum head envelope and nominal hex
  socket. Nuts are ISO 4032 style 1 maximum across-flats and height. Washers
  are ISO 7089 nominal diameter and thickness. The threaded shank is the
  ISO 724 basic minor diameter.
- Stepper faces and shafts are NEMA ICS 16 inch-dimension flanges (Table 4)
  and the default shaft (Tables 2 and 6). Body length is not in that standard.
- The two gearboxes are published mounting drawings for those designations:
  flange, shaft, length, ratio, torque, and, where the drawing states it, mass.
  The input face uses the NEMA ICS 16 bolt circle and pilot of the matching frame.
"""

from __future__ import annotations

from dataclasses import dataclass

# ISO 355 angle series: the standard gives a range. The solid uses the midpoint.
_ANGLE_SERIES_DEG = {
    "2": 12.0,  # 10° to 13°59'
    "3": 15.0,  # 14° to 15°59'
    "4": 17.0,  # 16° to 17°59'
    "5": 19.0,  # 18° to 19°59'
    "6": 22.0,  # 20° to 23°59'
    "7": 25.5,  # 24° to 26°59'
}

INCH = 25.4


@dataclass(frozen=True)
class DeepGroove:
    designation: str
    bore_mm: float
    outside_mm: float
    width_mm: float
    chamfer_mm: float
    dynamic_n: int | None
    static_n: int | None
    mass_g: float | None


@dataclass(frozen=True)
class TaperedRoller:
    designation: str
    bore_mm: float
    outside_mm: float
    width_mm: float
    cone_width_mm: float
    cup_width_mm: float
    chamfer_mm: float | None
    series: str
    dynamic_n: int | None
    static_n: int | None
    rating_source: str

    @property
    def angle_deg(self) -> float:
        """Cup race angle used for the solid. The series midpoint, or 15° when the series is blank."""
        if self.series and self.series[0] in _ANGLE_SERIES_DEG:
            return _ANGLE_SERIES_DEG[self.series[0]]
        return 15.0

    @property
    def angle_from_series(self) -> bool:
        return bool(self.series) and self.series[0] in _ANGLE_SERIES_DEG


@dataclass(frozen=True)
class ShaftSeal:
    shaft_mm: float
    housing_mm: float
    width_mm: float

    @property
    def designation(self) -> str:
        return f"{_trim(self.shaft_mm)}x{_trim(self.housing_mm)}x{_trim(self.width_mm)}"


@dataclass(frozen=True)
class CapScrew:
    size: str
    diameter_mm: float
    pitch_mm: float
    head_diameter_mm: float
    head_height_mm: float
    socket_across_flats_mm: float
    socket_depth_mm: float


@dataclass(frozen=True)
class HexNut:
    size: str
    diameter_mm: float
    pitch_mm: float
    across_flats_mm: float
    height_mm: float


@dataclass(frozen=True)
class PlainWasher:
    size: str
    diameter_mm: float
    inside_mm: float
    outside_mm: float
    thickness_mm: float


@dataclass(frozen=True)
class StepperMotor:
    designation: str
    flange: int
    bolt_circle_in: float
    pilot_in: float
    pilot_depth_max_in: float
    pilot_depth_min_in: float
    flange_square_in: float
    flange_circle_max_in: float
    hole_thread: str
    hole_diameter_in: float
    shaft_in: float
    shaft_length_in: float
    shaft_torque_lbf_in: float
    body_length_mm: float


@dataclass(frozen=True)
class Gearbox:
    designation: str
    frame: str
    ratio: float
    flange_mm: float
    body_length_mm: float
    output_diameter_mm: float
    output_length_mm: float
    input_bore_mm: float
    input_bore_depth_mm: float
    pilot_mm: float
    pilot_depth_mm: float
    bolt_circle_mm: float
    hole_diameter_mm: float
    torque_nm: float
    mass_g: float | None
    note: str


def _trim(value: float) -> str:
    text = f"{value:g}"
    return text


def _deep(*rows: tuple) -> tuple[DeepGroove, ...]:
    return tuple(DeepGroove(*row) for row in rows)


def _tapered(*rows: tuple) -> tuple[TaperedRoller, ...]:
    return tuple(TaperedRoller(*row) for row in rows)


def _seals(*rows: tuple) -> tuple[ShaftSeal, ...]:
    return tuple(ShaftSeal(*row) for row in rows)


# designation, bore, outside, width, chamfer, dynamic N, static N, mass g
DEEP_GROOVE: tuple[DeepGroove, ...] = _deep(
    ("624", 4, 13, 5, 0.2, None, None, None),
    ("625", 5, 16, 5, 0.3, None, None, None),
    ("626", 6, 19, 6, 0.3, None, None, None),
    ("608", 8, 22, 7, 0.3, 3450, 1370, 12),
    ("6000", 10, 26, 8, 0.3, 4620, 1960, 19),
    ("6001", 12, 28, 8, 0.3, 5070, 2360, 22),
    ("6002", 15, 32, 9, 0.3, 5590, 2850, None),
    ("6003", 17, 35, 10, 0.3, 6050, 3250, None),
    ("6004", 20, 42, 12, 0.6, 9360, 5000, None),
    ("6005", 25, 47, 12, 0.6, 11200, 6550, None),
    ("6006", 30, 55, 13, 1.0, 13300, 8300, None),
    ("6007", 35, 62, 14, 1.0, 15900, 10200, None),
    ("6008", 40, 68, 15, 1.0, 16800, 11600, None),
    ("6009", 45, 75, 16, 1.0, 20800, 14600, None),
    ("6010", 50, 80, 16, 1.0, 21600, 16000, None),
    ("6200", 10, 30, 9, 0.6, 5070, 2360, None),
    ("6201", 12, 32, 10, 0.6, 6890, 3100, None),
    ("6202", 15, 35, 11, 0.6, 7800, 3750, None),
    ("6203", 17, 40, 12, 0.6, 9560, 4750, None),
    ("6204", 20, 47, 14, 1.0, 12700, 6550, 106),
    ("6205", 25, 52, 15, 1.0, 14000, 7800, 128),
    ("6206", 30, 62, 16, 1.0, 19500, 11200, None),
    ("6207", 35, 72, 17, 1.1, 25500, 15300, None),
    ("6208", 40, 80, 18, 1.1, 30700, 19000, None),
    ("6209", 45, 85, 19, 1.1, 33200, 21600, None),
    ("6210", 50, 90, 20, 1.1, 35100, 23200, None),
    ("6300", 10, 35, 11, 0.6, 8060, 3400, None),
    ("6301", 12, 37, 12, 0.6, 9750, 4150, None),
    ("6302", 15, 42, 13, 1.0, 11400, 5400, None),
    ("6303", 17, 47, 14, 1.0, 13500, 6550, None),
    ("6304", 20, 52, 15, 1.1, 15900, 7800, None),
    ("6305", 25, 62, 17, 1.1, 22500, 11600, None),
    ("6306", 30, 72, 19, 1.1, 28100, 16000, None),
    ("6307", 35, 80, 21, 1.5, 33200, 19000, None),
    ("6308", 40, 90, 23, 1.5, 41000, 24000, None),
    ("6309", 45, 100, 25, 1.5, 52700, 31500, None),
    ("6310", 50, 110, 27, 2.0, 61800, 38000, None),
)

# Old bd_warehouse size code, bore-outside-width, kept so a saved session still opens.
_DEEP_GROOVE_LEGACY = {
    "M4-13-5": "624",
    "M5-16-5": "625",
    "M6-19-6": "626",
    "M8-22-7": "608",
    "M10-26-8": "6000",
    "M12-28-8": "6001",
    "M10-30-9": "6200",
}

# designation, bore, outside, T, cone B, cup C, chamfer, ISO 355 series, dynamic N, static N, source
TAPERED: tuple[TaperedRoller, ...] = _tapered(
    ("30202", 15, 35, 11.75, 11, 10, 0.6, "", 18500, 14600, "SKF"),
    ("30203", 17, 40, 13.25, 12, 11, 1.0, "2DB", 23400, 18600, "SKF"),
    ("30204", 20, 47, 15.25, 14, 12, 1.0, "2DB", 34100, 28000, "SKF"),
    ("30205", 25, 52, 16.25, 15, 13, 1.0, "3CC", 38100, 33500, "SKF"),
    ("30206", 30, 62, 17.25, 16, 14, 1.0, "3DB", 43500, 48000, "NTN"),
    ("30207", 35, 72, 18.25, 17, 15, 1.5, "3DB", 55500, 61500, "NTN"),
    ("30208", 40, 80, 19.75, 18, 16, 1.5, "3DB", 61000, 67000, "NTN"),
    ("30209", 45, 85, 20.75, 19, 16, 1.5, "3DB", 67500, 78500, "NTN"),
    ("30210", 50, 90, 21.75, 20, 17, None, "3DB", None, None, ""),
    ("32004X", 20, 42, 15, 15, 12, None, "3CC", 29700, 27000, "SKF"),
    ("32005X", 25, 47, 15, 15, 11.5, 1.0, "4CC", 33200, 32500, "SKF"),
    ("32006X", 30, 55, 17, 17, 13, None, "4CC", None, None, ""),
    ("32007X", 35, 62, 18, 18, 14, None, "4CC", None, None, ""),
    ("32008X", 40, 68, 19, 19, 14.5, None, "3CD", None, None, ""),
    ("32010X", 50, 80, 20, 20, 15.5, None, "3CC", None, None, ""),
)

_TAPERED_ALIASES = {
    "32004": "32004X",
    "32005": "32005X",
    "32006": "32006X",
    "32007": "32007X",
    "32008": "32008X",
    "32010": "32010X",
}

# ISO 6194-1:2007 Table 1, shaft diameters through 100 mm.
# Columns are shaft, housing bore, nominal width.
SHAFT_SEALS: tuple[ShaftSeal, ...] = _seals(
    (6, 16, 7),
    (6, 22, 7),
    (7, 22, 7),
    (8, 22, 7),
    (8, 24, 7),
    (9, 22, 7),
    (10, 22, 7),
    (10, 25, 7),
    (12, 24, 7),
    (12, 25, 7),
    (12, 30, 7),
    (15, 26, 7),
    (15, 30, 7),
    (15, 35, 7),
    (16, 30, 7),
    (18, 30, 7),
    (18, 35, 7),
    (20, 35, 7),
    (20, 40, 7),
    (22, 35, 7),
    (22, 40, 7),
    (22, 47, 7),
    (25, 40, 7),
    (25, 47, 7),
    (25, 52, 7),
    (28, 40, 7),
    (28, 47, 7),
    (28, 52, 7),
    (30, 42, 7),
    (30, 47, 7),
    (30, 52, 7),
    (32, 45, 8),
    (32, 47, 8),
    (32, 52, 8),
    (35, 50, 8),
    (35, 52, 8),
    (35, 55, 8),
    (38, 55, 8),
    (38, 58, 8),
    (38, 62, 8),
    (40, 55, 8),
    (40, 62, 8),
    (42, 55, 8),
    (42, 62, 8),
    (45, 62, 8),
    (45, 65, 8),
    (50, 65, 8),
    (50, 72, 8),
    (55, 72, 8),
    (55, 80, 8),
    (60, 80, 8),
    (60, 85, 8),
    (65, 85, 10),
    (65, 90, 10),
    (70, 90, 10),
    (70, 95, 10),
    (75, 95, 10),
    (75, 100, 10),
    (80, 100, 10),
    (80, 110, 10),
    (85, 110, 12),
    (85, 120, 12),
    (90, 120, 12),
    (95, 120, 12),
    (100, 125, 12),
)

# size, diameter, pitch, head diameter max, head height max, socket across flats, socket depth min
CAP_SCREWS: tuple[CapScrew, ...] = tuple(
    CapScrew(*row)
    for row in (
        ("M3", 3, 0.5, 5.5, 3, 2.5, 1.3),
        ("M4", 4, 0.7, 7, 4, 3, 2),
        ("M5", 5, 0.8, 8.5, 5, 4, 2.5),
        ("M6", 6, 1, 10, 6, 5, 3),
        ("M8", 8, 1.25, 13, 8, 6, 4),
        ("M10", 10, 1.5, 16, 10, 8, 5),
        ("M12", 12, 1.75, 18, 12, 10, 6),
        ("M14", 14, 2, 21, 14, 12, 7),
        ("M16", 16, 2, 24, 16, 14, 8),
        ("M20", 20, 2.5, 30, 20, 17, 10),
        ("M24", 24, 3, 36, 24, 19, 12),
    )
)

# size, diameter, pitch, across flats max, height max
HEX_NUTS: tuple[HexNut, ...] = tuple(
    HexNut(*row)
    for row in (
        ("M3", 3, 0.5, 5.5, 2.4),
        ("M4", 4, 0.7, 7, 3.2),
        ("M5", 5, 0.8, 8, 4.7),
        ("M6", 6, 1, 10, 5.2),
        ("M8", 8, 1.25, 13, 6.8),
        ("M10", 10, 1.5, 16, 8.4),
        ("M12", 12, 1.75, 18, 10.8),
        ("M14", 14, 2, 21, 12.8),
        ("M16", 16, 2, 24, 14.8),
        ("M20", 20, 2.5, 30, 18),
        ("M24", 24, 3, 36, 21.5),
    )
)

# size, nominal diameter, inside, outside, thickness
PLAIN_WASHERS: tuple[PlainWasher, ...] = tuple(
    PlainWasher(*row)
    for row in (
        ("M3", 3, 3.2, 7, 0.5),
        ("M4", 4, 4.3, 9, 0.8),
        ("M5", 5, 5.3, 10, 1),
        ("M6", 6, 6.4, 12, 1.6),
        ("M8", 8, 8.4, 16, 1.6),
        ("M10", 10, 10.5, 20, 2),
        ("M12", 12, 13, 24, 2.5),
        ("M14", 14, 15, 28, 2.5),
        ("M16", 16, 17, 30, 3),
        ("M20", 20, 21, 37, 3),
        ("M24", 24, 25, 44, 4),
    )
)

# Body length is a representative envelope so the motor is a solid. NEMA ICS 16
# does not fix it (designators L, LA, and LB are outside the standard).
STEPPERS: tuple[StepperMotor, ...] = (
    StepperMotor(
        designation="NEMA17",
        flange=17,
        bolt_circle_in=1.725,
        pilot_in=0.8661,
        pilot_depth_max_in=0.09,
        pilot_depth_min_in=0.03,
        flange_square_in=1.7,
        flange_circle_max_in=2.36,
        hole_thread="4-40 UNC",
        hole_diameter_in=0.112,
        shaft_in=0.1969,
        shaft_length_in=0.812,
        shaft_torque_lbf_in=1.0,
        body_length_mm=48,
    ),
    StepperMotor(
        designation="NEMA23",
        flange=23,
        bolt_circle_in=2.625,
        pilot_in=1.5000,
        pilot_depth_max_in=0.13,
        pilot_depth_min_in=0.06,
        flange_square_in=2.3,
        flange_circle_max_in=3.21,
        hole_thread="8-32 UNC",
        hole_diameter_in=0.164,
        shaft_in=0.2500,
        shaft_length_in=0.812,
        shaft_torque_lbf_in=1.35,
        body_length_mm=56,
    ),
)

# Published in-line planetary gearboxes. The input face is the NEMA ICS 16
# bolt circle and pilot. Input bore depth is the drawing's maximum motor-shaft
# engagement, which is shorter than the ICS 16 default shaft on the NEMA 17 unit.
GEARBOXES: tuple[Gearbox, ...] = (
    Gearbox(
        designation="S-MG17-G10",
        frame="NEMA17",
        ratio=10,
        flange_mm=42,
        body_length_mm=40,
        output_diameter_mm=8,
        output_length_mm=21.5,
        input_bore_mm=5,
        input_bore_depth_mm=10.5,
        pilot_mm=0.8661 * INCH,
        pilot_depth_mm=2.5,
        bolt_circle_mm=1.725 * INCH,
        hole_diameter_mm=3.3,
        torque_nm=5,
        mass_g=310,
        note=(
            "Published drawing: 42 mm flange, 40 mm body, 8 mm output shaft 21.5 mm long, "
            "5 mm input bore 6.5 to 10.5 mm deep, mass 310 g, ratio 10, permissible torque 5 N·m. "
            "The NEMA ICS 16 default shaft is 0.812 in long, so it is longer than this bore."
        ),
    ),
    Gearbox(
        designation="S-MG23-G10-D8",
        frame="NEMA23",
        ratio=10,
        flange_mm=57,
        body_length_mm=55.5,
        output_diameter_mm=14,
        output_length_mm=34,
        input_bore_mm=8,
        input_bore_depth_mm=14,
        pilot_mm=1.5 * INCH,
        pilot_depth_mm=3.5,
        bolt_circle_mm=2.625 * INCH,
        hole_diameter_mm=5.2,
        torque_nm=5,
        mass_g=None,
        note=(
            "Published drawing: 57 mm flange, bolt spacing 47.14 mm, body 55.5 mm, "
            "14 mm output shaft 34 mm long, 8 mm input bore for a shaft 8 to 14 mm long, ratio 10. "
            "The NEMA ICS 16 default shaft is 0.250 in, so this bore is for the 8 mm shaft variant."
        ),
    ),
)

_LBF_IN_TO_NM = 0.112984829


def stepper_mm(motor: StepperMotor) -> dict[str, float]:
    """NEMA ICS 16 inch values converted to millimeters."""
    return {
        "bolt_circle_mm": motor.bolt_circle_in * INCH,
        "pilot_mm": motor.pilot_in * INCH,
        "pilot_depth_mm": motor.pilot_depth_max_in * INCH,
        "pilot_depth_min_mm": motor.pilot_depth_min_in * INCH,
        "flange_mm": motor.flange_square_in * INCH,
        "flange_circle_max_mm": motor.flange_circle_max_in * INCH,
        "hole_diameter_mm": motor.hole_diameter_in * INCH,
        "shaft_mm": motor.shaft_in * INCH,
        "shaft_length_mm": motor.shaft_length_in * INCH,
        "shaft_torque_nm": motor.shaft_torque_lbf_in * _LBF_IN_TO_NM,
        "body_length_mm": motor.body_length_mm,
    }


def _key(text: str) -> str:
    return "".join(text.upper().split()).replace("×", "X")


def deep_groove(size: str) -> DeepGroove | None:
    token = _key(size)
    token = _DEEP_GROOVE_LEGACY.get(token, token)
    for row in DEEP_GROOVE:
        if row.designation == token:
            return row
    return None


def tapered(size: str) -> TaperedRoller | None:
    token = _key(size)
    token = _TAPERED_ALIASES.get(token, token)
    for row in TAPERED:
        if row.designation.upper() == token:
            return row
    return None


def shaft_seal(size: str) -> ShaftSeal | None:
    token = _key(size).replace("*", "X")
    for row in SHAFT_SEALS:
        if _key(row.designation) == token:
            return row
    return None


def _metric_size(size: str) -> tuple[str, float | None]:
    token = size.strip().upper().replace(" ", "")
    if "-" not in token:
        return token, None
    head, pitch = token.split("-", 1)
    try:
        return head, float(pitch)
    except ValueError:
        return token, None


def cap_screw(size: str) -> CapScrew | None:
    head, pitch = _metric_size(size)
    for row in CAP_SCREWS:
        if row.size == head and (pitch is None or abs(pitch - row.pitch_mm) < 1e-6):
            return row
    return None


def hex_nut(size: str) -> HexNut | None:
    head, pitch = _metric_size(size)
    for row in HEX_NUTS:
        if row.size == head and (pitch is None or abs(pitch - row.pitch_mm) < 1e-6):
            return row
    return None


def plain_washer(size: str) -> PlainWasher | None:
    head, _pitch = _metric_size(size)
    for row in PLAIN_WASHERS:
        if row.size == head:
            return row
    return None


def stepper(size: str) -> StepperMotor | None:
    token = _key(size).replace("-", "")
    aliases = {"17": "NEMA17", "23": "NEMA23"}
    token = aliases.get(token, token)
    for row in STEPPERS:
        if _key(row.designation) == token:
            return row
    return None


def gearbox(size: str) -> Gearbox | None:
    token = _key(size).replace("-", "")
    for row in GEARBOXES:
        if _key(row.designation).replace("-", "") == token:
            return row
    return None


def legacy_bearing_code(row: DeepGroove) -> str | None:
    for code, designation in _DEEP_GROOVE_LEGACY.items():
        if designation == row.designation:
            return code
    return None
