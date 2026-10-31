"""Smooth curves through sketch points.

The points stay ordinary sketch points, so a distance or a fix still moves
them. The curve is rebuilt from the solved points. A closed curve is a
profile. Samples are for the picture; the solid uses the same points.
"""

from __future__ import annotations

import math


def spline_samples(
    coords: list[tuple[float, float]],
    closed: bool,
    per_span: int = 8,
) -> list[tuple[float, float]]:
    """Points on a Catmull-Rom curve through ``coords``."""
    if len(coords) < 2:
        return list(coords)
    count = len(coords)
    spans = count if closed and count >= 3 else count - 1
    samples: list[tuple[float, float]] = []
    for index in range(spans):
        if closed and count >= 3:
            p0 = coords[(index - 1) % count]
            p1 = coords[index % count]
            p2 = coords[(index + 1) % count]
            p3 = coords[(index + 2) % count]
        else:
            p1 = coords[index]
            p2 = coords[index + 1]
            p0 = coords[index - 1] if index > 0 else p1
            p3 = coords[index + 2] if index + 2 < count else p2
        steps = max(1, per_span)
        for step in range(steps):
            samples.append(_catmull(p0, p1, p2, p3, step / steps))
    samples.append(coords[0] if closed and count >= 3 else coords[-1])
    return samples


def same_cycle(left: list, right: list) -> bool:
    """True when both lists are the same loop, including a reversed start."""
    if len(left) != len(right) or len(left) < 3:
        return False
    if any(item in left[index + 1 :] for index, item in enumerate(left)):
        return False
    doubled = list(right) + list(right)
    for start in range(len(right)):
        if doubled[start : start + len(left)] == list(left):
            return True
        if doubled[start : start + len(left)] == list(reversed(left)):
            return True
    return False


def _catmull(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    t2 = t * t
    t3 = t2 * t
    def coord(index: int) -> float:
        return 0.5 * (
            (2 * p1[index])
            + (-p0[index] + p2[index]) * t
            + (2 * p0[index] - 5 * p1[index] + 4 * p2[index] - p3[index]) * t2
            + (-p0[index] + 3 * p1[index] - 3 * p2[index] + p3[index]) * t3
        )
    return (coord(0), coord(1))


def curve_length(samples: list[tuple[float, float]]) -> float:
    total = 0.0
    for index in range(1, len(samples)):
        total += math.hypot(samples[index][0] - samples[index - 1][0], samples[index][1] - samples[index - 1][1])
    return total
