"""Published size tables used by gear bores.

DIN 6885-1 parallel-key cross-sections and ISO 14 straight-sided spline
nominal sizes. The numbers are the nominal dimensions from those standards,
not a fit class.
"""

from __future__ import annotations

# DIN 6885-1:1968 parallel keys. Each row is (d_max_mm, width_mm, height_mm,
# shaft_depth_t1_mm, hub_depth_t2_mm). The first row covers 6 mm through 8 mm.
# Every later row covers the diameters above the previous upper bound through
# its own upper bound. A bore of exactly 22 mm uses the 6 mm key.
DIN_6885_KEYS: tuple[tuple[float, float, float, float, float], ...] = (
    (8, 2, 2, 1.2, 1.0),
    (10, 3, 3, 1.8, 1.4),
    (12, 4, 4, 2.5, 1.8),
    (17, 5, 5, 3.0, 2.3),
    (22, 6, 6, 3.5, 2.8),
    (30, 8, 7, 4.0, 3.3),
    (38, 10, 8, 5.0, 3.3),
    (44, 12, 8, 5.0, 3.3),
    (50, 14, 9, 5.5, 3.8),
    (58, 16, 10, 6.0, 4.3),
    (65, 18, 11, 7.0, 4.4),
    (75, 20, 12, 7.5, 4.9),
    (85, 22, 14, 9.0, 5.4),
    (95, 25, 14, 9.0, 5.4),
    (110, 28, 16, 10.0, 6.4),
    (130, 32, 18, 11.0, 7.4),
    (150, 36, 20, 12.0, 8.4),
    (170, 40, 22, 13.0, 9.4),
    (200, 45, 25, 15.0, 10.4),
    (230, 50, 28, 17.0, 11.4),
)

# ISO 14:1982 Table 1. (minor d, count N, major D, width B) in millimetres.
# The designation is N x d x D. Medium 8 x 56 x 65 follows the major diameter
# in the table; the designation uses that major diameter.
ISO_14_LIGHT: tuple[tuple[float, int, float, float], ...] = (
    (23, 6, 26, 6),
    (26, 6, 30, 6),
    (28, 6, 32, 7),
    (32, 8, 36, 6),
    (36, 8, 40, 7),
    (42, 8, 46, 8),
    (46, 8, 50, 9),
    (52, 8, 58, 10),
    (56, 8, 62, 10),
    (62, 8, 68, 12),
    (72, 10, 78, 12),
    (82, 10, 88, 12),
    (92, 10, 98, 14),
    (102, 10, 108, 16),
    (112, 10, 120, 18),
)

ISO_14_MEDIUM: tuple[tuple[float, int, float, float], ...] = (
    (11, 6, 14, 3),
    (13, 6, 16, 3.5),
    (16, 6, 20, 4),
    (18, 6, 22, 5),
    (21, 6, 25, 5),
    (23, 6, 28, 6),
    (26, 6, 32, 6),
    (28, 6, 34, 7),
    (32, 8, 38, 6),
    (36, 8, 42, 7),
    (42, 8, 48, 8),
    (46, 8, 54, 9),
    (52, 8, 60, 10),
    (56, 8, 65, 10),
    (62, 8, 72, 12),
    (72, 10, 82, 12),
    (82, 10, 92, 12),
    (92, 10, 102, 14),
    (102, 10, 112, 16),
    (112, 10, 125, 18),
)

# DIN 5480-1 module series (DIN 780-1 / ISO 54 series used by that standard).
DIN_5480_MODULES: tuple[float, ...] = (
    0.5,
    0.6,
    0.75,
    0.8,
    1.0,
    1.25,
    1.5,
    1.75,
    2.0,
    2.5,
    3.0,
    4.0,
    5.0,
    6.0,
    8.0,
    10.0,
)


def din6885_key(bore_mm: float) -> dict[str, float]:
    """Nominal parallel-key section for a bore diameter, DIN 6885-1."""
    if bore_mm < 6 or bore_mm > 230:
        raise ValueError(bore_mm)
    previous = 6.0
    for d_max, width, height, t1, t2 in DIN_6885_KEYS:
        if bore_mm <= d_max + 1e-9:
            return {
                "bore_over_mm": previous if previous != 6 else 6.0,
                "bore_to_mm": d_max,
                "width_mm": width,
                "height_mm": height,
                "shaft_depth_mm": t1,
                "hub_depth_mm": t2,
            }
        previous = d_max
    raise ValueError(bore_mm)


def iso14_spline(series: str, minor_mm: float) -> dict[str, float | int | str]:
    """One ISO 14 nominal spline. ``series`` is ``light`` or ``medium``."""
    table = {"light": ISO_14_LIGHT, "medium": ISO_14_MEDIUM}.get(series)
    if table is None:
        raise ValueError(series)
    for minor, count, major, width in table:
        if abs(minor - minor_mm) <= 1e-9:
            return {
                "series": series,
                "count": count,
                "minor_mm": minor,
                "major_mm": major,
                "width_mm": width,
                "designation": f"{count} x { _num(minor) } x { _num(major) }",
            }
    raise ValueError(minor_mm)


def _num(value: float) -> str:
    text = f"{value:g}"
    return text
