"""Serial-chain forward kinematics, inverse kinematics, and a tool path.

A chain is the joints from the grounded instance to one tool instance.
Revolute values are degrees. Prismatic values are millimeters. A cylindrical
joint carries both. Fixed joints stay in the chain and do not move. A joint
driven by a gear, linkage, or cylinder is not a free variable.

Forward kinematics is the same rigid transform as the joint solver. The tool
frame is a point ``at`` and an optional ``rotation_deg`` in the tool instance.
``rotation_deg`` is X, then Y, then Z, the same order as ``kernel.place``.

Inverse kinematics returns every joint solution that places that frame on the
target and stays inside ``min`` and ``max``. Two parallel revolute joints have
a closed form: elbow up is the positive bend about the axis, elbow down is the
negative bend. Six revolute joints whose last three axes meet at one point
have a closed form as well, one solution for each shoulder, elbow, and wrist
branch that reaches the pose. Any other mix of revolute and prismatic joints
is solved with the Jacobian, starting from the given joint values.

A solution is kept when the tool point is within 1e-6 mm of the target. When
the target has an orientation, the tool frame must also be within 1e-8 radians.
A target outside the reach is ``unreachable``. A reachable pose that loses a
direction of motion is ``singular``. A pose that fits only past a stop is
``limit``, and the joint values that would have reached it are reported and
not used.

A path steps the tool point along a straight line from the current pose, or
through waypoints. ``steps`` on a line is the number of poses, including both
ends. On waypoints it is the number of poses on each segment, including the
segment ends, and the shared waypoint is not repeated. Orientation, when
given, is interpolated on the rotation. Each step is inverse kinematics seeded
from the previous joints, so the arm stays on one branch. Those joint values
are then a motion study: the same collision and joint-limit checks, on the
same poses. The record inside ``path.motion_study`` is ``motion_study.v1``.

The result record is ``kinematics.v1``.
"""

from __future__ import annotations

import math

import numpy as np

from aicad.errors import GeometryError
from aicad.honesty import VERIFIED_CHECK, finish
from aicad.kernel import finite_number, finite_vec3
from aicad.kinematics import _clamp, _euler, _matmat, _matrix, _matvec, _multiply
from aicad.kinematics import _rotation, _transform_point, _transform_vector, _unit_name, _worlds
from aicad.kinematics import normalize
from aicad.motion_study import _sample_times, run_study

SCHEMA = "kinematics.v1"
_POS_TOL_MM = 1e-6
_ORI_TOL_RAD = 1e-8
_MAX_PATH_STEPS = 201
_ANGLE_LIMIT = 1_000_000.0
_FREE = ("revolute", "prismatic", "cylindrical")

_REQUEST_KEYS = {"tool", "at", "rotation_deg", "joints", "slides", "target", "path"}
_TARGET_KEYS = {"position", "rotation_deg", "matrix"}
_PATH_KEYS = {"kind", "target", "waypoints", "steps", "clearance_mm", "duration_s"}


def solve_kinematics(spec: dict, shapes: dict | None, request: dict) -> dict:
    """Pose one chain, solve a target, or step a tool path.

    ``shapes`` is the part-name map the motion study uses. Without it a path
    still returns joint values and says that collisions were not checked.
    """
    if not isinstance(request, dict):
        raise GeometryError("Kinematics request must be an object.")
    unknown = set(request) - _REQUEST_KEYS
    if unknown:
        raise GeometryError(f"Kinematics request has unknown fields: {', '.join(sorted(unknown))}.")
    spec = normalize(spec)
    tool = str(request.get("tool") or "")
    if not tool:
        raise GeometryError("Kinematics needs a tool instance.")
    at = [0.0, 0.0, 0.0] if request.get("at") is None else list(finite_vec3(request.get("at"), "Tool point"))
    rotation_deg = _rotation_deg(request.get("rotation_deg"), "Tool rotation_deg")
    joints = _joint_map(request.get("joints"), spec, "joints")
    slides = _slide_map(request.get("slides"), spec)
    target = _target(request.get("target"))
    path = _path(request.get("path"))
    if target and path:
        raise GeometryError("Pass a target or a path, not both.")
    chain = _chain(spec, tool)
    driven = _driven_map(spec)
    if (target or path) and any(joint["name"] in driven for joint in chain):
        name = next(joint["name"] for joint in chain if joint["name"] in driven)
        raise GeometryError(
            f"Joint {name} is driven by relation {driven[name]!r}. "
            "Inverse kinematics solves a free serial chain."
        )
    free = [joint for joint in chain if joint["type"] in _FREE and joint["name"] not in driven]
    values, slide_values = _state(spec, joints, slides)
    clamped, limit_hits = _apply_limits(spec, values, slide_values)
    forward = _frame(spec, clamped, slide_values, tool, rotation_deg, at)
    forward["joints"] = {joint["name"]: clamped[joint["name"]] for joint in free}
    forward["slides"] = {joint["name"]: slide_values[joint["name"]] for joint in free if joint["type"] == "cylindrical"}
    forward["limits"] = limit_hits
    task = "forward"
    inverse = None
    traced = None
    if target:
        task = "pose" if target["rotation"] is not None else "position"
        inverse = _inverse(
            spec, free, clamped, slide_values, tool, rotation_deg, at, target, chain
        )
    if path:
        task = "path"
        traced = _trace(
            spec, shapes, free, clamped, slide_values, tool, rotation_deg, at, forward, path, chain
        )
    report = {
        "schema": SCHEMA,
        "assembly": spec["name"],
        "tool": {"instance": tool, "at": at, "rotation_deg": rotation_deg},
        "chain": [_chain_row(joint, driven) for joint in chain],
        "free_joints": [joint["name"] for joint in free],
        "dof": _dof(free),
        "task": task,
        "forward": forward,
    }
    if inverse is not None:
        report["inverse"] = inverse
    if traced is not None:
        report["path"] = traced
    return finish(report, kind=VERIFIED_CHECK, name="Kinematics", what=_what(task), limits=_limits(task),)


def _what(task: str) -> str:
    if task == "path":
        return (
            "The tool point is stepped along the path. Each pose is inverse kinematics, "
            "then the motion study checks those joint values for collisions and stops."
        )
    if task in ("position", "pose"):
        return (
            "Inverse kinematics returns the joint values that place the tool on the target. "
            "A solution is kept only when the forward pose matches."
        )
    return "The tool pose is the rigid transform of the joint chain."


def _limits(task: str) -> list[str]:
    rows = [
        "The chain is rigid. Links do not bend.",
        "A revolute value is degrees. A prismatic value is millimeters.",
        "A joint command past its stop is held at the stop in the forward pose.",
    ]
    if task in ("position", "pose", "path"):
        rows.append(
            "A solution is kept when the tool point is within 1e-6 mm. "
            "With an orientation, the tool frame must also be within 1e-8 radians."
        )
        rows.append(
            "Two parallel revolute joints return both elbows. "
            "A spherical wrist returns each shoulder, elbow, and wrist branch that reaches. "
            "Any other chain returns the solution reached from the seed."
        )
        rows.append("A joint value outside its min or max is reported and is not a solution.")
    if task == "path":
        rows.append(
            "The path is sampled. The motion study checks those poses. A hit between samples is not seen."
        )
        rows.append(f"A tool path has at most {_MAX_PATH_STEPS} poses.")
    return rows


def _inverse(spec, free, seed, slides, tool, rotation_deg, at, target, chain) -> dict:
    if not free:
        pose = _frame(spec, seed, slides, tool, rotation_deg, at)
        error = _pose_error(pose["matrix"], target["position"], target["rotation"])
        reached = _within_tolerance(error, target["rotation"] is not None)
        return _inverse_report(
            status="solved" if reached else "unreachable",
            reached=reached,
            message="The tool has no free joints." if not reached else "The tool is already on the target.",
            method="none",
            task="pose" if target["rotation"] is not None else "position",
            target=target,
            solutions=[]
            if not reached
            else [_solution("fixed", "none", {}, {}, pose, error, False, {})],
            rejected=[],
            closest=None if reached else error["position_mm"],
        )
    kind = _family(spec, free, seed, slides, tool, at, chain)
    pose_task = target["rotation"] is not None
    if kind == "planar_2r":
        found = _planar_family(spec, free, seed, slides, tool, rotation_deg, at, target)
        method = "closed_form"
    elif kind == "spherical_wrist" and pose_task:
        found = _spherical_family(spec, free, seed, slides, tool, rotation_deg, at, target, chain)
        method = "closed_form"
    else:
        found = _jacobian_family(spec, free, seed, slides, tool, rotation_deg, at, target)
        method = "jacobian"
    solutions, rejected = _partition(found["candidates"], free, pose_task)
    solutions = _dedup(solutions, free)
    rejected = _dedup(rejected, free)
    solutions.sort(key=lambda item: _seed_distance(item, seed, free))
    status, reached, message = _inverse_status(solutions, rejected, found, pose_task)
    nearest = solutions[0]["name"] if solutions else None
    best = solutions[0] if solutions else None
    return _inverse_report(
        status=status,
        reached=reached,
        message=message,
        method=method,
        task="pose" if pose_task else "position",
        target=target,
        solutions=solutions,
        rejected=rejected,
        closest=None if best else found.get("closest_mm"),
        nearest=nearest,
        best=best,
    )


def _inverse_report(
    *,
    status,
    reached,
    message,
    method,
    task,
    target,
    solutions,
    rejected,
    closest,
    nearest=None,
    best=None,
) -> dict:
    position_error = best["position_error_mm"] if best else closest
    orientation_error = best["orientation_error_deg"] if best else None
    return {
        "status": status,
        "reached": reached,
        "message": message,
        "method": method,
        "task": task,
        "target_position_mm": list(target["position"]),
        "nearest": nearest,
        "position_error_mm": position_error,
        "orientation_error_deg": orientation_error,
        "solutions": [_public_solution(item) for item in solutions],
        "rejected": [_public_rejected(item) for item in rejected],
    }


def _inverse_status(solutions, rejected, found, pose_task) -> tuple[str, bool, str]:
    if solutions:
        singular = all(item["singular"] for item in solutions)
        names = ", ".join(item["name"] for item in solutions)
        count = len(solutions)
        noun = "solution" if count == 1 else "solutions"
        if singular:
            why = found.get("singular_why") or "The arm is singular at the solution."
            return "singular", True, f"Singular: {why} {count} {noun}: {names}."
        extra = ""
        limited = [item for item in rejected if item.get("reason") == "limit"]
        if limited:
            extra = f" {len(limited)} more would pass a joint limit and were not used."
        return "solved", True, f"{count} {noun} reach this {'pose' if pose_task else 'position'}: {names}.{extra}"
    limited = [item for item in rejected if item.get("reason") == "limit"]
    if limited and not found.get("unreachable"):
        detail = _limit_sentence(limited[0])
        return "limit", False, f"The pose is outside the joint limits. {detail}"
    if found.get("singular") and not found.get("unreachable"):
        why = found.get("singular_why") or "The arm is singular."
        closest = found.get("closest_mm")
        extra = "" if closest is None else f" The closest tool point is {closest:.4f} mm away."
        return "singular", False, f"Singular: {why}{extra}"
    why = found.get("why") or "No joint values place the tool on the target."
    return "unreachable", False, f"Unreachable: {why}"


def _limit_sentence(item) -> str:
    hits = item.get("violations") or []
    if not hits:
        return f"{item['name']} reaches the pose only past a stop."
    hit = hits[0]
    return (
        f"{item['name']} would reach it with {hit['joint']} at {hit['value']:.4f} {hit['unit']}, "
        f"past its {hit['which']} of {hit['limit']:.4f}."
    )


def _partition(candidates, free, pose_task):
    solutions = []
    rejected = []
    for item in candidates:
        if item["position_error_mm"] > _POS_TOL_MM:
            continue
        if pose_task and item["orientation_error_rad"] > _ORI_TOL_RAD:
            rejected.append({**item, "reason": "orientation"})
            continue
        violations = _violations(item["joints"], item.get("slides") or {}, free)
        if violations:
            rejected.append({**item, "reason": "limit", "violations": violations})
            continue
        item["limit"] = "ok"
        solutions.append(item)
    return solutions, rejected


def _family(spec, free, seed, slides, tool, at, chain) -> str:
    revolute = [joint for joint in free if joint["type"] == "revolute"]
    others = [joint for joint in free if joint["type"] != "revolute"]
    if len(revolute) == 2 and not others and _axes_parallel(spec, revolute, seed, slides):
        return "planar_2r"
    if len(revolute) == 6 and not others and _wrist_geometry(spec, revolute, seed, slides, tool, at, chain):
        return "spherical_wrist"
    return "jacobian"


def _planar_family(spec, free, seed, slides, tool, rotation_deg, at, target) -> dict:
    proximal, distal = free
    raw = _planar_pair(
        spec,
        proximal,
        distal,
        tool,
        at,
        target["position"],
        _zeros_for(spec, seed, (proximal["name"], distal["name"])),
        slides,
        seed,
    )
    candidates = []
    singular = False
    for item in raw["solutions"]:
        values = dict(seed)
        values[proximal["name"]] = item["joints"][proximal["name"]]
        values[distal["name"]] = item["joints"][distal["name"]]
        pose = _frame(spec, values, slides, tool, rotation_deg, at)
        error = _pose_error(pose["matrix"], target["position"], target["rotation"])
        singular = singular or item["singular"]
        candidates.append(
            _solution(
                item["name"],
                "closed_form",
                {name: values[name] for name in (proximal["name"], distal["name"])},
                {},
                pose,
                error,
                item["singular"],
                {"elbow": item["elbow"]},
            )
        )
    return {
        "candidates": candidates,
        "unreachable": raw["unreachable"] and not candidates,
        "singular": singular and not raw["unreachable"],
        "singular_why": raw.get("singular_why"),
        "why": raw.get("why"),
        "closest_mm": _closest_error(candidates),
    }


def _planar_pair(spec, proximal, distal, tool, at, target_point, locked, slides, seed) -> dict:
    values = dict(locked)
    values[proximal["name"]] = 0.0
    values[distal["name"]] = 0.0
    worlds = _worlds(spec, values, slides)
    origin_a, axis_a = _joint_frame(worlds, proximal)
    origin_b, axis_b = _joint_frame(worlds, distal)
    if abs(_dot(axis_a, axis_b)) < 1.0 - 1e-8:
        return {"solutions": [], "unreachable": True, "why": "The two revolute axes are not parallel."}
    tool_point = _translation(_frame(spec, values, slides, tool, [0.0, 0.0, 0.0], at)["matrix"])
    u, v = _plane_basis(axis_a)
    link_a = _sub(origin_b, origin_a)
    link_b = _sub(tool_point, origin_b)
    planar_a = (_dot(link_a, u), _dot(link_a, v))
    planar_b = (_dot(link_b, u), _dot(link_b, v))
    length_a = math.hypot(*planar_a)
    length_b = math.hypot(*planar_b)
    if length_a < 1e-9 or length_b < 1e-9:
        return {"solutions": [], "unreachable": True, "why": "A link length is zero, so the elbow is undefined."}
    axial_tool = _dot(_sub(tool_point, origin_a), axis_a)
    delta = _sub(target_point, origin_a)
    axial = _dot(delta, axis_a)
    if abs(axial - axial_tool) > _POS_TOL_MM:
        return {
            "solutions": [],
            "unreachable": True,
            "why": (
                f"the target is {abs(axial - axial_tool):.4f} mm out of the plane of the joints."
            ),
        }
    radial = (_dot(delta, u), _dot(delta, v))
    radius = math.hypot(*radial)
    reach = length_a + length_b
    fold = abs(length_a - length_b)
    if radius > reach + _POS_TOL_MM:
        return {
            "solutions": [],
            "unreachable": True,
            "why": f"the target is {radius - reach:.4f} mm outside the reach of {reach:.4f} mm.",
        }
    if radius < fold - _POS_TOL_MM:
        return {
            "solutions": [],
            "unreachable": True,
            "why": f"the target is {fold - radius:.4f} mm inside the {fold:.4f} mm folded reach.",
        }
    cosine = (radius * radius - length_a * length_a - length_b * length_b) / (2.0 * length_a * length_b)
    cosine = max(-1.0, min(1.0, cosine))
    singular = abs(abs(cosine) - 1.0) <= 1e-8 or radius <= 1e-9
    gamma = math.acos(cosine)
    gammas = [gamma] if singular else [gamma, -gamma]
    theta_a0 = math.atan2(planar_a[1], planar_a[0])
    theta_b0 = math.atan2(planar_b[1], planar_b[0])
    beta0 = _wrap_rad(theta_b0 - theta_a0)
    sign = 1.0 if _dot(axis_b, axis_a) >= 0.0 else -1.0
    alpha = theta_a0 if radius <= 1e-12 else math.atan2(radial[1], radial[0])
    solutions = []
    for bend in gammas:
        distal_angle = (bend - beta0) / sign
        proximal_angle = alpha - math.atan2(
            length_b * math.sin(bend), length_a + length_b * math.cos(bend)
        )
        proximal_angle = _wrap_rad(proximal_angle - theta_a0)
        q_prox = _represent(math.degrees(proximal_angle), seed.get(proximal["name"], 0.0), proximal, True)
        q_dist = _represent(math.degrees(distal_angle), seed.get(distal["name"], 0.0), distal, True)
        elbow = "straight" if singular else ("up" if bend > 0.0 else "down")
        name = "elbow_straight" if elbow == "straight" else f"elbow_{elbow}"
        solutions.append(
            {
                "name": name,
                "elbow": elbow,
                "singular": singular,
                "joints": {proximal["name"]: q_prox, distal["name"]: q_dist},
            }
        )
    why = "The arm is fully stretched or fully folded, so the two elbows are the same solution."
    return {
        "solutions": solutions,
        "unreachable": False,
        "singular_why": why if singular else None,
        "why": None,
    }


def _spherical_family(spec, free, seed, slides, tool, rotation_deg, at, target, chain) -> dict:
    geometry = _wrist_geometry(spec, free, seed, slides, tool, at, chain)
    if geometry is None:
        return {
            "candidates": [],
            "unreachable": True,
            "singular": False,
            "why": "the wrist geometry is not a spherical wrist.",
            "closest_mm": None,
        }
    tool_matrix = _target_matrix(target)
    local = _matrix(_euler(*rotation_deg), at)
    instance = _multiply(tool_matrix, _rigid_inverse(local))
    wrist_center = _transform_point(instance, geometry["wrist_local"])
    desired_rotation = _rotation_of(instance)
    shoulder = geometry["shoulder"]
    distance = _norm(_sub(wrist_center, geometry["origin"]))
    reach = geometry["reach"]
    fold = geometry["fold"]
    if distance > reach + _POS_TOL_MM:
        return {
            "candidates": [],
            "unreachable": True,
            "singular": False,
            "why": (
                f"the wrist center is {distance - reach:.4f} mm outside the reach of {reach:.4f} mm."
            ),
            "closest_mm": distance - reach,
        }
    if distance < fold - _POS_TOL_MM:
        return {
            "candidates": [],
            "unreachable": True,
            "singular": False,
            "why": f"the wrist center is {fold - distance:.4f} mm inside the folded reach of {fold:.4f} mm.",
            "closest_mm": fold - distance,
        }
    radial = _project(_sub(wrist_center, geometry["origin"]), geometry["axis"])
    rho = _norm(radial)
    shoulder_singular = rho < 1e-4
    if shoulder_singular:
        planes = [("shoulder", seed.get(free[0]["name"], 0.0))]
    else:
        front = math.degrees(_angle_about(geometry["axis"], geometry["direction"], _scale(radial, 1.0 / rho)))
        planes = [("front", front), ("back", front + 180.0)]
    link_name = free[2]["child"]
    candidates = []
    singular_why = "The shoulder, the elbow, or the wrist is singular." if shoulder_singular else None
    for shoulder_name, q1 in planes:
        q1 = _represent(q1, seed.get(free[0]["name"], 0.0), free[0], True)
        locked = _zeros_for(spec, seed, (free[1]["name"], free[2]["name"]))
        locked[free[0]["name"]] = q1
        for name in (free[3]["name"], free[4]["name"], free[5]["name"]):
            locked[name] = 0.0
        arm = _planar_pair(
            spec,
            free[1],
            free[2],
            link_name,
            geometry["elbow_local"],
            wrist_center,
            locked,
            slides,
            seed,
        )
        if arm["unreachable"]:
            continue
        for elbow in arm["solutions"]:
            values = dict(seed)
            values[free[0]["name"]] = q1
            values[free[1]["name"]] = elbow["joints"][free[1]["name"]]
            values[free[2]["name"]] = elbow["joints"][free[2]["name"]]
            for wrist in _solve_wrist(geometry, spec, values, slides, free, desired_rotation, seed):
                posed = dict(values)
                posed[free[3]["name"]] = wrist["joints"][0]
                posed[free[4]["name"]] = wrist["joints"][1]
                posed[free[5]["name"]] = wrist["joints"][2]
                pose = _frame(spec, posed, slides, tool, rotation_deg, at)
                error = _pose_error(pose["matrix"], target["position"], target["rotation"])
                singular = shoulder_singular or elbow["singular"] or wrist["singular"]
                name = f"{shoulder_name}_{elbow['name']}_{wrist['name']}"
                candidates.append(
                    _solution(
                        name,
                        "closed_form",
                        {joint["name"]: posed[joint["name"]] for joint in free},
                        {},
                        pose,
                        error,
                        singular,
                        {
                            "shoulder": shoulder_name,
                            "elbow": elbow["elbow"],
                            "wrist": wrist["name"],
                        },
                    )
                )
                if singular and elbow["singular"]:
                    singular_why = "The arm is fully stretched or fully folded."
                elif singular and wrist["singular"]:
                    singular_why = "The wrist axes are aligned, so two wrist joints move the tool the same way."
                elif singular and shoulder_singular:
                    singular_why = "The wrist center is on the first joint axis."
    verified = [item for item in candidates if item["position_error_mm"] <= 1e-3]
    closest = _closest_error(candidates)
    if not verified:
        why = "the spherical wrist did not reach that pose."
        if closest is not None:
            why = f"the closest closed-form pose is {closest:.4f} mm from the target."
        return {
            "candidates": candidates,
            "unreachable": True,
            "singular": False,
            "why": why,
            "closest_mm": closest,
        }
    return {
        "candidates": candidates,
        "unreachable": False,
        "singular": all(item["singular"] for item in verified) if verified else False,
        "singular_why": singular_why,
        "why": None,
        "closest_mm": closest,
    }


def _wrist_geometry(spec, revolute, seed, slides, tool, at, chain):
    if len(revolute) != 6:
        return None
    values = dict(seed)
    for joint in revolute:
        values[joint["name"]] = 0.0
    worlds = _worlds(spec, values, slides)
    frames = [_joint_frame(worlds, joint) for joint in revolute]
    origins = [item[0] for item in frames]
    axes = [item[1] for item in frames]
    if abs(_dot(axes[0], axes[1])) > 1e-6:
        return None
    if abs(_dot(axes[1], axes[2])) < 1.0 - 1e-8:
        return None
    if _point_line_distance(origins[1], origins[0], axes[0]) > 1e-4:
        return None
    upper = _sub(origins[2], origins[1])
    if abs(_dot(upper, axes[1])) > 1e-4 or abs(_dot(upper, axes[0])) > 1e-4:
        return None
    center = _common_point(origins[3:], axes[3:])
    if center is None:
        return None
    if abs(_dot(_sub(center, origins[2]), axes[2])) > 1e-4:
        return None
    if abs(_dot(axes[3], axes[4])) > 1e-5 or abs(_dot(axes[4], axes[5])) > 1e-5:
        return None
    length_upper = _norm(upper)
    length_fore = _norm(_sub(center, origins[2]))
    if length_upper < 1e-6 or length_fore < 1e-6:
        return None
    link3 = revolute[2]["child"]
    elbow_local = _transform_point(_rigid_inverse(worlds[link3]), center)
    wrist_local = _transform_point(_rigid_inverse(worlds[tool]), center)
    direction = _normalize(upper)
    return {
        "shoulder": revolute[0]["name"],
        "origin": origins[1],
        "axis": axes[0],
        "direction": direction,
        "reach": length_upper + length_fore,
        "fold": abs(length_upper - length_fore),
        "elbow_local": elbow_local,
        "wrist_local": wrist_local,
        # Wrist angles are rotations in the parent frame. Those components are the
        # stored axes, not the world directions of the zero pose.
        "axes": [joint["axis"] for joint in revolute[3:]],
        "link3": link3,
        "parent": revolute[3]["parent"],
    }


def _solve_wrist(geometry, spec, values, slides, free, desired, seed) -> list[dict]:
    worlds = _worlds(spec, values, slides)
    parent = _rotation_of(worlds[geometry["parent"]])
    relative = _matmat(_transpose(parent), desired)
    a4, a5, a6 = geometry["axes"]
    seeds = [(seed.get(free[3]["name"], 0.0), seed.get(free[4]["name"], 0.0), seed.get(free[5]["name"], 0.0))]
    for angle in (0.0, 30.0, -30.0, 60.0, -60.0, 90.0, -90.0, 120.0, -120.0, 150.0, -150.0, 180.0):
        seeds.append((0.0, angle, 0.0))
    found = []
    for guess in seeds:
        solved = _wrist_newton(a4, a5, a6, relative, guess)
        if solved is None:
            continue
        angles = [
            _represent(solved[0], seed.get(free[3]["name"], 0.0), free[3], True),
            _represent(solved[1], seed.get(free[4]["name"], 0.0), free[4], True),
            _represent(solved[2], seed.get(free[5]["name"], 0.0), free[5], True),
        ]
        if any(_same_angles(angles, item["joints"]) for item in found):
            continue
        singular = _wrist_singular(a4, a5, a6, angles)
        found.append({"joints": angles, "singular": singular, "name": ""})
    found.sort(key=lambda item: -item["joints"][1])
    for index, item in enumerate(found):
        item["name"] = "wrist_a" if index == 0 else "wrist_b" if index == 1 else f"wrist_{index + 1}"
    return found[:4]


def _wrist_newton(a4, a5, a6, target, guess) -> list[float] | None:
    q = [float(guess[0]), float(guess[1]), float(guess[2])]
    for _ in range(25):
        current = _wrist_rotation(a4, a5, a6, q)
        error = _rotation_vector(_matmat(target, _transpose(current)))
        if _norm(error) < 1e-12:
            return q
        jacobian, _axes = _wrist_jacobian(a4, a5, a6, q)
        step = _solve_linear(jacobian, error)
        step = _limit_step(step, 25.0)
        if not _wrist_search(a4, a5, a6, target, q, step):
            break
    current = _wrist_rotation(a4, a5, a6, q)
    if _norm(_rotation_vector(_matmat(target, _transpose(current)))) < 1e-9:
        return q
    return None


def _wrist_search(a4, a5, a6, target, q, step) -> bool:
    current = _norm(_rotation_vector(_matmat(target, _transpose(_wrist_rotation(a4, a5, a6, q)))))
    scale = 1.0
    for _ in range(12):
        trial = [q[i] + scale * float(step[i]) for i in range(3)]
        error = _norm(_rotation_vector(_matmat(target, _transpose(_wrist_rotation(a4, a5, a6, trial)))))
        if error < current:
            q[:] = trial
            return True
        scale *= 0.5
    return False


def _wrist_rotation(a4, a5, a6, q) -> list[list[float]]:
    first = _rotation(a4, math.radians(q[0]))
    second = _rotation(a5, math.radians(q[1]))
    third = _rotation(a6, math.radians(q[2]))
    return _matmat(first, _matmat(second, third))


def _wrist_jacobian(a4, a5, a6, q):
    first = _rotation(a4, math.radians(q[0]))
    second = _matmat(first, _rotation(a5, math.radians(q[1])))
    axes = [a4, _matvec(first, a5), _matvec(second, a6)]
    scale = math.pi / 180.0
    jacobian = [[axes[column][row] * scale for column in range(3)] for row in range(3)]
    return jacobian, axes


def _wrist_singular(a4, a5, a6, q) -> bool:
    _jacobian, axes = _wrist_jacobian(a4, a5, a6, q)
    return abs(_dot(axes[0], _cross(axes[1], axes[2]))) < 1e-5


def _jacobian_family(spec, free, seed, slides, tool, rotation_deg, at, target) -> dict:
    variables = _variables(free)
    pose_task = target["rotation"] is not None
    guesses = [dict(seed)]
    if _arm_singular(spec, variables, seed, slides, tool, rotation_deg, at, target):
        for variable in variables:
            nudged = dict(seed)
            delta = 8.0 if variable["angular"] else 10.0
            nudged[variable["name"]] = _represent(
                seed[variable["name"]] + delta, seed[variable["name"]], variable["joint"], variable["angular"]
            )
            guesses.append(nudged)
    best = None
    best_slides = slides
    best_error = None
    limit_blocked = False
    singular = False
    for guess in guesses:
        for enforce in (True, False):
            solved, solved_slides, info = _newton(
                spec, variables, guess, slides, tool, rotation_deg, at, target, pose_task, enforce_limits=enforce
            )
            if info["error_mm"] is not None and (best_error is None or info["error_mm"] < best_error):
                best_error = info["error_mm"]
                best = solved
                best_slides = solved_slides
                limit_blocked = info["limit"]
                singular = info["singular"]
            if solved is not None and info["ok"]:
                pose = _frame(spec, solved, solved_slides, tool, rotation_deg, at)
                error = _pose_error(pose["matrix"], target["position"], target["rotation"])
                if _within_tolerance(error, pose_task):
                    candidate = _solution(
                        "seed",
                        "jacobian",
                        {joint["name"]: solved[joint["name"]] for joint in free},
                        {joint["name"]: solved_slides[joint["name"]] for joint in free if joint["type"] == "cylindrical"},
                        pose,
                        error,
                        info["singular"],
                        {},
                    )
                    return {
                        "candidates": [candidate],
                        "unreachable": False,
                        "singular": candidate["singular"],
                        "singular_why": "The Jacobian lost a direction of motion at the solution."
                        if candidate["singular"]
                        else None,
                        "why": None,
                        "closest_mm": error["position_mm"],
                    }
            if enforce and info["ok"]:
                break
    too_far, why = _beyond_reach(spec, free, seed, slides, tool, rotation_deg, at, target["position"])
    if too_far:
        return {
            "candidates": [],
            "unreachable": True,
            "singular": False,
            "why": why,
            "closest_mm": best_error,
        }
    if limit_blocked:
        return {
            "candidates": _blocked_candidate(spec, free, best, best_slides, tool, rotation_deg, at, target)
            if best
            else [],
            "unreachable": False,
            "singular": False,
            "why": "the joints that would continue are on a stop.",
            "closest_mm": best_error,
        }
    if singular:
        return {
            "candidates": [],
            "unreachable": False,
            "singular": True,
            "singular_why": "the Jacobian lost a direction of motion before the tool reached the target.",
            "why": None,
            "closest_mm": best_error,
        }
    return {
        "candidates": [],
        "unreachable": True,
        "singular": False,
        "why": "no joint values from the seed place the tool on the target.",
        "closest_mm": best_error,
    }


def _blocked_candidate(spec, free, values, slides, tool, rotation_deg, at, target):
    if values is None:
        return []
    pose = _frame(spec, values, slides, tool, rotation_deg, at)
    error = _pose_error(pose["matrix"], target["position"], target["rotation"])
    if not _within_tolerance(error, target["rotation"] is not None):
        return []
    return [
        _solution(
            "seed",
            "jacobian",
            {joint["name"]: values[joint["name"]] for joint in free},
            {joint["name"]: slides[joint["name"]] for joint in free if joint["type"] == "cylindrical"},
            pose,
            error,
            False,
            {},
        )
    ]


def _newton(spec, variables, seed, slides, tool, rotation_deg, at, target, pose_task, enforce_limits=True):
    values = dict(seed)
    local_slides = dict(slides)
    previous = None
    stalled = 0
    limit_blocked = False
    singular = False
    error_mm = None
    ok = False
    for _ in range(50):
        pose = _frame(spec, values, local_slides, tool, rotation_deg, at)
        error = _pose_error(pose["matrix"], target["position"], target["rotation"])
        error_mm = error["position_mm"]
        if _within_tolerance(error, pose_task):
            singular = _sigma_singular(spec, variables, values, local_slides, tool, rotation_deg, at, pose_task)
            return values, local_slides, {"ok": True, "error_mm": error_mm, "limit": False, "singular": singular}
        jacobian, residual, singular_now = _jacobian(
            spec, variables, values, local_slides, tool, rotation_deg, at, pose, target, pose_task
        )
        step, singular_step = _damped_step(jacobian, residual)
        singular = singular_now or singular_step
        step = _limit_step(step, 20.0)
        accepted, blocked, values, local_slides = _project_step(
            variables,
            values,
            step,
            spec,
            local_slides,
            tool,
            rotation_deg,
            at,
            target,
            pose_task,
            enforce_limits,
        )
        if blocked:
            limit_blocked = True
        if not accepted:
            stalled += 1
        else:
            stalled = 0
        if previous is not None and previous - error_mm < 1e-12:
            stalled += 1
        previous = error_mm
        if stalled >= 6:
            break
    return values, local_slides, {"ok": ok, "error_mm": error_mm, "limit": limit_blocked, "singular": singular}


def _jacobian(spec, variables, values, slides, tool, rotation_deg, at, pose, target, pose_task):
    worlds = _worlds(spec, values, slides)
    position = pose["position_mm"]
    rows = []
    for variable in variables:
        origin, axis = _joint_frame(worlds, variable["joint"])
        if variable["angular"]:
            arm = _cross(axis, _sub(position, origin))
            linear = _scale(arm, math.pi / 180.0)
            angular = _scale(axis, math.pi / 180.0)
        else:
            linear = list(axis)
            angular = [0.0, 0.0, 0.0]
        rows.append(linear + (angular if pose_task else []))
    jacobian = np.array(rows, dtype=float).T
    residual = list(_sub(target["position"], position))
    singular = False
    if pose_task and target["rotation"] is not None:
        twist = _rotation_vector(_matmat(target["rotation"], _transpose(_rotation_of(pose["matrix"]))))
        reach = _reach(spec, variables, values, slides, tool, rotation_deg, at)
        residual.extend([component * reach for component in twist])
        # Orientation rows were stored in radians per degree. Scale them to millimeters.
        for column in range(jacobian.shape[1]):
            for row in range(3, 6):
                jacobian[row, column] *= reach
    if jacobian.size:
        sigma = np.linalg.svd(jacobian, compute_uv=False)
        if len(sigma) and float(sigma[-1]) < 1e-4:
            singular = True
    return jacobian, np.array(residual, dtype=float), singular


def _damped_step(jacobian, residual):
    if jacobian.size == 0:
        return np.zeros(0), False
    sigma = np.linalg.svd(jacobian, compute_uv=False)
    smin = float(sigma[-1]) if len(sigma) else 0.0
    smax = float(sigma[0]) if len(sigma) else 0.0
    singular = smin < 1e-4
    if smin > 1e-6 * max(smax, 1e-9):
        step = np.linalg.lstsq(jacobian, residual, rcond=1e-12)[0]
        return step, singular
    damp = max(1e-3, 1e-2 * smax)
    gram = jacobian @ jacobian.T + (damp * damp) * np.eye(jacobian.shape[0])
    solved = np.linalg.lstsq(gram, residual, rcond=None)[0]
    return jacobian.T @ solved, True


def _project_step(
    variables, values, step, spec, slides, tool, rotation_deg, at, target, pose_task, enforce_limits=True
):
    if len(step) != len(variables):
        return False, False, values, slides
    current = _frame(spec, values, slides, tool, rotation_deg, at)
    current_error = _objective(current, target, pose_task)
    saw_block = False
    scale = 1.0
    for _ in range(12):
        trial = dict(values)
        trial_slides = dict(slides)
        blocked = False
        for index, variable in enumerate(variables):
            base = values[variable["name"]] if variable["field"] == "value" else slides[variable["name"]]
            raw = base + scale * float(step[index])
            if enforce_limits and variable["field"] == "value":
                limited, which = _clamp(raw, variable["joint"])
            else:
                limited, which = raw, None
            if which and abs(raw - limited) > 1e-12:
                blocked = True
            if variable["field"] == "value":
                trial[variable["name"]] = limited
            else:
                trial_slides[variable["name"]] = limited
        posed = _frame(spec, trial, trial_slides, tool, rotation_deg, at)
        if _objective(posed, target, pose_task) < current_error - 1e-15:
            # A shorter step that clears the stop is not a blocked solution.
            return True, blocked and scale == 1.0, trial, trial_slides
        if blocked:
            saw_block = True
        scale *= 0.5
    return False, saw_block, values, slides


def _objective(pose, target, pose_task) -> float:
    error = _pose_error(pose["matrix"], target["position"], target["rotation"] if pose_task else None)
    total = error["position_mm"]
    if pose_task:
        total += error["orientation_error_rad"] * 100.0
    return total


def _arm_singular(spec, variables, values, slides, tool, rotation_deg, at, target) -> bool:
    pose = _frame(spec, values, slides, tool, rotation_deg, at)
    jacobian, _residual, singular = _jacobian(
        spec, variables, values, slides, tool, rotation_deg, at, pose, target, target["rotation"] is not None
    )
    return singular or jacobian.size == 0


def _sigma_singular(spec, variables, values, slides, tool, rotation_deg, at, pose_task) -> bool:
    pose = _frame(spec, values, slides, tool, rotation_deg, at)
    dummy = {"position": pose["position_mm"], "rotation": _rotation_of(pose["matrix"]) if pose_task else None}
    _jacobian_matrix, _residual, singular = _jacobian(
        spec, variables, values, slides, tool, rotation_deg, at, pose, dummy, pose_task
    )
    return singular


def _beyond_reach(spec, free, seed, slides, tool, rotation_deg, at, target_point):
    if any(joint["type"] != "revolute" for joint in free):
        return False, None
    worlds = _worlds(spec, seed, slides)
    origin, _axis = _joint_frame(worlds, free[0])
    reach = _reach(spec, _variables(free), seed, slides, tool, rotation_deg, at)
    distance = _norm(_sub(target_point, origin))
    # The polyline reach is an upper bound on the distance from the first joint.
    if distance > reach + 1.0:
        return True, f"the target is {distance - reach:.4f} mm outside the reach of {reach:.4f} mm."
    return False, None


def _reach(spec, variables, values, slides, tool, rotation_deg, at) -> float:
    worlds = _worlds(spec, values, slides)
    points = []
    for variable in variables:
        origin, _axis = _joint_frame(worlds, variable["joint"])
        points.append(origin)
    points.append(_translation(_frame(spec, values, slides, tool, rotation_deg, at)["matrix"]))
    total = 0.0
    for left, right in zip(points, points[1:]):
        total += _norm(_sub(right, left))
    return max(total, 1.0)


def _trace(spec, shapes, free, seed, slides, tool, rotation_deg, at, forward, path, chain) -> dict:
    samples = _samples(forward, path)
    steps = []
    commands = []
    stopped = None
    seed_values = dict(seed)
    slides = dict(slides)
    for index, sample in enumerate(samples):
        if stopped is not None:
            steps.append(
                {
                    "index": index,
                    "time_s": sample["time_s"],
                    "target_position_mm": sample["position"],
                    "position_mm": None,
                    "joints": None,
                    "commanded": None,
                    "status": "not_solved",
                    "singular": False,
                    "solution": None,
                    "position_error_mm": None,
                    "orientation_error_deg": None,
                    "message": f"Not solved because step {stopped} did not reach the path.",
                }
            )
            continue
        target = {"position": sample["position"], "rotation": sample["rotation"]}
        solved = _inverse(spec, free, seed_values, slides, tool, rotation_deg, at, target, chain)
        step, command, failed = _path_step(index, sample, solved, free)
        if step["status"] == "limit" and step["joints"] and command is not None:
            posed = dict(seed_values)
            posed.update(step["joints"])
            posed_slides = dict(slides)
            posed_slides.update(command["slides"])
            pose = _frame(spec, posed, posed_slides, tool, rotation_deg, at)
            error = _pose_error(pose["matrix"], sample["position"], sample["rotation"])
            step["position_mm"] = pose["position_mm"]
            step["position_error_mm"] = error["position_mm"]
            step["orientation_error_deg"] = error["orientation_deg"]
        steps.append(step)
        if command is not None:
            commands.append(command)
            if step["status"] != "limit" and step["joints"]:
                seed_values.update(step["joints"])
                slides.update(command["slides"])
        if failed:
            stopped = index
    motion = None
    checked = False
    if shapes is not None and commands:
        motion = _motion(spec, shapes, free, commands, path)
        checked = True
    status, reached, message = _path_status(steps, motion, checked)
    return {
        "kind": path["kind"],
        "status": status,
        "reached": reached,
        "message": message,
        "step_count": len(steps),
        "steps": steps,
        "collisions_checked": checked,
        "motion_study": motion,
    }


def _path_step(index, sample, solved, free):
    chosen = solved["solutions"][0] if solved["solutions"] else None
    rejected = next((item for item in solved["rejected"] if item["reason"] == "limit"), None)
    failed = chosen is None
    if chosen is not None:
        command = {"joints": dict(chosen["joints"]), "slides": dict(chosen.get("slides") or {})}
        joints = dict(chosen["joints"])
        status = "singular" if chosen["singular"] or solved["status"] == "singular" else "solved"
        message = solved["message"]
        position = chosen["position_mm"]
        position_error = chosen["position_error_mm"]
        orientation_error = chosen["orientation_error_deg"]
        name = chosen["name"]
        singular = bool(chosen["singular"])
    elif rejected is not None:
        command = {"joints": dict(rejected["joints"]), "slides": dict(rejected.get("slides") or {})}
        joints = _clamped_joints(rejected["joints"], free)
        status = "limit"
        message = solved["message"]
        position = None
        position_error = rejected["position_error_mm"]
        orientation_error = rejected.get("orientation_error_deg")
        name = rejected["name"]
        singular = bool(rejected["singular"])
    else:
        command = None
        joints = None
        status = solved["status"]
        message = solved["message"]
        position = None
        position_error = solved["position_error_mm"]
        orientation_error = solved["orientation_error_deg"]
        name = None
        singular = solved["status"] == "singular"
    step = {
        "index": index,
        "time_s": sample["time_s"],
        "target_position_mm": sample["position"],
        "position_mm": position,
        "joints": joints,
        "commanded": command,
        "status": status,
        "singular": singular,
        "solution": name,
        "position_error_mm": position_error,
        "orientation_error_deg": orientation_error,
        "message": message,
    }
    return step, command, failed


def _path_status(steps, motion, checked):
    # A singular step that still reached is not a tracking failure.
    failure = next((step for step in steps if step["status"] in ("unreachable", "limit")), None)
    if failure is None:
        failure = next(
            (
                step
                for step in steps
                if step["status"] == "singular" and (step["position_error_mm"] is None or step["position_error_mm"] > _POS_TOL_MM)
            ),
            None,
        )
    if failure is not None:
        return failure["status"], False, f"Step {failure['index']}: {failure['message']}"
    if motion is not None:
        study = motion["status"]
        if study == "collision_and_limit":
            return study, True, "The path reaches the tool points. The motion study found a collision and a joint limit."
        if study == "collision":
            return "collision", True, "The path reaches the tool points. The motion study found a collision."
        if study == "limit":
            return "limit", True, "The path reaches the tool points. The motion study held a joint on a stop."
    singular = next((step for step in steps if step["singular"]), None)
    if singular is not None:
        return "singular", True, f"Step {singular['index']} reaches the tool point where the arm is singular."
    if not checked:
        return "solved", True, "The path reaches the tool points. Collisions were not checked because no solids were passed."
    return "solved", True, "The path reaches the tool points. The motion study found no collision and no joint limit."


def _motion(spec, shapes, free, commands, path) -> dict:
    times = _sample_times(path["duration_s"], len(commands))
    drivers = []
    for joint in free:
        frames = []
        for time_s, command in zip(times, commands):
            frame = {"time_s": time_s, "value": float(command["joints"][joint["name"]])}
            if joint["type"] == "cylindrical":
                frame["slide"] = float(command["slides"].get(joint["name"], joint["slide"]))
            frames.append(frame)
        drivers.append({"joint": joint["name"], "keyframes": frames})
    study = {
        "name": "tool_path",
        "steps": len(commands),
        "clearance_mm": path["clearance_mm"],
        "drivers": drivers,
    }
    if not path["duration_assumed"]:
        study["duration_s"] = path["duration_s"]
    return run_study(spec, shapes, study)


def _samples(forward, path) -> list[dict]:
    start = {"position": list(forward["position_mm"]), "rotation": _rotation_of(forward["matrix"])}
    if path["kind"] == "line":
        end_rotation = path["target"]["rotation"]
        end = {"position": list(path["target"]["position"]), "rotation": end_rotation}
        count = path["steps"]
        samples = []
        for index in range(count):
            blend = 0.0 if count == 1 else index / (count - 1)
            pose = _interpolate(start, end if end_rotation is not None else {**end, "rotation": None}, blend)
            if end_rotation is None:
                pose["rotation"] = None
            pose["time_s"] = _sample_times(path["duration_s"], count)[index]
            samples.append(pose)
        return samples
    waypoints = path["waypoints"]
    poses = []
    times = _sample_times(path["duration_s"], (path["steps"] - 1) * (len(waypoints) - 1) + 1)
    cursor = 0
    for segment in range(len(waypoints) - 1):
        for index in range(0 if segment == 0 else 1, path["steps"]):
            blend = index / (path["steps"] - 1)
            pose = _interpolate(waypoints[segment], waypoints[segment + 1], blend)
            pose["time_s"] = times[cursor]
            poses.append(pose)
            cursor += 1
    return poses


def _interpolate(start, end, blend) -> dict:
    position = [
        start["position"][index] + (end["position"][index] - start["position"][index]) * blend for index in range(3)
    ]
    if start.get("rotation") is None or end.get("rotation") is None:
        return {"position": position, "rotation": None}
    return {"position": position, "rotation": _slerp(start["rotation"], end["rotation"], blend)}


def _target(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise GeometryError("A kinematics target must be an object.")
    unknown = set(raw) - _TARGET_KEYS
    if unknown:
        raise GeometryError(f"A kinematics target has unknown fields: {', '.join(sorted(unknown))}.")
    rotation = None
    position = None
    if raw.get("matrix") is not None:
        matrix = _matrix4(raw.get("matrix"), "Target matrix")
        rotation = _rotation_of(matrix)
        position = _translation(matrix)
    if raw.get("rotation_deg") is not None:
        if rotation is not None:
            raise GeometryError("A target has both rotation_deg and a matrix.")
        rotation = _euler(*_rotation_deg(raw.get("rotation_deg"), "Target rotation_deg"))
    if raw.get("position") is not None:
        position = list(finite_vec3(raw.get("position"), "Target position"))
    if position is None:
        raise GeometryError("A target needs a position or a matrix.")
    return {"position": position, "rotation": rotation}


def _path(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise GeometryError("A tool path must be an object.")
    unknown = set(raw) - _PATH_KEYS
    if unknown:
        raise GeometryError(f"A tool path has unknown fields: {', '.join(sorted(unknown))}.")
    kind = raw.get("kind")
    if kind not in ("line", "waypoints"):
        raise GeometryError("A tool path kind is line or waypoints.")
    steps = raw.get("steps")
    if isinstance(steps, bool) or not isinstance(steps, (int, float)) or (
        isinstance(steps, float) and not steps.is_integer()
    ):
        raise GeometryError("A tool path needs steps, a whole number.")
    steps = int(steps)
    if steps < 2:
        raise GeometryError("A tool path needs at least two steps.")
    clearance = 0.0 if raw.get("clearance_mm") is None else finite_number(raw.get("clearance_mm"), "clearance_mm")
    if clearance < 0:
        raise GeometryError("clearance_mm cannot be negative.")
    if raw.get("duration_s") is None:
        duration = 1.0
        assumed = True
    else:
        duration = finite_number(raw.get("duration_s"), "duration_s", bound=None)
        if duration <= 0:
            raise GeometryError("duration_s must be positive.")
        assumed = False
    if kind == "line":
        if raw.get("waypoints") is not None:
            raise GeometryError("A line path has a target, not waypoints.")
        target = _target(raw.get("target"))
        if target is None:
            raise GeometryError("A line path needs a target.")
        if steps > _MAX_PATH_STEPS:
            raise GeometryError(f"A tool path can have at most {_MAX_PATH_STEPS} poses.")
        return {
            "kind": "line",
            "target": target,
            "steps": steps,
            "clearance_mm": clearance,
            "duration_s": duration,
            "duration_assumed": assumed,
        }
    if raw.get("target") is not None:
        raise GeometryError("A waypoint path lists waypoints, not a single target.")
    waypoints = raw.get("waypoints")
    if not isinstance(waypoints, list) or len(waypoints) < 2:
        raise GeometryError("A waypoint path needs at least two waypoints.")
    parsed = []
    oriented = None
    for index, waypoint in enumerate(waypoints):
        item = _target(waypoint)
        has_rotation = item["rotation"] is not None
        if oriented is None:
            oriented = has_rotation
        elif oriented != has_rotation:
            raise GeometryError("Every waypoint has an orientation, or none of them do.")
        parsed.append(item)
    total = (steps - 1) * (len(parsed) - 1) + 1
    if total > _MAX_PATH_STEPS:
        raise GeometryError(f"This path has {total} poses. A tool path can have at most {_MAX_PATH_STEPS}.")
    return {
        "kind": "waypoints",
        "waypoints": parsed,
        "steps": steps,
        "clearance_mm": clearance,
        "duration_s": duration,
        "duration_assumed": assumed,
    }


def _solution(name, method, joints, slides, pose, error, singular, branch) -> dict:
    return {
        "name": name,
        "method": method,
        "branch": branch,
        "singular": bool(singular),
        "joints": {key: float(value) for key, value in joints.items()},
        "slides": {key: float(value) for key, value in slides.items()},
        "position_mm": list(pose["position_mm"]),
        "rotation_deg": list(pose["rotation_deg"]),
        "matrix": pose["matrix"],
        "position_error_mm": error["position_mm"],
        "orientation_error_deg": error["orientation_deg"],
        "orientation_error_rad": error["orientation_error_rad"],
        "limit": "ok",
    }


def _public_solution(item) -> dict:
    public = {
        "name": item["name"],
        "method": item["method"],
        "singular": item["singular"],
        "joints": dict(item["joints"]),
        "position_mm": list(item["position_mm"]),
        "rotation_deg": list(item["rotation_deg"]),
        "matrix": item["matrix"],
        "position_error_mm": item["position_error_mm"],
        "orientation_error_deg": item["orientation_error_deg"],
        "limit": "ok",
    }
    if item.get("slides"):
        public["slides"] = dict(item["slides"])
    if item.get("branch"):
        public["branch"] = dict(item["branch"])
    return public


def _public_rejected(item) -> dict:
    public = {
        "name": item["name"],
        "reason": item.get("reason"),
        "joints": dict(item["joints"]),
        "position_mm": list(item["position_mm"]),
        "position_error_mm": item["position_error_mm"],
        "orientation_error_deg": item["orientation_error_deg"],
        "singular": item["singular"],
    }
    if item.get("slides"):
        public["slides"] = dict(item["slides"])
    if item.get("violations"):
        public["violations"] = list(item["violations"])
    if item.get("branch"):
        public["branch"] = dict(item["branch"])
    return public


def _frame(spec, values, slides, tool, rotation_deg, at) -> dict:
    worlds = _worlds(spec, values, slides)
    local = _matrix(_euler(*rotation_deg), at)
    world = _multiply(worlds[tool], local)
    return {
        "position_mm": _translation(world),
        "rotation_deg": _euler_from(_rotation_of(world)),
        "matrix": [list(row) for row in world],
    }


def _pose_error(matrix, position, rotation) -> dict:
    current = _translation(matrix)
    delta = _sub(position, current)
    distance = _norm(delta)
    orientation_rad = 0.0
    orientation_deg = None
    if rotation is not None:
        twist = _rotation_vector(_matmat(rotation, _transpose(_rotation_of(matrix))))
        orientation_rad = _norm(twist)
        orientation_deg = math.degrees(orientation_rad)
    return {
        "position_mm": distance,
        "orientation_error_rad": orientation_rad,
        "orientation_deg": orientation_deg,
    }


def _within_tolerance(error, pose_task) -> bool:
    if error["position_mm"] > _POS_TOL_MM:
        return False
    if pose_task and error["orientation_error_rad"] > _ORI_TOL_RAD:
        return False
    return True


def _closest_error(candidates):
    if not candidates:
        return None
    return min(item["position_error_mm"] for item in candidates)


def _variables(free) -> list[dict]:
    variables = []
    for joint in free:
        angular = joint["type"] in ("revolute", "cylindrical")
        variables.append(
            {"name": joint["name"], "field": "value", "angular": angular, "joint": joint}
        )
        if joint["type"] == "cylindrical":
            variables.append({"name": joint["name"], "field": "slide", "angular": False, "joint": joint})
    return variables


def _violations(joints, slides, free) -> list[dict]:
    hits = []
    for joint in free:
        value = joints[joint["name"]]
        snapped = _snap(value, joint)
        if "min" in joint and snapped < joint["min"] - 1e-8:
            hits.append(_hit(joint, snapped, "min"))
        if "max" in joint and snapped > joint["max"] + 1e-8:
            hits.append(_hit(joint, snapped, "max"))
    return hits


def _hit(joint, value, which) -> dict:
    return {
        "joint": joint["name"],
        "value": value,
        "which": which,
        "limit": joint[which],
        "unit": _unit_name(joint),
    }


def _clamped_joints(joints, free) -> dict:
    clamped = {}
    for joint in free:
        value, _which = _clamp(joints[joint["name"]], joint)
        clamped[joint["name"]] = value
    return clamped


def _dedup(items, free) -> list[dict]:
    kept = []
    for item in items:
        if any(_same_solution(item, other, free) for other in kept):
            continue
        kept.append(item)
    return kept


def _same_solution(left, right, free) -> bool:
    if not _same_joint_map(left["joints"], right["joints"], free):
        return False
    left_slides = left.get("slides") or {}
    right_slides = right.get("slides") or {}
    if set(left_slides) != set(right_slides):
        return False
    return all(abs(left_slides[name] - right_slides[name]) <= 1e-4 for name in left_slides)


def _same_joint_map(left, right, free) -> bool:
    if set(left) != set(right):
        return False
    angular = {joint["name"] for joint in free if joint["type"] in ("revolute", "cylindrical")}
    for name in left:
        delta = left[name] - right[name]
        if name in angular:
            delta = _wrap_deg(delta)
        if abs(delta) > 1e-4:
            return False
    return True


def _same_angles(left, right) -> bool:
    return all(abs(_wrap_deg(left[index] - right[index])) < 1e-4 for index in range(3))


def _seed_distance(item, seed, free) -> float:
    total = 0.0
    for joint in free:
        delta = item["joints"][joint["name"]] - seed.get(joint["name"], 0.0)
        if joint["type"] in ("revolute", "cylindrical"):
            delta = _wrap_deg(delta)
        total += delta * delta
    return total


def _represent(value, seed, joint, angular) -> float:
    if not angular:
        return _snap(value, joint)
    base = _wrap_deg(value)
    turn = round((seed - base) / 360.0)
    candidates = [base + 360.0 * (turn + offset) for offset in range(-2, 3)]
    inside = [item for item in candidates if _inside(item, joint)]
    chosen = min(inside or candidates, key=lambda item: abs(item - seed))
    return _snap(chosen, joint)


def _snap(value, joint) -> float:
    for bound in ("min", "max"):
        if bound in joint and abs(value - joint[bound]) <= 1e-7:
            return float(joint[bound])
    return float(value)


def _inside(value, joint) -> bool:
    if "min" in joint and value < joint["min"] - 1e-8:
        return False
    if "max" in joint and value > joint["max"] + 1e-8:
        return False
    return True


def _state(spec, joints, slides):
    values = {}
    slide_values = {}
    for joint in spec["joints"]:
        values[joint["name"]] = joints.get(joint["name"], joint["value"])
        slide_values[joint["name"]] = slides.get(joint["name"], joint["slide"])
    return values, slide_values


def _zeros_for(spec, seed, names) -> dict:
    values = {joint["name"]: seed.get(joint["name"], joint["value"]) for joint in spec["joints"]}
    for name in names:
        values[name] = 0.0
    return values


def _apply_limits(spec, values, slides):
    clamped = dict(values)
    hits = []
    for joint in spec["joints"]:
        limited, which = _clamp(values[joint["name"]], joint)
        clamped[joint["name"]] = limited
        if which:
            hits.append(
                {
                    "joint": joint["name"],
                    "commanded": values[joint["name"]],
                    "enforced": limited,
                    "which": which,
                    "limit": joint.get(which),
                    "unit": _unit_name(joint),
                }
            )
    return clamped, hits


def _chain(spec, tool) -> list[dict]:
    names = {item["name"] for item in spec["instances"]}
    if tool not in names:
        known = ", ".join(sorted(names)) or "(none)"
        raise GeometryError(f"No instance {tool!r}. Instances: {known}.")
    by_child = {joint["child"]: joint for joint in spec["joints"]}
    chain = []
    name = tool
    seen = set()
    while name in by_child:
        if name in seen:
            raise GeometryError(f"Joint tree cycles through {name!r}.")
        seen.add(name)
        chain.append(by_child[name])
        name = by_child[name]["parent"]
    chain.reverse()
    return chain


def _chain_row(joint, driven) -> dict:
    row = {
        "joint": joint["name"],
        "type": joint["type"],
        "parent": joint["parent"],
        "child": joint["child"],
        "unit": _unit_name(joint),
        "driven": joint["name"] in driven,
    }
    if "min" in joint:
        row["min"] = joint["min"]
    if "max" in joint:
        row["max"] = joint["max"]
    return row


def _driven_map(spec) -> dict[str, str]:
    driven = {}
    for relation in spec["relations"]:
        driven[relation["driven"]] = relation["name"]
        if relation["type"] == "cylinder":
            driven[relation["stroke"]] = relation["name"]
    return driven


def _dof(free) -> int:
    total = 0
    for joint in free:
        total += 2 if joint["type"] == "cylindrical" else 1
    return total


def _joint_map(raw, spec, label) -> dict[str, float]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise GeometryError(f"{label} must name joints.")
    known = {joint["name"]: joint for joint in spec["joints"]}
    parsed = {}
    for key, value in raw.items():
        name = str(key)
        joint = known.get(name)
        if joint is None:
            listed = ", ".join(known) or "(none)"
            raise GeometryError(f"{label} names unknown joint {name!r}. Joints: {listed}.")
        if joint["type"] == "fixed":
            raise GeometryError(f"{label} cannot set fixed joint {name}.")
        unit = "deg" if joint["type"] in ("revolute", "cylindrical") else "mm"
        parsed[name] = _scalar(value, f"{label} {name}", unit)
    return parsed


def _slide_map(raw, spec) -> dict[str, float]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise GeometryError("slides must name cylindrical joints.")
    known = {joint["name"]: joint for joint in spec["joints"]}
    parsed = {}
    for key, value in raw.items():
        name = str(key)
        joint = known.get(name)
        if joint is None:
            raise GeometryError(f"slides names unknown joint {name!r}.")
        if joint["type"] != "cylindrical":
            raise GeometryError(f"{name} is {joint['type']}, so it has no slide.")
        parsed[name] = _scalar(value, f"slides {name}", "mm")
    return parsed


def _scalar(value, label, unit) -> float:
    if unit == "mm":
        return finite_number(value, label, unit="mm")
    if isinstance(value, bool):
        raise GeometryError(f"{label} is not a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a finite number.") from exc
    if not math.isfinite(number) or abs(number) > _ANGLE_LIMIT:
        raise GeometryError(f"{label} must be a finite angle within {_ANGLE_LIMIT:.0f} deg.")
    return number


def _rotation_deg(value, label) -> list[float]:
    if value is None:
        return [0.0, 0.0, 0.0]
    return list(finite_vec3(value, label, bound=None))


def _axes_parallel(spec, joints, seed, slides) -> bool:
    worlds = _worlds(spec, seed, slides)
    _origin_a, axis_a = _joint_frame(worlds, joints[0])
    _origin_b, axis_b = _joint_frame(worlds, joints[1])
    return abs(_dot(axis_a, axis_b)) >= 1.0 - 1e-8


def _joint_frame(worlds, joint):
    parent = worlds[joint["parent"]]
    origin = _transform_point(parent, joint["parent_anchor"])
    axis = _transform_vector(parent, joint["axis"])
    return origin, axis


def _common_point(origins, axes):
    point = _line_intersection(origins[0], axes[0], origins[1], axes[1])
    if point is None:
        return None
    if _point_line_distance(point, origins[2], axes[2]) > 1e-4:
        return None
    return point


def _line_intersection(origin_a, axis_a, origin_b, axis_b):
    cross = _cross(axis_a, axis_b)
    denom = _dot(cross, cross)
    if denom < 1e-18:
        return None
    between = _sub(origin_b, origin_a)
    time_a = _dot(_cross(between, axis_b), cross) / denom
    time_b = _dot(_cross(between, axis_a), cross) / denom
    point_a = _add(origin_a, _scale(axis_a, time_a))
    point_b = _add(origin_b, _scale(axis_b, time_b))
    if _norm(_sub(point_a, point_b)) > 1e-4:
        return None
    return _scale(_add(point_a, point_b), 0.5)


def _point_line_distance(point, origin, axis) -> float:
    return _norm(_cross(_sub(point, origin), axis))


def _target_matrix(target) -> list[list[float]]:
    rotation = target["rotation"] if target["rotation"] is not None else _euler(0.0, 0.0, 0.0)
    return _matrix(rotation, target["position"])


def _matrix4(value, label) -> list[list[float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise GeometryError(f"{label} must be a 4×4 matrix.")
    rows = []
    for index, row in enumerate(value):
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            raise GeometryError(f"{label} must be a 4×4 matrix.")
        rows.append([finite_number(component, f"{label} row {index}", bound=None) for component in row])
    rotation = _rotation_of(rows)
    if abs(_determinant(rotation) - 1.0) > 1e-4:
        raise GeometryError(f"{label} is not a rigid transform.")
    return rows


def _euler_from(rotation) -> list[float]:
    sine = max(-1.0, min(1.0, -rotation[2][0]))
    pitch = math.asin(sine)
    if abs(math.cos(pitch)) > 1e-8:
        roll = math.atan2(rotation[2][1], rotation[2][2])
        yaw = math.atan2(rotation[1][0], rotation[0][0])
    else:
        roll = math.atan2(-rotation[0][1], rotation[1][1])
        yaw = 0.0
    return [math.degrees(roll), math.degrees(pitch), math.degrees(yaw)]


def _rotation_vector(rotation) -> list[float]:
    cosine = (rotation[0][0] + rotation[1][1] + rotation[2][2] - 1.0) / 2.0
    cosine = max(-1.0, min(1.0, cosine))
    angle = math.acos(cosine)
    if angle < 1e-12:
        return [0.0, 0.0, 0.0]
    if math.pi - angle < 1e-6:
        axis = _pi_axis(rotation)
        return _scale(axis, angle)
    factor = angle / (2.0 * math.sin(angle))
    return [
        factor * (rotation[2][1] - rotation[1][2]),
        factor * (rotation[0][2] - rotation[2][0]),
        factor * (rotation[1][0] - rotation[0][1]),
    ]


def _pi_axis(rotation) -> list[float]:
    diagonal = [rotation[0][0], rotation[1][1], rotation[2][2]]
    index = diagonal.index(max(diagonal))
    axis = [0.0, 0.0, 0.0]
    axis[index] = math.sqrt(max(0.0, (diagonal[index] + 1.0) / 2.0))
    if axis[index] < 1e-8:
        return [1.0, 0.0, 0.0]
    for other in range(3):
        if other == index:
            continue
        axis[other] = (rotation[index][other] + rotation[other][index]) / (4.0 * axis[index])
    return _normalize(axis)


def _slerp(start, end, blend) -> list[list[float]]:
    left = _quat_from_matrix(start)
    right = _quat_from_matrix(end)
    dot = sum(left[index] * right[index] for index in range(4))
    if dot < 0.0:
        right = [-component for component in right]
        dot = -dot
    if dot > 0.9995:
        mixed = [left[index] + (right[index] - left[index]) * blend for index in range(4)]
        return _quat_to_matrix(_normalize_quat(mixed))
    theta = math.acos(max(-1.0, min(1.0, dot)))
    sine = math.sin(theta)
    scale_left = math.sin((1.0 - blend) * theta) / sine
    scale_right = math.sin(blend * theta) / sine
    mixed = [scale_left * left[index] + scale_right * right[index] for index in range(4)]
    return _quat_to_matrix(mixed)


def _quat_from_matrix(rotation) -> list[float]:
    trace = rotation[0][0] + rotation[1][1] + rotation[2][2]
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        return [
            0.25 * scale,
            (rotation[2][1] - rotation[1][2]) / scale,
            (rotation[0][2] - rotation[2][0]) / scale,
            (rotation[1][0] - rotation[0][1]) / scale,
        ]
    if rotation[0][0] > rotation[1][1] and rotation[0][0] > rotation[2][2]:
        scale = math.sqrt(1.0 + rotation[0][0] - rotation[1][1] - rotation[2][2]) * 2.0
        return [
            (rotation[2][1] - rotation[1][2]) / scale,
            0.25 * scale,
            (rotation[0][1] + rotation[1][0]) / scale,
            (rotation[0][2] + rotation[2][0]) / scale,
        ]
    if rotation[1][1] > rotation[2][2]:
        scale = math.sqrt(1.0 + rotation[1][1] - rotation[0][0] - rotation[2][2]) * 2.0
        return [
            (rotation[0][2] - rotation[2][0]) / scale,
            (rotation[0][1] + rotation[1][0]) / scale,
            0.25 * scale,
            (rotation[1][2] + rotation[2][1]) / scale,
        ]
    scale = math.sqrt(1.0 + rotation[2][2] - rotation[0][0] - rotation[1][1]) * 2.0
    return [
        (rotation[1][0] - rotation[0][1]) / scale,
        (rotation[0][2] + rotation[2][0]) / scale,
        (rotation[1][2] + rotation[2][1]) / scale,
        0.25 * scale,
    ]


def _quat_to_matrix(quaternion) -> list[list[float]]:
    w, x, y, z = quaternion
    return [
        [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
        [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
        [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
    ]


def _limit_step(step, cap) -> np.ndarray:
    if len(step) == 0:
        return step
    peak = max(abs(float(component)) for component in step)
    if peak <= cap or peak == 0.0:
        return step
    return step * (cap / peak)


def _solve_linear(jacobian, residual) -> np.ndarray:
    matrix = np.array(jacobian, dtype=float)
    vector = np.array(residual, dtype=float)
    try:
        return np.linalg.solve(matrix, vector)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(matrix, vector, rcond=1e-12)[0]


def _plane_basis(axis):
    helper = [1.0, 0.0, 0.0] if abs(axis[0]) < 0.9 else [0.0, 1.0, 0.0]
    u = _normalize(_cross(helper, axis))
    v = _cross(axis, u)
    return u, v


def _angle_about(axis, start, end) -> float:
    start = _normalize(_project(start, axis))
    end = _normalize(_project(end, axis))
    return math.atan2(_dot(axis, _cross(start, end)), _dot(start, end))


def _project(vector, axis):
    return _sub(vector, _scale(axis, _dot(vector, axis)))


def _rigid_inverse(transform) -> list[list[float]]:
    rotation = _transpose(_rotation_of(transform))
    translation = _translation(transform)
    moved = [-sum(rotation[row][column] * translation[column] for column in range(3)) for row in range(3)]
    return _matrix(rotation, moved)


def _rotation_of(transform) -> list[list[float]]:
    return [list(row[:3]) for row in transform[:3]]


def _translation(transform) -> list[float]:
    return [float(transform[row][3]) for row in range(3)]


def _transpose(rotation) -> list[list[float]]:
    return [[rotation[column][row] for column in range(3)] for row in range(3)]


def _determinant(rotation) -> float:
    return _dot(rotation[0], _cross(rotation[1], rotation[2]))


def _wrap_rad(angle) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def _wrap_deg(angle) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def _add(left, right):
    return [left[index] + right[index] for index in range(3)]


def _sub(left, right):
    return [left[index] - right[index] for index in range(3)]


def _scale(vector, factor):
    return [component * factor for component in vector]


def _dot(left, right) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _cross(left, right):
    return [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]


def _norm(vector) -> float:
    return math.sqrt(_dot(vector, vector))


def _normalize(vector):
    length = _norm(vector)
    if length < 1e-15:
        raise GeometryError("A direction used by the chain solver is zero.")
    return _scale(vector, 1.0 / length)


def _normalize_quat(quaternion) -> list[float]:
    length = math.sqrt(sum(component * component for component in quaternion))
    if length < 1e-15:
        return [1.0, 0.0, 0.0, 0.0]
    return [component / length for component in quaternion]
