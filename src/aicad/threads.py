"""Modeled screw threads: a lead screw and its nut, or a metric fastener thread.

A design script cannot import bd_warehouse, which has the thread geometry,
so threads were written by hand in build123d for every screw. This module
builds them with bd_warehouse, loaded the way aicad loads it for fasteners,
and a script reaches it through ``aicad.threads``. A shape operation with
``"shape": "thread"`` uses the same functions: ``add`` makes a threaded rod,
``cut`` a tapped hole.

Each solid is built along +Z from z = 0 to ``length``, its axis on Z. A screw
and a nut made with the same numbers and both starting at z = 0 mesh. A nut
moved d along the axis meshes once it is also turned 360 * d / lead degrees
about it, the way it turns as it is screwed on (lead = pitch * starts).

Profiles:

- ``trapezoidal``: a lead-screw thread with flank ``angle`` (default 30, a
  metric Tr thread; 29 is ACME), ``starts`` (a two-start screw advances two
  pitches a turn), any diameter and pitch. Its flat crests print well.
- ``iso``: a metric ISO 68-1 fastener thread, ``diameter`` the major diameter,
  one start.

``clearance_mm`` is taken off each flank of a screw and added to a nut's
(bd_warehouse's manufacturing compensation), for the clearance a printed
pair needs. ``ends`` finishes each end: ``fade`` (the thread runs out
smoothly; default), ``square``, ``chamfer``, or ``raw``.
"""

from __future__ import annotations

import math

from aicad.errors import GeometryError

_PROFILES = ("trapezoidal", "iso")
_ENDS = ("fade", "square", "chamfer", "raw")


def screw(
    diameter: float,
    pitch: float,
    length: float,
    *,
    profile: str = "trapezoidal",
    angle: float = 30.0,
    starts: int = 1,
    hand: str = "right",
    clearance_mm: float = 0.0,
    ends: str = "fade",
):
    """A threaded rod: the core at the thread's root and the thread fused onto it."""
    from build123d import Cylinder, Pos

    thread = _thread(diameter, pitch, length, profile, angle, starts, hand, clearance_mm, ends, external=True)
    core = Pos(0, 0, length / 2.0) * Cylinder(float(thread.root_radius) + 1e-3, length)
    return core + thread


def nut_tool(
    diameter: float,
    pitch: float,
    length: float,
    *,
    profile: str = "trapezoidal",
    angle: float = 30.0,
    starts: int = 1,
    hand: str = "right",
    clearance_mm: float = 0.0,
    ends: str = "square",
):
    """What to cut from a body for a tapped hole: a bore at the thread's root, less the teeth."""
    from build123d import Cylinder, Pos

    from build123d import Rot

    thread = _thread(diameter, pitch, length, profile, angle, starts, hand, clearance_mm, ends, external=False)
    # bd_warehouse starts an internal thread's teeth where an external thread's teeth are. Half a
    # pitch round, they sit in the screw's grooves, so a screw and a nut from one height mesh.
    thread = Rot(0, 0, 180.0 / int(starts)) * thread
    bore = Pos(0, 0, length / 2.0) * Cylinder(float(thread.root_radius), length)
    return bore - thread


def thread_info(diameter: float, pitch: float, *, profile: str = "trapezoidal", angle: float = 30.0, starts: int = 1) -> dict:
    """The numbers a design needs: lead, helix angle, and the root and crest diameters."""
    _check(diameter, pitch, 1.0, profile, angle, starts, "right", 0.0, "fade")
    thread = _thread(diameter, pitch, max(pitch * starts, 1.0), profile, angle, starts, "right", 0.0, "raw", external=True)
    lead = pitch * starts
    mean = float(thread.root_radius + thread.apex_radius)
    return {
        "lead_mm": lead,
        "helix_angle_deg": math.degrees(math.atan(lead / (math.pi * mean))),
        "root_diameter_mm": 2.0 * float(thread.root_radius),
        "crest_diameter_mm": 2.0 * float(thread.apex_radius),
    }


def thread_op(op: dict, label: str):
    """The solid for a ``thread`` shape operation, placed at ``at`` along ``axis``.

    ``add`` gives a threaded rod, ``cut`` the tool for a tapped hole.
    """
    from build123d import Plane

    from aicad.kernel import finite_number, finite_vec3

    at = finite_vec3(op.get("at", [0, 0, 0]), f"{label} at")
    axis = finite_vec3(op.get("axis", [0, 0, 1]), f"{label} axis")
    if math.sqrt(sum(value * value for value in axis)) < 1e-9:
        raise GeometryError(f"{label}: the thread axis must be non-zero.")
    arguments = {
        "diameter": finite_number(op.get("diameter"), f"{label} diameter", positive=True),
        "pitch": finite_number(op.get("pitch"), f"{label} pitch", positive=True),
        "length": finite_number(op.get("length"), f"{label} length", positive=True),
        "profile": str(op.get("profile", "trapezoidal")),
        "angle": finite_number(op.get("angle", 30.0), f"{label} angle", positive=True),
        "starts": op.get("starts", 1),
        "hand": str(op.get("hand", "right")),
        "clearance_mm": finite_number(op.get("clearance_mm", 0.0), f"{label} clearance_mm", bound=None),
    }
    if op.get("ends") is not None:
        arguments["ends"] = str(op["ends"])
    solid = screw(**arguments) if op.get("op") == "add" else nut_tool(**arguments)
    return Plane(origin=at, z_dir=axis) * solid


def _check(diameter, pitch, length, profile, angle, starts, hand, clearance_mm, ends) -> None:
    for value, name in ((diameter, "diameter"), (pitch, "pitch"), (length, "length")):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise GeometryError(f"A thread's {name} must be a positive number of millimetres.")
    if profile not in _PROFILES:
        raise GeometryError(f"Thread profile {profile!r} is unknown. Use {', '.join(_PROFILES)}.")
    if not isinstance(starts, int) or isinstance(starts, bool) or not 1 <= starts <= 8:
        raise GeometryError("A thread has 1 to 8 starts.")
    if profile == "iso" and starts != 1:
        raise GeometryError("An ISO fastener thread has one start. Use profile trapezoidal for a multi-start lead screw.")
    if not 0 < float(angle) < 90:
        raise GeometryError("A thread's flank angle is above 0 and below 90 degrees, such as 30 (metric Tr) or 29 (ACME).")
    if hand not in ("right", "left"):
        raise GeometryError("A thread's hand is right or left.")
    if not isinstance(clearance_mm, (int, float)) or not 0 <= clearance_mm < pitch / 4:
        raise GeometryError("clearance_mm is at least 0 and less than a quarter of the pitch.")
    if ends not in _ENDS:
        raise GeometryError(f"A thread's ends are one of {', '.join(_ENDS)}.")
    if pitch * starts >= math.pi * diameter:
        raise GeometryError("The lead (pitch times starts) is longer than the thread's circumference.")


def _thread(diameter, pitch, length, profile, angle, starts, hand, clearance_mm, ends, *, external: bool):
    _check(diameter, pitch, length, profile, angle, starts, hand, clearance_mm, ends)
    import os

    if not getattr(os.environ, "_aicad_locked", False):
        # In a design script's sandbox the environment was scrubbed before the
        # script started and is locked, so the loader's own scrub is not needed.
        from aicad.library import _load_bd_warehouse

        _load_bd_warehouse()
    from bd_warehouse.thread import IsoThread, TrapezoidalThread

    finishes = (ends, ends)
    try:
        if profile == "iso":
            thread = IsoThread(
                major_diameter=float(diameter),
                pitch=float(pitch),
                length=float(length),
                external=external,
                hand=hand,
                end_finishes=finishes,
                manufacturing_compensation=float(clearance_mm),
            )
        else:
            thread = TrapezoidalThread(
                diameter=float(diameter),
                pitch=float(pitch),
                thread_angle=float(angle),
                length=float(length),
                external=external,
                starts=int(starts),
                hand=hand,
                end_finishes=finishes,
                manufacturing_compensation=float(clearance_mm),
            )
    except (ValueError, TypeError) as exc:
        raise GeometryError(f"The thread could not be built: {exc}") from exc
    if not thread.is_valid:
        raise GeometryError("The thread came out as an invalid solid. Try another pitch, angle, or end finish.")
    return thread
