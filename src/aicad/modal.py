"""Natural frequencies, resonance checks, and a simple fatigue estimate.

Gmsh meshes the solid and CalculiX runs ``*FREQUENCY``. Units stay millimeters
and newtons, so mass is tonnes and a frequency in the output is hertz. An
assembly is tied at its joints and solved in the posed frame. The pictures
magnify the mode shape; the color is the unscaled eigenvector magnitude.

Harmonic response, a Miles random-vibration estimate, and a Goodman fatigue
check are closed-form add-ons. They are not a second finite-element solve.
Every report lists the assumptions behind the numbers.
"""

from __future__ import annotations

import math
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from aicad import kernel
from aicad.errors import GeometryError
from aicad.honesty import ROUGH_ESTIMATE, finish
from aicad.fea import (
    _ccx_error,
    _mesh_solid,
    _nodes_for,
    fea_available,
    lookup_material,
    select_faces,
)
from aicad.render import render_scalar_views

_FLOAT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?")
_SOLVER = "CalculiX *FREQUENCY + Gmsh"

# Ultimate strength for the fatigue stand-in, MPa. The static table stores yield
# for metals and tensile strength for plastics, which is not the same number.
_ULTIMATE_MPA = {
    "steel": 420.0,
    "aluminum_6061": 310.0,
}
_SURFACE = {
    "polished": 0.9,
    "machined": 0.8,
    "as_rolled": 0.7,
    "as-rolled": 0.7,
    "printed": 0.5,
}
_ROTATING = {"motor", "spindle", "drill", "rotation", "engine", "rotary"}
_PULSING = {"pump", "hydraulic", "pulsation", "pulse"}


def solve_modal(
    shape: kernel.Shape,
    setup: dict,
    out_dir: Path,
    stem: str = "modal",
    part_name: str | None = None,
) -> dict:
    """Natural frequencies of one solid. ``setup`` names the material and the held faces."""
    if not isinstance(setup, dict):
        raise GeometryError("A modal setup is an object with material, fixes, and optional excitations.")
    ready, reason = fea_available()
    if not ready:
        raise GeometryError(reason)
    material = lookup_material(str(setup.get("material") or ""))
    mesh_size = _positive(setup.get("mesh_size_mm"), 3.0, "mesh_size_mm")
    nmodes = int(_positive(setup.get("modes"), 6, "modes"))
    if nmodes < 1 or nmodes > 20:
        raise GeometryError("modes is how many frequencies to ask for, from 1 to 20.")
    order = 2 if int(setup.get("order") or 2) != 1 else 1
    fixes = setup.get("fixes")
    if not isinstance(fixes, list) or not fixes:
        raise GeometryError("Name at least one face to hold. A free solid's first modes are rigid-body motion.")
    catalog = kernel.describe_faces(shape)
    held = []
    for index, item in enumerate(fixes):
        if not isinstance(item, dict):
            raise GeometryError(f"Fix {index} must be an object with select.")
        held.extend(select_faces(catalog, item.get("select") or {}))
    if not held:
        raise GeometryError("The support selector did not match a face.")

    mesh, used_order, warnings = _mesh_with_fallback(shape, mesh_size, order)
    degraded = bool(mesh.get("degraded"))
    simplifications = list(mesh.get("simplifications") or [])
    fixed = _nodes_for(mesh, [face["index"] for face in held])
    if len(fixed) == 0:
        raise GeometryError("The support face has no mesh nodes. Use a finer mesh, or pick a larger face.")
    body = {
        "name": part_name or "part",
        "points": mesh["points"],
        "elements": mesh["elements"],
        "order": used_order,
        "material": material,
        "offset": 0,
    }
    frequencies, vectors, log = _run_frequency([body], fixed, [], nmodes, _timeout(setup, 180))
    if int(body["order"]) != used_order:
        warnings.append(
            "Quadratic tetrahedra had a nonpositive Jacobian. Linear tetrahedra were solved instead. "
            "Linear elements are too stiff, so these frequencies are high and are not a clear or a resonance."
        )
        used_order = int(body["order"])
        degraded = True
        simplifications.append(
            "Quadratic tetrahedra had a nonpositive Jacobian. Linear tetrahedra were solved instead."
        )
    surface = _surface_mesh([mesh])
    return _report(
        frequencies,
        vectors,
        surface,
        setup,
        out_dir,
        stem,
        warnings,
        log,
        part=part_name,
        material_note=str(material["name"]),
        mesh_size=mesh_size,
        order=used_order,
        node_count=int(mesh["node_count"]),
        element_count=int(len(mesh["elements"])),
        degraded=degraded,
        simplifications=simplifications,
        support={
            "automatic": False,
            "faces": [face["index"] for face in held],
            "description": "The faces named in fixes are held on all three translations.",
        },
    )


def solve_modal_assembly(spec: dict, shapes: dict, setup: dict | None, out_dir: Path) -> dict:
    """Frequencies of a posed assembly. Joints are tied, so a revolute does not flex."""
    from aicad.kinematics import solve as solve_kinematics

    setup = setup or {}
    if not isinstance(setup, dict):
        raise GeometryError("An assembly modal setup is an object. Fixes may be omitted.")
    ready, reason = fea_available()
    if not ready:
        raise GeometryError(reason)
    solved = solve_kinematics(spec)
    mesh_size = _positive(setup.get("mesh_size_mm"), 8.0, "mesh_size_mm")
    nmodes = int(_positive(setup.get("modes"), 6, "modes"))
    if nmodes < 1 or nmodes > 20:
        raise GeometryError("modes is how many frequencies to ask for, from 1 to 20.")
    order = 2 if int(setup.get("order") or 2) != 1 else 1
    fallback = str(setup.get("material") or "steel")

    volumes = {}
    for instance in solved["instances"]:
        shape = shapes.get(instance["part"])
        if shape is None:
            raise GeometryError(f"Instance {instance['name']!r} needs part {instance['part']!r}.")
        volumes[instance["name"]] = float(kernel.measure(shape).volume_mm3)
    largest = max(volumes.values()) if volumes else 1.0
    cutoff = 0.02 * largest
    skipped = []
    kept = []
    for instance in solved["instances"]:
        if volumes[instance["name"]] < cutoff:
            skipped.append(
                {
                    "instance": instance["name"],
                    "volume_mm3": round(volumes[instance["name"]], 2),
                    "reason": "Under 2% of the largest instance, so it is left out of the mesh.",
                }
            )
            continue
        kept.append(instance)
    if not kept:
        raise GeometryError("Every instance was below the volume cutoff, so there is nothing to mesh.")

    warnings = [
        f"Skipped {item['instance']} ({item['volume_mm3']} mm³)." for item in skipped
    ]
    bodies = []
    local_meshes = []
    offset = 0
    used_order = order
    degraded = False
    simplifications: list[str] = []
    for instance in kept:
        shape = shapes[instance["part"]]
        mesh, used_order, mesh_warnings = _mesh_with_fallback(shape, mesh_size, order)
        warnings.extend(f"{instance['name']}: {item}" for item in mesh_warnings)
        if mesh.get("degraded"):
            degraded = True
            simplifications.extend(f"{instance['name']}: {item}" for item in mesh.get("simplifications") or [])
        matrix = np.array(instance["matrix"], dtype=np.float64)
        rotation = matrix[:3, :3]
        origin = matrix[:3, 3]
        world = mesh["points"] @ rotation.T + origin
        material = lookup_material(str(instance.get("material") or fallback))
        bodies.append(
            {
                "name": instance["name"],
                "points": world,
                "elements": mesh["elements"],
                "order": used_order,
                "material": material,
                "offset": offset,
            }
        )
        local_meshes.append(mesh)
        offset += len(world)
    if any(body["order"] != bodies[0]["order"] for body in bodies):
        warnings.append("Instances did not all keep the same element order. The lower order is what was solved.")

    by_name = {body["name"]: body for body in bodies}
    mesh_by_name = {instance["name"]: mesh for instance, mesh in zip(kept, local_meshes)}
    shape_by_name = {instance["name"]: shapes[instance["part"]] for instance in kept}
    skipped_names = {item["instance"] for item in skipped}
    equations, tie_notes = _tie_joints(spec, solved, by_name, mesh_size, skipped_names)
    warnings.extend(tie_notes)

    support, fixed_local = _assembly_supports(setup, kept, shape_by_name, mesh_by_name, solved)
    fixed = []
    dependent = {dep for dep, _indep in equations}
    for name, nodes in fixed_local:
        body = by_name[name]
        for node in nodes:
            global_node = int(node) + body["offset"]
            if global_node in dependent:
                continue
            fixed.append(global_node)
    if not fixed:
        raise GeometryError(
            "No support nodes were found. Name a face on a grounded instance, or use a finer mesh so the bottom face has nodes."
        )
    frequencies, vectors, log = _run_frequency(bodies, np.array(sorted(set(fixed)), dtype=np.int32), equations, nmodes, _timeout(setup, 300))
    if int(bodies[0]["order"]) != order:
        warnings.append(
            "Quadratic tetrahedra had a nonpositive Jacobian. Linear tetrahedra were solved instead. "
            "Linear elements are too stiff, so these frequencies are high and are not a clear or a resonance."
        )
        degraded = True
        simplifications.append("Linear tetrahedra were solved because the quadratic elements were folded.")
    surface = _surface_mesh(local_meshes, bodies)
    materials = sorted({str(body["material"]["name"]) for body in bodies})
    return _report(
        frequencies,
        vectors,
        surface,
        setup,
        out_dir,
        "modal",
        warnings,
        log,
        assembly=spec.get("name"),
        material_note=", ".join(materials),
        mesh_size=mesh_size,
        order=int(bodies[0]["order"]),
        node_count=offset,
        element_count=sum(len(body["elements"]) for body in bodies),
        degraded=degraded,
        simplifications=simplifications,
        support=support,
        extra={
            "skipped_instances": skipped,
            "ties": {"pair_count": len(equations) // 3, "note": "Each pair constrains three translations."},
            "pose": "Joints are locked at the stored pose. A revolute does not flex in this solve.",
        },
    )


def classify_resonance(frequencies, excitations, margin: float = 0.10) -> dict:
    """Flag a natural frequency that sits near an excitation or one of its harmonics."""
    modes = _frequency_list(frequencies)
    sources = [_normalize_excitation(item, index) for index, item in enumerate(excitations or [])]
    if not 0.01 <= float(margin) <= 0.5:
        raise GeometryError("margin is the fractional gap that counts as near, from 0.01 to 0.5.")
    flags = []
    rigid = [item for item in modes if item["frequency_hz"] < 1.0]
    flexible = [item for item in modes if item["frequency_hz"] >= 1.0]
    for mode in flexible:
        fn = mode["frequency_hz"]
        for source in sources:
            for harmonic, excited in enumerate(source["harmonics_hz"], start=1):
                if excited <= 0:
                    continue
                separation = abs(fn - excited) / fn
                if separation <= float(margin):
                    flags.append(
                        {
                            "mode": mode["index"],
                            "frequency_hz": round(fn, 3),
                            "source": source["name"],
                            "kind": source["kind"],
                            "harmonic": harmonic,
                            "excitation_hz": round(excited, 3),
                            "separation": round(separation, 4),
                        }
                    )
    suggestions = _suggestions(flags, rigid, sources, float(margin))
    return {
        "status": "resonance" if flags else "clear",
        "margin": float(margin),
        "flags": flags,
        "rigid_body_modes": [item["index"] for item in rigid],
        "suggestions": suggestions,
        "excitations": sources,
    }


def harmonic_response(spec: dict, natural_hz: float, modal_force: float, phi_norm: float) -> dict:
    """Single-mode displacement at the driven node. Modal mass is 1 tonne."""
    if not isinstance(spec, dict):
        raise GeometryError("harmonic is an object with frequency_hz and force_n.")
    drive = _positive(spec.get("frequency_hz"), None, "harmonic frequency_hz")
    zeta = _positive(spec.get("zeta"), 0.02, "zeta")
    if not 0.001 <= zeta <= 0.3:
        raise GeometryError("zeta is the viscous damping ratio, from 0.001 to 0.3.")
    force = spec.get("force_n")
    if force is None:
        raise GeometryError("harmonic.force_n is the force vector in newtons.")
    receptance, amplification = _receptance(natural_hz, drive, zeta)
    modal_displacement = abs(receptance * float(modal_force))
    displacement = modal_displacement * float(phi_norm)
    magnitude = float(np.linalg.norm(np.array(force, dtype=np.float64)))
    stress = None
    if spec.get("stress_per_newton") is not None:
        stress = float(spec["stress_per_newton"]) * magnitude * amplification
    elif spec.get("static_stress_mpa") is not None:
        stress = float(spec["static_stress_mpa"]) * amplification
    return finish(
        {
            "model": "single-degree-of-freedom",
            "natural_hz": round(float(natural_hz), 3),
            "drive_hz": round(drive, 3),
            "zeta": zeta,
            "modal_force": round(float(modal_force), 6),
            "amplification": round(amplification, 3),
            "displacement_mm": round(displacement, 6),
            "stress_amplitude_mpa": None if stress is None else round(stress, 4),
            "assumptions": [
                "One mode. The eigenvector is mass-normalized, so the modal mass is 1 tonne and the receptance is in mm/N.",
                f"Damping ratio ζ = {zeta:g} is an assumption. A welded steel frame is often near 0.02. It was not measured.",
                "The force is applied at one node, along the eigenvector there. A distributed load would use the integral of φ·f.",
                "Stress is reported only when static_stress_mpa or stress_per_newton is supplied, and it is that static stress times the amplification.",
                "Modes other than this one are omitted. Close modes would add to the displacement.",
            ],
        },
        kind=ROUGH_ESTIMATE,
        name="Harmonic response",
        what="Harmonic displacement is a single-mode estimate with an assumed damping ratio, not a second finite-element solve.",
        limits=[
            "Other modes are left out.",
            "The damping ratio was not measured on this part.",
        ],
    )


def random_response(spec: dict, natural_hz: float) -> dict:
    """Miles' equation: the 1σ acceleration of one mode in a flat acceleration PSD."""
    if not isinstance(spec, dict):
        raise GeometryError("random is an object with psd_g2_per_hz.")
    psd = _positive(spec.get("psd_g2_per_hz"), None, "psd_g2_per_hz")
    zeta = _positive(spec.get("zeta"), 0.02, "zeta")
    if not 0.001 <= zeta <= 0.3:
        raise GeometryError("zeta is the viscous damping ratio, from 0.001 to 0.3.")
    quality = 1.0 / (2.0 * zeta)
    sigma_g = math.sqrt(math.pi / 2.0 * float(natural_hz) * quality * psd)
    stress = None
    if spec.get("stress_per_g") is not None:
        stress = float(spec["stress_per_g"]) * sigma_g
    return finish(
        {
            "model": "Miles",
            "natural_hz": round(float(natural_hz), 3),
            "zeta": zeta,
            "quality_factor": round(quality, 2),
            "psd_g2_per_hz": psd,
            "sigma_g": round(sigma_g, 4),
            "stress_sigma_mpa": None if stress is None else round(stress, 4),
            "assumptions": [
                "Miles' equation for a single mode in a white acceleration spectrum: σ_g = sqrt(π/2 · fn · Q · PSD), with Q = 1/(2ζ).",
                "The PSD is flat across the resonance in g²/Hz. A real spectrum that rolls off changes this number.",
                "The result is a 1σ acceleration. Peaks of a Gaussian ride near 3σ. This is not a fatigue spectrum yet.",
                "Stress is σ_g times stress_per_g when that number is supplied. It is not computed from the mesh.",
            ],
        },
        kind=ROUGH_ESTIMATE,
        name="Random vibration",
        what="Random response is Miles' equation on a flat spectrum. It is a rough estimate, not a finite-element solve.",
        limits=[
            "The spectrum is flat. A real spectrum that rolls off changes the number.",
            "This is a 1σ acceleration, not a fatigue life.",
        ],
    )


def fatigue_check(spec: dict) -> dict:
    """Goodman check of a stated stress amplitude against an estimated endurance limit."""
    if not isinstance(spec, dict):
        raise GeometryError("A fatigue check needs stress_amplitude_mpa and a material.")
    amplitude = _positive(spec.get("stress_amplitude_mpa"), None, "stress_amplitude_mpa")
    mean = float(spec.get("mean_stress_mpa") or 0.0)
    material_name = str(spec.get("material") or "steel")
    material = lookup_material(material_name)
    key = str(material["id"])
    if spec.get("ultimate_mpa") is not None:
        ultimate = _positive(spec["ultimate_mpa"], None, "ultimate_mpa")
        ultimate_note = "Ultimate strength was supplied with the check."
    elif key in _ULTIMATE_MPA:
        ultimate = _ULTIMATE_MPA[key]
        ultimate_note = f"Ultimate strength is the handbook stand-in {_ULTIMATE_MPA[key]:.0f} MPa for {material['name']}."
    else:
        ultimate = float(material["strength_mpa"])
        ultimate_note = (
            f"{material['name']} has no separate ultimate in this library. "
            f"The tensile strength {ultimate:.0f} MPa is used as Sut."
        )
    surface_name = str(spec.get("surface") or ("printed" if material.get("printed") else "machined")).lower().replace(" ", "_")
    if surface_name not in _SURFACE:
        known = ", ".join(sorted({name for name in _SURFACE if "_" in name or name in ("polished", "machined", "printed")}))
        raise GeometryError(f"Surface {surface_name!r} is unknown. Use polished, machined, as_rolled, or printed.")
    surface = _SURFACE[surface_name]
    section = float(spec.get("section_mm") or 8.0)
    if section <= 0:
        raise GeometryError("section_mm is the thickness that sets the size factor, and it must be positive.")
    size = 0.85 if section > 10.0 else 0.9
    if key == "steel":
        prime = 0.5 * ultimate if ultimate <= 1400.0 else 700.0
        endurance_note = "Steel uses Se' = 0.5·Sut up to 1400 MPa, then 700 MPa."
    elif key == "aluminum_6061":
        prime = 0.3 * ultimate
        endurance_note = "Aluminum has no true endurance limit. Se' = 0.3·Sut is a long-life stand-in, not a knee in the S-N curve."
    else:
        prime = 0.3 * ultimate
        endurance_note = "This material uses Se' = 0.3·Sut. Plastics and prints do not have a steel endurance limit."
    endurance = prime * surface * size
    mean_used = max(mean, 0.0)
    if amplitude <= 0:
        raise GeometryError("stress_amplitude_mpa must be positive.")
    goodman = amplitude / endurance + mean_used / ultimate
    life_factor = 1.0 / goodman if goodman > 0 else None
    amplification = spec.get("amplification")
    scaled = amplitude
    if amplification is not None:
        amplification = float(amplification)
        if amplification > 1.0:
            scaled = amplitude * amplification
            goodman = scaled / endurance + mean_used / ultimate
            life_factor = 1.0 / goodman if goodman > 0 else None
    target = float(spec.get("target_safety_factor") or 2.0)
    status = "pass" if life_factor is not None and life_factor >= target else "fail"
    return finish(
        {
        "status": status,
        "material": material["name"],
        "stress_amplitude_mpa": round(amplitude, 3),
        "mean_stress_mpa": round(mean, 3),
        "mean_used_mpa": round(mean_used, 3),
        "ultimate_mpa": round(ultimate, 1),
        "surface": surface_name,
        "surface_factor": surface,
        "size_factor": size,
        "section_mm": section,
        "endurance_mpa": round(endurance, 2),
        "amplification": None if amplification is None else round(float(amplification), 3),
        "compared_amplitude_mpa": round(scaled, 3),
        "safety_factor": None if life_factor is None else round(life_factor, 3),
        "target_safety_factor": target,
        "assumptions": [
            ultimate_note,
            endurance_note,
            f"Surface factor {surface:g} is for {surface_name.replace('_', ' ')}. Size factor {size:g} is {('0.85 above' if section > 10 else '0.9 at or below')} 10 mm.",
            "Goodman: 1/n = σa/Se + σm/Sut. A compressive mean is not added. There is no Marin reliability or temperature factor.",
            "The stress amplitude is an input. This check does not compute it from a load or a mesh.",
            "A resonance nearby multiplies the amplitude only when amplification is passed in. Quasi-static stress is not the resonant stress.",
            "Passing a factor of 2 on this estimate is not a certified life. The S-N curve, the notch, and the real spectrum are not in the model.",
        ],
        "note": (
            f"Goodman safety factor {life_factor:.2f} against a target of {target:g}. "
            f"Endurance limit used here is {endurance:.1f} MPa."
            if life_factor is not None
            else "The Goodman denominator was zero."
        ),
        },
        kind=ROUGH_ESTIMATE,
        name="Fatigue",
        what="The Goodman check is a rough estimate of a stress you type in. It is not a life from the mesh.",
        limits=[
            "The stress amplitude is an input. This check does not compute it from a load.",
            "There is no notch, no reliability factor, and no measured S-N curve.",
            "A pass against a factor of 2 is not a certified life.",
        ],
    )


def _report(
    frequencies,
    vectors,
    surface,
    setup,
    out_dir,
    stem,
    warnings,
    log,
    support,
    mesh_size,
    order,
    node_count,
    element_count,
    material_note,
    part=None,
    assembly=None,
    extra=None,
    degraded: bool = False,
    simplifications: list | None = None,
) -> dict:
    modes = []
    for index, (freq, vector) in enumerate(zip(frequencies, vectors), start=1):
        modes.append(
            {
                "index": index,
                "frequency_hz": round(freq["hz"], 3),
                "omega_rad_s": round(freq["omega"], 4),
                "eigenvalue": freq["eigenvalue"],
                "rigid_body": freq["hz"] < 1.0,
            }
        )
    margin = float(setup.get("margin") or 0.10)
    resonance = demote_if_simplified(
        classify_resonance(modes, setup.get("excitations") or [], margin),
        degraded,
    )
    if resonance["rigid_body_modes"]:
        warnings.append(
            "A natural frequency below 1 Hz is a rigid-body mode. A support is missing, or a joint was not tied."
        )
    views = _render_modes(modes, vectors, surface, setup, out_dir, stem, warnings)
    flexible = [mode for mode in modes if not mode["rigid_body"]]
    chosen = flexible[0] if flexible else (modes[0] if modes else None)
    harmonic = None
    if setup.get("harmonic") and chosen is not None:
        harmonic = _harmonic_from_vector(setup["harmonic"], chosen, vectors[chosen["index"] - 1], surface["points_ref"])
    random_est = None
    if setup.get("random") and chosen is not None:
        random_est = random_response(setup["random"], chosen["frequency_hz"])
    fatigue = None
    if setup.get("fatigue"):
        fatigue_spec = dict(setup["fatigue"])
        if "material" not in fatigue_spec and setup.get("material"):
            fatigue_spec["material"] = setup["material"]
        fatigue = fatigue_check(fatigue_spec)
        if resonance["status"] == "resonance" and fatigue_spec.get("amplification") is None:
            warnings.append(
                "A resonance was flagged. The fatigue stress was not multiplied by a dynamic amplification. "
                "Pass fatigue.amplification when the stress amplitude is quasi-static and you want the resonant value."
            )
    flat_views = []
    for mode in modes:
        flat_views.extend(mode.get("views") or [])
    assumptions = _assumptions(assembly is not None)
    if simplifications:
        assumptions = [*assumptions, *simplifications]
    note = _headline(modes, resonance)
    if degraded:
        what = (
            "Natural frequencies from a simplified or linear mesh. "
            "These numbers run high because the mesh is too stiff. This is not a clear and not a confirmed resonance."
        )
        limits = [
            "A simplified solid or linear tetrahedra cannot be read as a clear or as a resonance.",
            "Remesh the original solid with quadratic elements before treating a frequency as real.",
            "The pictures are magnified shapes, not how far the part moves.",
        ]
    else:
        what = (
            "Natural frequencies from a Gmsh mesh and a CalculiX frequency solve. "
            "A rough estimate. The pictures are magnified shapes, not how far the part moves."
        )
        limits = [
            "Damping is not in the eigenvalue problem.",
            "No contact, no plasticity, and no joint flexibility on a single part.",
            "CalculiX runs as a separate program. It is not linked into aicad.",
        ]
    payload = {
        "solver": _SOLVER,
        "part": part,
        "assembly": assembly,
        "material": material_note,
        "mesh_size_mm": mesh_size,
        "element_order": order,
        "node_count": node_count,
        "element_count": element_count,
        "modes": modes,
        "resonance": resonance,
        "support": support,
        "harmonic": harmonic,
        "random": random_est,
        "fatigue": fatigue,
        "warnings": warnings,
        "assumptions": assumptions,
        "views": flat_views,
        "note": note,
        "log_tail": log[-400:] if log else "",
    }
    if extra:
        payload.update(extra)
    return finish(payload, kind=ROUGH_ESTIMATE, name="Vibration", what=what, limits=limits)


def _harmonic_from_vector(spec: dict, mode: dict, vector: np.ndarray, points: np.ndarray) -> dict:
    force = np.array(spec.get("force_n") or [0.0, 0.0, 0.0], dtype=np.float64)
    if force.shape != (3,):
        raise GeometryError("harmonic.force_n is a vector of three newtons.")
    if spec.get("at") is not None:
        target = np.array(spec["at"], dtype=np.float64)
        node = int(np.argmin(np.linalg.norm(points - target, axis=1)))
        where = "the node nearest the given point"
    else:
        node = int(np.argmax(np.linalg.norm(vector, axis=1)))
        where = "the node with the largest modal displacement"
    phi = vector[node]
    modal_force = float(np.dot(phi, force))
    report = harmonic_response(spec, mode["frequency_hz"], modal_force, float(np.linalg.norm(phi)))
    report["mode"] = mode["index"]
    report["node"] = node + 1
    report["where"] = where
    report["assumptions"] = [f"The force is applied at {where}."] + report["assumptions"]
    return report


def _render_modes(modes, vectors, surface, setup, out_dir, stem, warnings) -> list:
    how_many = int(setup.get("render_modes") if setup.get("render_modes") is not None else 2)
    if how_many <= 0 or surface["triangles"].size == 0:
        return []
    points = surface["points"]
    span = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    target = 0.15 * span if span > 1e-6 else 1.0
    rendered = 0
    pictures = []
    for mode, vector in zip(modes, vectors):
        if mode["rigid_body"]:
            continue
        if rendered >= how_many:
            break
        full = vector
        if len(full) != len(surface["points_ref"]):
            warnings.append(f"Mode {mode['index']} could not be drawn because the mesh and the result disagreed.")
            continue
        magnitude = np.linalg.norm(full, axis=1)
        peak = float(magnitude.max()) if len(magnitude) else 0.0
        scale = target / peak if peak > 1e-15 else 0.0
        displaced = surface["points_ref"] + full * scale
        shown = displaced[surface["used"]]
        color = magnitude[surface["used"]]
        title = f"Mode {mode['index']}  {mode['frequency_hz']:.2f} Hz  ·  shape magnified, not an amplitude"
        views = render_scalar_views(
            shown,
            surface["triangles"],
            color,
            Path(out_dir),
            stem,
            f"mode{mode['index']}",
            title,
            "rel",
            "modal displacement, unscaled",
        )
        mode["views"] = [view.to_dict() for view in views]
        mode["display_scale"] = round(scale, 4)
        pictures.extend(mode["views"])
        rendered += 1
    return pictures


def _mesh_with_fallback(shape, mesh_size: float, order: int):
    """Mesh, including the safer retries inside ``_mesh_solid``. A failure stays a GeometryError."""
    mesh = _mesh_solid(shape, mesh_size, order)
    warnings = [str(line) for line in mesh.get("mesh_notes") or []]
    return mesh, int(mesh["order"]), warnings


def demote_if_simplified(resonance: dict, degraded: bool) -> dict:
    """A stiff mesh must not read as a clear or as a confirmed resonance."""
    if not degraded:
        return resonance
    updated = dict(resonance)
    updated["computed_status"] = resonance.get("status")
    updated["status"] = "inconclusive"
    updated["reliable"] = False
    updated["suggestions"] = [
        "The mesh was simplified or uses linear tetrahedra, which are too stiff. "
        "A frequency from this mesh is high. Do not treat a clear or a resonance flag as a result. "
        "Mesh the original solid with quadratic elements and read that run.",
        *(resonance.get("suggestions") or []),
    ]
    return updated


def _run_frequency(bodies, fixed_nodes, equations, nmodes: int, timeout: int):
    warning = _frequency_once(bodies, fixed_nodes, equations, nmodes, timeout)
    if warning[0] is None and warning[2] and "nonpositive jacobian" in warning[2].lower():
        if any(int(body["order"]) == 2 for body in bodies):
            for body in bodies:
                body["elements"] = np.array(body["elements"])[:, :4]
                body["order"] = 1
            warning = _frequency_once(bodies, fixed_nodes, equations, nmodes, timeout)
            if warning[2] is None:
                warning = (warning[0], warning[1], "linear-fallback")
    frequencies, vectors, detail = warning
    if frequencies is None:
        raise GeometryError(detail or "CalculiX did not return frequencies.")
    if detail == "linear-fallback":
        detail = ""
    return frequencies, vectors, detail or ""


def _frequency_once(bodies, fixed_nodes, equations, nmodes: int, timeout: int):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        from aicad.paths import publish_text

        publish_text(
            path / "job.inp",
            _frequency_deck(bodies, fixed_nodes, equations, nmodes),
            root=path,
        )
        # ccx is GPL-2.0-only. Frequency solves launch it as a separate process.
        from aicad.prereqs import find_program

        solver = find_program("ccx")
        if solver is None:
            return None, None, "CalculiX (ccx) is not on PATH."
        from aicad.sandbox import program_environment

        completed = subprocess.run(
            [solver, "-i", "job"],
            cwd=directory,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            env=program_environment(),
        )
        dat = path / "job.dat"
        frd = path / "job.frd"
        stdout = completed.stdout or ""
        if "nonpositive jacobian" in stdout.lower():
            return None, None, "nonpositive jacobian"
        if completed.returncode != 0 or not dat.is_file():
            return None, None, _ccx_error(stdout)
        node_count = sum(len(body["points"]) for body in bodies)
        try:
            from aicad.file_access import read_text

            frequencies = _parse_dat(read_text(dat, maximum=64 * 1024 * 1024, errors="replace"), nmodes)
        except GeometryError as exc:
            return None, None, str(exc)
        vectors = _parse_mode_shapes(frd, node_count) if frd.is_file() else []
        while len(vectors) < len(frequencies):
            vectors.append(np.zeros((node_count, 3), dtype=np.float64))
        vectors = vectors[: len(frequencies)]
        return frequencies, vectors, stdout


def _frequency_deck(bodies, fixed_nodes, equations, nmodes: int) -> str:
    lines = ["*HEADING", "aicad frequency", "*NODE"]
    for body in bodies:
        offset = int(body["offset"])
        for index, point in enumerate(body["points"]):
            node = offset + index + 1
            lines.append(f"{node}, {point[0]:.6f}, {point[1]:.6f}, {point[2]:.6f}")
    for body_index, body in enumerate(bodies):
        order = int(body["order"])
        element_type = "C3D10" if order == 2 else "C3D4"
        width = 10 if order == 2 else 4
        elset = f"E{body_index}"
        lines.append(f"*ELEMENT, TYPE={element_type}, ELSET={elset}")
        element_base = sum(len(earlier["elements"]) for earlier in bodies[:body_index])
        offset = int(body["offset"])
        for index, element in enumerate(body["elements"]):
            nodes = ", ".join(str(int(node) + offset + 1) for node in element[:width])
            lines.append(f"{element_base + index + 1}, {nodes}")
    for body_index, body in enumerate(bodies):
        material = body["material"]
        name = f"MAT{body_index}"
        density = float(material["density_g_cm3"]) * 1e-9
        lines += [
            f"*MATERIAL, NAME={name}",
            "*ELASTIC",
            f"{float(material['youngs_modulus_mpa'])}, {float(material['poisson'])}",
            "*DENSITY",
            f"{density:.8e}",
            f"*SOLID SECTION, ELSET=E{body_index}, MATERIAL={name}",
        ]
    if len(fixed_nodes):
        lines.append("*BOUNDARY")
        for node in fixed_nodes:
            lines.append(f"{int(node) + 1}, 1, 3, 0.")
    for dependent, independent in equations:
        for dof in (1, 2, 3):
            lines += [
                "*EQUATION",
                "2",
                f"{dependent + 1}, {dof}, 1., {independent + 1}, {dof}, -1.",
            ]
    lines += ["*STEP", "*FREQUENCY", str(int(nmodes)), "*NODE FILE", "U", "*END STEP"]
    return "\n".join(lines) + "\n"


def _parse_dat(text: str, nmodes: int) -> list[dict]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        folded = line.upper()
        if "EIGENVALUE" in folded and "FREQUENCY" in folded:
            start = index + 1
            break
        if "CYCLES/TIME" in folded:
            start = index + 1
            break
    if start is None:
        raise GeometryError("CalculiX finished without an eigenvalue table.")
    found = []
    for line in lines[start:]:
        folded = line.upper()
        compact = "".join(folded.split())
        if "PARTICIPATION" in compact or "EFFECTIVEMODAL" in compact:
            break
        if not line.strip():
            continue
        numbers = _FLOAT.findall(line.replace("D", "E").replace("d", "E"))
        if len(numbers) < 4:
            continue
        mode_no = int(float(numbers[0]))
        if mode_no < 1 or mode_no > nmodes + 5:
            continue
        # mode, eigenvalue (ω²), rad/time, cycles/time, optional imaginary
        eigenvalue = float(numbers[1])
        omega = float(numbers[2])
        hz = float(numbers[3])
        if any(item["index"] == mode_no for item in found):
            continue
        found.append({"index": mode_no, "eigenvalue": eigenvalue, "omega": omega, "hz": hz})
    if not found:
        raise GeometryError("The eigenvalue table had no modes.")
    found.sort(key=lambda item: item["index"])
    return found[:nmodes]


def _parse_mode_shapes(path: Path, node_count: int) -> list[np.ndarray]:
    if path is None or not Path(path).is_file():
        return []
    modes: list[np.ndarray] = []
    current = None
    seen = 0
    from aicad.file_access import read_text

    for line in read_text(path, maximum=64 * 1024 * 1024, errors="replace").splitlines():
        head = line.strip().upper()
        if "PMODE" in head:
            if current is not None and seen > 0:
                modes.append(current)
            current = np.zeros((node_count, 3), dtype=np.float64)
            seen = 0
            continue
        if line.startswith(" -4") and "DISP" in line.upper():
            if current is None:
                current = np.zeros((node_count, 3), dtype=np.float64)
                seen = 0
            continue
        if line.startswith(" -3"):
            continue
        if current is None or not line.startswith(" -1"):
            continue
        body = line[3:]
        matched = re.match(r"\s*(\d+)", body)
        if not matched:
            continue
        node_id = int(matched.group(1))
        if node_id < 1 or node_id > node_count:
            continue
        values = [float(token) for token in re.findall(r"[+-]?\d+\.\d+E[+-]\d+", body[matched.end() :], flags=re.I)]
        if len(values) >= 3:
            current[node_id - 1] = values[:3]
            seen += 1
    if current is not None and seen > 0:
        modes.append(current)
    return modes


def _tie_joints(spec, solved, bodies, mesh_size: float, skipped: set[str]):
    origins = {row["name"]: np.array(row["origin_mm"], dtype=np.float64) for row in solved["joints"]}
    equations = []
    notes = []
    seen_dependent = set()
    for joint in spec.get("joints") or []:
        parent = joint["parent"]
        child = joint["child"]
        if parent in skipped or child in skipped:
            notes.append(f"Joint {joint['name']} was not tied because {parent} or {child} was left out of the mesh.")
            continue
        if parent not in bodies or child not in bodies:
            notes.append(f"Joint {joint['name']} was not tied.")
            continue
        origin = origins.get(joint["name"])
        if origin is None:
            continue
        pairs, radius = _nearest_pairs(bodies[parent], bodies[child], origin, mesh_size, seen_dependent)
        if len(pairs) < 3:
            notes.append(
                f"Joint {joint['name']} has only {len(pairs)} node pairs within {radius:.1f} mm. The tie is weak."
            )
        for dependent, independent in pairs:
            equations.append((dependent, independent))
            seen_dependent.add(dependent)
        notes.append(
            f"Joint {joint['name']} ties {len(pairs)} node pairs around {origin.tolist()} within {radius:.1f} mm. "
            "The child nodes follow the parent. The joint cannot rotate in this model."
        )
    return equations, notes


def _nearest_pairs(parent, child, origin, mesh_size: float, used: set[int]):
    """Spread a few ties across the joint face. A cluster at the center acts like a hinge."""
    radius = max(float(mesh_size) * 1.25, 2.0)
    reach = max(float(mesh_size) * 1.75, 3.0)
    best = []
    best_radius = radius
    for _ in range(7):
        parent_ids = np.where(np.linalg.norm(parent["points"] - origin, axis=1) <= radius)[0]
        child_ids = np.where(np.linalg.norm(child["points"] - origin, axis=1) <= radius)[0]
        picked = _spread_nodes(parent["points"], parent_ids, origin, 8)
        pairs = []
        taken = set()
        for local_parent in picked:
            if len(child_ids) == 0:
                break
            distances = np.linalg.norm(child["points"][child_ids] - parent["points"][int(local_parent)], axis=1)
            choice = int(np.argmin(distances))
            if float(distances[choice]) > reach:
                continue
            local_child = int(child_ids[choice])
            global_child = local_child + int(child["offset"])
            if local_child in taken or global_child in used:
                continue
            taken.add(local_child)
            pairs.append((global_child, int(local_parent) + int(parent["offset"])))
        if len(pairs) > len(best):
            best, best_radius = pairs, radius
        if len(pairs) >= 6:
            return pairs, radius
        radius *= 1.55
        reach = max(reach, radius * 0.65)
    return best, best_radius


def _spread_nodes(points: np.ndarray, ids: np.ndarray, origin: np.ndarray, count: int) -> list[int]:
    if len(ids) == 0:
        return []
    remaining = [int(node) for node in ids]
    if len(remaining) <= count:
        return remaining
    first = max(remaining, key=lambda node: float(np.linalg.norm(points[node] - origin)))
    chosen = [first]
    while len(chosen) < count and len(chosen) < len(remaining):
        def clearance(node: int) -> float:
            return min(float(np.linalg.norm(points[node] - points[other])) for other in chosen)

        nxt = max((node for node in remaining if node not in chosen), key=clearance)
        chosen.append(nxt)
    return chosen


def _assembly_supports(setup, kept, shapes, meshes, solved):
    children = {joint["child"] for joint in solved["joints"]}
    grounded = [instance["name"] for instance in kept if instance["name"] not in children]
    requested = setup.get("fixes")
    chosen = []
    automatic = False
    if requested:
        if not isinstance(requested, list):
            raise GeometryError("fixes is a list of {instance, select}.")
        for item in requested:
            if not isinstance(item, dict):
                raise GeometryError("Each assembly fix is an object with instance and select.")
            instance_name = item.get("instance") or (grounded[0] if len(grounded) == 1 else None)
            if instance_name not in meshes:
                raise GeometryError(f"Fix instance {instance_name!r} is not in the meshed assembly.")
            catalog = meshes[instance_name]["surface_of_face"]["catalog"]
            faces = select_faces(catalog, item.get("select") or {})
            nodes = _nodes_for(meshes[instance_name], [face["index"] for face in faces])
            chosen.append((instance_name, nodes, f"{instance_name} faces {[face['index'] for face in faces]}"))
    else:
        automatic = True
        if not grounded:
            raise GeometryError("This assembly has no grounded instance to hold.")
        for name in grounded:
            matrix = np.array(next(item["matrix"] for item in kept if item["name"] == name), dtype=np.float64)
            selector, description = _bottom_face(shapes[name], matrix)
            faces = select_faces(meshes[name]["surface_of_face"]["catalog"], selector)
            nodes = _nodes_for(meshes[name], [face["index"] for face in faces])
            chosen.append((name, nodes, description))
    description = "; ".join(item[2] for item in chosen)
    if automatic:
        description = "Held automatically on the grounded bottom face. " + description
    return (
        {"automatic": automatic, "description": description},
        [(name, nodes) for name, nodes, _text in chosen],
    )


def _bottom_face(shape, matrix: np.ndarray):
    catalog = kernel.describe_faces(shape)
    rotation = matrix[:3, :3]
    origin = matrix[:3, 3]
    best = None
    best_z = None
    for face in catalog:
        if face.get("kind") != "plane" or not face.get("normal"):
            continue
        normal = rotation @ np.array(face["normal"], dtype=np.float64)
        if float(normal[2]) > -0.85:
            continue
        center = rotation @ np.array(face["center_mm"], dtype=np.float64) + origin
        if best is None or float(center[2]) < best_z:
            best = face
            best_z = float(center[2])
    if best is None:
        planes = [face for face in catalog if face.get("kind") == "plane"]
        if not planes:
            raise GeometryError("The grounded part has no planar face to hold.")
        best = min(planes, key=lambda face: float(face["center_mm"][2]))
    return (
        {"kind": "index", "index": best["index"]},
        f"face {best['index']} at z={best_z if best_z is not None else best['center_mm'][2]:.1f} mm",
    )


def _surface_mesh(meshes, bodies=None):
    chunks_points = []
    chunks_tri = []
    chunks_ref = []
    cursor = 0
    for index, mesh in enumerate(meshes):
        local_tri, used = mesh["surface"]
        if len(used) == 0:
            continue
        if bodies is None:
            points = mesh["points"]
        else:
            points = bodies[index]["points"]
        shown = points[used]
        base = cursor
        chunks_points.append(shown)
        chunks_tri.append(local_tri + base)
        chunks_ref.append(points)
        cursor += len(shown)
    if not chunks_points:
        return {
            "points": np.zeros((0, 3)),
            "triangles": np.zeros((0, 3), dtype=np.int32),
            "used": np.array([], dtype=np.int32),
            "points_ref": np.zeros((0, 3)),
        }
    # points_ref is the full cloud the eigenvector indexes. For one body that is the mesh.
    # For an assembly the eigenvector is concatenated in body order, which matches bodies[].points.
    if bodies is None:
        points_ref = meshes[0]["points"]
        used = meshes[0]["surface"][1]
        return {
            "points": chunks_points[0],
            "triangles": chunks_tri[0],
            "used": used,
            "points_ref": points_ref,
        }
    points_ref = np.vstack([body["points"] for body in bodies])
    # Rebuild used indices into the concatenated cloud.
    used_global = []
    tri_global = []
    taken = 0
    for mesh, body in zip(meshes, bodies):
        local_tri, used = mesh["surface"]
        offset = int(body["offset"])
        # local_tri already indexes the compact `used` list, not the volume nodes.
        for local in used:
            used_global.append(offset + int(local))
        for tri in local_tri:
            tri_global.append([int(node) + taken for node in tri])
        taken += len(used)
    return {
        "points": points_ref[np.array(used_global, dtype=np.int32)] if used_global else np.zeros((0, 3)),
        "triangles": np.array(tri_global, dtype=np.int32) if tri_global else np.zeros((0, 3), dtype=np.int32),
        "used": np.array(used_global, dtype=np.int32),
        "points_ref": points_ref,
    }


def _normalize_excitation(item, index: int) -> dict:
    if not isinstance(item, dict):
        raise GeometryError(f"Excitation {index} must be an object with a kind and rpm or hz.")
    kind = str(item.get("kind") or item.get("type") or "rotation").lower()
    name = str(item.get("name") or kind)
    if item.get("rpm") is not None:
        hz = float(item["rpm"]) / 60.0
        basis = "rpm/60"
    elif item.get("hz") is not None or item.get("frequency_hz") is not None:
        hz = float(item.get("hz") if item.get("hz") is not None else item["frequency_hz"])
        basis = "Hz"
    else:
        raise GeometryError(f"Excitation {name!r} needs rpm or hz.")
    if hz < 0:
        raise GeometryError(f"Excitation {name!r} has a negative frequency.")
    if item.get("harmonics") is not None:
        harmonics = [int(value) for value in item["harmonics"]]
    elif kind in _PULSING:
        harmonics = [1, 2]
    else:
        harmonics = [1, 2, 3]
    if kind not in _ROTATING and kind not in _PULSING:
        kind = "source"
    return {
        "name": name,
        "kind": kind,
        "fundamental_hz": round(hz, 4),
        "from": basis,
        "harmonics": harmonics,
        "harmonics_hz": [hz * value for value in harmonics],
    }


def _frequency_list(frequencies) -> list[dict]:
    rows = []
    for index, item in enumerate(frequencies, start=1):
        if isinstance(item, dict):
            hz = float(item.get("frequency_hz", item.get("hz")))
            number = int(item.get("index") or index)
        else:
            hz = float(item)
            number = index
        rows.append({"index": number, "frequency_hz": hz})
    return rows


def _suggestions(flags, rigid, sources, margin: float) -> list[str]:
    lines = []
    if rigid:
        lines.append(
            "A frequency under 1 Hz is rigid-body motion. Hold a face, or tie the joint, before reading the flexible modes."
        )
    if not sources:
        lines.append("No excitation was declared, so nothing was compared. Pass rpm or Hz for the motor, drill, pump, or engine.")
        return lines
    if not flags:
        lines.append(
            f"No flexible frequency sits within {margin:.0%} of a declared excitation or its harmonics. "
            "That is a clearance, not a proof that the real assembly is quiet. Joint compliance would lower these frequencies."
        )
        return lines
    for flag in flags:
        if flag["frequency_hz"] >= flag["excitation_hz"]:
            move = (
                "The mode is just above the excitation. Adding mass lowers it. "
                "Stiffening (a rib, a taller section, a shorter span) pushes it further up. "
                "Changing the running speed moves the excitation instead."
            )
        else:
            move = (
                "The mode is just below the excitation. Stiffening raises it past the excitation. "
                "Adding mass drops it further away. A shorter span or a taller section is the usual stiffening. "
                "Changing the running speed moves the excitation instead."
            )
        lines.append(
            f"Mode {flag['mode']} at {flag['frequency_hz']:.2f} Hz is within {flag['separation']:.0%} of "
            f"{flag['source']} harmonic {flag['harmonic']} at {flag['excitation_hz']:.2f} Hz. {move}"
        )
    return lines


def _receptance(natural_hz: float, drive_hz: float, zeta: float) -> tuple[float, float]:
    omega_n = 2.0 * math.pi * float(natural_hz)
    omega = 2.0 * math.pi * float(drive_hz)
    if omega_n <= 0:
        raise GeometryError("A harmonic check needs a positive natural frequency.")
    denom = math.sqrt((omega_n * omega_n - omega * omega) ** 2 + (2.0 * zeta * omega_n * omega) ** 2)
    receptance = 1.0 / denom
    amplification = receptance * omega_n * omega_n
    return receptance, amplification


def _assumptions(assembly: bool) -> list[str]:
    lines = [
        "Length is millimeters and force is newtons, so CalculiX mass is tonnes. Density is the handbook g/cm³ times 1e-9.",
        "The frequency is the cycles/time column, in hertz. The eigenvalue beside it is ω² in rad²/s².",
        "The material is linear, isotropic, and uniform in each solid. Damping is not in the eigenvalue problem.",
        "Eigenvectors are mass-normalized. The pictures scale the shape so the largest arrow is about 15% of the bounding diagonal. Color is the unscaled magnitude. The picture is a shape, not an operating amplitude.",
        "A frequency under 1 Hz is treated as a rigid-body mode.",
        "Resonance is a fractional gap: |fn − k·f| / fn at or under the margin, default 10%. Rotating sources use harmonics 1, 2, and 3. A pump uses 1 and 2. rpm is divided by 60.",
        "Moving a mode off an excitation means stiffening (ribs, a taller section, a shorter span), adding mass, changing the geometry, or changing the running speed.",
    ]
    if assembly:
        lines.append(
            "Each joint is a handful of translation equations between nearby nodes. The joint is locked at the pose, so a revolute does not flex. These frequencies are an upper bound on a real pinned joint."
        )
        lines.append("An instance under 2% of the largest volume is omitted. A fastener or a washer is not in the modes.")
    else:
        lines.append("The named faces are fixed in translation. Rotation of a node on that face is not independently constrained beyond the surrounding elements.")
    lines.append(
        "A harmonic number is a single-mode oscillator with an assumed damping ratio. A random number is Miles' equation on a flat PSD. Fatigue is a Goodman check of a stress you supply. None of the three is a second finite-element solve."
    )
    return lines


def _headline(modes, resonance) -> str:
    if not modes:
        return "No modes were returned."
    flexible = [mode for mode in modes if not mode["rigid_body"]]
    if not flexible:
        return "Only rigid-body modes were found. Hold a face and solve again."
    first = flexible[0]["frequency_hz"]
    if resonance["status"] == "inconclusive":
        return (
            f"First flexible frequency {first:.2f} Hz. "
            "The mesh was simplified, so this is not a clear and not a confirmed resonance."
        )
    if resonance["status"] == "resonance":
        return (
            f"First flexible frequency {first:.2f} Hz. "
            f"{len(resonance['flags'])} excitation line(s) fall inside the resonance margin."
        )
    return f"First flexible frequency {first:.2f} Hz. No declared excitation sits inside the resonance margin."


def _positive(value, default, label: str) -> float:
    if value is None:
        if default is None:
            raise GeometryError(f"{label} is required.")
        return float(default)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a number.") from exc
    if number <= 0:
        raise GeometryError(f"{label} must be positive.")
    return number


def _timeout(setup: dict, default: int) -> int:
    if setup.get("timeout_s") is None:
        return default
    return int(_positive(setup["timeout_s"], default, "timeout_s"))


def modal_available() -> tuple[bool, str]:
    return fea_available()
