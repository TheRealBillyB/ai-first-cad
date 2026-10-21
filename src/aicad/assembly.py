"""Place an assembly, sweep it, and check cables and actuator loads.

Geometry stays in :mod:`aicad.kernel`. This module turns a spec into posed
solids, collision records, route checks, and a power estimate.
"""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np

from aicad import cable as cable_mod
from aicad import kernel
from aicad.errors import GeometryError
from aicad.fea import lookup_material
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
            overlap = kernel.overlap_volume(placed[left], placed[right])
            if overlap <= 1e-3:
                continue
            _overlap, distance, point_a, point_b = kernel.clearance_between(placed[left], placed[right])
            at = point_a or point_b
            hits.append(
                {
                    "a": left,
                    "b": right,
                    "interference_volume_mm3": overlap,
                    "gap_mm": distance,
                    "at_mm": None if at is None else [at.x, at.y, at.z],
                }
            )
    return hits


def run_motion(spec: dict, shapes: dict[str, kernel.Shape], motion: dict, out_dir: Path | None = None) -> dict:
    samples = expand_motion(spec, motion)
    frames = []
    meshes = []
    any_hit = False
    for sample in samples:
        solved, placed = posed_shapes(spec, shapes, sample["joints"])
        hits = collisions(solved, placed, spec.get("ignore_collisions"))
        any_hit = any_hit or bool(hits)
        frame = {
            "index": sample["index"],
            "time_s": sample["time_s"],
            "joints": {joint["name"]: joint["value"] for joint in solved["joints"]},
            "origins_mm": {instance["name"]: instance["origin_mm"] for instance in solved["instances"]},
            "warnings": solved["warnings"],
            "collisions": hits,
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
    return {
        "motion": motion.get("name") or "motion",
        "duration_s": samples[0]["duration_s"] if samples else 0,
        "duration_assumed": bool(samples and samples[0]["duration_assumed"]),
        "frames": frames,
        "collision_count": sum(len(frame["collisions"]) for frame in frames),
        "colliding_frames": [frame["index"] for frame in frames if frame["collisions"]],
        "status": "collision" if any_hit else "clear",
        "pictures": pictures,
        "note": (
            "Collisions are boolean intersections between instances that are not joined to each other "
            "and are not listed in ignore_collisions. The joint pair itself is allowed to touch."
        ),
    }


def check_cables(
    spec: dict,
    shapes: dict[str, kernel.Shape],
    motion: dict | None = None,
) -> dict:
    samples = expand_motion(spec, motion) if motion else [{"index": 0, "time_s": 0.0, "joints": {}, "duration_s": 0.0}]
    cables = [cable_mod.normalize_cable(item, index) for index, item in enumerate(spec.get("cables") or [])]
    if not cables:
        raise GeometryError(f"Assembly {spec.get('name')!r} has no cables.")
    rest: dict[str, float] = {}
    per_cable: dict[str, list[dict]] = {cable["name"]: [] for cable in cables}
    for sample in samples:
        solved, placed = posed_shapes(spec, shapes, sample["joints"] or None)
        for cable in cables:
            points = cable_mod.world_waypoints(cable, solved)
            if cable["name"] not in rest:
                rest[cable["name"]] = cable["rest_length_mm"] if cable["rest_length_mm"] is not None else cable_mod.measure_route(points, cable, None)["length_mm"]
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
        worst_bend = min((row["min_bend_radius_mm"] or 1e9) for row in rows)
        max_strain = max(row["strain"] for row in rows)
        pinches = [pinch for row in rows for pinch in row["pinches"]]
        summaries.append(
            {
                "name": cable["name"],
                "kind": cable["kind"],
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
    return {
        "cables": summaries,
        "status": "fail" if any(item["status"] == "fail" for item in summaries) else "pass",
        "assumptions": cable_mod.assumptions(),
    }


def estimate_power(
    spec: dict,
    shapes: dict[str, kernel.Shape],
    motion: dict | None = None,
    actuators: list[dict] | None = None,
) -> dict:
    normalized = normalize(spec)
    chosen = actuators_for(normalized, actuators)
    if not chosen:
        raise GeometryError(f"Assembly {normalized['name']!r} has no actuators.")
    samples = expand_motion(normalized, motion) if motion else [
        {"index": 0, "time_s": 0.0, "duration_s": 1.0, "joints": {}, "duration_assumed": True}
    ]
    frame_loads = []
    for sample in samples:
        solved = solve(normalized, sample["joints"] or None)
        bodies = _bodies(solved, shapes)
        local = joint_loads(solved, bodies)
        reflected = reflect(local, solved, normalized, chosen)
        by_joint = {row["joint"]: row for row in reflected}
        frame_loads.append({"time_s": sample["time_s"], "joints": by_joint, "warnings": solved["warnings"]})
    report = estimate_sequence(frame_loads, chosen, samples[0]["duration_s"])
    report["motion"] = None if motion is None else motion.get("name")
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
    return report


def preview_mesh(spec: dict, shapes: dict[str, kernel.Shape], overrides: dict | None = None) -> dict:
    solved, _placed = posed_shapes(spec, shapes, overrides)
    positions: list[float] = []
    normals: list[float] = []
    colors: list[float] = []
    triangle_face: list[int] = []
    faces = []
    edges = []
    for index, instance in enumerate(solved["instances"]):
        piece = _transform_mesh(kernel.tessellate(shapes[instance["part"]]), instance["matrix"])
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
    hits = []
    for instance, shape in placed.items():
        for sample in samples:
            if _near_waypoint(sample, points, instance, seat):
                continue
            gap = kernel.distance_to_point(shape, sample)
            clearance = -gap["distance_mm"] if gap["inside"] else gap["distance_mm"] - cable["diameter_mm"] / 2.0
            if gap["inside"] or gap["distance_mm"] < limit:
                hits.append(
                    {
                        "part": instance,
                        "at_mm": [round(component, 3) for component in sample],
                        "distance_mm": gap["distance_mm"],
                        "inside": gap["inside"],
                        "clearance_mm": round(clearance, 3),
                    }
                )
                break
    return hits


def _near_waypoint(sample: list[float], points: list[dict], instance: str, seat: float) -> bool:
    for point in points:
        if point["instance"] != instance:
            continue
        at = point["at_mm"]
        if sum((sample[axis] - at[axis]) ** 2 for axis in range(3)) <= seat * seat:
            return True
    return False


def _instance_color(instance: dict, index: int) -> tuple[float, float, float]:
    color = instance.get("color")
    if color:
        return (float(color[0]), float(color[1]), float(color[2]))
    return _PALETTE[index % len(_PALETTE)]


def _colored_mesh(shapes: dict[str, kernel.Shape], solved: dict) -> kernel.TriangleMesh:
    pieces = []
    for index, instance in enumerate(solved["instances"]):
        piece = _transform_mesh(kernel.tessellate(shapes[instance["part"]]), instance["matrix"])
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
