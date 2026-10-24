"""Involute gears: spur, helical, straight bevel, spiral bevel, and hypoid.

A hypoid is an offset-axis ring and pinion. The ring tooth is a face-mill
blade. The pinion is the blank left after that blade is rolled at the tooth ratio.
"""

from aicad.gears.geometry import (
    INTERSECTING,
    OFFSET,
    PARALLEL,
    families,
    gear_data,
    mesh_placement,
    normalize_spec,
    pair_geometry,
    profile_points,
    register_family,
)
from aicad.gears.geometry import compute_bevel, compute_cylindrical
from aicad.gears.hypoid import compute_hypoid
from aicad.gears.meshcheck import (
    check_pair,
    register_mesh_check,
    registered_mesh_checks,
    unregister_mesh_check,
)
from aicad.gears.solid import build_bevel, build_cylindrical, build_gear, build_hypoid

__all__ = [
    "INTERSECTING",
    "OFFSET",
    "PARALLEL",
    "build_gear",
    "check_pair",
    "families",
    "gear_data",
    "mesh_placement",
    "normalize_spec",
    "pair_geometry",
    "profile_points",
    "register_family",
    "register_mesh_check",
    "registered_mesh_checks",
    "unregister_mesh_check",
]


def _register() -> None:
    if "spur" in families():
        return
    register_family(
        "spur",
        PARALLEL,
        compute_cylindrical,
        build_cylindrical,
        "External spur gear. ISO 21771 tooth proportions and an ISO 53 rack.",
    )
    register_family(
        "helical",
        PARALLEL,
        compute_cylindrical,
        build_cylindrical,
        "External helical gear. The transverse involute rides a constant-lead helicoid.",
    )
    register_family(
        "straight_bevel",
        INTERSECTING,
        compute_bevel,
        build_bevel,
        "Straight bevel gear. ISO 23509 pitch cones and an AGMA 2005 virtual spur.",
    )
    register_family(
        "spiral_bevel",
        INTERSECTING,
        compute_bevel,
        build_bevel,
        "Spiral bevel gear. The same virtual spur, rotated by a constant spiral angle.",
    )
    register_family(
        "hypoid",
        OFFSET,
        compute_hypoid,
        build_hypoid,
        "Hypoid ring and pinion. The ring tooth is a face-mill blade; the pinion is the blank that blade leaves.",
    )


_register()
