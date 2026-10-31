"""Circle geometry for the three arc tools.

A three-point arc is the circle through the picks. A center-start-end arc
uses the center the person picked. A tangent arc continues a line: its
circle is perpendicular to that line at the shared end, and the sweep
leaves in the line's direction. The same numbers are what the sketch
canvas stores as the initial guess.
"""

from __future__ import annotations

import math


def circle_through(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]):
    """Center and radius of the circle through three points, or None when they are colinear."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    determinant = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(determinant) < 1e-8:
        return None
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / determinant
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / determinant
    radius = math.hypot(ax - ux, ay - uy)
    if radius < 1e-6:
        return None
    return (ux, uy), radius


def sweep_contains(
    center: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
    point: tuple[float, float],
) -> bool:
    """True when ``point`` lies on the counterclockwise sweep from start to end."""
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    point_angle = math.atan2(point[1] - center[1], point[0] - center[0])
    sweep = (end_angle - start_angle) % (2.0 * math.pi)
    if sweep < 1e-6:
        return False
    delta = (point_angle - start_angle) % (2.0 * math.pi)
    return delta <= sweep + 1e-6


def three_point_order(
    center: tuple[float, float],
    start: tuple[float, float],
    middle: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    """True when the counterclockwise arc from start to end already contains the middle point."""
    return sweep_contains(center, start, end, middle)


def tangent_continuation(
    line_start: tuple[float, float],
    line_end: tuple[float, float],
    at_end: bool,
    target: tuple[float, float],
):
    """Center, radius, and whether the arc entity starts at the line point.

    The continuation leaves the line in the direction it was drawn. A left
    turn is stored from the line point to the target. A right turn is the
    same circle stored so the counterclockwise entity still bulges forward.
    Returns None when the target sits on the line and no circle fits.
    """
    if at_end:
        origin = line_end
        dx = line_end[0] - line_start[0]
        dy = line_end[1] - line_start[1]
    else:
        origin = line_start
        dx = line_start[0] - line_end[0]
        dy = line_start[1] - line_end[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return None
    dx /= length
    dy /= length
    normal_x, normal_y = -dy, dx
    offset_x = target[0] - origin[0]
    offset_y = target[1] - origin[1]
    along = normal_x * offset_x + normal_y * offset_y
    if abs(along) < 1e-9:
        return None
    signed = (offset_x * offset_x + offset_y * offset_y) / (2.0 * along)
    if abs(signed) < 1e-6:
        return None
    center = (origin[0] + signed * normal_x, origin[1] + signed * normal_y)
    return {
        "center": center,
        "radius": abs(signed),
        "start_at_line": signed > 0,
    }
