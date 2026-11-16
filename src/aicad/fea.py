"""Linear static mechanics: Gmsh meshes, CalculiX solves.

An assistant picks a material, names the faces that are held and the faces
that are loaded, and reads the stress, the displacement, and a pass/fail
against a safety factor. This module does not invent a solver. Gmsh builds
the tetrahedra and CalculiX (``ccx``) runs the elastic solution.

Units stay millimeters and newtons, so stress comes out in megapascals.
"""

from __future__ import annotations

import contextlib
import ctypes
import math
import re
import signal
import sys
import threading
from pathlib import Path

import numpy as np

from aicad import kernel
from aicad.errors import GeometryError
from aicad.models import FeaReport, Vec3, round_mm
from aicad.render import render_scalar_views

_SOLVER = "CalculiX (ccx) + Gmsh"
_CCX_TIMEOUT_S = 180
# Above this many equations the direct solver (SPOOLES) can need more memory
# than a helper may use: it ran out of 4 GiB near 233,000 equations. The
# iterative Cholesky solver needs a small part of that and gives the same
# answer on a linear static case.
_DIRECT_SOLVER_EQUATIONS = 150_000
# About what the iterative solver finishes inside the time limit on a desktop
# (780,000 equations took 130 s on one core). A coarser mesh is suggested
# towards this size when a solve does not fit.
_ITERATIVE_SOLVER_EQUATIONS = 600_000
# Two selector matches this close along the normal, or from the axis, share a
# plane or a cylinder. Face records are rounded to 0.001 mm.
_SAME_SURFACE_MM = 0.01
_LOCK = threading.Lock()
_FRD_NUMBER = re.compile(r"[+-]?\d+\.\d+E[+-]\d+")
# Gmsh tet10 lists edge 2-3 before edge 1-3. CalculiX C3D10 wants the opposite.
_TET10_TO_C3D10 = (0, 1, 2, 3, 4, 5, 6, 7, 9, 8)
# The two corners of each C3D10 mid-edge node, in C3D10 order.
_C3D10_EDGES = ((4, 0, 1), (5, 1, 2), (6, 2, 0), (7, 0, 3), (8, 1, 3), (9, 2, 3))
STRAIGHT_EDGE_NOTE = (
    "The quadratic mesh had folded elements on curved faces, so each mid-edge node was put on the straight "
    "line between its corners. The elements are still quadratic. Curved faces are faceted at the mesh size."
)


def straight_sided(points: np.ndarray, elements: np.ndarray) -> np.ndarray:
    """Points with every C3D10 mid-edge node at the middle of its edge.

    A straight-sided quadratic tet has the same positive Jacobian as its corner tet, so this cannot fold
    where the linear mesh does not, and it keeps the quadratic displacement field.
    """
    out = np.array(points, dtype=float, copy=True)
    elements = np.asarray(elements)
    for mid, a, b in _C3D10_EDGES:
        out[elements[:, mid]] = 0.5 * (out[elements[:, a]] + out[elements[:, b]])
    return out

# Handbook ballparks, not certified allowables. Modulus and strength are MPa.
# `printed` materials get the layer-direction caveat in every report.
_MATERIALS: dict[str, dict[str, object]] = {
    "pla": {
        "name": "PLA",
        "youngs_modulus_mpa": 3500.0,
        "poisson": 0.36,
        "strength_mpa": 50.0,
        "strength_kind": "tensile",
        "across_layer_strength_mpa": 28.0,
        "density_g_cm3": 1.24,
        "printed": True,
        "note": (
            "Typical in-plane tensile values for PLA. Printed parts vary with temperature, pigment, and print settings. "
            "Across layers, typical printed PLA tests at about 25-35 MPa, roughly half to two thirds of in-plane: "
            "28 MPa is used for the across-layer safety factor."
        ),
    },
    "petg": {
        "name": "PETG",
        "youngs_modulus_mpa": 2000.0,
        "poisson": 0.40,
        "strength_mpa": 50.0,
        "strength_kind": "tensile",
        "across_layer_strength_mpa": 32.0,
        "density_g_cm3": 1.27,
        "printed": True,
        "note": (
            "Typical PETG tensile values. The real filament datasheet should replace these before a design is released. "
            "PETG bonds between layers better than PLA; about 32 MPa is used across layers."
        ),
    },
    "abs": {
        "name": "ABS",
        "youngs_modulus_mpa": 2300.0,
        "poisson": 0.35,
        "strength_mpa": 40.0,
        "strength_kind": "tensile",
        "density_g_cm3": 1.04,
        "printed": True,
        "note": "Typical ABS tensile values.",
    },
    "nylon": {
        "name": "nylon",
        "youngs_modulus_mpa": 1600.0,
        "poisson": 0.40,
        "strength_mpa": 45.0,
        "strength_kind": "tensile",
        "density_g_cm3": 1.14,
        "printed": True,
        "note": "Dry nylon, roughly PA6 or PA12. Moisture lowers both the modulus and the strength.",
    },
    "tpu_95a": {
        "name": "TPU 95A",
        "youngs_modulus_mpa": 26.0,
        "poisson": 0.48,
        "strength_mpa": 8.6,
        "strength_kind": "yield",
        "density_g_cm3": 1.22,
        "printed": True,
        "note": (
            "Flexible filament, Shore 95A. Typical filament datasheet values: tensile modulus 26 MPa, "
            "yield 8.6 MPa, density 1.22 g/cm³. The rubber stretches far past yield, so a linear stress "
            "check is only a small-strain estimate."
        ),
    },
    "aluminum_6061": {
        "name": "aluminum 6061-T6",
        "youngs_modulus_mpa": 68900.0,
        "poisson": 0.33,
        "strength_mpa": 276.0,
        "strength_kind": "yield",
        "density_g_cm3": 2.70,
        "printed": False,
        "note": "6061-T6 yield strength. The safety factor is against yield, not ultimate rupture.",
    },
    "steel": {
        "name": "mild steel",
        "youngs_modulus_mpa": 210000.0,
        "poisson": 0.30,
        "strength_mpa": 250.0,
        "strength_kind": "yield",
        "density_g_cm3": 7.85,
        "printed": False,
        "note": "A generic mild steel, near AISI 1020. The safety factor is against yield.",
    },
    "steel_42crmo4": {
        "name": "42CrMo4 quenched and tempered",
        "youngs_modulus_mpa": 210000.0,
        "poisson": 0.30,
        "strength_mpa": 750.0,
        "strength_kind": "yield",
        "ultimate_mpa": 1000.0,
        "density_g_cm3": 7.85,
        "printed": False,
        "endurance": "steel",
        "note": (
            "Through-hardened gear and shaft steel. EN 10083-3:2006, 42CrMo4+QT, "
            "minimum 0.2% proof strength 750 MPa and tensile strength 1000 MPa to 1200 MPa "
            "for a ruling section from 16 mm to 40 mm. The 1000 MPa figure is the bottom of that band. "
            "Young's modulus 210000 MPa, Poisson's ratio 0.30, and density 7.85 g/cm³ are the steel "
            "figures already used for mild steel in this library. The safety factor is against the proof strength."
        ),
    },
    "steel_16mncr5": {
        "name": "16MnCr5 case-hardened",
        "youngs_modulus_mpa": 210000.0,
        "poisson": 0.30,
        "strength_mpa": 590.0,
        "strength_kind": "yield",
        "ultimate_mpa": 780.0,
        "density_g_cm3": 7.85,
        "printed": False,
        "endurance": "steel",
        "note": (
            "Case-hardening gear steel 16MnCr5, EN 10084:2008. That standard specifies composition "
            "and hardenability, not a finished core yield. The 590 MPa proof strength and 780 MPa "
            "tensile strength are handbook figures for the core of a small section, not the carburized case. "
            "The case is harder than this core. Modulus, Poisson's ratio, and density match the steel "
            "figures in this library. The safety factor is against the core proof strength."
        ),
    },
    "aluminum_7075": {
        "name": "aluminum 7075-T6",
        "youngs_modulus_mpa": 71700.0,
        "poisson": 0.33,
        "strength_mpa": 503.0,
        "strength_kind": "yield",
        "ultimate_mpa": 572.0,
        "density_g_cm3": 2.81,
        "printed": False,
        # Typical 7075-T6 sheet and bar values in the ASM aluminum handbook data:
        # tensile yield 503 MPa, ultimate 572 MPa, modulus 71.7 GPa, density 2.81 g/cm3.
        "note": (
            "7075-T6, typical handbook values: yield 503 MPa, ultimate 572 MPa. A design allowable "
            "(MMPDS A- or B-basis) for the actual product form and thickness is lower. Weaker in "
            "fatigue and more notch-sensitive than 6061, and stress-corrosion prone across the grain. "
            "The safety factor is against yield."
        ),
    },
    "titanium_6al4v": {
        "name": "titanium Ti-6Al-4V (grade 5, annealed)",
        "youngs_modulus_mpa": 113800.0,
        "poisson": 0.342,
        "strength_mpa": 880.0,
        "strength_kind": "yield",
        "ultimate_mpa": 950.0,
        "density_g_cm3": 4.43,
        "printed": False,
        # Typical annealed Ti-6Al-4V (ASTM B348 grade 5) in the ASM titanium data:
        # yield 880 MPa, ultimate 950 MPa, modulus 113.8 GPa, Poisson 0.342, density 4.43 g/cm3.
        "note": (
            "Ti-6Al-4V grade 5, annealed, typical handbook values: yield 880 MPa, ultimate 950 MPa. "
            "Additively made or solution-treated material differs; use its own datasheet. "
            "The safety factor is against yield."
        ),
    },
    "magnesium_az31b": {
        "name": "magnesium AZ31B-H24",
        "youngs_modulus_mpa": 45000.0,
        "poisson": 0.35,
        "strength_mpa": 220.0,
        "strength_kind": "yield",
        "ultimate_mpa": 290.0,
        "density_g_cm3": 1.77,
        "printed": False,
        # Typical AZ31B-H24 sheet (ASTM B90) in the ASM magnesium data: tensile yield
        # about 220 MPa, ultimate 290 MPa, modulus 45 GPa, density 1.77 g/cm3.
        "note": (
            "AZ31B-H24 sheet, typical handbook values: tensile yield 220 MPa, ultimate 290 MPa. "
            "Compressive yield is much lower than tensile in wrought magnesium (often about 60 %), "
            "and extrusions and castings are weaker. The safety factor is against tensile yield."
        ),
    },
    "dyneema": {
        "name": "Dyneema (UHMWPE fibre)",
        "youngs_modulus_mpa": 109000.0,
        "poisson": 0.35,
        "strength_mpa": 3400.0,
        "strength_kind": "tensile",
        "density_g_cm3": 0.975,
        "printed": False,
        "line": True,
        # Dyneema SK75/SK78 fibre datasheet range: modulus 109-132 GPa, tenacity
        # about 3.5 N/tex (about 3.4 GPa at 0.975 g/cm3). The low end is used.
        "note": (
            "Dyneema (UHMWPE) fibre, low end of the SK75/SK78 datasheet range: modulus 109 GPa, "
            "strength about 3.4 GPa, density 0.975 g/cm³. These are fibre numbers. A braided line "
            "stretches more and breaks lower per area, so a tendon should state its line's own "
            "modulus_mpa when the datasheet gives one. Dyneema creeps under a held load. It is a line "
            "material, not a solid: a strength or vibration study refuses it."
        ),
    },
    "grey_cast_iron": {
        "name": "grey cast iron, ASTM A48 Class 30",
        "youngs_modulus_mpa": 100000.0,
        "poisson": 0.26,
        "strength_mpa": 207.0,
        "strength_kind": "tensile",
        "density_g_cm3": 7.15,
        "printed": False,
        # Sources: ASTM A48/A48M-22 Table 1 sets Class 30 by a minimum tensile
        # strength of 30 ksi (207 MPa) in separately cast test bars. Modulus
        # 14.5 Msi (100 GPa), Poisson's ratio 0.26, density 0.258 lb/in3
        # (7.15 g/cm3), and compressive strength 109 ksi (752 MPa) are the typical
        # Class 30 figures in the AFS gray iron property comparison and foundry
        # data sheets. Grey iron's modulus falls with stress, so 100 GPa is the
        # low end of the published 100-117 GPa range.
        "note": (
            "Grey (gray) cast iron, ASTM A48 Class 30, close to EN-GJL-200 (EN 1561). "
            "207 MPa is the ASTM A48 minimum tensile strength of a separately cast test bar; a thick "
            "casting section can be weaker. Grey iron is brittle and has no yield point, so the safety "
            "factor is against tensile rupture. The check compares von Mises stress with the tensile "
            "strength, which is conservative where the part is in compression (about 750 MPa) but does "
            "not model crack growth from graphite flakes. Modulus 100 GPa, Poisson's ratio 0.26, and "
            "density 7.15 g/cm³ are typical Class 30 values; the modulus falls as stress rises."
        ),
    },
}
_ALIASES = {
    "7075": "aluminum_7075",
    "al7075": "aluminum_7075",
    "7075_t6": "aluminum_7075",
    "titanium": "titanium_6al4v",
    "ti6al4v": "titanium_6al4v",
    "ti_6al_4v": "titanium_6al4v",
    "grade_5_titanium": "titanium_6al4v",
    "magnesium": "magnesium_az31b",
    "az31b": "magnesium_az31b",
    "az31": "magnesium_az31b",
    "uhmwpe": "dyneema",
    "dyneema_sk78": "dyneema",
    "dyneema_sk75": "dyneema",
    "aluminum": "aluminum_6061",
    "aluminium": "aluminum_6061",
    "6061": "aluminum_6061",
    "al6061": "aluminum_6061",
    "pa6": "nylon",
    "pa12": "nylon",
    "tpu": "tpu_95a",
    "tpu95a": "tpu_95a",
    "mild_steel": "steel",
    "42crmo4": "steel_42crmo4",
    "16mncr5": "steel_16mncr5",
    "cast_iron": "grey_cast_iron",
    "grey_iron": "grey_cast_iron",
    "gray_iron": "grey_cast_iron",
    "gray_cast_iron": "grey_cast_iron",
    "astm_a48_class_30": "grey_cast_iron",
    "a48_class_30": "grey_cast_iron",
    "en_gjl_200": "grey_cast_iron",
}


def fea_available() -> tuple[bool, str]:
    """Whether this machine can mesh with Gmsh and solve with CalculiX."""
    from aicad import prereqs

    status = prereqs.ccx_status()
    if not status.found:
        return False, status.message
    try:
        import gmsh  # noqa: F401
    except ImportError:
        return False, "The gmsh Python module is not installed. pip install gmsh."
    return True, "Gmsh and CalculiX are available."


def list_materials() -> dict[str, object]:
    """The small material library, with the numbers the solver will use."""
    return {
        "units": {"modulus": "MPa", "strength": "MPa", "length": "mm"},
        "materials": [
            {"id": key, **{k: v for k, v in value.items()}}
            for key, value in _MATERIALS.items()
        ],
        "note": (
            "These are typical handbook numbers so an assistant can start a study. "
            "They are not a certified allowable. Replace them with the datasheet before release."
        ),
    }


def lookup_material(name: str) -> dict[str, object]:
    if not isinstance(name, str) or not name.strip():
        known = ", ".join(_MATERIALS)
        raise GeometryError(f"Name a material. Known materials: {known}.")
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    key = _ALIASES.get(key, key)
    if key not in _MATERIALS:
        known = ", ".join(_MATERIALS)
        raise GeometryError(f"No material {name!r}. Known materials: {known}.")
    return {"id": key, **_MATERIALS[key]}


def solid_material(name: str) -> dict[str, object]:
    """A library material a solid part can be made of. A line material such as Dyneema is refused."""
    material = lookup_material(name)
    if material.get("line"):
        raise GeometryError(
            f"{material['name']} is a line material for a tendon or a cord, not a solid, so it cannot be "
            "used in a strength or vibration study. Choose the part's own material."
        )
    return material


def select_faces(catalog: list[dict], selector: dict, shape: kernel.Shape | None = None) -> list[dict]:
    """Pick faces from :func:`aicad.kernel.describe_faces` output.

    ``kind`` is ``plane``, ``cylinder``, ``nearest``, or ``index``.

    A plane selector uses the outward ``normal``. Pass ``at`` to take the
    matching plane closest to that point; omit ``at`` to take every matching
    plane. When several faces lie in that plane, ``at`` takes the one nearest
    the point. A cylinder selector matches ``radius`` (millimeters, default
    tolerance 0.05) and an optional ``axis``; faces on one axis are told apart
    the same way. Omit ``at`` to take every match, which is how "the four
    frame holes" is one selector. ``nearest`` needs ``at`` and returns the face
    nearest the point. ``all: true`` forces every match instead of the single
    closest face.

    With ``shape``, the catalog's solid, "nearest the point" is the distance
    from the point to the face itself, so a point on an L-shaped or ring face
    takes that face even when another face's center is closer. Without it, or
    for a viewport ref, whose point is the face's own center, it is the
    distance to each face's center.
    """
    if not isinstance(selector, dict):
        raise GeometryError(
            "A face selector needs kind: plane, cylinder, nearest, or index, or a ref from the viewport."
        )
    if selector.get("ref"):
        from aicad.refs import ref_to_selector

        expanded = ref_to_selector(str(selector["ref"]))
        if expanded.get("kind") == "edge":
            raise GeometryError(f"Reference {selector['ref']!r} is an edge. A load or a support needs a face.")
        selector = {**expanded, **{key: value for key, value in selector.items() if key not in ("ref",)}}
        # A ref's point is the center of the face it names, so centers tell it apart exactly.
        shape = None
    if "kind" not in selector:
        raise GeometryError(
            "A face selector needs kind: plane, cylinder, nearest, or index, or a ref from the viewport."
        )
    kind = selector["kind"]
    if kind == "index":
        index = _integer(selector.get("index"), "face index")
        matches = [face for face in catalog if face["index"] == index]
        if not matches:
            raise GeometryError(f"This part has no face index {index}.")
        return matches
    if kind == "nearest":
        point = _vec(selector.get("at"), "nearest at")
        if not catalog:
            return []
        apart = _face_distances(shape, catalog, point)
        return [min(catalog, key=lambda face: (round(apart(face), 6), _distance(face["center_mm"], point)))]
    if kind == "plane":
        direction = _unit(_vec(selector.get("normal"), "plane normal"))
        matches = [
            face
            for face in catalog
            if face["kind"] == "plane"
            and face.get("normal")
            and _dot(face["normal"], direction) > 0.9
        ]
        return _narrow(matches, selector, _plane_distance, direction, shape)
    if kind == "cylinder":
        tolerance = _optional_float(selector.get("tolerance"), 0.05)
        axis = _unit(_vec(selector["axis"], "cylinder axis")) if selector.get("axis") else None
        radius = _optional_float(selector.get("radius"), None) if "radius" in selector else None
        matches = []
        for face in catalog:
            if face["kind"] != "cylinder" or face.get("radius_mm") is None:
                continue
            if radius is not None and abs(float(face["radius_mm"]) - radius) > tolerance:
                continue
            if axis is not None and abs(_dot(face["axis_direction"], axis)) < 0.9:
                continue
            matches.append(face)
        return _narrow(matches, selector, _axis_distance, axis, shape)
    raise GeometryError(
        f"Unknown face selector {kind!r}. Use plane, cylinder, nearest, or index."
    )


def solve_part(
    shape: kernel.Shape,
    setup: dict,
    out_dir: Path | None = None,
    stem: str = "part",
    part_name: str | None = None,
    probes_mm: list | None = None,
) -> FeaReport:
    """Mesh a solid and run a linear static solve.

    ``setup`` keys: ``material``, ``fixes``, ``loads``, optional
    ``mesh_size_mm`` and ``target_safety_factor`` (default 2). A fix or a load
    has ``name``, ``select``, and, for a load, ``type`` of ``force``,
    ``pressure``, ``bolt``, or ``torque``.

    ``probes_mm`` is one point, or three points, in the part frame. Each probe
    reports the displacement of the nearest mesh node. Three points are enough
    for a caller to fit a small rotation. The points do not change the solve.

    ``setup["relative"]`` lists pairs of faces, ``{"name", "a": select, "b": select}``.
    Each reports how far face b moved relative to face a: the mean displacement
    of each face's nodes, their difference, and gap_change_mm, the part of the
    difference along the line from a's nodes to b's (positive: apart).
    """
    probe_points = _probe_points(probes_mm)
    if not isinstance(setup, dict):
        raise GeometryError("A mechanics setup is an object with material, fixes, and loads.")
    material = solid_material(str(setup.get("material", "")))
    measurement = kernel.measure(shape)
    size = measurement.bounding_box.size
    diagonal = math.sqrt(size.x**2 + size.y**2 + size.z**2)
    smallest = min(size.x, size.y, size.z)
    mesh_size = _optional_float(setup.get("mesh_size_mm"), max(smallest / 6.0, 0.4))
    if mesh_size is None or not math.isfinite(mesh_size) or mesh_size <= 0:
        raise GeometryError("mesh_size_mm must be a positive finite length, in millimeters.")
    target = _optional_float(setup.get("target_safety_factor"), 2.0)
    if target is None or not math.isfinite(target) or target <= 0:
        raise GeometryError("target_safety_factor must be a positive finite number.")
    catalog = kernel.describe_faces(shape)
    fixes_in = _as_list(setup.get("fixes", []), "fixes")
    loads_in = _as_list(setup.get("loads", []), "loads")
    warnings: list[str] = []
    if not measurement.watertight:
        warnings.append(
            "The solid is not watertight. The mesh can come out wrong, and a stress number from it is not evidence."
        )

    fixes = [_bind_support(item, catalog, index, shape) for index, item in enumerate(fixes_in)]
    loads = [_bind_load(item, catalog, index, shape) for index, item in enumerate(loads_in)]
    relative_in = _bind_relative(setup.get("relative"), catalog, shape)
    available, reason = fea_available()
    if not available:
        raise GeometryError(reason)
    for item in fixes + loads:
        if not item["faces"]:
            warnings.append(
                f"{item['kind'].title()} {item['name']!r} matched no face. "
                "The selector's normal, radius, or location does not land on this part."
            )
    matched_loads = [item for item in loads if item["faces"]]
    if not matched_loads or all(item["zero"] for item in matched_loads):
        warnings.append("No load reaches the part. A force, pressure, or bolt load has to land on a face.")
    if not any(item["faces"] for item in fixes):
        warnings.append(
            "No face is fixed, so the part can float. Name the faces that are bolted or clamped "
            "before reading a stress."
        )
        return _unsolved(
            part_name, material, mesh_size, fixes, loads, warnings, target, "not_solved",
            "Nothing was solved. Hold the part and put a load on a face that exists.",
        )

    for item in fixes + matched_loads:
        for face in item["faces"]:
            if face["kind"] == "cylinder" and face.get("radius_mm") and mesh_size > float(face["radius_mm"]):
                warnings.append(
                    f"Mesh size {mesh_size:g} mm is larger than the {face['radius_mm']:g} mm radius "
                    f"selected by {item['name']!r}. That hole may be only one element across, so the "
                    "stress on it is rough."
                )
                break

    blocking = [item for item in warnings if item.startswith("No load")]
    if blocking:
        return _unsolved(
            part_name, material, mesh_size, fixes, loads, warnings, target, "not_solved",
            "Nothing was solved. The load does not land on the part.",
        )

    from aicad.progress import report

    order = 2
    report(f"meshing at {mesh_size:g} mm")
    mesh = _mesh_solid(shape, mesh_size, order)
    for note in mesh.get("mesh_notes") or []:
        if note not in warnings:
            warnings.append(note)
    if mesh.get("degraded"):
        warnings.append(
            "The mesh was simplified or dropped to linear elements. A safety factor from it is not a pass."
        )
    used_size = float(mesh.get("mesh_size_mm") or mesh_size)
    if mesh["element_count"] < 80 or used_size > smallest / 2.0:
        warnings.append(
            f"Very coarse mesh: target size {used_size:g} mm, {mesh['element_count']} elements, "
            f"smallest part dimension {smallest:g} mm. Peak stress from a mesh this coarse is a hint, not a measurement."
        )
    thin_note = _thin_feature_mesh_note(mesh)
    if thin_note:
        mesh["thin_feature"] = True
        if thin_note not in warnings:
            warnings.append(thin_note)
    fixed_nodes = _nodes_for(mesh, [face["index"] for item in fixes for face in item["faces"]])
    if len(fixed_nodes) == 0:
        warnings.append(
            "The fixed faces did not pick up any mesh nodes. The part is still free, so nothing was solved."
        )
        return _unsolved(
            part_name, material, mesh_size, fixes, loads, warnings, target, "not_solved",
            "Nothing was solved. The supports did not land on the mesh.",
            mesh=mesh,
        )
    if len(fixed_nodes) < 3 or _support_rank(mesh["points"][fixed_nodes]) < 2:
        warnings.append(
            "The held nodes lie on a point or a line, so the part can still spin. "
            "Fix a face, or more than one hole, not a single edge."
        )
    forces = np.zeros((len(mesh["points"]), 3), dtype=np.float64)
    loaded_nodes: set[int] = set()
    for load in matched_loads:
        nodes = _apply_load(mesh, load, forces)
        if len(nodes) == 0 and not load["zero"]:
            warnings.append(
                f"Load {load['name']!r} matched a face but none of that face's mesh nodes. "
                "The load is sitting on nothing in the mesh."
            )
        loaded_nodes.update(int(node) for node in nodes)
    if not loaded_nodes:
        warnings.append("No load reaches a mesh node, so there is nothing to solve.")
        return _unsolved(
            part_name, material, mesh_size, fixes, loads, warnings, target, "not_solved",
            "Nothing was solved. The loads did not land on the mesh.",
            mesh=mesh,
        )
    fixed_ids = {int(node) for node in fixed_nodes}
    if loaded_nodes <= fixed_ids:
        warnings.append(
            "Every loaded node is on a fixed face. The load goes straight into the support, "
            "so the stress is not a result."
        )
        return _unsolved(
            part_name, material, mesh_size, fixes, loads, warnings, target, "not_solved",
            "Nothing was solved. The load is on the held face.",
            mesh=mesh,
        )
    wasted = loaded_nodes.intersection(fixed_nodes)
    if wasted:
        warnings.append(
            f"{len(wasted)} nodes are both loaded and fixed. That share of the load goes straight "
            "into the support and never stresses the part. Move the load off the held face."
        )

    solved, disp, stress, solve_warning, order_used = _execute(mesh, fixed_nodes, forces, material, order)
    if STRAIGHT_EDGE_NOTE in (mesh.get("mesh_notes") or []) and STRAIGHT_EDGE_NOTE not in warnings:
        warnings.append(STRAIGHT_EDGE_NOTE)
    if order_used != order:
        warnings.append(
            "The quadratic mesh had folded elements, so the solve used linear tetrahedra instead. "
            "Linear tets are too stiff: expect the displacement to be low and the stress to be rough."
        )
        mesh["order"] = 1
        mesh["degraded"] = True
        mesh.setdefault("simplifications", []).append(
            "Quadratic tetrahedra had a nonpositive Jacobian. Linear tetrahedra were solved instead. "
            "Linear elements are too stiff, so this stress is not a pass."
        )
    if solve_warning:
        warnings.append(solve_warning)
    if not solved:
        return _unsolved(
            part_name, material, mesh_size, fixes, loads, warnings, target, "solver_failed",
            "CalculiX did not finish. Read the warnings. A singular stiffness usually means the part is still free to move.",
            mesh=mesh,
        )

    vm = _von_mises(stress)
    magnitude = np.linalg.norm(disp, axis=1)
    peak_index = int(np.argmax(vm))
    move_index = int(np.argmax(magnitude))
    strength = float(material["strength_mpa"])
    peak = float(vm[peak_index])
    safety = None if peak < 1e-9 else strength / peak
    across = material.get("across_layer_strength_mpa")
    across_safety = None if across is None or peak < 1e-9 else float(across) / peak
    if across_safety is not None and across_safety < target:
        warnings.append(
            f"Across the print layers this part has a safety factor of {across_safety:.2f} "
            f"({material['name']} {float(across):g} MPa across layers), below the target {target:g}. "
            "If the peak stress pulls the layers apart, turn the part so the load runs along the layers."
        )
    if peak < 1e-6:
        warnings.append("The stress is essentially zero. The load may have missed the solid, or it was applied on a fixed face.")
    if float(magnitude[move_index]) > 0.3 * diagonal:
        warnings.append(
            "The displacement is large compared with the part. The support may not actually hold it, "
            "or the load is far outside the small-displacement assumption. Do not trust the stress picture."
        )

    review = _review(mesh, vm, strength, target, warnings, safety if safety is not None else math.inf)
    assumptions = _assumptions(material, mesh, fixes, loads, target, mesh_size)
    views = []
    if out_dir is not None and mesh["surface"] is not None:
        surface, surface_nodes = mesh["surface"]
        views.extend(
            render_scalar_views(
                mesh["points"][surface_nodes],
                surface,
                vm[surface_nodes],
                out_dir,
                stem,
                "stress",
                f"von Mises  max {peak:.3g} MPa",
                "MPa",
                "von Mises",
                maximum=peak,
            )
        )
        views.extend(
            render_scalar_views(
                mesh["points"][surface_nodes],
                surface,
                magnitude[surface_nodes],
                out_dir,
                stem,
                "displacement",
                f"displacement  max {float(magnitude[move_index]):.3g} mm",
                "mm",
                "displacement",
                maximum=float(magnitude[move_index]),
            )
        )
    note = _summary(material, peak, safety, magnitude[move_index], review, warnings)
    probes = None if probe_points is None else tuple(_probe_nodes(mesh["points"], disp, probe_points))
    relative = None if relative_in is None else [_relative_motion(mesh, disp, pair) for pair in relative_in]
    return FeaReport(
        part=part_name,
        status="solved",
        solver=_SOLVER,
        material=_public_material(material),
        mesh=_public_mesh(mesh, mesh_size),
        fixes=[_public_region(item) for item in fixes],
        loads=[_public_region(item) for item in loads],
        max_von_mises_mpa=round(peak, 4),
        max_von_mises_at_mm=_point(mesh["points"][peak_index]),
        max_displacement_mm=round_mm(float(magnitude[move_index])),
        max_displacement_vector_mm=_point(disp[move_index]),
        max_displacement_at_mm=_point(mesh["points"][move_index]),
        min_safety_factor=None if safety is None else round(safety, 3),
        min_safety_factor_at_mm=_point(mesh["points"][peak_index]),
        min_safety_factor_across_layers=None if across_safety is None else round(across_safety, 3),
        warnings=warnings,
        assumptions=assumptions,
        review=review,
        views=views,
        note=note,
        probes=probes,
        relative=relative,
    )


def _bind_relative(value, catalog: list[dict], shape) -> list[dict] | None:
    """The face pairs whose relative motion a solve reports, resolved before the mesh is made."""
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise GeometryError("relative is a list of {name, a, b}, each a face select.")
    pairs = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not isinstance(item.get("a"), dict) or not isinstance(item.get("b"), dict):
            raise GeometryError(f"relative {index} needs a and b, each a face select.")
        name = item.get("name") or f"pair_{index + 1}"
        faces_a = select_faces(catalog, item["a"], shape)
        faces_b = select_faces(catalog, item["b"], shape)
        if not faces_a or not faces_b:
            raise GeometryError(f"relative {name!r}: a select matched no face.")
        pairs.append({"name": str(name), "a": faces_a, "b": faces_b})
    return pairs


def _relative_motion(mesh: dict, disp: np.ndarray, pair: dict) -> dict:
    """How far face b moved relative to face a, from the mean displacement of each face's nodes."""
    nodes_a = _nodes_for(mesh, [face["index"] for face in pair["a"]])
    nodes_b = _nodes_for(mesh, [face["index"] for face in pair["b"]])
    row: dict = {
        "name": pair["name"],
        "a_faces": [face["index"] for face in pair["a"]],
        "b_faces": [face["index"] for face in pair["b"]],
    }
    if len(nodes_a) == 0 or len(nodes_b) == 0:
        row["note"] = "A face has no mesh nodes, so its motion is not known."
        return row
    points = mesh["points"]
    move_a = disp[nodes_a].mean(axis=0)
    move_b = disp[nodes_b].mean(axis=0)
    relative = move_b - move_a
    line = points[nodes_b].mean(axis=0) - points[nodes_a].mean(axis=0)
    length = float(np.linalg.norm(line))
    row.update(
        {
            "a_mean_displacement_mm": [round(float(value), 6) for value in move_a],
            "b_mean_displacement_mm": [round(float(value), 6) for value in move_b],
            "relative_mm": [round(float(value), 6) for value in relative],
            "relative_magnitude_mm": round(float(np.linalg.norm(relative)), 6),
            "gap_change_mm": None if length < 1e-9 else round(float(relative @ line) / length, 6),
            "distance_between_mm": round(length, 6),
        }
    )
    return row


def _probe_points(value) -> list[list[float]] | None:
    """One point, or three, in the part frame. None means the solve has no probes."""
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or not value:
        raise GeometryError("probes_mm must be one point, or three points, in millimeters.")
    if len(value) not in (1, 3):
        raise GeometryError("probes_mm must be one point, or three points that fit a small rotation.")
    return [list(kernel.finite_vec3(point, "Probe")) for point in value]


def _probe_nodes(points: np.ndarray, displacement: np.ndarray, probes: list[list[float]]) -> list[dict]:
    """Displacement of the mesh node nearest each probe. The node may sit off the point."""
    cloud = np.asarray(points, dtype=float)
    field = np.asarray(displacement, dtype=float)
    rows = []
    for probe in probes:
        target = np.asarray(probe, dtype=float)
        distance = np.linalg.norm(cloud - target, axis=1)
        index = int(np.argmin(distance))
        gap = float(distance[index])
        rows.append(
            {
                "at_mm": [round(float(component), 6) for component in target],
                "node_mm": [round(float(component), 6) for component in cloud[index]],
                "distance_mm": round(gap, 6),
                "displacement_mm": [round(float(component), 6) for component in field[index]],
            }
        )
    return rows


def _bind_support(item: dict, catalog: list[dict], index: int, shape: kernel.Shape | None = None) -> dict:
    if not isinstance(item, dict):
        raise GeometryError(f"Fix {index} must be an object with a select.")
    name = item.get("name") or f"fix_{index + 1}"
    faces = select_faces(catalog, item.get("select") or {}, shape)
    return {"kind": "fix", "name": name, "faces": faces, "zero": False, "spec": item}


def _bind_load(item: dict, catalog: list[dict], index: int, shape: kernel.Shape | None = None) -> dict:
    if not isinstance(item, dict):
        raise GeometryError(f"Load {index} must be an object with type, select, and a magnitude.")
    kind = item.get("type")
    name = item.get("name") or f"load_{index + 1}"
    if kind not in ("force", "pressure", "bolt", "torque", "remote"):
        raise GeometryError(f"Load {name!r} has type {kind!r}. Use force, pressure, bolt, torque, or remote.")
    faces = select_faces(catalog, item.get("select") or {}, shape)
    zero = False
    if kind == "force":
        vector = _vec(item.get("vector"), f"{name} vector")
        zero = _distance(vector, (0, 0, 0)) < 1e-12
    elif kind == "remote":
        if item.get("at") is None:
            raise GeometryError(
                f"Remote load {name!r} needs at [x, y, z], the point in millimetres where the force acts."
            )
        _vec(item.get("at"), f"{name} at")
        vector = _vec(item.get("vector"), f"{name} vector") if item.get("vector") is not None else (0.0, 0.0, 0.0)
        moment = _vec(item.get("moment_nm"), f"{name} moment_nm") if item.get("moment_nm") is not None else (0.0, 0.0, 0.0)
        zero = _distance(vector, (0, 0, 0)) < 1e-12 and _distance(moment, (0, 0, 0)) < 1e-15
    elif kind == "pressure":
        magnitude = _optional_float(item.get("magnitude"), None)
        if magnitude is None or not math.isfinite(magnitude):
            raise GeometryError(
                f"Pressure {name!r} needs a finite magnitude, in MPa. Positive pressure pushes into the face."
            )
        zero = abs(magnitude) < 1e-15
    elif kind == "torque":
        torque = _optional_float(item.get("torque_nm"), None)
        if torque is None or not math.isfinite(torque):
            raise GeometryError(
                f"Torque {name!r} needs a finite torque_nm, in newton-metres, right-handed about the axis."
            )
        if item.get("axis") is not None:
            axis = _vec(item.get("axis"), f"{name} axis")
            if _distance(axis, (0, 0, 0)) < 1e-12:
                raise GeometryError(f"Torque {name!r} has a zero axis.")
        elif not any(face.get("axis_direction") for face in faces):
            raise GeometryError(
                f"Torque {name!r} needs an axis, or a cylindrical face in its select so the axis is known."
            )
        zero = abs(torque) < 1e-15
    else:
        axial = _optional_float(item.get("axial"), None)
        if axial is None or not math.isfinite(axial):
            raise GeometryError(
                f"Bolt load {name!r} needs a finite axial force, in newtons, along the hole axis. "
                "Positive follows the axis (upward for a vertical hole)."
            )
        zero = abs(axial) < 1e-15
    return {"kind": kind, "name": name, "faces": faces, "zero": zero, "spec": item}


def _signal_numbers() -> list[int]:
    """The signals whose action Gmsh changes and this module puts back.

    Only SIGPIPE. Gmsh and the OpenCascade inside it may install their own
    handlers for faults while they run, and those must stay in place, so the
    other signals are left alone.
    """
    number = getattr(signal, "SIGPIPE", None)
    return [] if number is None else [int(number)]


@contextlib.contextmanager
def _signal_actions_kept():
    """Put back the SIGPIPE action that the Gmsh library changes.

    ``gmshInitialize`` sets SIGPIPE back to the default action. Python
    ignores SIGPIPE, so a write to a closed pipe raises BrokenPipeError. With
    the default action that same write ends the whole server with status 141
    and no message, which is what happened after the first stress check when
    the client went away. Python's own ``signal.signal`` cannot repair this:
    it can be called only on the main thread, and the workspace server meshes
    on a worker thread. So the process-wide action is read with ``sigaction``
    before Gmsh runs and written back after it starts and after it finishes.
    The saved structure is copied as raw bytes, so its layout does not
    matter. Other signals are not touched: putting back a fault handler while
    Gmsh is running would take away the one OpenCascade relies on. Windows
    has no SIGPIPE and no ``sigaction``, so nothing is done there.
    """
    numbers = _signal_numbers()
    if sys.platform == "win32" or not numbers:
        yield lambda: None
        return
    libc = ctypes.CDLL(None, use_errno=True)
    action = libc.sigaction
    action.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    action.restype = ctypes.c_int
    saved: dict[int, ctypes.Array] = {}
    for number in numbers:
        # Larger than struct sigaction on every supported system.
        buffer = ctypes.create_string_buffer(512)
        if action(number, None, buffer) == 0:
            saved[number] = buffer

    def restore() -> None:
        for number, buffer in saved.items():
            action(number, buffer, None)

    try:
        yield restore
    finally:
        restore()


def _refuse_dense_mesh(shape: kernel.Shape, mesh_size: float) -> None:
    """Stop a mesh that would run far past the triangle budget.

    Meshing happens in this process, so the script wall clock does not cover it.
    """

    if mesh_size < 0.4:
        raise GeometryError("mesh_size_mm is finer than 0.4 mm. Use a coarser mesh.")
    measurement = kernel.measure(shape)
    size = measurement.bounding_box.size
    volume = abs(float(size.x) * float(size.y) * float(size.z))
    if volume / (mesh_size**3) > 2_000_000:
        raise GeometryError("That mesh would be too large. Use a larger mesh_size_mm.")


def _mesh_solid(shape: kernel.Shape, mesh_size: float, order: int) -> dict:
    """Mesh the solid. A Gmsh failure is retried, not raised raw."""
    _refuse_dense_mesh(shape, mesh_size)
    prepared, mesh = _mesh_ladder(shape, mesh_size, order)
    if mesh.get("points") is None:
        return mesh
    catalog = kernel.describe_faces(shape)
    surfaces = mesh["surface_of_face"]["surfaces"]
    _assign_surface_faces(shape, catalog, surfaces, mesh["points"], float(mesh["mesh_size_mm"]))
    mesh["surface_of_face"]["catalog"] = catalog
    mesh["surface_of_face"]["shape"] = shape
    if prepared is not shape:
        from aicad.mesh import face_count

        if face_count(prepared) != len(catalog):
            mesh.setdefault("mesh_notes", []).append(
                "The simplified solid does not have the same faces as the model. "
                "A load or a support picked on the original part may miss."
            )
    return mesh


def _mesh_ladder(shape: kernel.Shape, mesh_size: float, order: int):
    """Try the requested mesh, then safer settings, then a simplified solid.

    The first attempt is the mesh the caller asked for. Later attempts use a
    different mesher, a finer size, a sewed solid, small-face removal, and
    finally linear tetrahedra. The notes say which attempt was used.
    """
    from aicad.mesh import defeature, face_count, heal, volume_mm3

    attempts = _mesh_attempts(shape, mesh_size, order)
    notes: list[str] = []
    tried: set[tuple] = set()
    last = "Gmsh did not mesh this solid."
    with _LOCK:
        for attempt in attempts:
            current = shape
            prep_notes: list[str] = []
            geometry_changed = False
            try:
                if attempt["heal"]:
                    current, heal_note, healed_changed = heal(current, float(attempt["sew_mm"]))
                    prep_notes.append(heal_note)
                    geometry_changed = geometry_changed or healed_changed
                if attempt["defeature"]:
                    current, drop_note, removed = defeature(current, float(attempt["min_face_mm2"]))
                    prep_notes.append(drop_note)
                    geometry_changed = geometry_changed or removed > 0
            except GeometryError as exc:
                notes.append(f"{attempt['label']} was not prepared: {exc}")
                last = str(exc)
                continue
            signature = (
                round(float(attempt["size"]), 4),
                int(attempt["order"]),
                round(volume_mm3(current), 3),
                face_count(current),
                int(attempt["algorithm"]),
            )
            if signature in tried:
                notes.append(f"Skipped {attempt['label']}: those settings were already tried on this solid.")
                continue
            tried.add(signature)
            try:
                mesh = _mesh_attempt(
                    current,
                    float(attempt["size"]),
                    int(attempt["order"]),
                    algorithm=int(attempt["algorithm"]),
                    optimize=int(attempt["optimize"]),
                    occ_fix=bool(attempt["occ_fix"]),
                )
            except GeometryError as exc:
                notes.append(f"{attempt['label']} failed: {exc}")
                last = str(exc)
                continue
            except Exception as exc:
                detail = " ".join(str(exc).split())[:400] or exc.__class__.__name__
                notes.append(f"{attempt['label']} failed: {detail}")
                last = detail
                continue
            simplifications: list[str] = []
            if geometry_changed:
                simplifications.extend(prep_notes)
            if int(mesh["order"]) < order or int(attempt["order"]) < order:
                simplifications.append(
                    "Linear tetrahedra were used. They are too stiff: displacement comes out low and a frequency comes out high."
                )
            degraded = bool(simplifications)
            success = attempt["success"]
            if attempt["defeature"] and not any(str(line).startswith("Removed ") for line in prep_notes):
                success = "No face was small enough to remove. The mesh used the prepared solid."
            mesh_notes = list(notes)
            if prep_notes and not geometry_changed:
                mesh_notes.extend(prep_notes)
            if success:
                mesh_notes.append(success)
            mesh["mesh_notes"] = [line for line in mesh_notes if line]
            mesh["simplifications"] = simplifications
            mesh["degraded"] = degraded
            mesh["requested_size_mm"] = mesh_size
            mesh["requested_order"] = order
            return current, mesh
    detail = " ".join(notes[-6:])
    raise GeometryError(
        "Gmsh could not mesh this solid after safer settings and a simplified shape. " + (detail or last)
    )


def _mesh_attempts(shape: kernel.Shape, mesh_size: float, order: int) -> list[dict]:
    box = shape.impl.bounding_box().size
    diagonal = math.sqrt(float(box.X) ** 2 + float(box.Y) ** 2 + float(box.Z) ** 2)
    finest = max(0.4, min(mesh_size / 4.0, diagonal / 25.0 if diagonal else mesh_size))
    sizes = [mesh_size]
    half = mesh_size / 2.0
    if half >= finest and half < mesh_size - 1e-6:
        sizes.append(half)
    quarter = mesh_size / 4.0
    if quarter > finest and quarter < sizes[-1] - 1e-6:
        sizes.append(quarter)
    if sizes[-1] > finest * 1.15:
        sizes.append(finest)
    fine = sizes[-1]
    sew = 0.05
    min_face = min(1.0, max(0.02, mesh_size * mesh_size * 0.02))
    attempts = [
        {
            "label": "requested mesh",
            "size": mesh_size,
            "order": order,
            "algorithm": 1,
            "optimize": 1,
            "occ_fix": False,
            "heal": False,
            "defeature": False,
            "sew_mm": sew,
            "min_face_mm2": min_face,
            "success": "",
        }
    ]
    if order >= 1:
        attempts.append(
            {
                "label": "frontal mesher",
                "size": mesh_size,
                "order": order,
                "algorithm": 4,
                "optimize": 0,
                "occ_fix": True,
                "heal": False,
                "defeature": False,
                "sew_mm": sew,
                "min_face_mm2": min_face,
                "success": (
                    "Gmsh's first mesh failed. The frontal mesher was used on the same solid, "
                    "with optimization off and OpenCascade's small-edge repair on."
                ),
            }
        )
    for size in sizes[1:]:
        attempts.append(
            {
                "label": f"finer mesh at {size:.3g} mm",
                "size": size,
                "order": order,
                "algorithm": 1,
                "optimize": 0,
                "occ_fix": True,
                "heal": False,
                "defeature": False,
                "sew_mm": sew,
                "min_face_mm2": min_face,
                "success": (
                    f"Gmsh failed at {mesh_size:g} mm. The same solid was meshed at {size:.3g} mm "
                    "so a thin wall could be split."
                ),
            }
        )
    attempts.append(
        {
            "label": "healed solid",
            "size": fine,
            "order": order,
            "algorithm": 1,
            "optimize": 0,
            "occ_fix": True,
            "heal": True,
            "defeature": False,
            "sew_mm": sew,
            "min_face_mm2": min_face,
            "success": "The solid was sewed and fixed, then meshed.",
        }
    )
    attempts.append(
        {
            "label": "defeatured solid",
            "size": fine,
            "order": order,
            "algorithm": 4,
            "optimize": 0,
            "occ_fix": True,
            "heal": True,
            "defeature": True,
            "sew_mm": sew,
            "min_face_mm2": min_face,
            "success": "Small faces were removed so the solid could mesh. The report lists what was removed.",
        }
    )
    if order != 1:
        attempts.append(
            {
                "label": "linear tetrahedra",
                "size": fine,
                "order": 1,
                "algorithm": 1,
                "optimize": 0,
                "occ_fix": True,
                "heal": True,
                "defeature": True,
                "sew_mm": sew,
                "min_face_mm2": min_face,
                "success": "Quadratic tetrahedra would not mesh. Linear tetrahedra were used.",
            }
        )
    return attempts


_GMSH_THREAD_LOCK = threading.Lock()
_GMSH_EXECUTOR = None
_GMSH_THREAD_IDENT = None


def _on_the_gmsh_thread(func):
    """Run ``func`` on the one long-lived thread that owns Gmsh.

    Gmsh and the OpenCascade code inside it keep per-thread state. On macOS a
    ``gmsh.initialize`` on a new thread crashes the process (SIGSEGV) once an
    earlier thread that initialised and finalised Gmsh has exited. A server
    that answers each request on its own thread hits that on its second
    request. A single thread that never exits keeps the state valid, so every
    mesh runs there and the caller waits for the result. A call made from that
    thread runs directly.
    """

    global _GMSH_EXECUTOR, _GMSH_THREAD_IDENT
    if threading.get_ident() == _GMSH_THREAD_IDENT:
        return func()
    with _GMSH_THREAD_LOCK:
        if _GMSH_EXECUTOR is None:
            from concurrent.futures import ThreadPoolExecutor

            from aicad.thread_stack import prepare_geometry_thread_stack

            prepare_geometry_thread_stack()
            _GMSH_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aicad-gmsh")
    import contextvars

    context = contextvars.copy_context()

    def run():
        global _GMSH_THREAD_IDENT
        _GMSH_THREAD_IDENT = threading.get_ident()
        return context.run(func)

    return _GMSH_EXECUTOR.submit(run).result()


def _mesh_attempt(shape: kernel.Shape, mesh_size: float, order: int, *, algorithm: int, optimize: int, occ_fix: bool) -> dict:
    import gmsh
    from aicad.paths import guest_path, held_tool_directory, use_held_thread

    with held_tool_directory() as directory:
        kernel.export_step_file(shape, Path(directory) / "part.step")
        from aicad.sandbox import scrub_library_environment

        scrub_library_environment()

        def mesh_on_the_gmsh_thread():
            with _signal_actions_kept() as restore_signals:
                # interruptible=False keeps the Python wrapper from replacing the
                # SIGINT handler, which it can do only on the main thread.
                gmsh.initialize(readConfigFiles=False, interruptible=False)
                restore_signals()
                try:
                    _gmsh_option(gmsh, "General.Terminal", 0)
                    _gmsh_option(gmsh, "General.Verbosity", 0)
                    gmsh.model.add("part")
                    with use_held_thread(directory):
                        gmsh.model.occ.importShapes(str(guest_path(directory, "part.step")))
                    gmsh.model.occ.synchronize()
                    gmsh.model.mesh.setSize(gmsh.model.getEntities(0), mesh_size)
                    _gmsh_option(gmsh, "Mesh.ElementOrder", order)
                    _gmsh_option(gmsh, "Mesh.Optimize", optimize)
                    _gmsh_option(gmsh, "Mesh.Algorithm3D", algorithm)
                    if occ_fix:
                        for name in (
                            "Geometry.OCCFixDegenerated",
                            "Geometry.OCCSewFaces",
                            "Geometry.OCCFixSmallEdges",
                            "Geometry.OCCFixSmallFaces",
                        ):
                            _gmsh_option(gmsh, name, 1)
                    try:
                        gmsh.model.mesh.generate(3)
                    except Exception as exc:
                        raise GeometryError(_gmsh_failure(exc)) from exc
                    surfaces = _surface_centroids(gmsh)
                    points, elements, gmsh_order, tag_to_index = _volume_elements(gmsh)
                finally:
                    try:
                        gmsh.finalize()
                    except Exception:
                        pass
            return surfaces, points, elements, gmsh_order, tag_to_index

        surfaces, points, elements, gmsh_order, tag_to_index = _on_the_gmsh_thread(mesh_on_the_gmsh_thread)
    if len(elements) == 0:
        raise GeometryError("Gmsh produced no volume elements. The solid may be too thin for this mesh size.")
    for surface in surfaces:
        surface["point_indices"] = {tag_to_index[tag] for tag in surface["nodes"] if tag in tag_to_index}
    surface_triangles, surface_nodes = _boundary_triangles(elements, gmsh_order)
    return {
        "points": points,
        "elements": elements,
        "order": gmsh_order,
        "element_count": len(elements),
        "node_count": len(points),
        "surface_of_face": {"surfaces": surfaces, "catalog": None, "shape": shape},
        "surface": (surface_triangles, surface_nodes),
        "mesh_size_mm": mesh_size,
    }


def _gmsh_option(gmsh, name: str, value: float) -> None:
    try:
        gmsh.option.setNumber(name, value)
    except Exception:
        return


def _gmsh_failure(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = exc.__class__.__name__
    return text[:500]


def _surface_centroids(gmsh) -> list[dict]:
    found = []
    for _dim, tag in gmsh.model.getEntities(2):
        node_tags, coords, _ = gmsh.model.mesh.getNodes(2, tag, includeBoundary=True)
        if len(node_tags) == 0:
            continue
        xyz = np.array(coords, dtype=np.float64).reshape(-1, 3)
        found.append({"tag": int(tag), "nodes": set(int(node) for node in node_tags), "center": xyz.mean(axis=0)})
    return found


def _volume_elements(gmsh) -> tuple[np.ndarray, np.ndarray, int, dict[int, int]]:
    node_tags, coords, _ = gmsh.model.mesh.getNodes()
    coords = np.array(coords, dtype=np.float64).reshape(-1, 3)
    order = np.argsort(node_tags)
    tags = node_tags[order].astype(int)
    points = coords[order]
    tag_to_index = {int(tag): index for index, tag in enumerate(tags)}
    types, _etags, nodes = gmsh.model.mesh.getElements(3)
    types = list(types)
    if 11 in types:
        raw = nodes[types.index(11)].reshape(-1, 10)
        raw = raw[:, list(_TET10_TO_C3D10)]
        gmsh_order = 2
    elif 4 in types:
        raw = nodes[types.index(4)].reshape(-1, 4)
        gmsh_order = 1
    else:
        raise GeometryError(f"Gmsh returned element types {types}, not tetrahedra.")
    elements = np.array([[tag_to_index[int(tag)] for tag in element] for element in raw], dtype=np.int32)
    return points, elements, gmsh_order, tag_to_index


def _assign_surface_faces(
    shape, catalog: list[dict], surfaces: list[dict], points: np.ndarray, mesh_size: float
) -> None:
    """Label each Gmsh surface with the CAD face its nodes lie on.

    The centroid of a hole's nodes sits on the axis, not on the cylinder, so
    matching uses sample nodes. The mean distance prefers the face the interior
    nodes actually touch when a boundary node is shared with a neighbor.
    """
    tolerance = max(1.0, mesh_size * 0.75)
    sampled: list[tuple[dict, int, int]] = []
    samples: list[tuple[float, float, float]] = []
    for surface in surfaces:
        indices = sorted(surface.get("point_indices") or [])
        if not indices:
            surface["face_index"] = None
            continue
        step = max(1, len(indices) // 6)
        start = len(samples)
        for index in indices[::step][:6]:
            sample = points[index]
            samples.append((float(sample[0]), float(sample[1]), float(sample[2])))
        sampled.append((surface, start, len(samples)))
    if not sampled:
        return
    face_indices = [face["index"] for face in catalog]
    # One pass over the part's faces for every sample: the face list is not
    # rebuilt for each distance.
    table = kernel.distances_points_to_faces(shape, face_indices, samples) if face_indices else []
    for surface, start, end in sampled:
        best_face = None
        best_distance = None
        for face_index, row in zip(face_indices, table):
            distances = row[start:end]
            mean = sum(distances) / len(distances)
            if best_distance is None or mean < best_distance:
                best_distance = mean
                best_face = face_index
        surface["face_index"] = best_face if best_distance is not None and best_distance <= tolerance else None


def _nodes_for(mesh: dict, face_indices: list[int]) -> np.ndarray:
    wanted = set(face_indices)
    if not wanted:
        return np.array([], dtype=np.int32)
    chosen: set[int] = set()
    for surface in mesh["surface_of_face"]["surfaces"]:
        if surface.get("face_index") not in wanted:
            continue
        chosen.update(surface.get("point_indices") or ())
    return np.array(sorted(chosen), dtype=np.int32)


def _execute(mesh, fixed_nodes, forces, material, order: int):
    disp, stress, warning = _run_ccx(mesh, fixed_nodes, forces, material)
    if warning and "nonpositive jacobian" in warning.lower() and order == 2:
        straight = dict(mesh)
        straight["points"] = straight_sided(mesh["points"], mesh["elements"])
        disp, stress, warning = _run_ccx(straight, fixed_nodes, forces, material)
        _keep_solver_record(mesh, straight)
        if warning is None:
            mesh["points"] = straight["points"]
            mesh.setdefault("mesh_notes", []).append(STRAIGHT_EDGE_NOTE)
            return True, disp, stress, None, 2
    if warning and "nonpositive jacobian" in warning.lower() and order == 2:
        linear = dict(mesh)
        linear["elements"] = mesh["elements"][:, :4]
        linear["order"] = 1
        disp, stress, warning = _run_ccx(linear, fixed_nodes, forces, material)
        _keep_solver_record(mesh, linear)
        if warning is None:
            mesh["elements"] = linear["elements"]
            mesh["order"] = 1
            mesh["element_count"] = len(linear["elements"])
            return True, disp, stress, None, 1
    if warning:
        return False, None, None, warning, order
    return True, disp, stress, None, order


def _keep_solver_record(mesh: dict, attempt: dict) -> None:
    for key in ("equations", "linear_solver"):
        if key in attempt:
            mesh[key] = attempt[key]


def solver_deck(lines: list[str]) -> str:
    """One CalculiX deck. Each entry is one line, with no break inside it.

    A part name, a note, or a material name is not a line in this deck. A
    line that already contains a break is refused, so it cannot add a card.
    """

    import unicodedata

    rendered: list[str] = []
    for line in lines:
        if not isinstance(line, str) or any(
            unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in line
        ):
            raise GeometryError("A solver input line has to stay on one line.")
        rendered.append(line)
    return "\n".join(rendered) + "\n"


def _equation_count(node_count: int, fixed_nodes) -> int:
    """Unknowns in the stiffness system: three per node that is not held."""
    held = len(np.unique(np.asarray(fixed_nodes, dtype=np.int64))) if len(fixed_nodes) else 0
    return 3 * max(int(node_count) - held, 0)


def _linear_solver(equations: int) -> str:
    return "direct" if equations <= _DIRECT_SOLVER_EQUATIONS else "iterative"


def _coarser_mesh_size(mesh_size: float, equations: int) -> float:
    """A mesh size that brings the solve within reach. Equations go as 1 / size cubed."""
    target = _ITERATIVE_SOLVER_EQUATIONS if equations > _ITERATIVE_SOLVER_EQUATIONS else equations / 2.0
    size = mesh_size * (equations / max(target, 1.0)) ** (1.0 / 3.0)
    return math.ceil(size * 10.0 - 1e-9) / 10.0


# Threads CalculiX may use. On a 24-core computer a 580,000-equation iterative
# solve took 198 s on one thread, 167 s on 4, and 149 s on 8, and ran out of
# its 4 GiB on 24: each thread holds its own working memory.
_CCX_MAX_THREADS = 8


def ccx_threads() -> int:
    """Threads for one CalculiX run: the cores this process may run on, at most _CCX_MAX_THREADS.

    The count is the process's CPU affinity where the system has one, as for
    a design script, so a container limited to two cores gets two threads.
    """
    from aicad.sandbox import script_cores

    return max(1, min(_CCX_MAX_THREADS, script_cores()))


def ccx_thread_environment(threads: int, *, equation_solver: bool = True) -> dict[str, str]:
    """The variables CalculiX reads for its thread count.

    Without ``equation_solver``, the direct (SPOOLES) solver stays on one
    thread: on this computer more threads saved about a tenth of its time
    and made it run out of memory on a system it solved on one. Building the
    stiffness matrix and the stresses still use every thread.
    """
    count = str(max(1, int(threads)))
    return {
        "OMP_NUM_THREADS": count,
        "NUMBER_OF_CPUS": count,
        "CCX_NPROC_STIFFNESS": count,
        "CCX_NPROC_RESULTS": count,
        "CCX_NPROC_EQUATION_SOLVER": count if equation_solver else "1",
    }


# Words CalculiX, SPOOLES, or the C library print when an allocation fails.
_ALLOCATION_FAILED = ("allocat", "out of memory", "not enough memory", "bad_alloc")
# Windows exit codes of a process that crashed: an access violation, no memory,
# and a stack overflow. A failed allocation in the solver ends in one of these.
_WINDOWS_CRASHES = frozenset({0xC0000005, 0xC0000017, 0xC00000FD})


def _died_in_a_crash(code: int) -> bool:
    """Whether the exit code is a crash, not a program that ran and stopped.

    Elsewhere a crash is a death by signal, a negative code. On Windows a
    negative code is any NTSTATUS, including a program that could not start
    (0xC0000135, a missing DLL), so only the crash codes count there.
    """
    code = int(code)
    if sys.platform == "win32":
        return (code & 0xFFFFFFFF) in _WINDOWS_CRASHES
    return code < 0


def _ccx_stop(completed) -> str | None:
    """Why a CalculiX run that left no result stopped, when it may be the solver's size.

    ``limit`` is a memory or CPU limit: the helper saw it, or an allocation
    failed. The Linux helper has no memory cap of its own, so there the
    limit is the computer's or a container's memory: the kernel may kill the
    solver, which the helper sees, or an allocation fails, which it does not.
    ``crash`` is a crash with no error line of CalculiX's own (a signal, or a
    Windows crash code), which is how a failed allocation in the solver often
    ends. ``None`` is anything else: an error CalculiX reported, such as a bad
    input deck, or a run that never started, such as the sandbox refusing it
    (exit 126 or 127) or a missing DLL. Another solver would not fix those.
    """
    if getattr(completed, "limit_hit", False):
        return "limit"
    text = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    if any(word in text.lower() for word in _ALLOCATION_FAILED):
        return "limit"
    if any("ERROR" in line for line in str(completed.stdout or "").splitlines()):
        return None
    if _died_in_a_crash(completed.returncode):
        return "crash"
    return None


def _helper_has_caps() -> bool:
    """Whether the CalculiX helper runs under memory and CPU caps of its own: a Windows job object."""
    return sys.platform == "win32"


def helper_limit_stop(timeout: float, threads: int = 1) -> str:
    """How a CalculiX run stopped at a limit, naming the limits it actually had.

    Only the Windows helper has caps of its own: memory, and CPU time of
    ``timeout`` seconds for each thread it was told to use, counted over all
    of them. On Linux and macOS the helper has neither cap. There the limit is
    the computer's free memory: the system kills the solver, or an allocation
    fails.
    """
    if _helper_has_caps():
        from aicad.sandbox import memory_bytes

        each = max(1, int(timeout))
        count = max(1, int(threads))
        cpu = f"{each * count} s of CPU time"
        if count > 1:
            cpu += f" ({each} s for each of {count} threads)"
        return f"was stopped at the memory or CPU limit ({memory_bytes() // (1024 * 1024)} MiB of memory, {cpu})"
    return (
        "ran out of memory (on this system the solver has no memory or CPU cap of its own, "
        "so the limit is the computer's free memory)"
    )


def _solve_too_big(what: str, equations: int, node_count: int, mesh_size: float) -> str:
    text = f"CalculiX {what} while solving {equations:,} equations ({node_count:,} nodes"
    if mesh_size > 0:
        text += f" at a {mesh_size:g} mm mesh). Use mesh_size_mm {_coarser_mesh_size(mesh_size, equations):g} or larger"
    else:
        text += "). Use a larger mesh_size_mm"
    return text + ", or solve a smaller region of the part."


def _run_ccx(mesh, fixed_nodes, forces, material, method: str | None = None):
    points = mesh["points"]
    elements = mesh["elements"]
    order = mesh["order"]
    equations = _equation_count(len(points), fixed_nodes)
    if method is None:
        method = _linear_solver(equations)
    mesh["equations"] = equations
    mesh["linear_solver"] = method
    from aicad.progress import report

    report(f"solving {equations:,} equations with the {method} solver")
    mesh_size = float(mesh.get("mesh_size_mm") or 0.0)
    element_type = "C3D10" if order == 2 else "C3D4"
    width = 10 if order == 2 else 4
    from aicad.paths import held_entry_is_file, held_subprocess_kwargs, held_tool_directory

    with held_tool_directory() as path:
        lines = ["*HEADING", "aicad linear static", "*NODE"]
        for index, point in enumerate(points, start=1):
            lines.append(f"{index}, {point[0]:.6f}, {point[1]:.6f}, {point[2]:.6f}")
        lines.append(f"*ELEMENT, TYPE={element_type}, ELSET=EALL")
        for index, element in enumerate(elements, start=1):
            nodes = ", ".join(str(int(node) + 1) for node in element[:width])
            lines.append(f"{index}, {nodes}")
        young = float(material["youngs_modulus_mpa"])
        poisson = float(material["poisson"])
        lines += [
            "*MATERIAL, NAME=MAT",
            "*ELASTIC",
            f"{young}, {poisson}",
            "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT",
            "*BOUNDARY",
        ]
        for node in fixed_nodes:
            lines.append(f"{int(node) + 1}, 1, 3, 0.")
        static = "*STATIC" if method == "direct" else "*STATIC, SOLVER=ITERATIVE CHOLESKY"
        lines += ["*STEP", static, "*CLOAD"]
        loaded = np.nonzero(np.linalg.norm(forces, axis=1) > 1e-15)[0]
        if len(loaded) == 0:
            return None, None, "The load produced no nodal forces."
        for node in loaded:
            fx, fy, fz = forces[int(node)]
            base = int(node) + 1
            if abs(fx) > 1e-15:
                lines.append(f"{base}, 1, {fx:.8g}")
            if abs(fy) > 1e-15:
                lines.append(f"{base}, 2, {fy:.8g}")
            if abs(fz) > 1e-15:
                lines.append(f"{base}, 3, {fz:.8g}")
        lines += ["*NODE FILE", "U", "*EL FILE", "S", "*END STEP"]
        from aicad.paths import publish_text

        publish_text(path / "job.inp", solver_deck(lines), root=path)
        # ccx is GPL-2.0-only. It stays a separate process and is not linked.
        from aicad.prereqs import find_program

        solver = find_program("ccx")
        if solver is None:
            return None, None, "CalculiX (ccx) is not on PATH."
        import subprocess

        from aicad.sandbox import program_environment, run_confined_helper

        threads = ccx_threads()
        environment = program_environment()
        environment.update(ccx_thread_environment(threads, equation_solver=method == "iterative"))
        try:
            completed = run_confined_helper(
                [solver, "-i", "job"],
                env=environment,
                timeout=_CCX_TIMEOUT_S,
                threads=threads,
                **held_subprocess_kwargs(path),
            )
        except subprocess.TimeoutExpired:
            return None, None, _solve_too_big(
                f"did not finish in {_CCX_TIMEOUT_S} s", equations, len(points), mesh_size
            )
        failed = completed.returncode != 0 or not held_entry_is_file(path, "job.frd")
        stop = _ccx_stop(completed) if failed else None
        if stop is None:
            if failed:
                detail = _ccx_error(completed.stdout)
                return None, None, detail or "CalculiX stopped without a result file."
            if "nonpositive jacobian" in completed.stdout.lower():
                return None, None, "nonpositive jacobian"
            return _parse_frd(path / "job.frd", len(points)) + (None,)
    if method == "direct":
        # The direct solver's memory grows much faster than the system it
        # solves. The iterative one may still fit, so it gets one try.
        return _run_ccx(mesh, fixed_nodes, forces, material, method="iterative")
    if stop == "crash":
        return None, None, (
            f"CalculiX stopped without a result file or an error of its own (exit code {completed.returncode}), "
            f"with the iterative solver, on {equations:,} equations. "
            "A failed allocation can end this way; a larger mesh_size_mm needs less memory."
        )
    # The helper says a limit stopped it, not which one.
    return None, None, _solve_too_big(helper_limit_stop(_CCX_TIMEOUT_S, threads), equations, len(points), mesh_size)


def _parse_frd(path: Path, node_count: int):
    disp = np.zeros((node_count, 3), dtype=np.float64)
    stress = np.zeros((node_count, 6), dtype=np.float64)
    mode = None
    seen_disp = 0
    seen_stress = 0
    from aicad.file_access import read_text

    # About 300 bytes a node for coordinates, elements, displacement, and stress.
    maximum = max(64 * 1024 * 1024, 600 * int(node_count))
    for line in read_text(path, maximum=maximum, errors="replace").splitlines():
        if line.startswith(" -4"):
            if "DISP" in line:
                mode = "disp"
            elif "STRESS" in line:
                mode = "stress"
            else:
                mode = None
            continue
        if line.startswith(" -3"):
            mode = None
            continue
        if mode is None or not line.startswith(" -1"):
            continue
        body = line[3:]
        matched = re.match(r"\s*(\d+)", body)
        if not matched:
            continue
        node_id = int(matched.group(1))
        if node_id < 1 or node_id > node_count:
            continue
        values = [float(token) for token in _FRD_NUMBER.findall(body[matched.end() :])]
        if mode == "disp" and len(values) >= 3:
            disp[node_id - 1] = values[:3]
            seen_disp += 1
        elif mode == "stress" and len(values) >= 6:
            stress[node_id - 1] = values[:6]
            seen_stress += 1
    if seen_disp == 0 or seen_stress == 0:
        raise GeometryError("CalculiX finished but the result file had no displacement or stress.")
    return disp, stress


def _apply_load(mesh: dict, load: dict, forces: np.ndarray) -> set[int]:
    spec = load["spec"]
    faces = load["faces"]
    each = bool(spec.get("each", False))
    touched: set[int] = set()
    if load["kind"] == "pressure":
        magnitude = float(spec["magnitude"])
        for face in faces:
            if face["kind"] == "plane" and face.get("normal"):
                nodes = _nodes_for(mesh, [face["index"]])
                if len(nodes) == 0:
                    continue
                area = max(float(face["area_mm2"]), 1e-9)
                # Positive pressure compresses: force is opposite the outward normal.
                normal = np.array(face["normal"], dtype=np.float64)
                total = -magnitude * area * normal
                share = total / len(nodes)
                forces[nodes] += share
                touched.update(int(node) for node in nodes)
            else:
                touched.update(_apply_curved_pressure(mesh, face, magnitude, forces))
        return touched
    if load["kind"] == "torque":
        return _apply_torque(mesh, load, forces)
    if load["kind"] == "remote":
        return _apply_remote(mesh, load, forces)
    if load["kind"] == "force":
        vector = np.array(_vec(spec["vector"], load["name"]), dtype=np.float64)
    else:
        axis = _mean_axis(faces)
        vector = float(spec["axial"]) * axis
    groups = faces if each else [None]
    for group in groups:
        selected = [group] if group is not None else faces
        nodes = _nodes_for(mesh, [face["index"] for face in selected])
        if len(nodes) == 0:
            continue
        forces[nodes] += vector / len(nodes)
        touched.update(int(node) for node in nodes)
    return touched


def _apply_torque(mesh: dict, load: dict, forces: np.ndarray) -> set[int]:
    """Spread a couple over the selected nodes.

    Each node gets a force square to the axis and to its arm from the centroid of
    the loaded nodes, in proportion to that arm, like a rigid twist. The arms sum
    to zero, so the net force is zero and the moment is the same about every
    parallel axis.
    """
    spec = load["spec"]
    nodes = _nodes_for(mesh, [face["index"] for face in load["faces"]])
    if len(nodes) == 0:
        return set()
    if spec.get("axis") is not None:
        axis = np.array(_vec(spec["axis"], load["name"]), dtype=np.float64)
        axis /= float(np.linalg.norm(axis))
    else:
        axis = _mean_axis(load["faces"], "A torque")
    points = mesh["points"][nodes].astype(np.float64)
    arms = points - points.mean(axis=0)
    arms -= np.outer(arms @ axis, axis)
    second_moment = float(np.einsum("ij,ij->", arms, arms))
    if second_moment < 1e-12:
        raise GeometryError(
            f"Torque {load['name']!r} landed on nodes that lie on its axis, so it has no lever arm."
        )
    scale = float(spec["torque_nm"]) * 1000.0 / second_moment
    forces[nodes] += scale * np.cross(axis, arms)
    return {int(node) for node in nodes}


def _apply_remote(mesh: dict, load: dict, forces: np.ndarray) -> set[int]:
    """Carry a force acting at a point off the part to the selected nodes.

    Like a distributing coupling with equal weights: each node takes an equal
    share of the force, plus the force a small rigid twist about the nodes'
    centroid gives it. The twist is chosen so the nodal forces sum to the
    force, and their moment about the centroid is the force's moment from
    its point, plus any moment_nm. So the part feels the lever arm of a load
    at the end of a handle or a bracket that is not modeled.
    """
    spec = load["spec"]
    nodes = _nodes_for(mesh, [face["index"] for face in load["faces"]])
    if len(nodes) == 0:
        return set()
    force = np.array(_vec(spec["vector"], load["name"]) if spec.get("vector") is not None else (0, 0, 0), dtype=np.float64)
    at = np.array(_vec(spec["at"], load["name"]), dtype=np.float64)
    moment = np.zeros(3)
    if spec.get("moment_nm") is not None:
        moment = np.array(_vec(spec["moment_nm"], load["name"]), dtype=np.float64) * 1000.0
    points = mesh["points"][nodes].astype(np.float64)
    center = points.mean(axis=0)
    arms = points - center
    # N·mm about the centroid of the loaded nodes.
    wanted = np.cross(at - center, force) + moment
    inertia = np.einsum("ij,ij->", arms, arms) * np.eye(3) - arms.T @ arms
    spin = np.linalg.pinv(inertia, rcond=1e-10) @ wanted
    nodal = force / len(nodes) + np.cross(spin, arms)
    carried = np.cross(arms, nodal).sum(axis=0)
    if float(np.linalg.norm(carried - wanted)) > 1e-6 * max(1.0, float(np.linalg.norm(wanted))):
        raise GeometryError(
            f"Remote load {load['name']!r} has a moment the selected faces cannot carry: their nodes lie on one "
            "line, and the load twists about that line. Select a face with some width, or more faces."
        )
    forces[nodes] += nodal
    return {int(node) for node in nodes}


def _apply_curved_pressure(mesh: dict, face: dict, magnitude: float, forces: np.ndarray) -> set[int]:
    """Push each triangle of a curved face along its own outward normal.

    A cylinder or a cone has one catalog normal, taken at the face center. Using
    that normal for the whole area turns pressure into one large force. The
    triangle normals cancel around a closed hole, and each node is pushed into
    the material when the magnitude is positive.
    """
    shape = (mesh.get("surface_of_face") or {}).get("shape")
    nodes = _nodes_for(mesh, [face["index"]])
    if shape is None or len(nodes) == 0:
        return set()
    deflection = max(0.15, float(mesh.get("mesh_size_mm") or 1.0) * 0.35)
    triangles = kernel.face_pressure_triangles(shape, int(face["index"]), deflection)
    if not triangles:
        return set()
    points = mesh["points"]
    node_points = points[nodes]
    touched: set[int] = set()
    for area, normal, corners in triangles:
        load = -magnitude * area * np.array(normal, dtype=np.float64)
        share = load / 3.0
        for corner in corners:
            delta = node_points - np.array(corner, dtype=np.float64)
            nearest = int(nodes[int(np.argmin(np.einsum("ij,ij->i", delta, delta)))])
            forces[nearest] += share
            touched.add(nearest)
    return touched


def _mean_axis(faces: list[dict], what: str = "A bolt load") -> np.ndarray:
    axes = [np.array(face["axis_direction"], dtype=np.float64) for face in faces if face.get("axis_direction")]
    if not axes:
        raise GeometryError(f"{what} needs a cylindrical face, so the hole axis is known.")
    axis = np.mean(axes, axis=0)
    length = float(np.linalg.norm(axis))
    if length < 1e-9:
        if what == "A bolt load":
            raise GeometryError("The selected holes do not share an axis, so one bolt direction is ambiguous.")
        raise GeometryError("The selected faces do not share an axis, so the torque axis is ambiguous. Give axis.")
    return axis / length


def _boundary_triangles(elements: np.ndarray, order: int) -> tuple[np.ndarray, np.ndarray]:
    corners = elements[:, :4]
    faces = (
        (0, 1, 2),
        (0, 3, 1),
        (1, 3, 2),
        (2, 3, 0),
    )
    count: dict[tuple[int, int, int], list] = {}
    for element in corners:
        for a, b, c in faces:
            tri = (int(element[a]), int(element[b]), int(element[c]))
            key = tuple(sorted(tri))
            count.setdefault(key, []).append(tri)
    boundary = [entries[0] for entries in count.values() if len(entries) == 1]
    if not boundary:
        return np.zeros((0, 3), dtype=np.int32), np.array([], dtype=np.int32)
    triangles = np.array(boundary, dtype=np.int32)
    # Remap to a compact surface node list for rendering.
    used = np.unique(triangles.reshape(-1))
    remap = {int(node): index for index, node in enumerate(used)}
    local = np.array([[remap[int(node)] for node in tri] for tri in triangles], dtype=np.int32)
    return local, used


def _von_mises(stress: np.ndarray) -> np.ndarray:
    sxx, syy, szz, sxy, syz, szx = (stress[:, i] for i in range(6))
    return np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3.0 * (sxy**2 + syz**2 + szx**2)
    )


def _thin_feature_mesh_note(mesh: dict) -> str | None:
    """Flag a mesh that does not resolve a thin region.

    The bounding box of a hollow shell is the outside of the shell, so a mesh
    size check against that box never sees the wall. A tet's smallest altitude
    is the thickness of that element. When the thin end of the mesh is much
    thinner than the element size, that region is one element through.
    """
    points = mesh.get("points")
    elements = mesh.get("elements")
    if points is None or elements is None or len(elements) == 0:
        return None
    corners = np.asarray(elements)[:, :4]
    pts = np.asarray(points, dtype=np.float64)[corners]
    pairs = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
    lengths = np.stack(
        [np.linalg.norm(pts[:, a] - pts[:, b], axis=1) for a, b in pairs],
        axis=1,
    )
    longest = lengths.max(axis=1)
    volumes = _tet_volumes(pts)
    face_index = ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0))
    areas = []
    for a, b, c in face_index:
        areas.append(0.5 * np.linalg.norm(np.cross(pts[:, b] - pts[:, a], pts[:, c] - pts[:, a]), axis=1))
    area = np.maximum(np.stack(areas, axis=1), 1e-12)
    altitude = np.where(volumes[:, None] > 0, 3.0 * volumes[:, None] / area, 1e9)
    min_altitude = altitude.min(axis=1)
    flatness = longest / np.maximum(min_altitude, 1e-12)

    # The 5th percentile is a region, not one short tet at a hole. On the
    # motor-mount mesh at 1.5 mm that percentile is about 0.7 mm, above
    # 1.5/3, so the 2.4 mm gusset still passes. A 0.05 mm foil and a 0.3 mm
    # rib sit under a third of the mesh size asked for.
    used = float(mesh.get("mesh_size_mm") or 0.0)
    if used > 0:
        thin = float(np.percentile(min_altitude, 5))
        if thin < used / 3.0:
            return (
                f"Coarse mesh for a thin feature: the thinner elements are {thin:.3g} mm through, "
                f"less than a third of the {used:g} mm mesh size. "
                "There are not enough elements through the thickness. "
                "A safety factor from this mesh is not a pass."
            )
    worst = float(np.percentile(flatness, 95))
    if worst > 25:
        return (
            f"Coarse mesh for a thin feature: the 95th percentile element aspect ratio is {worst:.0f} "
            "(longest edge over the through-thickness altitude). "
            "There are not enough elements through the thickness. "
            "A safety factor from this mesh is not a pass."
        )
    return None


def _review(mesh, vm, strength: float, target: float, warnings: list[str], safety: float) -> dict:
    elements = mesh["elements"][:, :4]
    points = mesh["points"]
    centroids = points[elements].mean(axis=1)
    corner_vm = vm[elements]
    element_peak = corner_vm.max(axis=1)
    element_mean = corner_vm.mean(axis=1)
    volumes = _tet_volumes(points[elements])
    total = float(volumes.sum())
    allowable = strength / target
    peak = float(vm.max()) if len(vm) else 0.0
    coarse = bool(mesh.get("degraded")) or any(
        "coarse mesh" in item.lower() or "linear tetrahedra" in item.lower() or "simplified" in item.lower()
        for item in warnings
    )
    # A huge displacement is a floating body or a load outside the linear model.
    # A near-zero stress is a load that never entered the part. Neither one is a pass.
    untrustworthy = coarse or any(
        "do not trust" in item.lower() or "essentially zero" in item.lower()
        for item in warnings
    )
    if safety >= target and not untrustworthy:
        status = "pass"
    elif safety >= target and untrustworthy:
        status = "inconclusive"
    else:
        status = "fail"
    pitch = max(4.0, float(mesh["mesh_size_mm"]) * 2.0)
    over_bins: dict[tuple, dict] = {}
    under_bins: dict[tuple, dict] = {}
    under_limit = max(0.08 * peak, 1e-6)
    for centroid, high, mean, volume in zip(centroids, element_peak, element_mean, volumes):
        key = tuple(int(math.floor(float(component) / pitch)) for component in centroid)
        if high > allowable:
            slot = over_bins.setdefault(key, {"peak": 0.0, "volume": 0.0, "center": np.zeros(3), "weight": 0.0})
            slot["peak"] = max(slot["peak"], float(high))
            slot["volume"] += float(volume)
            slot["center"] += centroid * volume
            slot["weight"] += float(volume)
        if mean < under_limit:
            slot = under_bins.setdefault(key, {"mean_sum": 0.0, "volume": 0.0, "center": np.zeros(3)})
            slot["mean_sum"] += float(mean) * float(volume)
            slot["volume"] += float(volume)
            slot["center"] += centroid * float(volume)
    overstressed = _top_bins(over_bins, "peak", True, strength)
    understressed = _top_bins(under_bins, "volume", True, strength, mean=True)
    over_volume = sum(item["volume_mm3"] for item in overstressed)
    under_volume = float(sum(slot["volume"] for slot in under_bins.values()))
    if status == "pass" and under_volume > 0.7 * total and total > 0:
        quiet = (
            "Most of the volume is far below the peak stress. The part is lightly loaded for this "
            "safety factor, so a lot of it is a candidate for removed material, or the load is smaller "
            "than the part was sized for."
        )
    elif status == "fail":
        quiet = (
            "The minimum safety factor is below the target. The overstressed regions are candidates "
            "for added material: a thicker wall, a gusset, or a larger fillet. Quiet regions are "
            "candidates for removed material only after the hot spot is fixed."
        )
    elif status == "inconclusive":
        quiet = (
            "The safety factor clears the target, but this is not a pass. The mesh may be coarse, "
            "linear, or simplified, the displacement may be too large for a linear solve, or the "
            "stress is not a real load path."
        )
    else:
        quiet = "The hot spot is inside the target safety factor. Quiet regions could lose material."
    return {
        "target_safety_factor": target,
        "status": status,
        "allowable_mpa": round(allowable, 4),
        "overstressed": overstressed,
        "understressed": understressed[:3],
        "overstressed_volume_mm3": round_mm(over_volume),
        "understressed_volume_mm3": round_mm(under_volume),
        "part_volume_mm3": round_mm(total),
        "note": quiet,
    }


def _top_bins(bins: dict, rank: str, reverse: bool, strength: float, mean: bool = False) -> list[dict]:
    rows = []
    for slot in bins.values():
        weight = slot.get("weight", slot.get("volume", 0.0)) or 1.0
        center = slot["center"] / weight
        if mean:
            average = slot["mean_sum"] / max(slot["volume"], 1e-12)
            rows.append(
                {
                    "center_mm": [round_mm(float(v)) for v in center],
                    "mean_von_mises_mpa": round(average, 4),
                    "volume_mm3": round_mm(slot["volume"]),
                    "note": "Carries almost none of the peak stress. A candidate for removed material.",
                    "_rank": slot["volume"],
                }
            )
        else:
            factor = strength / slot["peak"] if slot["peak"] > 1e-9 else None
            rows.append(
                {
                    "center_mm": [round_mm(float(v)) for v in center],
                    "peak_von_mises_mpa": round(slot["peak"], 4),
                    "min_safety_factor": None if factor is None else round(factor, 3),
                    "volume_mm3": round_mm(slot["volume"]),
                    "note": "Above the allowable stress. A candidate for added material.",
                    "_rank": slot["peak"],
                }
            )
    rows.sort(key=lambda row: row["_rank"], reverse=reverse)
    for row in rows:
        row.pop("_rank", None)
    return rows[:4]


def _tet_volumes(corners: np.ndarray) -> np.ndarray:
    a = corners[:, 1] - corners[:, 0]
    b = corners[:, 2] - corners[:, 0]
    c = corners[:, 3] - corners[:, 0]
    return np.abs(np.einsum("ij,ij->i", np.cross(a, b), c)) / 6.0


def _assumptions(material, mesh, fixes, loads, target, mesh_size) -> list[str]:
    strength_kind = material["strength_kind"]
    lines = [
        "Linear static analysis with small displacements. The geometry is the unloaded CAD shape.",
        (
            f"Material {material['name']}: Young's modulus {material['youngs_modulus_mpa']:g} MPa, "
            f"Poisson's ratio {material['poisson']}, {strength_kind} strength {material['strength_mpa']:g} MPa. "
            f"{material['note']}"
        ),
        "Isotropic linear elasticity. No plasticity, contact, buckling, or fatigue. "
        "A safety factor below 1 means the linear model has left the elastic range; the pictured stress is not a yielded shape.",
        (
            f"Mesh target size {mesh_size:g} mm, {mesh['element_count']} "
            f"{'quadratic' if mesh['order'] == 2 else 'linear'} tetrahedra, {mesh['node_count']} nodes. "
            "Solver is CalculiX. The picture colors nodal von Mises stress extrapolated by CalculiX; "
            "the reported maximum is the highest nodal value."
        ),
        "Units are millimeters and newtons, so stress is in megapascals.",
        *(
            [
                f"The {int(mesh['equations']):,} equations were solved with CalculiX's iterative Cholesky solver, "
                "which needs far less memory than the direct solver used for smaller meshes. "
                "On a linear static case the two agree to well under 1 %."
            ]
            if mesh.get("linear_solver") == "iterative" and mesh.get("equations")
            else []
        ),
        f"The design review uses a target safety factor of {target:g} on this load case, not on a factored copy of it.",
        "A total force or bolt load is shared equally by the nodes on the selected faces. "
        "Positive pressure pushes into the face, opposite the outward normal. "
        "On a curved face that normal is local to each triangle, so pressure on a round hole "
        "does not become one sideways force. "
        "A bolt load is an axial force along the hole, not a thread or a contact patch. "
        "A torque is a couple: forces square to the axis, in proportion to each node's arm from the centroid "
        "of the loaded nodes, so the net force is zero. "
        "A remote load acts at a point off the part. It is carried to the selected faces like a distributing "
        "coupling: the nodes share the force equally, plus a rigid twist about their centroid, so they carry "
        "the same force and moment. The material between the point and the part is not modeled.",
    ]
    if material["printed"] and material.get("across_layer_strength_mpa") is not None:
        lines.append(
            "This is a 3D-printed material. min_safety_factor uses the in-plane tensile strength "
            f"({float(material['strength_mpa']):g} MPa). min_safety_factor_across_layers uses "
            f"{float(material['across_layer_strength_mpa']):g} MPa, for a peak stress that pulls the layers apart. "
            "The solve does not know the print direction, so read the one that matches how the part is printed."
        )
    elif material["printed"]:
        lines.append(
            "This is a 3D-printed material. The strength used here is an in-plane tensile number. "
            "A printed part is weaker across layers, often about half, so a load that pulls the layers apart "
            "can fail while this safety factor still looks acceptable."
        )
    else:
        lines.append(
            "A 3D-printed part is weaker across layers, often about half the in-plane strength. "
            f"This study uses {material['name']}, a {strength_kind} strength from the library, "
            "with no layer-direction reduction."
        )
    if fixes:
        held = ", ".join(item["name"] for item in fixes) or "(none)"
        lines.append(f"Fixed faces: {held}. Every node on those faces is held in X, Y, and Z.")
    if loads:
        described = ", ".join(f"{item['name']} ({item['kind']})" for item in loads)
        lines.append(f"Loads: {described}.")
    for note in mesh.get("simplifications") or []:
        lines.append(note)
    for note in mesh.get("mesh_notes") or []:
        if note not in lines:
            lines.append(note)
    lines.append("CalculiX is run as a separate program. It is not linked into aicad.")
    return lines


def _summary(material, peak, safety, displacement, review, warnings) -> str:
    factor = "n/a" if safety is None else f"{safety:.2f}"
    text = (
        f"{material['name']}: peak von Mises {peak:.3g} MPa, minimum safety factor {factor}, "
        f"peak displacement {displacement:.3g} mm. Design review: {review['status']} "
        f"at a target safety factor of {review['target_safety_factor']:g}."
    )
    if warnings:
        text += f" Warnings: {len(warnings)}."
    return text


def _unsolved(part, material, mesh_size, fixes, loads, warnings, target, status, note, mesh=None) -> FeaReport:
    mesh_info = _public_mesh(mesh, mesh_size) if mesh else {
        "size_mm": mesh_size,
        "elements": 0,
        "nodes": 0,
        "order": None,
    }
    return FeaReport(
        part=part,
        status=status,
        solver=_SOLVER,
        material=_public_material(material),
        mesh=mesh_info,
        fixes=[_public_region(item) for item in fixes],
        loads=[_public_region(item) for item in loads],
        max_von_mises_mpa=None,
        max_von_mises_at_mm=None,
        max_displacement_mm=None,
        max_displacement_vector_mm=None,
        max_displacement_at_mm=None,
        min_safety_factor=None,
        min_safety_factor_at_mm=None,
        warnings=warnings,
        assumptions=_assumptions(material, mesh or {"element_count": 0, "node_count": 0, "order": 2, "mesh_size_mm": mesh_size}, fixes, loads, target, mesh_size),
        review={
            "target_safety_factor": target,
            "status": "not_reviewed",
            "overstressed": [],
            "understressed": [],
            "note": "There is no stress field to review.",
        },
        views=[],
        note=note,
    )


def _public_material(material: dict) -> dict[str, object]:
    return {
        "id": material["id"],
        "name": material["name"],
        "youngs_modulus_mpa": material["youngs_modulus_mpa"],
        "poisson": material["poisson"],
        "strength_mpa": material["strength_mpa"],
        "strength_kind": material["strength_kind"],
        "printed": material["printed"],
        **(
            {"across_layer_strength_mpa": material["across_layer_strength_mpa"]}
            if material.get("across_layer_strength_mpa") is not None
            else {}
        ),
    }


def _public_mesh(mesh: dict, mesh_size: float) -> dict[str, object]:
    order = mesh.get("order")
    used = mesh.get("mesh_size_mm", mesh_size)
    return {
        "size_mm": used,
        "requested_size_mm": mesh.get("requested_size_mm", mesh_size),
        "elements": int(mesh.get("element_count", 0)),
        "nodes": int(mesh.get("node_count", 0)),
        "order": order,
        "kind": "quadratic tetrahedra" if order == 2 else "linear tetrahedra" if order == 1 else None,
        "degraded": bool(mesh.get("degraded")),
        "thin_feature": bool(mesh.get("thin_feature")),
        "equations": mesh.get("equations"),
        "linear_solver": mesh.get("linear_solver"),
        "notes": list(mesh.get("mesh_notes") or []),
        "simplifications": list(mesh.get("simplifications") or []),
    }


def _public_region(item: dict) -> dict[str, object]:
    return {
        "name": item["name"],
        "type": item["kind"],
        "face_count": len(item["faces"]),
        "face_indices": [face["index"] for face in item["faces"]],
        "area_mm2": round_mm(sum(float(face["area_mm2"]) for face in item["faces"])),
        "centers_mm": [face["center_mm"] for face in item["faces"]],
    }


def _support_rank(points: np.ndarray) -> int:
    if len(points) == 0:
        return 0
    centered = points - points.mean(axis=0)
    singular = np.linalg.svd(centered, compute_uv=False)
    return int(np.sum(singular > 0.2))


def _ccx_error(stdout: str) -> str:
    from aicad.labels import plain_line

    text = stdout if isinstance(stdout, str) else ""
    lines = [plain_line(line) for line in text.splitlines() if "ERROR" in line or "*ERROR" in line]
    lines = [line for line in lines if line]
    if not lines:
        tail = plain_line(text)[-500:]
        return f"CalculiX failed. {tail}" if tail else "CalculiX failed."
    return "CalculiX failed. " + " ".join(lines[-3:])[:500]


def _face_distances(shape: kernel.Shape | None, faces: list[dict], point):
    """How far the point is from each face: from the face itself with a shape, else from its center."""
    if shape is None:
        return lambda face: _distance(face["center_mm"], point)
    measured = kernel.face_distances(shape, [face["index"] for face in faces], point)
    return lambda face: measured.get(face["index"], _distance(face["center_mm"], point))


def _narrow(matches: list[dict], selector: dict, score, direction, shape: kernel.Shape | None = None) -> list[dict]:
    if not matches:
        return []
    explicit = selector.get("all")
    point = _vec(selector["at"], "at") if selector.get("at") is not None else None
    if point is None:
        return matches if explicit is not False else matches[:1]
    # Faces in one plane, or on one cylinder, are the same distance from the
    # point along the normal or from the axis. Among those, the face nearest
    # the point is the one it names, and a tie goes to the nearer center.
    offsets = [score(face, point, direction) for face in matches]
    nearest = min(offsets)
    tied = [face for face, offset in zip(matches, offsets) if offset <= nearest + _SAME_SURFACE_MM]
    apart = _face_distances(shape, tied, point)

    def rank(position: int) -> tuple[int, float, float, float]:
        offset = offsets[position]
        face = matches[position]
        center = _distance(face["center_mm"], point)
        if offset <= nearest + _SAME_SURFACE_MM:
            return (0, 0.0, round(apart(face), 6), center)
        return (1, offset, 0.0, center)

    ranked = [matches[position] for position in sorted(range(len(matches)), key=rank)]
    if explicit:
        return ranked
    return ranked[:1]


def _plane_distance(face: dict, point, direction) -> float:
    normal = face["normal"]
    return abs(_dot(_sub(face["center_mm"], point), normal))


def _axis_distance(face: dict, point, direction) -> float:
    origin = face["axis_origin_mm"]
    axis = face["axis_direction"]
    delta = _sub(point, origin)
    cross = (
        delta[1] * axis[2] - delta[2] * axis[1],
        delta[2] * axis[0] - delta[0] * axis[2],
        delta[0] * axis[1] - delta[1] * axis[0],
    )
    return _distance(cross, (0, 0, 0))


def _point(values) -> Vec3:
    return Vec3(round_mm(float(values[0])), round_mm(float(values[1])), round_mm(float(values[2])))


def _as_list(value, label: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise GeometryError(f"{label} must be a list.")
    return value


def _integer(value, label: str) -> int:
    if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
        raise GeometryError(f"{label} must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be an integer.") from exc


def _optional_float(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        raise GeometryError(f"Expected a number, got {value!r}.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"Expected a number, got {value!r}.") from exc


def _vec(value, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryError(f"{label} must be a list of three numbers.")
    if any(isinstance(component, bool) for component in value):
        raise GeometryError(f"{label} must be a list of three numbers.")
    try:
        result = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a list of three numbers.") from exc
    if not all(math.isfinite(component) for component in result):
        raise GeometryError(f"{label} must be a list of three finite numbers.")
    return result


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = _distance(vector, (0, 0, 0))
    if length < 1e-12:
        raise GeometryError("A direction vector must be non-zero.")
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _dot(a, b) -> float:
    return float(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])


def _sub(a, b) -> tuple[float, float, float]:
    return (float(a[0]) - float(b[0]), float(a[1]) - float(b[1]), float(a[2]) - float(b[2]))


def _distance(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)
