"""Place an assembly, sweep it, and check cables and actuator loads.

Geometry stays in :mod:`aicad.kernel`. This module turns a spec into posed
solids, collision records, route checks, and a power estimate.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import numpy as np

from aicad import cable as cable_mod
from aicad import kernel
from aicad import tendon as tendon_mod
from aicad.errors import GeometryError
from aicad.fea import lookup_material
from aicad.honesty import ROUGH_ESTIMATE, VERIFIED_CHECK, finish
from aicad.kinematics import apply_pose, expand_motion, normalize, solve, transform_point
from aicad.power import actuators_for, estimate_sequence, joint_loads, reflect
from aicad.render import render_motion_frames

# Distinct enough that a fabricated joint, a yellow cylinder, and a dark rod
# separate in one picture. An instance may override this with color: [r, g, b].
_PALETTE = (
    (78.0, 84.0, 92.0),
    (186.0, 194.0, 204.0),
    (46.0, 104.0, 168.0),
    (196.0, 114.0, 48.0),
    (214.0, 176.0, 42.0),
    (222.0, 198.0, 130.0),
    (36.0, 40.0, 46.0),
    (164.0, 108.0, 58.0),
    (176.0, 64.0, 52.0),
    (72.0, 132.0, 96.0),
    (116.0, 96.0, 164.0),
    (214.0, 216.0, 220.0),
)


def solved_pose(spec: dict, overrides: dict | None = None) -> dict:
    return solve(spec, overrides)


def posed_shapes(spec: dict, shapes: dict[str, kernel.Shape], overrides: dict | None = None) -> tuple[dict, dict[str, kernel.Shape]]:
    solved = solve(spec, overrides)
    placed = {}
    for instance in solved["instances"]:
        base = shapes.get(instance["part"])
        if base is None:
            raise GeometryError(
                f"Instance {instance['name']!r} needs part {instance['part']!r}, which is not in the document."
            )
        placed[instance["name"]] = kernel.transform_shape(base, instance["matrix"])
    return solved, placed


def mount_gaps(solved: dict, placed: dict[str, kernel.Shape]) -> list[dict]:
    """Surface gap from each child solid to its parent solid.

    A gap of zero with overlap means the solids touch or pass through each
    other. A shaft in a clearance bore reports the radial gap and no overlap.
    """
    rows = []
    for joint in solved["joints"]:
        parent = joint["parent"]
        child = joint["child"]
        overlap, distance, *_rest = kernel.clearance_between(placed[parent], placed[child])
        rows.append(
            {
                "joint": joint["name"],
                "parent": parent,
                "child": child,
                "type": joint["type"],
                "gap_mm": distance,
                "overlap_mm3": overlap,
                "boxes_meet": not kernel.aabb_separated(placed[parent], placed[child]),
            }
        )
    return rows


def loose_mounts(
    solved: dict,
    placed: dict[str, kernel.Shape],
    *,
    gap_mm: float = 3.0,
    bore_gap_mm: float = 12.0,
) -> list[dict]:
    """Children that neither meet their parent nor sit in its bore.

    ``gap_mm`` is the mounting gap. ``bore_gap_mm`` is the extra allowance
    when the boxes already meet, which is a pin or a piston in a clearance hole.
    """
    loose = []
    for row in mount_gaps(solved, placed):
        if row["overlap_mm3"] > 0 or row["gap_mm"] <= gap_mm:
            continue
        if row["boxes_meet"] and row["gap_mm"] <= bore_gap_mm:
            continue
        loose.append(row)
    return loose


# A gap under this, or any overlap, is treated as one body after printing.
# The STL chord error is about 0.05 mm on each surface, so 0.10 mm can close
# in the mesh. 0.15 mm stays above that and is the usual fuse line for a
# 0.4 mm nozzle.
_DEFAULT_FUSE_MM = 0.15
_DEFAULT_PRINT_CLEARANCE_MM = 0.2
_DEFAULT_MESH_DEFLECTION_MM = 0.05
_PRINT_KEYS = {"fuse_mm", "min_clearance_mm", "pose", "joints", "mesh_deflection_mm"}


def print_clearance(spec: dict, shapes: dict[str, kernel.Shape], setup: dict | None = None) -> dict:
    """Gap between parts that print as one piece, including pairs a motion skips.

    Motion collisions leave out the two solids of a joint, so a pin that fills
    its bore is reported clear there. This check measures those pairs. Overlap,
    a gap under ``fuse_mm``, or a gap the export mesh can close, is fused. A
    larger gap under ``min_clearance_mm`` is tight. Other pairs are included
    when they are at least that close.
    """
    setup = setup or {}
    if not isinstance(setup, dict):
        raise GeometryError("A print clearance check is an object.")
    unknown = set(setup) - _PRINT_KEYS
    if unknown:
        raise GeometryError(
            "A print clearance check has unknown fields: " + ", ".join(sorted(unknown)) + "."
        )
    fuse_mm = _DEFAULT_FUSE_MM if setup.get("fuse_mm") is None else _nonnegative(setup.get("fuse_mm"), "fuse_mm")
    minimum = (
        _DEFAULT_PRINT_CLEARANCE_MM
        if setup.get("min_clearance_mm") is None
        else _nonnegative(setup.get("min_clearance_mm"), "min_clearance_mm")
    )
    if fuse_mm > minimum + 1e-12:
        raise GeometryError("fuse_mm cannot be larger than min_clearance_mm.")
    deflection = (
        _DEFAULT_MESH_DEFLECTION_MM
        if setup.get("mesh_deflection_mm") is None
        else _nonnegative(setup.get("mesh_deflection_mm"), "mesh_deflection_mm")
    )
    if deflection <= 0:
        raise GeometryError("mesh_deflection_mm must be positive.")
    posed = spec
    pose_name = setup.get("pose")
    if pose_name not in (None, ""):
        if not isinstance(pose_name, str):
            raise GeometryError("pose must be the name of a stored pose.")
        posed = apply_pose(spec, pose_name)
    overrides = setup.get("joints")
    if overrides is not None and not isinstance(overrides, dict):
        raise GeometryError("joints is an object of joint values for this check only.")
    solved, placed = posed_shapes(posed, shapes, overrides)
    joint_of = {
        tuple(sorted((joint["parent"], joint["child"]))): joint["name"] for joint in solved["joints"]
    }
    mesh_limit = 2.0 * deflection
    names = sorted(placed)
    rows = []
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            key = tuple(sorted((left, right)))
            joint_name = joint_of.get(key)
            box_gap = kernel.aabb_gap_lower_bound(placed[left], placed[right])
            if joint_name is None and box_gap > minimum + 1e-9:
                continue
            overlap, distance, point_a, point_b = kernel.clearance_between(placed[left], placed[right])
            fused_solid = overlap > 1e-4
            gap = 0.0 if fused_solid else distance
            mesh_closes = (not fused_solid) and gap + 1e-9 < mesh_limit
            if fused_solid or gap + 1e-9 < fuse_mm or mesh_closes:
                state = "fused"
            elif gap + 1e-9 < minimum:
                state = "tight"
            else:
                state = "clear"
            at = point_a or point_b
            rows.append(
                {
                    "a": left,
                    "b": right,
                    "joint": joint_name,
                    "gap_mm": gap,
                    "overlap_mm3": overlap,
                    "mesh_closes": mesh_closes,
                    "status": state,
                    "at_mm": None if at is None else [at.x, at.y, at.z],
                }
            )
    rows.sort(key=lambda row: (row["gap_mm"], row["a"], row["b"]))
    fused = [row for row in rows if row["status"] == "fused"]
    tight = [row for row in rows if row["status"] == "tight"]
    if fused:
        status = "fail"
    elif tight:
        status = "review"
    else:
        status = "pass"
    worst = rows[0] if rows else None
    report = {
        "assembly": solved["name"],
        "status": status,
        "pose": None if pose_name in (None, "") else pose_name,
        "fuse_mm": fuse_mm,
        "min_clearance_mm": minimum,
        "mesh_deflection_mm": deflection,
        "pair_count": len(rows),
        "fused_count": len(fused),
        "tight_count": len(tight),
        "min_gap_mm": None if worst is None else worst["gap_mm"],
        "min_gap_pair": None if worst is None else [worst["a"], worst["b"]],
        "pairs": rows,
        "note": _print_note(status, fused, tight, fuse_mm, minimum),
        "assumptions": [
            f"A pair is fused when the solids overlap, when the gap is under {fuse_mm:g} mm, or when the gap is under {mesh_limit:g} mm so a mesh of deflection {deflection:g} mm can close it.",
            f"A pair is tight when the gap is at least the fuse line and under {minimum:g} mm.",
            "Joint pairs are always measured. Other pairs are measured when their boxes are within the minimum gap.",
            "The gap is the minimum distance between the two solids at this pose. It is not a section through a full turn.",
            "The stored assembly is not moved. pose and joints apply to this check only.",
            "This is not a slicer extrusion and not a contact force.",
        ],
    }
    return finish(
        report,
        kind=VERIFIED_CHECK,
        name="Print clearance",
        what=(
            "The gap is the kernel distance between the posed solids, and overlap is their boolean intersection. "
            "A joint pair is included."
        ),
        limits=[
            "Only this pose is measured. A clash at another angle is not seen.",
            "The mesh rule is two chord errors. A coarser export than mesh_deflection_mm can still close a wider gap.",
            "A pair whose boxes are farther apart than the minimum gap is not measured, except a joint pair.",
            "This is not a printed trial.",
        ],
    )


def _nonnegative(value, label: str) -> float:
    number = kernel.finite_number(value, label)
    if number < 0:
        raise GeometryError(f"{label} cannot be negative.")
    return number


def _print_note(status: str, fused: list[dict], tight: list[dict], fuse_mm: float, minimum: float) -> str:
    if status == "fail":
        shown = ", ".join(f"{row['a']}/{row['b']}" for row in fused[:4])
        extra = "" if len(fused) <= 4 else f" and {len(fused) - 4} more"
        return (
            f"{len(fused)} pair{'s' if len(fused) != 1 else ''} will fuse when printed as one piece "
            f"({shown}{extra}). The fuse line is {fuse_mm:g} mm."
        )
    if status == "review":
        shown = ", ".join(f"{row['a']}/{row['b']}" for row in tight[:4])
        return (
            f"{len(tight)} pair{'s' if len(tight) != 1 else ''} are tighter than {minimum:g} mm ({shown}). "
            "None are under the fuse line."
        )
    return f"Every measured pair is at least {minimum:g} mm apart."


def collisions(solved: dict, placed: dict[str, kernel.Shape], ignore: list[list[str]] | None = None) -> list[dict]:
    skip = set()
    for pair in ignore or []:
        skip.add(tuple(sorted(pair)))
    for joint in solved["joints"]:
        skip.add(tuple(sorted((joint["parent"], joint["child"]))))
    names = list(placed)
    hits = []
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            if tuple(sorted((left, right))) in skip:
                continue
            # One boolean. The volume and its center come from that solid.
            # A second boolean only to find the center repeated the same cut.
            overlap, point = kernel.overlap_center(placed[left], placed[right])
            if overlap <= 1e-3:
                continue
            hits.append(
                {
                    "a": left,
                    "b": right,
                    "interference_volume_mm3": overlap,
                    "gap_mm": 0.0,
                    "at_mm": None if point is None else [point.x, point.y, point.z],
                }
            )
    return hits


def joint_seats(solved: dict, placed: dict[str, kernel.Shape]) -> list[dict]:
    """Gap and overlap for each joint pair.

    Motion collisions skip these pairs, so a pin that fills its bore is not a
    collision. Overlap here means the two solids of the joint are one volume.
    """
    rows = []
    for joint in solved["joints"]:
        overlap, distance, point_a, point_b = kernel.clearance_between(
            placed[joint["parent"]], placed[joint["child"]]
        )
        fused = overlap > 1e-4
        at = point_a or point_b
        rows.append(
            {
                "joint": joint["name"],
                "parent": joint["parent"],
                "child": joint["child"],
                "type": joint["type"],
                "gap_mm": 0.0 if fused else distance,
                "overlap_mm3": overlap,
                "fused": fused,
                "at_mm": None if at is None else [at.x, at.y, at.z],
            }
        )
    return rows


def run_motion(spec: dict, shapes: dict[str, kernel.Shape], motion: dict, out_dir: Path | None = None) -> dict:
    samples = expand_motion(spec, motion)
    frames = []
    meshes = []
    any_hit = False
    for sample in samples:
        solved, placed = posed_shapes(spec, shapes, sample["joints"])
        hits = collisions(solved, placed, spec.get("ignore_collisions"))
        seats = joint_seats(solved, placed)
        any_hit = any_hit or bool(hits)
        frame = {
            "index": sample["index"],
            "time_s": sample["time_s"],
            "joints": {joint["name"]: joint["value"] for joint in solved["joints"]},
            "origins_mm": {instance["name"]: instance["origin_mm"] for instance in solved["instances"]},
            "warnings": solved["warnings"],
            "collisions": hits,
            "joint_seats": seats,
        }
        frames.append(frame)
        if out_dir is not None:
            meshes.append(_colored_mesh(shapes, solved))
    pictures = None
    if out_dir is not None and meshes:
        titles = [
            f"{motion.get('name') or 'motion'}  t={frame['time_s']:.2f}s"
            + ("  collision" if frame["collisions"] else "")
            for frame in frames
        ]
        pictures = render_motion_frames(meshes, out_dir, str(motion.get("name") or "motion"), titles)
    report = {
        "motion": motion.get("name") or "motion",
        "duration_s": samples[0]["duration_s"] if samples else 0,
        "duration_assumed": bool(samples and samples[0]["duration_assumed"]),
        "frames": frames,
        "collision_count": sum(len(frame["collisions"]) for frame in frames),
        "colliding_frames": [frame["index"] for frame in frames if frame["collisions"]],
        "status": "collision" if any_hit else "clear",
        "pictures": pictures,
        "fused_joints": _fused_joint_names(frames),
        "note": (
            "Collisions are boolean intersections between instances that are not joined to each other "
            "and are not listed in ignore_collisions. The joint pair itself is allowed to touch. "
            "Overlap of that pair is fused_joints, because a printed joint with no gap is one body."
        ),
        "assumptions": [
            "Each frame is a posed copy of the CAD solids.",
            "The intersection volume is the kernel boolean. It is not a contact force.",
            "A joint pair is left out of collisions. joint_seats on each frame is the gap or the overlap of that pair.",
        ],
    }
    return finish(
        report,
        kind=VERIFIED_CHECK,
        name="Motion",
        what="Collisions are a verified check of the modeled solids at the sampled frames.",
        limits=[
            "Only the frames in the motion are checked. A hit between frames is not seen.",
            "Parts joined to each other are allowed to touch. Overlap of that pair is fused_joints, not a collision, and it does not change status.",
            "A room drawn as lines is not a solid, so the check cannot see it.",
            "This is not a contact force.",
        ],
    )


def _fused_joint_names(frames: list[dict]) -> list[str]:
    names = []
    for frame in frames:
        for seat in frame.get("joint_seats") or []:
            if seat["fused"] and seat["joint"] not in names:
                names.append(seat["joint"])
    return names


def check_cables(
    spec: dict,
    shapes: dict[str, kernel.Shape],
    motion: dict | None = None,
    study: dict | None = None,
    steps: int | None = None,
) -> dict:
    cables = [cable_mod.normalize_cable(item, index) for index, item in enumerate(spec.get("cables") or [])]
    if not cables:
        raise GeometryError(f"Assembly {spec.get('name')!r} has no cables.")
    if steps is not None and study is None:
        raise GeometryError("steps is the number of poses in a motion study. Name a study, or pass one.")
    samples = _cable_samples(spec, shapes, motion, study, steps)
    rest: dict[str, float] = {}
    per_cable: dict[str, list[dict]] = {cable["name"]: [] for cable in cables}
    for sample in samples:
        solved = sample["solved"]
        placed = sample["placed"]
        for cable in cables:
            if cable["route"] == "pulley":
                metrics, used = tendon_mod.evaluate(cable, solved, placed, rest.get(cable["name"]))
                rest.setdefault(cable["name"], used)
            else:
                points = cable_mod.world_waypoints(cable, solved)
                if cable["name"] not in rest:
                    rest[cable["name"]] = (
                        cable["rest_length_mm"]
                        if cable["rest_length_mm"] is not None
                        else cable_mod.measure_route(points, cable, None)["length_mm"]
                    )
                metrics = cable_mod.measure_route(points, cable, rest[cable["name"]])
                pinches = _pinches(cable, points, placed)
                metrics["pinches"] = pinches
                metrics["status"] = cable_mod.route_status(metrics, pinches)
            metrics["frame"] = sample["index"]
            metrics["time_s"] = sample["time_s"]
            per_cable[cable["name"]].append(metrics)
    summaries = []
    for cable in cables:
        rows = per_cable[cable["name"]]
        if cable["route"] == "pulley":
            summaries.append(tendon_mod.summarize(cable, rows))
            continue
        worst_bend = min((row["min_bend_radius_mm"] or 1e9) for row in rows)
        max_strain = max(row["strain"] for row in rows)
        pinches = [pinch for row in rows for pinch in row["pinches"]]
        summaries.append(
            {
                "name": cable["name"],
                "kind": cable["kind"],
                "route": "polyline",
                "status": "fail" if any(row["status"] == "fail" for row in rows) else "pass",
                "rest_length_mm": rows[0]["rest_length_mm"],
                "length_mm": [row["length_mm"] for row in rows],
                "length_change_mm": [row["length_change_mm"] for row in rows],
                "max_strain": round(max_strain, 5),
                "strain_limit": cable["max_strain"],
                "min_bend_radius_mm": None if worst_bend >= 1e8 else round(worst_bend, 3),
                "bend_limit_mm": cable["min_bend_radius_mm"],
                "pinch_count": len(pinches),
                "frames": rows,
            }
        )
    has_tendon = any(cable["route"] == "pulley" for cable in cables)
    has_polyline = any(cable["route"] != "pulley" for cable in cables)
    what, limits, notes = _cable_presentation(has_polyline, has_tendon)
    payload = {
        "cables": summaries,
        "status": "fail" if any(item["status"] == "fail" for item in summaries) else "pass",
        "assumptions": notes,
    }
    if study is not None:
        payload["study"] = study.get("name")
        payload["steps"] = len(samples)
    return finish(payload, kind=ROUGH_ESTIMATE, name="Cables", what=what, limits=limits)


def _cable_samples(spec, shapes, motion, study, steps):
    if study is not None:
        from aicad.motion_study import iter_study_poses

        return iter_study_poses(spec, shapes, study, steps=steps)
    if motion is not None:
        rows = []
        for sample in expand_motion(spec, motion):
            solved, placed = posed_shapes(spec, shapes, sample["joints"] or None)
            rows.append(
                {
                    "index": sample["index"],
                    "time_s": sample["time_s"],
                    "solved": solved,
                    "placed": placed,
                }
            )
        return rows
    solved, placed = posed_shapes(spec, shapes, None)
    return [{"index": 0, "time_s": 0.0, "solved": solved, "placed": placed}]


def _cable_presentation(has_polyline: bool, has_tendon: bool) -> tuple[str, list[str], list[str]]:
    polyline_what = "Length, bend, and pinch are a rough estimate of a polyline through fixed clips. The line does not sag."
    polyline_limits = [
        "Bend radius is the circumradius of three waypoints, which is harsher than a filleted route.",
        "Clips do not slip, and the line does not stretch except as a single strain number.",
        "A pinch is a centerline sample, not a swept volume of the hose.",
    ]
    tendon_what, tendon_limits = tendon_mod.presentation()
    if has_tendon and not has_polyline:
        return tendon_what, tendon_limits, tendon_mod.assumptions()
    if has_polyline and not has_tendon:
        return polyline_what, polyline_limits, cable_mod.assumptions()
    return (
        polyline_what + " A tendon adds pulley arcs and capstan tension from the actuator force.",
        [*polyline_limits, *tendon_limits],
        [*cable_mod.assumptions(), *tendon_mod.assumptions()],
    )


def estimate_power(
    spec: dict,
    shapes: dict[str, kernel.Shape],
    motion: dict | None = None,
    actuators: list[dict] | None = None,
    study: dict | None = None,
) -> dict:
    normalized = normalize(spec)
    chosen = actuators_for(normalized, actuators)
    if not chosen:
        raise GeometryError(f"Assembly {normalized['name']!r} has no actuators.")
    if study is not None and motion is not None:
        raise GeometryError("Pass a motion or a motion study, not both.")
    samples = _power_samples(normalized, motion, study)
    frame_loads = []
    for sample in samples:
        frame_spec = sample.get("spec") or normalized
        solved = solve(frame_spec, sample["joints"] or None)
        bodies = _bodies(solved, shapes)
        local = joint_loads(solved, bodies)
        reflected = reflect(local, solved, frame_spec, chosen)
        by_joint = {row["joint"]: row for row in reflected}
        frame_loads.append({"time_s": sample["time_s"], "joints": by_joint, "warnings": solved["warnings"]})
    report = estimate_sequence(frame_loads, chosen, samples[0]["duration_s"])
    report["motion"] = None if motion is None else motion.get("name")
    if study is not None:
        report["study"] = study.get("name")
    report["frames"] = [
        {
            "time_s": frame["time_s"],
            "joints": {
                name: {"torque_nm": row["torque_nm"], "force_n": row["force_n"], "value": row["value"]}
                for name, row in frame["joints"].items()
            },
        }
        for frame in frame_loads
    ]
    if samples[0].get("duration_assumed"):
        report["assumptions"] = [
            "duration_s was omitted, so rates use 1 second.",
            *report["assumptions"],
        ]
    return finish(
        report,
        kind=ROUGH_ESTIMATE,
        name="Power",
        what="Joint torque and pump or motor power are a quasi-static estimate. Inertia and friction are left out.",
        limits=[
            "Gravity and the stated payload are included. Inertia, seal drag, and line loss are not.",
            "A hydraulic moment arm on a revolute joint is a number you supply. An actuator on the stroke of a cylinder relation uses the in-plane pin-to-pin rate.",
            "Electric braking energy is counted as spent.",
        ],
    )


def _power_samples(spec: dict, motion: dict | None, study: dict | None) -> list[dict]:
    """Joint samples for a stored motion, a motion study, or the pose held for one second."""
    if study is not None:
        from aicad.motion_study import _commands, _sample_times, _with_couplings, normalize_study

        chosen = normalize_study(study, spec["joints"], spec["relations"], name=study.get("name"))
        posed = _with_couplings(spec, chosen)
        samples = []
        for index, time_s in enumerate(_sample_times(chosen["duration_s"], chosen["steps"])):
            samples.append(
                {
                    "index": index,
                    "time_s": round(float(time_s), 6),
                    "duration_s": chosen["duration_s"],
                    "joints": _commands(chosen, time_s),
                    "duration_assumed": chosen["duration_assumed"],
                    "spec": posed,
                }
            )
        return samples
    if motion is not None:
        return expand_motion(spec, motion)
    return [{"index": 0, "time_s": 0.0, "duration_s": 1.0, "joints": {}, "duration_assumed": True}]


def preview_mesh(spec: dict, shapes: dict[str, kernel.Shape], overrides: dict | None = None) -> dict:
    solved, _placed = posed_shapes(spec, shapes, overrides)
    positions: list[float] = []
    normals: list[float] = []
    colors: list[float] = []
    triangle_face: list[int] = []
    faces = []
    edges = []
    for index, instance in enumerate(solved["instances"]):
        piece = _transform_mesh(_stored_mesh(shapes[instance["part"]]), instance["matrix"])
        color = _instance_color(instance, index)
        faces.append(
            {
                "index": index,
                "kind": "instance",
                "ref": f"instance/{instance['name']}",
                "center_mm": instance["origin_mm"],
            }
        )
        for tri in piece.triangles:
            for corner in tri:
                vertex = piece.vertices[int(corner)]
                normal = piece.normals[int(corner)]
                positions.extend(float(component) for component in vertex)
                normals.extend(float(component) for component in normal)
                colors.extend(color)
            triangle_face.append(index)
        for edge in piece.edges:
            edges.append(
                {
                    "index": len(edges),
                    "kind": "curve",
                    "ref": f"instance/{instance['name']}",
                    "points": [[round(float(component), 4) for component in point] for point in edge],
                }
            )
    if not positions:
        raise GeometryError("The assembly produced an empty mesh.")
    mins = [min(positions[i::3]) for i in range(3)]
    maxs = [max(positions[i::3]) for i in range(3)]
    return {
        "part": spec.get("name"),
        "assembly": spec.get("name"),
        "units": "mm",
        "positions": [round(value, 4) for value in positions],
        "normals": [round(value, 4) for value in normals],
        "colors": [round(value, 1) for value in colors],
        "triangle_face": triangle_face,
        "faces": faces,
        "edges": edges,
        "bounding_box": {"min": [round(value, 4) for value in mins], "max": [round(value, 4) for value in maxs]},
        "instances": [{"name": instance["name"], "origin_mm": instance["origin_mm"]} for instance in solved["instances"]],
    }


def motion_by_name(spec: dict, name: str | None, inline: dict | None = None) -> dict | None:
    if inline:
        motion = copy.deepcopy(inline)
        motion.setdefault("name", name or "motion")
        return motion
    if not name:
        return None
    motions = spec.get("motions") or {}
    if name not in motions:
        known = ", ".join(motions) or "(none)"
        raise GeometryError(f"No motion {name!r}. Motions: {known}.")
    motion = copy.deepcopy(motions[name])
    motion["name"] = name
    return motion


def _bodies(solved: dict, shapes: dict[str, kernel.Shape]) -> dict[str, dict]:
    bodies = {}
    for instance in solved["instances"]:
        measured = kernel.measure(shapes[instance["part"]])
        material = lookup_material(instance["material"])
        grams = measured.volume_mm3 * float(material["density_g_cm3"]) / 1000.0
        com_local = [
            measured.center_of_mass_mm.x,
            measured.center_of_mass_mm.y,
            measured.center_of_mass_mm.z,
        ]
        com = transform_point(instance["matrix"], com_local)
        payload_at = instance.get("payload_at")
        payload_mm = transform_point(instance["matrix"], payload_at) if payload_at else com
        bodies[instance["name"]] = {
            "mass_kg": grams / 1000.0,
            "com_mm": com,
            "payload_kg": float(instance.get("payload_kg") or 0.0),
            "payload_mm": payload_mm,
        }
    return bodies


def _pinches(cable: dict, points: list[dict], placed: dict[str, kernel.Shape]) -> list[dict]:
    step = max(2.0, cable["diameter_mm"])
    samples = cable_mod.sample_route(points, step)
    limit = cable["diameter_mm"] / 2.0 + cable["pinch_mm"]
    # A clip is allowed to sit on its own part. Samples next to that waypoint are the seat.
    seat = max(step, cable["diameter_mm"])
    radius = cable["diameter_mm"] / 2.0
    hits = []
    for instance, shape in placed.items():
        chosen = None
        for sample in samples:
            if _near_waypoint(sample, points, instance, seat):
                continue
            gap = kernel.distance_to_point(shape, sample)
            if not gap["inside"] and gap["distance_mm"] >= limit:
                continue
            clearance = -gap["distance_mm"] if gap["inside"] else gap["distance_mm"] - radius
            if not cable_mod.prefer_sample(chosen, gap["inside"], round(clearance, 3)):
                continue
            chosen = {
                "part": instance,
                "at_mm": [round(component, 3) for component in sample],
                "distance_mm": gap["distance_mm"],
                "inside": gap["inside"],
                "clearance_mm": round(clearance, 3),
            }
        if chosen is not None:
            hits.append(chosen)
    return hits


def _near_waypoint(sample: list[float], points: list[dict], instance: str, seat: float) -> bool:
    for point in points:
        if point["instance"] != instance:
            continue
        at = point["at_mm"]
        if sum((sample[axis] - at[axis]) ** 2 for axis in range(3)) <= seat * seat:
            return True
    return False


def _byte_channels(color) -> tuple[float, float, float]:
    """RGB bytes for a picture.

    An int channel is a byte from 0 to 255, so ``(1, 1, 0)`` stays nearly
    black. A float from 0 to 1 is a fraction of full scale: ``(1.0, 1.0, 0.0)``
    is yellow, and ``(0.75, 0.78, 0.82)`` is scaled before shading. Shade is
    about 0.2 to 1 and the picture stores bytes, so an unscaled fraction
    truncates to 0 and the solid is black. A float above 1 is a byte already
    on that scale, which is how a saved color is stored. Black stays black.
    """
    if not isinstance(color, (list, tuple)) or len(color) != 3:
        raise GeometryError("A color is three ints from 0 to 255, or three floats from 0 to 1.")
    kind = None
    numbers = []
    for channel in color:
        if isinstance(channel, bool) or not isinstance(channel, (int, float)):
            raise GeometryError("A color is three ints from 0 to 255, or three floats from 0 to 1.")
        if isinstance(channel, float) and not math.isfinite(channel):
            raise GeometryError("A color channel must be finite.")
        this = "int" if isinstance(channel, int) else "float"
        if kind is None:
            kind = this
        elif kind != this:
            raise GeometryError("A color is three ints from 0 to 255, or three floats from 0 to 1.")
        numbers.append(channel)
    if any(channel < 0 or channel > 255 for channel in numbers):
        if kind == "int":
            raise GeometryError("An int color channel is a byte from 0 to 255.")
        raise GeometryError("A float color is a fraction from 0 to 1, or a byte above 1 and at most 255.")
    if kind == "float" and max(numbers) <= 1.0:
        return tuple(float(channel) * 255.0 for channel in numbers)
    return tuple(float(channel) for channel in numbers)


def _instance_color(instance: dict, index: int) -> tuple[float, float, float]:
    color = instance.get("color")
    if color:
        return _byte_channels(color)
    return _PALETTE[index % len(_PALETTE)]


def _stored_mesh(shape: kernel.Shape) -> kernel.TriangleMesh:
    """Tessellation of one solid, kept for every instance and later pose.

    The solid object does not change when the assembly moves. A second
    instance, or the next pose, only needs the same vertices moved by a
    new matrix. A replaced solid is a new object, so it is meshed again.
    """
    mesh = getattr(shape, "_tessellation", None)
    if mesh is None:
        mesh = kernel.tessellate(shape)
        shape._tessellation = mesh
    return mesh


def _colored_mesh(shapes: dict[str, kernel.Shape], solved: dict) -> kernel.TriangleMesh:
    pieces = []
    for index, instance in enumerate(solved["instances"]):
        piece = _transform_mesh(_stored_mesh(shapes[instance["part"]]), instance["matrix"])
        color = np.array(_instance_color(instance, index), dtype=np.float64)
        piece.colors = np.tile(color, (len(piece.vertices), 1))
        pieces.append(piece)
    return _concatenate(pieces)


def _instance_vertex_count(shape: kernel.Shape) -> int:
    return len(kernel.tessellate(shape).vertices)


def _transform_mesh(mesh: kernel.TriangleMesh, matrix: list[list[float]]) -> kernel.TriangleMesh:
    rotation = np.array(matrix, dtype=np.float64)[:3, :3]
    translation = np.array(matrix, dtype=np.float64)[:3, 3]
    vertices = mesh.vertices @ rotation.T + translation
    normals = mesh.normals @ rotation.T
    edges = [edge @ rotation.T + translation for edge in mesh.edges]
    return kernel.TriangleMesh(
        vertices=vertices,
        normals=normals,
        triangles=mesh.triangles,
        edges=edges,
        colors=mesh.colors,
    )


def _concatenate(pieces: list[kernel.TriangleMesh]) -> kernel.TriangleMesh:
    vertices = []
    normals = []
    triangles = []
    edges = []
    colors = []
    offset = 0
    for piece in pieces:
        vertices.append(piece.vertices)
        normals.append(piece.normals)
        triangles.append(piece.triangles + offset)
        edges.extend(piece.edges)
        if piece.colors is not None:
            colors.append(piece.colors)
        offset += len(piece.vertices)
    return kernel.TriangleMesh(
        vertices=np.vstack(vertices),
        normals=np.vstack(normals),
        triangles=np.vstack(triangles),
        edges=edges,
        colors=np.vstack(colors) if colors else None,
    )
